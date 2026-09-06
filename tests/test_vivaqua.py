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

from unittest.mock import AsyncMock

import pytest

from custom_components.be_water_prices.providers import ExtractorError, _html, vivaqua
from custom_components.be_water_prices.providers.vivaqua import parse_tariff
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
    html = fixture_html("vivaqua_linear_2026.html")
    monkeypatch.setattr(_html, "fetch_html", AsyncMock(return_value=html))
    tariff = await vivaqua.fetch(session=None)  # type: ignore[arg-type]
    assert tariff.utility == "vivaqua"
    assert round(tariff.linear_eur_per_m3 * 1.06, 2) == 2.62


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
    page = fixture_html("vivaqua_linear_2026.html")
    needle = "Price from January 1st 2026 (VAT included 6 %)"
    assert page.count(needle) == 1
    # Turn the 2026 card into a non-residential one: the parser must not
    # bind to it and falls back to the 2025 card, as for a missing card.
    t = parse_tariff(
        page.replace(needle, "Price from January 1st 2026 (VAT included 21 %)"), year=2026
    )
    assert t.valid_from.year == 2025
