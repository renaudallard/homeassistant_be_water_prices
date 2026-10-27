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

"""VIVAQUA extractor against the captured 2026 fixture."""

from __future__ import annotations

from datetime import date

import pytest

from custom_components.be_water_prices.providers import ExtractorError, _html, vivaqua
from custom_components.be_water_prices.providers.base import TransientFetchError
from custom_components.be_water_prices.providers.vivaqua import parse_intervention, parse_tariff
from tests import fixture_html


def test_parses_2026_rates_ex_vat() -> None:
    html = fixture_html("vivaqua_linear_2026.html")
    t = parse_tariff(html, year=2026)

    # Ex-VAT components should reconstruct the published VAT-incl headlines
    # to the cent.
    assert round(t.linear_eur_per_m3 * 1.06, 2) == 2.62
    assert round(t.sanering_gemeentelijk_eur_per_m3 * 1.06, 2) == 2.73
    assert round((t.linear_eur_per_m3 + t.sanering_gemeentelijk_eur_per_m3) * 1.06, 2) == 5.35
    assert round(t.yearly_fixed_fee * 1.06, 2) == 40.23

    assert t.utility == "vivaqua"
    assert t.region == "brussels"
    assert t.valid_from.year == 2026
    assert t.valid_until is not None and t.valid_until.year == 2026
    assert t.basis_eur_per_m3 is None and t.comfort_eur_per_m3 is None
    assert t.vat_rate == 0.06
    assert t.publication_label.startswith("Price from January 1st 2026")
    assert t.source_url.startswith("https://www.vivaqua.be/")


def test_falls_back_to_previous_year_when_target_missing() -> None:
    html = fixture_html("vivaqua_linear_2026.html")
    # Asking for 2027 should fall back to 2026 (the most recent year present).
    t = parse_tariff(html, year=2027)
    assert t.valid_from.year == 2026


def test_raises_when_no_year_table_present() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>", year=2026)


def test_unparseable_current_year_table_does_not_serve_last_year() -> None:
    """A present but unreadable current-year card must fail, not fall back.

    Falling back treats a broken parser like a card Brugel has not published
    yet: last year's rates get served under this year's label, and the
    March 31 grace period keeps the staleness Repair quiet while it happens.
    """
    # Rename only the 2026 table's two "supply" rows; it is the first card
    # on the page, so the 2025 one below it stays intact.
    html = fixture_html("vivaqua_linear_2026.html").replace(">supply</td>", ">water supply</td>", 2)
    with pytest.raises(ExtractorError):
        parse_tariff(html, year=2026)


async def test_fetch_parses_off_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """fetch() routes the bs4 parse through fetch_and_parse (off the loop).

    Patches the network read and checks the tariff still parses, covering
    the fetch_and_parse wiring shared by every HTML extractor.
    """
    pages = {
        vivaqua.SOURCE_URL: fixture_html("vivaqua_linear_2026.html"),
        vivaqua.INTERVENTION_URL: fixture_html("vivaqua_intervention_2026.html"),
    }

    async def _read(_session: object, url: str, **_kwargs: object) -> str:
        return pages[url]

    monkeypatch.setattr(_html, "fetch_html", _read)
    monkeypatch.setattr(vivaqua, "belgian_today", lambda: date(2026, 6, 1))
    tariff = await vivaqua.fetch(session=None)  # type: ignore[arg-type]
    assert tariff.utility == "vivaqua"
    assert round(tariff.linear_eur_per_m3 * 1.06, 2) == 2.62
    assert tariff.social_intervention_first_person == 58.70
    assert tariff.social_intervention_per_extra_person == 53.36


