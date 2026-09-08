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

"""AGSO Knokke-Heist extractor against the captured fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.agso_knokke import parse_tariff
from tests import fixture_html


def test_picks_the_table_the_page_labels_with_the_year() -> None:
    # The fixture has both 2025 and 2026 tables, each under its own
    # "OVERZICHT TARIEVEN" heading; the requested year's table wins.
    t = parse_tariff(fixture_html("agso_knokke_2026.html"), year=2026)
    assert t.basis_eur_per_m3 == 2.3295  # drinkwater 2026
    assert t.comfort_eur_per_m3 == 2.0 * 2.3295  # VMM 2× rule
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572  # afvoer 2026
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019  # zuivering 2026
    assert t.yearly_fixed_fee == 100.0  # standard VMM 50+30+20
    assert t.yearly_fixed_fee_per_resident_discount == 20.0  # 10+6+4


@pytest.mark.parametrize(
    "heading", ["TARIEVEN VANAF 1 JANUARI 2026", "Wijziging per 1 januari 2026"]
)
def test_a_reworded_heading_does_not_hand_its_table_the_older_year(heading: str) -> None:
    """The walk-back from the 2026 table found the 2025 heading and served that card."""
    page = fixture_html("agso_knokke_2026.html")
    marker = "OVERZICHT TARIEVEN&nbsp; PER 1/1/2026"
    assert page.count(marker) == 1
    t = parse_tariff(page.replace(marker, heading), year=2026)
    assert t.basis_eur_per_m3 == 2.3295
    assert t.valid_from.year == 2026


def test_a_reworded_older_heading_keeps_the_newest_card_dated() -> None:
    page = fixture_html("agso_knokke_2026.html")
    assert page.count("OVERZICHT TARIEVEN 2025") == 1
    t = parse_tariff(page.replace("OVERZICHT TARIEVEN 2025", "TARIEVEN 2025"), year=2026)
    assert t.basis_eur_per_m3 == 2.3295
    assert t.valid_from.year == 2026


def test_raises_when_table_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>")


def test_asking_for_last_year_returns_last_years_table() -> None:
    t = parse_tariff(fixture_html("agso_knokke_2026.html"), year=2025)
    assert t.basis_eur_per_m3 == 2.2602
    assert t.valid_from.year == 2025


def test_in_january_the_newest_started_year_is_served(monkeypatch: pytest.MonkeyPatch) -> None:
    """The branch every AGSO user hits on 1 January had no test."""
    from datetime import date

    from custom_components.be_water_prices.providers import agso_knokke

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2027, 1, 5)

    monkeypatch.setattr(agso_knokke, "date", _FakeDate)
    t = parse_tariff(fixture_html("agso_knokke_2026.html"))
    assert t.basis_eur_per_m3 == 2.3295
    assert t.valid_from.year == 2026


def test_without_headings_the_dearest_table_is_served_as_this_years() -> None:
    """No heading to date the tables: fall back to the higher integrale price."""
    import re

    page = fixture_html("agso_knokke_2026.html")
    stripped = re.sub(r"OVERZICHT\s+TARIEVEN", "OVERZICHT", page, flags=re.IGNORECASE)
    assert stripped != page
    t = parse_tariff(stripped, year=2026)
    assert t.basis_eur_per_m3 == 2.3295
    assert t.valid_from.year == 2026


def test_a_leg_that_does_not_add_up_to_the_printed_total_is_refused() -> None:
    """The page sums the three legs itself; reading one wrong is visible."""
    page = fixture_html("agso_knokke_2026.html")
    # The neighbouring non-household cell, which a column shift would take.
    mutated = page.replace("\u20ac 1,9572", "\u20ac 2,2173", 1)
    assert mutated != page, "the mutation did not land"
    with pytest.raises(ExtractorError, match="do not add up to the integrale"):
        parse_tariff(mutated, year=2026)
