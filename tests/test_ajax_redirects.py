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

"""The AJAX fetchers stay where they were pointed; a redirect is a failure."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.be_water_prices.providers import de_watergroep, farys
from custom_components.be_water_prices.providers.base import ExtractorError


def _redirecting_app(hits: list[str | None]) -> web.Application:
    async def moved(_request: web.Request) -> web.Response:
        return web.Response(status=302, headers={"Location": "/elsewhere"})

    async def elsewhere(request: web.Request) -> web.Response:
        hits.append(request.headers.get("Cookie"))
        return web.Response(text="Basistarief 2,1888")

    app = web.Application()
    app.router.add_route("*", "/endpoint/{tail:.*}", moved)
    app.router.add_route("*", "/endpoint", moved)
    app.router.add_route("*", "/elsewhere", elsewhere)
    return app


async def test_de_watergroep_does_not_carry_the_commune_cookie_through_a_redirect(
    socket_enabled: None,
) -> None:
    hits: list[str | None] = []
    async with TestServer(_redirecting_app(hits)) as server, aiohttp.ClientSession() as session:
        url_fmt = str(server.make_url("/endpoint")) + "/{year}"
        with (
            patch.object(de_watergroep, "COMMUNE_DETAIL_URL_FMT", url_fmt),
            pytest.raises(ExtractorError, match="HTTP 302"),
        ):
            await de_watergroep._fetch_commune_ajax(session, "guid-1")
    assert hits == []


async def test_farys_does_not_follow_a_redirect_off_its_endpoint(socket_enabled: None) -> None:
    hits: list[str | None] = []
    async with TestServer(_redirecting_app(hits)) as server, aiohttp.ClientSession() as session:
        with (
            patch.object(farys, "ENDPOINT_URL", str(server.make_url("/endpoint"))),
            pytest.raises(ExtractorError, match="HTTP 302"),
        ):
            await farys._post_for_commune(session, "1")
    assert hits == []
