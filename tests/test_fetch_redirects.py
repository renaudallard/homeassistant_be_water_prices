# Copyright (c) 2026, Renaud Allard <renaud@allard.it>
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice,
#    this list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
# ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE
# LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR
# CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF
# SUBSTITUTE GOODS OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS
# INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN
# CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE)
# ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.

"""A redirect is checked before anything is sent to where it points.

aiohttp follows a redirect before its caller sees the answer, so a check
on the URL a fetch landed on came after the request to the target. The
servers here run on the loopback address, with every name resolved to
it, and the one standing for the household's network logs what reaches
it.
"""

from __future__ import annotations

import socket
from collections.abc import AsyncIterator
from typing import Any

import aiohttp
import pytest
from aiohttp import web
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.test_utils import TestServer

from custom_components.be_water_prices.providers import _pdf, inbw
from custom_components.be_water_prices.providers.base import ExtractorError


class _Loopback(AbstractResolver):
    """Every name is the loopback address the servers listen on."""

    async def resolve(
        self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET
    ) -> list[ResolveResult]:
        return [
            ResolveResult(
                hostname=host,
                host="127.0.0.1",
                port=port,
                family=socket.AF_INET,
                proto=0,
                flags=socket.AI_NUMERICHOST,
            )
        ]

    async def close(self) -> None:
        return None


class _Network:
    """A utility's site that answers from ``routes``, and a host of the
    household's network that logs every request in ``hits``."""

    def __init__(self, site: TestServer, lan: TestServer, session: aiohttp.ClientSession) -> None:
        self.site = site
        self.lan = lan
        self.session = session
        self.routes: dict[str, tuple[int, str]] = {}
        self.asked: list[str] = []
        self.hits: list[str] = []

    def url(self, host: str, path: str) -> str:
        return f"http://{host}:{self.site.port}{path}"

    def lan_url(self, host: str, path: str) -> str:
        return f"http://{host}:{self.lan.port}{path}"


@pytest.fixture
async def network(socket_enabled: None) -> AsyncIterator[_Network]:
    holder: list[_Network] = []

    async def site(request: web.Request) -> web.Response:
        net = holder[0]
        net.asked.append(f"{request.host.split(':')[0]}{request.path_qs}")
        status, value = net.routes.get(request.path_qs, (404, ""))
        if 300 <= status < 400:
            return web.Response(status=status, headers={"Location": value})
        return web.Response(status=status, text=value)

    async def lan(request: web.Request) -> web.Response:
        holder[0].hits.append(f"{request.method} {request.path_qs}")
        return web.Response(text="internal admin page")

    apps = []
    for handler in (site, lan):
        app = web.Application()
        app.router.add_route("*", "/{tail:.*}", handler)
        apps.append(app)
    connector = aiohttp.TCPConnector(resolver=_Loopback())
    async with (
        TestServer(apps[0]) as site_server,
        TestServer(apps[1]) as lan_server,
        aiohttp.ClientSession(connector=connector) as session,
    ):
        holder.append(_Network(site_server, lan_server, session))
        yield holder[0]


async def _pdf_fetch(session: aiohttp.ClientSession, url: str) -> str:
    return await _pdf.fetch_pdf_text_layout(session, url)


async def _picture_fetch(session: aiohttp.ClientSession, url: str) -> str:
    async def reader(_url: str, _payload: bytes) -> str:
        return "read"

    async def archived() -> str | None:
        return None

    with _pdf.read_pictures_through(reader):
        return str(await _pdf.fetch_picture_text(session, url, archived))


