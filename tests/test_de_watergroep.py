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

"""De Watergroep extractor against the captured 2026 news article."""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from tests import fixture_html


class _FakeResp:
    def __init__(self, status: int) -> None:
        self.status = status

    async def text(self) -> str:
        return ""


class _FakeAjaxCtx:
    def __init__(self, *, status: int | None = None, exc: BaseException | None = None) -> None:
        self._status = status
        self._exc = exc

    async def __aenter__(self) -> _FakeResp:
        if self._exc is not None:
            raise self._exc
        assert self._status is not None
        return _FakeResp(self._status)

    async def __aexit__(self, *_a: object) -> bool:
        return False


class _FakeJar:
    """Just enough of aiohttp's cookie jar for the dwg_l cleanup."""

    def __init__(self) -> None:
        self.cleared = 0

    def clear(self, _predicate: object = None) -> None:
        self.cleared += 1


class _FakeGetSession:
    def __init__(self, *, status: int | None = None, exc: BaseException | None = None) -> None:
        self._status = status
        self._exc = exc
        self.cookie_jar = _FakeJar()

    def get(self, *_a: object, **_k: object) -> _FakeAjaxCtx:
        return _FakeAjaxCtx(status=self._status, exc=self._exc)


async def test_fetch_commune_ajax_maps_5xx_to_transient() -> None:
    from custom_components.be_water_prices.providers import de_watergroep
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with pytest.raises(TransientFetchError):
        await de_watergroep._fetch_commune_ajax(  # type: ignore[arg-type]
            _FakeGetSession(status=503), "{guid}"
        )


async def test_fetch_commune_ajax_maps_network_errors_to_transient_with_a_message() -> None:
    """An argless TimeoutError has an empty str(); the message must not end at the colon."""
    import aiohttp

    from custom_components.be_water_prices.providers import de_watergroep
    from custom_components.be_water_prices.providers.base import TransientFetchError

    for exc in (TimeoutError(), aiohttp.ClientConnectionError()):
        with pytest.raises(TransientFetchError, match=f"endpoint: {type(exc).__name__}"):
            await de_watergroep._fetch_commune_ajax(  # type: ignore[arg-type]
                _FakeGetSession(exc=exc), "{guid}"
            )


async def test_fetch_commune_ajax_3xx_is_a_moved_endpoint() -> None:
    from custom_components.be_water_prices.providers import de_watergroep
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with pytest.raises(ExtractorError, match="HTTP 302") as exc:
        await de_watergroep._fetch_commune_ajax(  # type: ignore[arg-type]
            _FakeGetSession(status=302), "{guid}"
        )
    assert not isinstance(exc.value, TransientFetchError)


class _Body:
    def __init__(self, text: str) -> None:
        self._chunks = [text.encode("utf-8")]

    async def iter_chunked(self, _n: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeBodyResp:
    """A 200 with a body, or the status a year's endpoint answers with."""

    def __init__(self, text: str = "", status: int = 200) -> None:
        self.status = status
        self.history = ()
        self.content = _Body(text)
        self.content_length = None
        self.charset = "utf-8"


class _YearSession:
    """Answers each UpdateDetailTariefJaar/<year> URL as told; records the years asked."""

    def __init__(self, answers: dict[int, _FakeBodyResp]) -> None:
        self._answers = answers
        self.asked: list[int] = []
        self.cookie_jar = _FakeJar()

    def get(self, url: str, **_k: object) -> _YearSession:
        year = int(url.rsplit("/", 1)[1])
        self.asked.append(year)
        self._resp = self._answers[year]
        return self

    async def __aenter__(self) -> _FakeBodyResp:
        return self._resp

    async def __aexit__(self, *_a: object) -> None:
        return None


def test_the_served_year_is_read_off_the_active_tab() -> None:
    from custom_components.be_water_prices.providers.de_watergroep import _served_year

    page = fixture_html("dewatergroep_halle_2026.html")
    assert _served_year(page, 2027) == 2026
    assert _served_year("<p>Basistarief</p>", 2027) == 2027


@pytest.mark.parametrize("clamped", [True, False])
async def test_in_january_last_years_commune_card_stands_until_31_march(
    monkeypatch: pytest.MonkeyPatch, clamped: bool
) -> None:
    """Asked for a year it has not published, the endpoint either answers with
    the newest card it has or fails; both used to end as this year's card or
    the drinkwater-only article."""
    from datetime import date

    from custom_components.be_water_prices.providers import _html, de_watergroep

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2027, 1, 5)

    monkeypatch.setattr(de_watergroep, "date", _FakeDate)
    monkeypatch.setattr(_html, "fetch_html", AsyncMock(side_effect=AssertionError("news ladder")))
    card = fixture_html("dewatergroep_halle_2026.html")
    answers = {
        2027: _FakeBodyResp(card) if clamped else _FakeBodyResp(status=404),
        2026: _FakeBodyResp(card),
    }
    for fetch in (
        lambda s: de_watergroep.fetch(s),
        lambda s: de_watergroep.fetch_for_commune(s, "{guid}"),
    ):
        session = _YearSession(answers)
        tariff = await fetch(session)  # type: ignore[arg-type]
        assert tariff.valid_from.year == 2026
        assert tariff.valid_until == date(2027, 3, 31)
        assert "tarieven 2026" in tariff.publication_label
        assert tariff.sanering_gemeentelijk_eur_per_m3 > 0
        assert session.asked == ([2027] if clamped else [2027, 2026])


