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

"""Water-link extractor against the captured 2026 PDF."""

from __future__ import annotations

import functools

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.water_link import EXTRACTOR, parse_tariff
from tests import fixture_bytes


@functools.cache
def _pdf_text() -> str:
    return extract_pdf_text_layout(fixture_bytes("water_link_2026.pdf"))


def test_parses_2026_antwerpen_default() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    assert t.basis_eur_per_m3 == 1.6692
    assert t.comfort_eur_per_m3 == 3.3384  # = 2× basis
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.3345  # Antwerpen-specific
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019
    assert t.yearly_fixed_fee == 100.0
    assert t.yearly_fixed_fee_per_resident_discount == 20.0


def test_parses_2026_ring_commune_overrides_sanering() -> None:
    # Edegem, Hove, Mortsel etc. carry the higher 1.9572 afvoer rate.
    t = parse_tariff(_pdf_text(), year=2026, commune="Edegem")
    assert t.basis_eur_per_m3 == 1.6692  # uniform
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572  # ring rate
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019  # uniform


def test_a_row_that_lost_a_column_is_refused() -> None:
    """Three amounts read off a shifted row passed the 2x check with the wrong rate."""
    text = _pdf_text()
    row = "Antwerpen 3,3384 2,6690 3,4038 9,4112 9,9759"
    assert text.count(row) == 1
    with pytest.raises(ExtractorError, match="does not add up"):
        parse_tariff(text.replace(row, "Antwerpen 2,6690 3,4038 9,4112 9,9759"), year=2026)


def test_raises_when_pdf_text_is_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("nothing here", year=2026)


def test_extractor_supports_communes_and_lists_them() -> None:
    # Per-commune support is what unlocks the OptionsFlow commune dropdown.
    from custom_components.be_water_prices.providers.water_link import _COMMUNE_LINE_RE

    assert EXTRACTOR.supports_communes
    text = _pdf_text()
    cut = text.find("BASISTARIEF")
    end = text.find("COMFORTTARIEF", cut)
    block = text[cut:end]
    found = [m.group(1).strip() for m in _COMMUNE_LINE_RE.finditer(block)]
    assert "Antwerpen" in found
    assert "Edegem" in found
    assert "Mortsel" in found


def test_parse_tariff_with_specific_commune_returns_ring_sanering() -> None:

    text = _pdf_text()
    edegem = parse_tariff(text, year=2026, commune="Edegem")
    assert edegem.sanering_gemeentelijk_eur_per_m3 == 1.9572  # ring rate


def _tariff_page(year: int) -> str:
    """The three PDF links the real Antwerpen tariff page carries."""
    base = "https://water-link.be/sites/default/files"
    return (
        f'<a href="{base}/{year}-01/{year}%20andere.pdf">andere</a>'
        f'<a href="{base}/{year}-01/{year}%20HH.pdf">huishoudelijk</a>'
        f'<a href="{base}/{year}-01/{year}%20NHH_0.pdf">niet-huishoudelijk</a>'
    )


def test_find_pdf_href_picks_the_household_card() -> None:
    """The page lists three cards; only the HH one is ours.

    "NHH" ends in the same two letters, so an anchor on a bare HH would
    bind to the non-household rates.
    """
    from custom_components.be_water_prices.providers.water_link import _find_pdf_href

    assert _find_pdf_href(_tariff_page(2026), 2026).endswith("2026%20HH.pdf")


def test_find_pdf_href_ignores_the_year_in_the_upload_directory() -> None:
    """The directory carries a year too, so the match must be on the file.

    Water-link uploads next year's card in December, under a directory
    still named for this year. A loose match reads that as this year's
    card and serves next year's rates months early.
    """
    from custom_components.be_water_prices.providers.water_link import _find_pdf_href

    page = '<a href="https://water-link.be/sites/default/files/2026-12/2027%20HH.pdf">2027</a>'
    with pytest.raises(ExtractorError):
        _find_pdf_href(page, 2026)
    assert _find_pdf_href(page, 2027).endswith("2027%20HH.pdf")


def test_find_pdf_href_raises_when_year_absent() -> None:
    from custom_components.be_water_prices.providers.water_link import _find_pdf_href

    with pytest.raises(ExtractorError):
        _find_pdf_href(_tariff_page(2026), 2027)


async def test_pdf_url_is_discovered_from_the_page() -> None:
    """A card uploaded outside January must still be found.

    The upload directory carries the publication month, so the templated
    "-01" path 404s whenever a card lands in another month.
    """
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, water_link

    page = _tariff_page(2026).replace("2026-01", "2026-02")
    with patch.object(_html, "fetch_html", new=AsyncMock(return_value=page)):
        url = await water_link._pdf_url_for(None, 2026)  # type: ignore[arg-type]
    assert url == "https://water-link.be/sites/default/files/2026-02/2026%20HH.pdf"


async def test_pdf_url_falls_back_to_the_january_path() -> None:
    """A page without the link keeps the behaviour that works today."""
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, water_link

    with patch.object(_html, "fetch_html", new=AsyncMock(return_value="<p>geen pdf</p>")):
        url = await water_link._pdf_url_for(None, 2025)  # type: ignore[arg-type]
    assert url == water_link.SOURCE_URL_FMT.format(year=2025)