async def test_a_page_the_intervention_cannot_be_read_from_still_gives_the_card(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Only a household with BIM status is priced on the intervention. A
    reworded page used to fail the card for every Brussels entry; it is now
    served without the two figures, which leaves that household's cost
    unknown, and says so."""
    page = fixture_html("vivaqua_intervention_2026.html")
    reworded = page.replace("l’intervention sociale s&#8217;élève à", "le montant est de")
    assert reworded != page
    pages = {
        vivaqua.SOURCE_URL: fixture_html("vivaqua_linear_2026.html"),
        vivaqua.INTERVENTION_URL: reworded,
    }

    async def _read(_session: object, url: str, **_kwargs: object) -> str:
        return pages[url]

    monkeypatch.setattr(_html, "fetch_html", _read)
    monkeypatch.setattr(vivaqua, "belgian_today", lambda: date(2026, 6, 1))
    tariff = await vivaqua.fetch(session=None)  # type: ignore[arg-type]
    assert round(tariff.linear_eur_per_m3 * 1.06, 2) == 2.62
    assert tariff.social_intervention_first_person == 0.0
    assert tariff.social_intervention_per_extra_person == 0.0
    assert "intervention sociale could not be read" in caplog.text


async def test_a_blip_on_the_intervention_page_fails_the_fetch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A network failure says nothing about the page, so the refresh keeps
    the card it holds, intervention included, rather than dropping it."""

    async def _read(_session: object, url: str, **_kwargs: object) -> str:
        if url == vivaqua.INTERVENTION_URL:
            raise TransientFetchError(f"HTTP 503 fetching {url}")
        return fixture_html("vivaqua_linear_2026.html")

    monkeypatch.setattr(_html, "fetch_html", _read)
    monkeypatch.setattr(vivaqua, "belgian_today", lambda: date(2026, 6, 1))
    with pytest.raises(TransientFetchError):
        await vivaqua.fetch(session=None)  # type: ignore[arg-type]


def test_parses_2026_intervention() -> None:
    page = fixture_html("vivaqua_intervention_2026.html")
    assert parse_intervention(page, year=2026) == (58.70, 53.36)


@pytest.mark.parametrize("year", [2025, 2027])
def test_intervention_of_a_year_either_side_is_taken(year: int) -> None:
    page = fixture_html("vivaqua_intervention_2026.html")
    assert parse_intervention(page, year=year) == (58.70, 53.36)


def test_intervention_two_years_off_is_refused() -> None:
    page = fixture_html("vivaqua_intervention_2026.html")
    with pytest.raises(ExtractorError, match="states 2026, not 2028"):
        parse_intervention(page, year=2028)


def test_intervention_is_read_off_the_years_own_lines() -> None:
    """The example below the amounts and the FAQ further down both repeat
    them. With the first person's line gone from the block the year's
    sentence opens, the parse fails rather than read on into either."""
    page = fixture_html("vivaqua_intervention_2026.html")
    line = "<li>58,70 € pour un ménage d&#8217;1 personne</li>"
    assert page.count(line) == 2
    with pytest.raises(ExtractorError, match="could not be parsed"):
        parse_intervention(page.replace(line, "", 1), year=2026)


def test_intervention_page_without_the_year_sentence_is_refused() -> None:
    with pytest.raises(ExtractorError, match="states no year"):
        parse_intervention("<html><body>58,70 € pour un ménage d'1 personne</body></html>")


@pytest.mark.parametrize(
    ("printed", "doctored", "passes"),
    [
        ("€ 20,54", "€ 20,55", True),  # one cent on the fixed rows, other cents than 2025
        ("€ 20,54", "€ 20,56", False),
        ("€ 2,73", "€ 2,74", True),  # one cent on the variable rows
        ("€ 2,73", "€ 2,75", False),
    ],
)
def test_the_cross_check_allows_one_cent_of_rounding_whatever_the_cents(
    printed: str, doctored: str, passes: bool
) -> None:
    """The real 2025 card passes its one-cent gap only by float noise."""
    page = fixture_html("vivaqua_linear_2026.html")
    assert page.count(printed) >= 1
    edited = page.replace(printed, doctored, 1)
    if passes:
        parse_tariff(edited, year=2026)
    else:
        with pytest.raises(ExtractorError, match="!= total"):
            parse_tariff(edited, year=2026)


def test_a_reworded_header_still_finds_the_residential_card() -> None:
    """Only the 6 % marker pins the card; the header wording is not a contract.

    Matching "VAT included 6" literally read any rewording as "card not
    published" and served last year's rates until 31 March.
    """
    page = fixture_html("vivaqua_linear_2026.html")
    needle = "Price from January 1st 2026 (VAT included 6 %)"
    assert page.count(needle) == 1
    t = parse_tariff(
        page.replace(needle, "Price from January 1st 2026 (6 % VAT included)"), year=2026
    )
    assert t.valid_from.year == 2026
    assert round(t.linear_eur_per_m3 or 0, 4) == round(2.62 / 1.06, 4)


def test_a_21_percent_card_for_the_same_year_is_not_the_residential_one() -> None:
    """A non-residential card beside it also names the year and VAT; the
    household's is the one at the lower rate, wherever it sits."""
    page = fixture_html("vivaqua_linear_2026.html")
    needle = "Price from January 1st 2026 (VAT included 6 %)"
    assert page.count(needle) == 1
    start = page.rfind("<table", 0, page.index(needle))
    end = page.index("</table>", start) + len("</table>")
    other = page[start:end].replace("(VAT included 6 %)", "(VAT included 21 %)")
    t = parse_tariff(page[:start] + other + page[start:], year=2026)
    assert t.valid_from.year == 2026
    assert t.vat_rate == 0.06


def test_the_rate_the_header_states_is_the_one_billed() -> None:
    """The figures are printed with VAT; they are taken off at the rate the
    card states, not a rate kept in the code."""
    page = fixture_html("vivaqua_linear_2026.html")
    needle = "Price from January 1st 2026 (VAT included 6 %)"
    assert page.count(needle) == 1
    t = parse_tariff(
        page.replace(needle, "Price from January 1st 2026 (VAT included 9 %)"), year=2026
    )
    assert t.vat_rate == 0.09
    assert t.linear_eur_per_m3 == pytest.approx(2.62 / 1.09)
    assert t.yearly_fixed_fee == pytest.approx(40.23 / 1.09)


def test_a_wider_row_is_refused_rather_than_read_from_its_last_column() -> None:
    """A merged multi-year table with this year first would price last year."""
    html = fixture_html("vivaqua_linear_2026.html")
    cell = '<td style="width: 50%; height: 24px;">\u20ac 2,62\xa0</td>'
    assert cell in html
    mutated = html.replace(
        cell, cell + '<td style="width: 50%; height: 24px;">\u20ac 2,17\xa0</td>', 1
    )
    with pytest.raises(ExtractorError, match="so a wider row needs the column picking"):
        parse_tariff(mutated, year=2026)


def test_a_wide_row_the_parser_does_not_read_is_skipped() -> None:
    """A footnote row used to be harmless; refusing it killed the card."""
    html = fixture_html("vivaqua_linear_2026.html")
    row = (
        '<tr style="height: 24px;"><td style="width: 50%;">note</td>'
        '<td style="width: 25%;">a</td><td style="width: 25%;">b</td></tr>\n'
    )
    anchor = '<tr style="height: 24px;">\n<td style="width: 50%; height: 24px;">supply</td>'
    assert anchor in html
    mutated = html.replace(anchor, row + anchor, 1)
    assert mutated != html, "the mutation did not land"
    t = parse_tariff(mutated, year=2026)
    assert t.linear_eur_per_m3 == pytest.approx(2.62 / 1.06)