def test_an_unshowable_leg_is_refused_rather_than_billed_as_free() -> None:
    """3660 Opglabbeek was billed 575.26 EUR/yr instead of 782.73."""
    from custom_components.be_water_prices.providers.de_watergroep import parse_commune_tariff

    page = fixture_html("dewatergroep_opglabbeek_2026.html")
    assert "De kostprijs kan momenteel niet getoond worden" in page
    with pytest.raises(ExtractorError, match="gemeentelijke saneringsbijdrage cannot be shown"):
        parse_commune_tariff(page, year=2026, commune_label="Opglabbeek")


def test_the_zuivering_leg_is_refused_on_the_same_wording() -> None:
    """Both legs carry the sentence; Opglabbeek only happened to hit one."""
    from custom_components.be_water_prices.providers.de_watergroep import parse_commune_tariff

    page = (
        "<html><body>"
        "<div>Basistarief per m\u00b3</div>"
        "<div><span>Waterverbruik drinkwater</span><span>\u20ac 2,9251</span></div>"
        "<div><span>Afvoer van afvalwater</span><span>\u20ac 1,9572</span></div>"
        "<div><span>Zuivering van afvalwater</span>"
        "<span>De kostprijs kan momenteel niet getoond worden.</span></div>"
        "<div>Basistarief per liter</div>"
        "</body></html>"
    )
    with pytest.raises(
        ExtractorError, match="bovengemeentelijke saneringsbijdrage cannot be shown"
    ):
        parse_commune_tariff(page, year=2026, commune_label="Elders")


def test_a_commune_card_that_prints_both_legs_is_untouched() -> None:
    from custom_components.be_water_prices.providers.de_watergroep import parse_commune_tariff

    t = parse_commune_tariff(
        fixture_html("dewatergroep_halle_2026.html"), year=2026, commune_label="Halle"
    )
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019


async def test_the_no_commune_fetch_has_nothing_under_it() -> None:
    """The news article billed 355.32 EUR a year against Halle's 782.73."""
    from custom_components.be_water_prices.providers import de_watergroep as dwg

    with (
        patch.object(dwg, "_newest_commune_card", new=AsyncMock(side_effect=ExtractorError("404"))),
        pytest.raises(ExtractorError, match="404"),
    ):
        await dwg.fetch(session=None)  # type: ignore[arg-type]
    assert not hasattr(dwg, "parse_news_tariff")


async def test_the_commune_cookie_does_not_outlive_its_request() -> None:
    """A dwg_l left in the shared jar could answer for another commune."""
    from custom_components.be_water_prices.providers import de_watergroep as dwg

    session = _FakeGetSession(status=500)
    with pytest.raises(ExtractorError):
        await dwg._fetch_commune_ajax(session, "{GUID}", 2026)  # type: ignore[arg-type]
    assert session.cookie_jar.cleared == 1
