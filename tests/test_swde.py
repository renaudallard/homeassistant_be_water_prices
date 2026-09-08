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

"""SWDE extractor against the captured 2026 fixture."""

from __future__ import annotations

import logging

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.swde import parse_tariff
from tests import fixture_html


def test_parses_2026_components(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    from custom_components.be_water_prices.providers import _walloon_simple

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)

    # From 2027 the card stands until 31 March; the year's own end is asserted.
    monkeypatch.setattr(_walloon_simple, "date", _FakeDate)
    t = parse_tariff(fixture_html("swde_2026.html"), year=2026)

    assert t.cvd_eur_per_m3 == 3.24
    # CVA / FSE are stored from const.py (not the page) but we still verify
    # the page hadn't drifted: the parser warns on drift above 0.005 for the
    # CVA and 0.001 for the much smaller FSE, and the constants are the
    # source of truth.
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3

    # Redevance is the regulator-defined 20·CVD + 30·CVA.
    assert round(t.yearly_fixed_fee, 2) == round(20 * 3.24 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)

    assert t.utility == "swde"
    assert t.region == "wallonia"
    assert t.valid_from.year == 2026
    assert t.valid_until is not None and t.valid_until.year == 2026
    # Wallonia uses CVD-based pricing; the Flanders / Brussels rate slots
    # stay None.
    assert t.linear_eur_per_m3 is None
    assert t.basis_eur_per_m3 is None
    assert t.comfort_eur_per_m3 is None
    assert t.vat_rate == 0.06
    assert t.source_url.startswith("https://www.swde.be/")


def test_raises_when_cvd_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>")


def test_fse_drift_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """A moved Fonds Social must stop the parse, not just log about it.

    SWDE is the drift sentinel for the shared SPGE constants, but the FSE
    is only ~0.03 EUR/m3, so the default 0.005 tolerance would have let a
    15% move through unreported. A warning was not enough on its own:
    the extractor still returned a tariff priced on the old constant, so
    nothing downstream could tell that every Walloon entry had gone
    wrong.
    """
    html = fixture_html("swde_2026.html").replace("€ 0.0339/m³", "€ 0.0375/m³")
    with caplog.at_level(logging.WARNING), pytest.raises(ExtractorError, match="SWDE FSE"):
        parse_tariff(html, year=2026)
    assert "SWDE FSE" in caplog.text


def test_the_untouched_page_still_parses() -> None:
    """The guard must only fire on a real move, not on every fetch."""
    tariff = parse_tariff(fixture_html("swde_2026.html"), year=2026)
    assert tariff.cvd_eur_per_m3 is not None


def test_cvd_section_without_a_figure_does_not_borrow_the_cva() -> None:
    """A CVD section that stops carrying € must fail, not read the CVA.

    The section scan used to run on past its own section and into the next
    one's wrapper, so dropping the € from the CVD prose silently answered
    with the CVA from the following section, which is a plausible enough
    rate that nothing downstream would reject it.
    """
    html = fixture_html("swde_2026.html").replace("€ 3.24/m³", "EUR 3.24/m3")
    with pytest.raises(ExtractorError):
        parse_tariff(html, year=2026)


def test_headings_are_matched_with_their_accents_folded() -> None:
    """The comment promised folding; the code only lowercased."""
    from bs4 import BeautifulSoup

    from custom_components.be_water_prices.providers.swde import _find_component

    soup = BeautifulSoup(
        "<h3>Coût-vérité de distribution</h3><p>Le montant est <strong>€ 3.24/m³</strong>.</p>",
        "html.parser",
    )
    assert _find_component(soup, ("cout-verite de distribution",)) == 3.24


def test_a_cvd_section_printing_two_figures_serves_the_current_one() -> None:
    """A historic rate printed before the current one used to win."""
    from custom_components.be_water_prices.providers.swde import parse_tariff

    page = fixture_html("swde_2026.html")
    # Put last year's figure ahead of this year's inside the CVD section.
    mutated = page.replace(
        "The current CVD &nbsp;amounts to <strong>&euro; 3.24/m&sup3;</strong>.",
        "Until 2025 it was <strong>&euro; 3.11/m&sup3;</strong>. "
        "The current CVD &nbsp;amounts to <strong>&euro; 3.24/m&sup3;</strong>.",
        1,
    )
    if mutated == page:
        mutated = page.replace(
            "The current CVD &nbsp;amounts to",
            "Until 2025 it was € 3.11/m³. The current CVD amounts to",
            1,
        )
    assert mutated != page, "the mutation did not land"
    assert parse_tariff(mutated, year=2026).cvd_eur_per_m3 == 3.24


def test_a_cva_section_is_still_read_first_amount_first() -> None:
    """Only the CVD reasons about indexation; CVA and FSE are pinned."""
    from custom_components.be_water_prices.providers.swde import parse_tariff

    assert parse_tariff(fixture_html("swde_2026.html"), year=2026).cva_eur_per_m3 == 2.748
