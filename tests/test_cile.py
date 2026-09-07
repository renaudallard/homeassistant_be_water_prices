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

"""CILE extractor against the captured 2026 fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.cile import parse_tariff
from tests import fixture_html


def test_parses_2026_components() -> None:
    t = parse_tariff(fixture_html("cile_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 3.5552
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3
    assert round(t.yearly_fixed_fee, 2) == round(20 * 3.5552 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)


def test_raises_when_table_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>")


def test_a_page_still_on_last_years_card_is_dated_last_year(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In January the clock says the new year; the page says which card it shows."""
    from datetime import date

    from custom_components.be_water_prices.providers import cile

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2027, 1, 5)

    monkeypatch.setattr(cile, "date", _FakeDate)
    t = parse_tariff(fixture_html("cile_2026.html"))
    assert t.valid_from == date(2026, 1, 1)
    assert t.valid_until == date(2026, 12, 31)


@pytest.mark.parametrize("new_first", [True, False])
def test_a_comparison_column_is_read_by_its_year_not_its_position(new_first: bool) -> None:
    """With "new | old" the last cell is last year's; the heading with the year is not."""
    new = ("Tarif au 1er janvier 2026", "3,5552 €/m³", "2,7480 €/m³", "0,0339 €/m³")
    old = ("Tarif au 1er janvier 2025", "3,3000 €/m³", "2,7480 €/m³", "0,0339 €/m³")
    first, second = (new, old) if new_first else (old, new)
    rows = zip(("Poste", "C.V.D", "C.V.A", "Fonds social"), first, second, strict=True)
    table = "".join(f"<tr><td>{a}</td><td>{b}</td><td>{c}</td></tr>" for a, b, c in rows)
    tariff = parse_tariff(f"<html><body><table>{table}</table></body></html>", year=2026)
    assert tariff.cvd_eur_per_m3 == 3.5552