async def test_pdf_url_rejects_an_off_site_link() -> None:
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, water_link

    page = '<a href="https://evil.test/2026%20HH.pdf">huishoudelijk</a>'
    with (
        patch.object(_html, "fetch_html", new=AsyncMock(return_value=page)),
        pytest.raises(ExtractorError, match="off-site"),
    ):
        await water_link._pdf_url_for(None, 2026)  # type: ignore[arg-type]


async def test_transient_error_propagates_not_masked() -> None:
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, water_link
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with (
        patch.object(_html, "fetch_html", new=AsyncMock(return_value=_tariff_page(2026))),
        patch.object(
            water_link,
            "fetch_pdf_text_layout",
            new=AsyncMock(side_effect=TransientFetchError("HTTP 503")),
        ) as mock,
        pytest.raises(TransientFetchError),
    ):
        await water_link._fetch_pdf_text(session=None)  # type: ignore[arg-type]
    # Must not have masked it by falling back to last year's PDF.
    assert mock.await_count == 1


async def test_transient_error_on_the_tariff_page_propagates() -> None:
    """The page fetch is on the same path, so its blips must not mask either."""
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, water_link
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with (
        patch.object(
            _html, "fetch_html", new=AsyncMock(side_effect=TransientFetchError("HTTP 503"))
        ),
        pytest.raises(TransientFetchError),
    ):
        await water_link._fetch_pdf_text(session=None)  # type: ignore[arg-type]


async def test_hard_error_falls_back_to_prior_year() -> None:
    from datetime import date
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, water_link

    text = _pdf_text()
    with (
        patch.object(_html, "fetch_html", new=AsyncMock(return_value=_tariff_page(2026))),
        patch.object(
            water_link,
            "fetch_pdf_text_layout",
            new=AsyncMock(side_effect=[ExtractorError("HTTP 404"), text]),
        ) as mock,
    ):
        _out, year, _url = await water_link._fetch_pdf_text(session=None)  # type: ignore[arg-type]
    assert mock.await_count == 2
    assert year == date.today().year - 1


async def test_tariff_cites_the_pdf_it_actually_read() -> None:
    """The source_url attribute must point at the card that was parsed.

    The link is discovered rather than templated, so a card uploaded
    outside January lives under a different path; citing the templated one
    sends anyone verifying the figures to a URL that 404s.
    """
    from datetime import date
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, water_link

    # The page links the 2026 card only, so the fetch has to ask for 2026
    # whatever year the test runs in.
    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)

    page = _tariff_page(2026).replace("2026-01", "2026-02")
    with (
        patch.object(water_link, "date", _FakeDate),
        patch.object(_html, "fetch_html", new=AsyncMock(return_value=page)),
        patch.object(water_link, "fetch_pdf_text_layout", new=AsyncMock(return_value=_pdf_text())),
    ):
        tariff = await water_link.fetch(session=None)  # type: ignore[arg-type]
    assert tariff.source_url == ("https://water-link.be/sites/default/files/2026-02/2026%20HH.pdf")


@pytest.mark.timeout(5)
def test_the_commune_scan_stays_linear_on_a_run_of_whitespace() -> None:
    """A padded line must fail fast, not hold the loop for minutes.

    The old pattern kept a space inside the lazy name class and followed
    it with `\\s+`, so a name-like line with thousands of spaces and no
    amounts backtracked quadratically, and the config flow ran that scan
    on the event loop.
    """
    from custom_components.be_water_prices.providers.water_link import _parse_commune_lines

    text = "BASISTARIEF\nB" + " " * 50_000 + "\nCOMFORTTARIEF"
    with pytest.raises(ExtractorError, match="no commune rows"):
        _parse_commune_lines(text)


def test_the_commune_scan_still_reads_multi_word_and_hyphenated_names() -> None:
    from custom_components.be_water_prices.providers.water_link import _parse_commune_lines

    text = (
        "BASISTARIEF\n"
        "Antwerpen 1,6692 1,3345 1,7019 4,7056 4,9879\n"
        "Beveren-Kruibeke-Zwijndrecht 1,6692 1,9572 1,7019 5,3283 5,6480\n"
        "Sint Niklaas Oost 1,6692 1,9572 1,7019 5,3283 5,6480\n"
        "COMFORTTARIEF\n"
    )
    assert [c.id for c in _parse_commune_lines(text)] == [
        "Antwerpen",
        "Beveren-Kruibeke-Zwijndrecht",
        "Sint Niklaas Oost",
    ]


def test_find_pdf_href_accepts_the_cms_dedupe_suffix() -> None:
    """A corrected card re-uploaded as "2026 HH_0.pdf" must be found."""
    from custom_components.be_water_prices.providers.water_link import _find_pdf_href

    href = "/sites/default/files/2026-02/2026%20HH_0.pdf"
    assert _find_pdf_href(f'<a href="{href}">x</a>', 2026) == href
    # The non-household card carries the same suffix and stays excluded.
    with pytest.raises(ExtractorError):
        _find_pdf_href('<a href="/sites/default/files/2026-01/2026%20NHH_0.pdf">x</a>', 2026)
