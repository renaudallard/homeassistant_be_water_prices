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

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.water_link import EXTRACTOR, parse_tariff
from tests import fixture_bytes


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


def test_raises_when_pdf_text_is_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("nothing here", year=2026)


def test_extractor_supports_communes_and_lists_them() -> None:
    # Per-commune support is what unlocks the OptionsFlow commune dropdown.
    from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
    from custom_components.be_water_prices.providers.water_link import _COMMUNE_LINE_RE

    assert EXTRACTOR.supports_communes
    text = extract_pdf_text_layout(fixture_bytes("water_link_2026.pdf"))
    cut = text.find("BASISTARIEF")
    end = text.find("COMFORTTARIEF", cut)
    block = text[cut:end]
    found = [m.group(1).strip() for m in _COMMUNE_LINE_RE.finditer(block)]
    assert "Antwerpen" in found
    assert "Edegem" in found
    assert "Mortsel" in found


def test_parse_tariff_with_specific_commune_returns_ring_sanering() -> None:
    from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout

    text = extract_pdf_text_layout(fixture_bytes("water_link_2026.pdf"))
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

    text = extract_pdf_text_layout(fixture_bytes("water_link_2026.pdf"))
    with (
        patch.object(_html, "fetch_html", new=AsyncMock(return_value=_tariff_page(2026))),
        patch.object(
            water_link,
            "fetch_pdf_text_layout",
            new=AsyncMock(side_effect=[ExtractorError("HTTP 404"), text]),
        ) as mock,
    ):
        _out, year = await water_link._fetch_pdf_text(session=None)  # type: ignore[arg-type]
    assert mock.await_count == 2
    assert year == date.today().year - 1