@pytest.mark.parametrize("fetch", [_pdf.fetch_text, _pdf_fetch, _picture_fetch])
async def test_a_redirect_off_the_site_is_never_requested(
    network: _Network, fetch: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """A card host answered 302 to a LAN appliance, and the appliance got
    the GET, path, query and Home Assistant's User-Agent, before the old
    check on the landing URL refused the answer. The target stays out of
    the message, which lands in last_error and the diagnostics dump."""
    caplog.set_level("DEBUG", logger=_pdf.__name__)
    network.routes["/card"] = (302, network.lan_url("router.lan", "/admin/reboot?confirm=1"))
    with pytest.raises(ExtractorError, match="redirected off-site") as err:
        await fetch(network.session, network.url("www.acme.test", "/card"))
    assert network.hits == []
    assert "router.lan" not in str(err.value)
    assert "router.lan" in caplog.text


async def test_a_redirect_to_a_private_address_is_never_requested(network: _Network) -> None:
    """Two address literals share their last two labels as often as not,
    which is all the site check compares, so an address is refused for
    being one."""
    network.routes["/card"] = (302, network.lan_url("127.0.0.1", "/admin/reboot?confirm=1"))
    with pytest.raises(ExtractorError, match="private address"):
        await _pdf.fetch_text(network.session, network.url("127.0.0.1", "/card"))
    assert network.hits == []


async def test_inbw_does_not_follow_a_redirect_off_its_site(
    network: _Network, monkeypatch: pytest.MonkeyPatch
) -> None:
    """inBW is read through a context of its own; its redirects are held to
    the same rules."""
    monkeypatch.setattr(inbw, "SOURCE_URL", network.url("eau.inbw.be", "/prix-de-leau"))
    network.routes["/prix-de-leau"] = (302, network.lan_url("192.168.1.1", "/cgi-bin/reboot"))
    with pytest.raises(ExtractorError, match="redirected off-site"):
        await inbw.fetch(network.session)
    assert network.hits == []


@pytest.mark.parametrize("location", ["/tarieven", "http://www.acme.test:{port}/tarieven"])
async def test_a_redirect_on_the_site_is_followed(network: _Network, location: str) -> None:
    network.routes["/old"] = (302, location.format(port=network.site.port))
    network.routes["/tarieven"] = (200, "Tarieven 2026")
    text = await _pdf.fetch_text(network.session, network.url("acme.test", "/old"))
    assert text == "Tarieven 2026"


async def test_a_redirect_past_the_limit_is_never_requested(network: _Network) -> None:
    for hop in range(_pdf.MAX_REDIRECTS + 1):
        network.routes[f"/hop{hop}"] = (301, f"/hop{hop + 1}")
    network.routes[f"/hop{_pdf.MAX_REDIRECTS + 1}"] = (200, "too far")
    with pytest.raises(ExtractorError, match="more than"):
        await _pdf.fetch_text(network.session, network.url("acme.test", "/hop0"))
    assert network.asked == [f"acme.test/hop{hop}" for hop in range(_pdf.MAX_REDIRECTS + 1)]


class _Chunks:
    def __init__(self, body: bytes) -> None:
        self._body = body

    async def iter_chunked(self, _n: int) -> AsyncIterator[bytes]:
        yield self._body


class _Answer:
    """A canned answer: a redirect to ``location``, or ``body``."""

    def __init__(self, status: int, location: str | None = None, body: bytes = b"hello") -> None:
        self.status = status
        self.headers = {} if location is None else {"Location": location}
        self.content_type = "text/plain"
        self.content_length = len(body)
        self.charset = None
        self.content = _Chunks(body)

    async def __aenter__(self) -> _Answer:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


class _Stub:
    """A session over a map of https URLs, which records what it is asked."""

    def __init__(self, answers: dict[str, _Answer]) -> None:
        self._answers = answers
        self.asked: list[str] = []

    def get(self, url: object, **_kwargs: object) -> _Answer:
        self.asked.append(str(url))
        return self._answers[str(url)]


async def test_a_redirect_that_drops_https_is_never_requested() -> None:
    session = _Stub({"https://water-link.be/x": _Answer(302, "http://water-link.be/x")})
    with pytest.raises(ExtractorError, match="redirected off https"):
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]
    assert session.asked == ["https://water-link.be/x"]


@pytest.mark.parametrize("location", ["file:///etc/passwd", "javascript:alert(1)", "http://[::1"])
async def test_a_redirect_nowhere_a_fetch_can_go_is_refused(location: str) -> None:
    session = _Stub({"https://water-link.be/x": _Answer(302, location)})
    with pytest.raises(ExtractorError, match="redirected"):
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]
    assert session.asked == ["https://water-link.be/x"]


async def test_a_redirect_with_no_location_is_an_http_error() -> None:
    session = _Stub({"https://water-link.be/x": _Answer(302)})
    with pytest.raises(ExtractorError, match="HTTP 302"):
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("asked", "location", "landed"),
    [
        ("https://www.vivaqua.be/en/the-domestic-linear-rate/", "/", "https://www.vivaqua.be/"),
        (
            "https://www.vivaqua.be/en/the-domestic-linear-rate/",
            "/fr/",
            "https://www.vivaqua.be/fr/",
        ),
        (
            "https://www.agsoknokke-heist.be/waterbedrijf/tarieven",
            "/pagenotfound?page=tarieven.aspx",
            "https://www.agsoknokke-heist.be/pagenotfound?page=tarieven.aspx",
        ),
    ],
)
async def test_a_redirect_to_the_home_or_a_not_found_page_is_a_moved_page(
    asked: str, location: str, landed: str
) -> None:
    """VIVAQUA sends a removed page to its home page and AGSO Knokke-Heist
    to "/pagenotfound", both with a 200. Parsed, that page failed as a
    layout change; it is a page that moved."""
    session = _Stub({asked: _Answer(302, location), landed: _Answer(200)})
    with pytest.raises(ExtractorError, match="the page moved or was removed"):
        await _pdf.fetch_text(session, asked)  # type: ignore[arg-type]
    assert session.asked == [asked, landed]


async def test_a_redirect_from_the_home_page_to_its_language_root_is_followed() -> None:
    session = _Stub(
        {
            "https://www.vivaqua.be/": _Answer(302, "/fr/"),
            "https://www.vivaqua.be/fr/": _Answer(200),
        }
    )
    assert await _pdf.fetch_text(session, "https://www.vivaqua.be/") == "hello"  # type: ignore[arg-type]


async def test_a_redirect_within_the_site_is_followed() -> None:
    session = _Stub(
        {
            "https://water-link.be/x": _Answer(301, "https://www.water-link.be/x"),
            "https://www.water-link.be/x": _Answer(200),
        }
    )
    assert await _pdf.fetch_text(session, "https://water-link.be/x") == "hello"  # type: ignore[arg-type]
