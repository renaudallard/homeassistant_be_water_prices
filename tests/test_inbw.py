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

"""inBW extractor against the captured 2026 fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.pricing import compute_annual_cost
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.inbw import parse_tariff
from tests import fixture_html


def test_parses_2026_components() -> None:
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 2.6
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3
    # Redevance = 20·CVD + 30·CVA = 52 + 82.44 = 134.44
    assert round(t.yearly_fixed_fee, 2) == round(20 * 2.6 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)


def test_matches_published_facture_for_100_m3() -> None:
    # The inBW page itself shows 584.261 € TVAC for a 100 m³ sample bill.
    # Our cost engine has to reproduce that to the cent or we know either
    # the parser or the Wallonia tier math is off.
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert compute_annual_cost(t, 100, 1) == 584.26


def test_raises_when_table_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>no table here</body></html>")


def test_a_page_still_on_last_years_card_is_dated_last_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import date

    from custom_components.be_water_prices.providers import _walloon_simple, inbw

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2027, 1, 5)

    monkeypatch.setattr(inbw, "date", _FakeDate)
    monkeypatch.setattr(_walloon_simple, "date", _FakeDate)
    t = parse_tariff(fixture_html("inbw_2026.html"))
    assert t.valid_from == date(2026, 1, 1)
    # Dated last year, so it stands until 31 March of this one.
    assert t.valid_until == date(2027, 3, 31)


def test_a_moved_cva_fails_the_fetch_rather_than_logging() -> None:
    """The 30 x CVA row is the page's own statement of the CVA."""
    page = fixture_html("inbw_2026.html")
    assert "82,440 €" in page
    with pytest.raises(ExtractorError, match="CVA published value"):
        parse_tariff(page.replace("82,440 €", "87,000 €"), year=2026)


async def test_only_a_tls_failure_earns_the_unverified_retry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Any other error retried without verification would hand an on-path attacker the hint."""
    import ssl
    from unittest.mock import AsyncMock

    from custom_components.be_water_prices.providers import inbw
    from custom_components.be_water_prices.providers.base import TransientFetchError

    def _failing(cause: BaseException) -> AsyncMock:
        async def _fetch(*_a: object, **_k: object) -> str:
            try:
                raise cause
            except BaseException as err:
                raise TransientFetchError("network error fetching inBW") from err

        return AsyncMock(side_effect=_fetch)

    timeout = _failing(TimeoutError())
    monkeypatch.setattr(inbw, "fetch_html", timeout)
    with pytest.raises(TransientFetchError):
        await inbw.fetch(None)  # type: ignore[arg-type]
    assert timeout.await_count == 1

    calls: list[dict[str, object]] = []

    async def _tls_then_page(*_a: object, **kwargs: object) -> str:
        calls.append(kwargs)
        if not kwargs.get("verify_ssl", True):
            return fixture_html("inbw_2026.html")
        try:
            raise ssl.SSLError("certificate verify failed")
        except ssl.SSLError as err:
            raise TransientFetchError("network error fetching inBW") from err

    monkeypatch.setattr(inbw, "fetch_html", _tls_then_page)
    tariff = await inbw.fetch(None)  # type: ignore[arg-type]
    assert tariff.utility == "inbw"
    assert [c.get("verify_ssl", True) for c in calls] == [True, False]
