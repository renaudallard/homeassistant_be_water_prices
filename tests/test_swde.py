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

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.swde import parse_tariff
from tests import SPGE_CVA_2026, SPGE_FSE_2026, fixture_html


def test_parses_2026_components(monkeypatch: pytest.MonkeyPatch) -> None:
    from datetime import date

    from custom_components.be_water_prices.providers import _walloon_simple

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)

    # From 2027 the card stands until 31 March; the year's own end is asserted.
    monkeypatch.setattr(_walloon_simple, "belgian_today", _FakeDate.today)
    t = parse_tariff(fixture_html("swde_2026.html"), year=2026)

    assert t.cvd_eur_per_m3 == 3.24
    # CVA / FSE are stored from const.py (not the page) but we still verify
    # the page hadn't drifted: the parser warns on drift above 0.005 for the
    # CVA and 0.001 for the much smaller FSE, and the constants are the
    # source of truth.
    assert t.cva_eur_per_m3 == SPGE_CVA_2026
    assert t.fse_eur_per_m3 == SPGE_FSE_2026

    # Redevance is the regulator-defined 20·CVD + 30·CVA.
    assert round(t.yearly_fixed_fee, 2) == round(20 * 3.24 + 30 * SPGE_CVA_2026, 2)

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


def test_a_moved_fonds_social_is_priced_as_printed() -> None:
    """The page is the source for the Fonds Social: a move reaches the bill
    with no release."""
    html = fixture_html("swde_2026.html").replace("€ 0.0339/m³", "€ 0.0375/m³")
    assert parse_tariff(html, year=2026).fse_eur_per_m3 == 0.0375


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


def test_a_cvd_section_printing_two_figures_reads_the_first() -> None:
    """First-figure-first, deliberately: see _amounts_after on why.

    Taking the largest was tried, to serve the current rate off a section
    that prints a historic one before it, and reverted, because no
    threshold separates a 6 % VAT twin from a few percent of indexation
    and reading the TVAC figure bills the VAT twice.
    """
    from custom_components.be_water_prices.providers.swde import parse_tariff

    page = fixture_html("swde_2026.html")
    mutated = page.replace(
        "The current CVD &nbsp;amounts to <strong>\u20ac 3.24/m\u00b3</strong>.",
        "Until 2025 it was <strong>\u20ac 3.11/m\u00b3</strong>. "
        "The current CVD &nbsp;amounts to <strong>\u20ac 3.24/m\u00b3</strong>.",
        1,
    )
    assert mutated != page, "the mutation did not land"
    assert parse_tariff(mutated, year=2026).cvd_eur_per_m3 == 3.11


def test_a_cva_section_is_still_read_first_amount_first() -> None:
    """Only the CVD reasons about indexation; CVA and FSE are pinned."""
    from custom_components.be_water_prices.providers.swde import parse_tariff

    assert parse_tariff(fixture_html("swde_2026.html"), year=2026).cva_eur_per_m3 == 2.748


def test_a_clock_dated_swde_card_says_so() -> None:
    """The page names no year, so nothing downstream can tell it is stale."""
    from custom_components.be_water_prices.providers.swde import parse_tariff

    page = fixture_html("swde_2026.html")
    assert "states no year" in parse_tariff(page).publication_label
    # An explicitly asked-for year is the caller's claim, not the page's.
    assert "states no year" not in parse_tariff(page, year=2026).publication_label


def test_a_vat_inclusive_twin_is_not_mistaken_for_a_higher_cvd() -> None:
    """Taking it would apply the 6 % twice: 3.24 billed as 3.43."""
    from custom_components.be_water_prices.providers.swde import parse_tariff

    page = fixture_html("swde_2026.html")
    mutated = page.replace(
        "The current CVD &nbsp;amounts to <strong>\u20ac 3.24/m\u00b3</strong>.",
        "The current CVD &nbsp;amounts to <strong>\u20ac 3.24/m\u00b3</strong> "
        "(\u20ac 3.43/m\u00b3 incl. VAT).",
        1,
    )
    assert mutated != page, "the mutation did not land"
    assert parse_tariff(mutated, year=2026).cvd_eur_per_m3 == 3.24


def test_a_rate_announced_for_next_year_is_not_taken_as_this_one() -> None:
    from custom_components.be_water_prices.providers.swde import parse_tariff

    page = fixture_html("swde_2026.html")
    mutated = page.replace(
        "The current CVD &nbsp;amounts to <strong>\u20ac 3.24/m\u00b3</strong>.",
        "The current CVD &nbsp;amounts to <strong>\u20ac 3.24/m\u00b3</strong>. "
        "From 1 January 2027 it becomes <strong>\u20ac 4.10/m\u00b3</strong>.",
        1,
    )
    assert mutated != page, "the mutation did not land"
    assert parse_tariff(mutated, year=2026).cvd_eur_per_m3 == 3.24
