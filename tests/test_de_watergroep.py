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

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.de_watergroep import _BASIS_NEWS_RE, parse_tariff
from tests import fixture_html


def test_news_regex_matches_published_wording() -> None:
    match = _BASIS_NEWS_RE.search("Dat kost 2,9521 euro voor 1.000 liter water.")
    assert match is not None
    assert match.group(1) == "2,9521"


def test_news_regex_does_not_backtrack_on_long_digit_run() -> None:
    # A long unbroken digit run without the required tail used to make the
    # unbounded integer part backtrack quadratically; the bounded form
    # returns immediately (the 30s pytest-timeout guards against regression).
    assert _BASIS_NEWS_RE.search("9" * 200_000) is None


def test_parses_2026_basistarief() -> None:
    t = parse_tariff(fixture_html("dewatergroep_2026.html"), year=2026)
    assert t.basis_eur_per_m3 == 2.9521
    assert t.comfort_eur_per_m3 == 5.9042  # 2× basis


def test_uses_drinkwater_only_vastrecht() -> None:
    # The news article only covers the drinkwater leg, so vastrecht is
    # 50 EUR / 10 EUR-per-persoon (not the full 100/20 integrale fee).
    t = parse_tariff(fixture_html("dewatergroep_2026.html"), year=2026)
    assert t.yearly_fixed_fee == 50.0
    assert t.yearly_fixed_fee_per_resident_discount == 10.0
    # Sanering stays at 0 -- per-commune data is a v0.4 polish.
    assert t.sanering_gemeentelijk_eur_per_m3 == 0.0
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 0.0


def test_raises_on_missing_basistarief_phrase() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>", year=2026)


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


class _FakeGetSession:
    def __init__(self, *, status: int | None = None, exc: BaseException | None = None) -> None:
        self._status = status
        self._exc = exc

    def get(self, *_a: object, **_k: object) -> _FakeAjaxCtx:
        return _FakeAjaxCtx(status=self._status, exc=self._exc)


async def test_fetch_commune_ajax_maps_5xx_to_transient() -> None:
    from custom_components.be_water_prices.providers import de_watergroep
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with pytest.raises(TransientFetchError):
        await de_watergroep._fetch_commune_ajax(  # type: ignore[arg-type]
            _FakeGetSession(status=503), "{guid}"
        )


async def test_fetch_reraises_transient_instead_of_news_fallback() -> None:
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import de_watergroep
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with (
        patch.object(
            de_watergroep,
            "_fetch_commune_ajax",
            new=AsyncMock(side_effect=TransientFetchError("HTTP 503")),
        ),
        patch.object(de_watergroep, "fetch_html", new=AsyncMock()) as news,
        pytest.raises(TransientFetchError),
    ):
        await de_watergroep.fetch(session=None)  # type: ignore[arg-type]
    # The drinkwater-only news fallback must NOT run on a transient blip.
    news.assert_not_awaited()
