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

"""Pidpa extractor against the captured Tariefplan PDF + commune HTML."""

from __future__ import annotations

import functools
import logging

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.pidpa import (
    EXTRACTOR,
    parse_commune_tariff,
    parse_tariff,
)
from tests import fixture_bytes, fixture_html


@functools.cache
def _pdf_text() -> str:
    # Twelve seconds per extraction on this hardware, nine tests: once is enough.
    return extract_pdf_text_layout(fixture_bytes("pidpa_tariefplan_2025-2030.pdf"))


def test_parses_2026_drinkwater_block() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    assert t.basis_eur_per_m3 == 2.0848
    assert t.comfort_eur_per_m3 == 4.1696  # exactly 2× basis per VMM
    assert t.region == "flanders"
    assert t.valid_from.year == 2026
    assert t.valid_until is not None and t.valid_until.year == 2026


def test_parses_saneringsbijdragen() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    # gemeentelijke (afvoer) > bovengemeentelijke (zuivering) -- the parser
    # used to swap them when anchoring on the substring "gemeentelijke".
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.6533
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.1809


def test_uses_standard_flemish_vastrecht_structure() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    assert t.yearly_fixed_fee == 100.0  # 50 + 30 + 20
    assert t.yearly_fixed_fee_per_resident_discount == 20.0  # 10 + 6 + 4


def test_falls_back_to_last_year_when_target_outside_window() -> None:
    # Tariefplan covers 2025-2030; asking for 2031 should clamp to 2030.
    t = parse_tariff(_pdf_text(), year=2031)
    assert t.valid_from.year == 2030


def test_raises_when_pdf_text_is_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("nothing here", year=2026)


# --- per-commune path -------------------------------------------------------


def test_per_commune_parses_2026_basistarief_columns() -> None:
    html = fixture_html("pidpa_geel_2026.html")
    t = parse_commune_tariff(html, commune_slug="geel", year=2026)
    # Pidpa's per-commune page carries the live numbers, which are higher than
    # the 2024-frozen Tariefplan PDF projection (basis 2,0848 → 2,1888).
    assert t.basis_eur_per_m3 == 2.1888
    assert t.comfort_eur_per_m3 == 4.3776  # exactly 2× basis per VMM
    # Drinkwater + gemeentelijke afvoer + bovengemeentelijke zuivering --
    # Pidpa publishes them per commune; today they are uniform province-wide.
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019


def test_per_commune_publication_label_carries_slug() -> None:
    html = fixture_html("pidpa_geel_2026.html")
    t = parse_commune_tariff(html, commune_slug="geel", year=2026)
    assert "geel" in t.publication_label
    assert t.source_url.endswith("/ons-aanbod/je-gemeente/geel")


def test_per_commune_uses_standard_flemish_vastrecht_structure() -> None:
    html = fixture_html("pidpa_geel_2026.html")
    t = parse_commune_tariff(html, commune_slug="geel", year=2026)
    assert t.yearly_fixed_fee == 100.0
    assert t.yearly_fixed_fee_per_resident_discount == 20.0


def test_per_commune_raises_when_year_tab_missing() -> None:
    html = fixture_html("pidpa_geel_2026.html")
    with pytest.raises(ExtractorError):
        # The fixture inlines 2018-2026; 2099 is not a tab. An explicitly
        # requested missing year stays a hard error (no silent fallback).
        parse_commune_tariff(html, commune_slug="geel", year=2099)


def test_per_commune_falls_back_to_latest_year_on_rollover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Production path (year not pinned): in the Jan 1 window before Pidpa
    # publishes the new-year tab, fall back to the latest tab present
    # rather than going blank, mirroring the PDF path.
    from datetime import date

    from custom_components.be_water_prices.providers import pidpa

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2099, 1, 1)

    monkeypatch.setattr(pidpa, "date", _FakeDate)
    html = fixture_html("pidpa_geel_2026.html")
    t = parse_commune_tariff(html, commune_slug="geel")  # year=None -> 2099
    # Fixture tops out at 2026, so the still-in-force 2026 column is served.
    assert "2026" in t.publication_label
    assert t.valid_from.year == 2026


def test_extractor_advertises_per_commune_support() -> None:
    assert EXTRACTOR.fetch_for_commune is not None
    assert EXTRACTOR.list_communes is not None
    assert EXTRACTOR.supports_communes


def test_unservable_slugs_blocklist_includes_antwerpen() -> None:
    # Pidpa's sitemap lists "antwerpen" but the corresponding tariff
    # page has no huishoudelijk table (Water-link's territory). The
    # blocklist must keep dropping it from list_communes() so users
    # can't pick a crashing option.
    from custom_components.be_water_prices.providers.pidpa import _UNSERVABLE_COMMUNE_SLUGS

    assert "antwerpen" in _UNSERVABLE_COMMUNE_SLUGS


async def test_default_fetch_reads_the_commune_page(monkeypatch: pytest.MonkeyPatch) -> None:
    """The no-commune fetch serves the published rate, not the 2024 projection."""
    from datetime import date
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, pidpa

    # Pin the clock to the fixture's year: from 2027 this test would only
    # pass through the rollover fallback, which is not what it checks.
    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)

    monkeypatch.setattr(pidpa, "date", _FakeDate)
    with (
        patch.object(
            _html, "fetch_html", new=AsyncMock(return_value=fixture_html("pidpa_geel_2026.html"))
        ) as page,
        patch.object(pidpa, "fetch_pdf_text_layout", new=AsyncMock()) as pdf,
    ):
        t = await pidpa.fetch(session=None)  # type: ignore[arg-type]
    page.assert_awaited_once()
    pdf.assert_not_awaited()
    assert t.basis_eur_per_m3 == 2.1888
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019
    assert "province-wide default" in t.publication_label
    assert t.source_url.endswith("/ons-aanbod/je-gemeente/geel")


async def test_default_fetch_falls_back_to_the_tariefplan_pdf(
    caplog: pytest.LogCaptureFixture,
) -> None:
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, pidpa
    from custom_components.be_water_prices.providers._flanders import build_flanders_tariff

    # Extracting the real PDF inside an async test drags every pdfminer
    # debug record through the event loop and trips the timeout; the
    # routing is what matters here, so hand the fallback a ready tariff.
    projection = build_flanders_tariff(
        utility_id="pidpa",
        year=2026,
        publication_label="Pidpa Tariefplan 2025-2030 column 2026",
        source_url=pidpa.SOURCE_URL,
        basis=2.0848,
        comfort=4.1696,
    )
    with (
        patch.object(_html, "fetch_html", new=AsyncMock(side_effect=ExtractorError("HTTP 404"))),
        patch.object(pidpa, "fetch_tariefplan", new=AsyncMock(return_value=projection)) as pdf,
        caplog.at_level(logging.WARNING),
    ):
        t = await pidpa.fetch(session=None)  # type: ignore[arg-type]
    pdf.assert_awaited_once()
    assert t is projection
    assert "Tariefplan PDF projection" in caplog.text


async def test_default_fetch_reraises_transient_instead_of_the_pdf() -> None:
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import _html, pidpa
    from custom_components.be_water_prices.providers.base import TransientFetchError

    with (
        patch.object(
            _html, "fetch_html", new=AsyncMock(side_effect=TransientFetchError("HTTP 503"))
        ),
        patch.object(pidpa, "fetch_pdf_text_layout", new=AsyncMock()) as pdf,
        pytest.raises(TransientFetchError),
    ):
        await pidpa.fetch(session=None)  # type: ignore[arg-type]
    # The projection must not stand in for an outage the live check should see.
    pdf.assert_not_awaited()


async def test_list_communes_drops_the_blocklisted_slug() -> None:
    """The blocklist is only useful if the filter that applies it exists."""
    from unittest.mock import AsyncMock, patch

    from custom_components.be_water_prices.providers import pidpa

    sitemap = (
        "<urlset><url><loc>https://www.pidpa.be/ons-aanbod/je-gemeente/antwerpen</loc></url>"
        "<url><loc>https://www.pidpa.be/ons-aanbod/je-gemeente/geel</loc></url></urlset>"
    )
    with patch.object(pidpa, "fetch_html", new=AsyncMock(return_value=sitemap)):
        communes = await pidpa.list_communes(session=None)  # type: ignore[arg-type]
    assert [c.id for c in communes] == ["geel"]


def test_a_year_tab_outside_the_household_tab_is_not_used() -> None:
    """The walk-up to the outer tab is what keeps the business table out."""
    from bs4 import BeautifulSoup

    from custom_components.be_water_prices.providers.pidpa import _is_huishoudelijk_year_tab

    household = BeautifulSoup(
        '<div class="tariff-tab-content" id="tabid-1-tab-0">'
        '<div class="tariff-tab-content" id="tabid-1-tab-2026"></div></div>',
        "html.parser",
    )
    business = BeautifulSoup(
        '<div class="tariff-tab-content" id="tabid-1-tab-1">'
        '<div class="tariff-tab-content" id="tabid-1-tab-2026"></div></div>',
        "html.parser",
    )
    inner = household.find("div", id="tabid-1-tab-2026")
    assert inner is not None and _is_huishoudelijk_year_tab(inner, year=2026)
    inner = business.find("div", id="tabid-1-tab-2026")
    assert inner is not None and not _is_huishoudelijk_year_tab(inner, year=2026)


def test_a_table_short_of_columns_is_refused() -> None:
    rows = "".join(
        f"<tr><td>{label}</td><td>1 euro excl. btw</td></tr>"
        for label in ("", "Vastrecht", "Korting", "Basistarief", "Comforttarief")
    )
    html = (
        '<div class="tariff-tab-content" id="t-tab-0"><div class="tariff-tab-content" '
        f'id="t-tab-2026"><table><tr><td>Integrale waterprijs</td></tr>{rows}</table>'
        "</div></div>"
    )
    with pytest.raises(ExtractorError, match="malformed"):
        parse_commune_tariff(html, commune_slug="x", year=2026)


def test_a_sanering_line_for_another_year_does_not_win() -> None:
    """The last line used to win; the year printed between the label and the colon is read now."""
    from custom_components.be_water_prices.providers.pidpa import _sanering_for_year

    text = (
        "Tarief gemeentelijke sanering (afvoer) : 1,0000 €/m³ basistarief\n"
        "Tarief gemeentelijke sanering (afvoer) 2030 : 9,9999 €/m³ basistarief\n"
        "Tarief bovengemeentelijke sanering (zuivering) 2026 : 1,1809 €/m³\n"
        "Tarief bovengemeentelijke sanering (zuivering) 2025 : 1,1000 €/m³\n"
    )
    assert _sanering_for_year(text, 2026) == {"afvoer": 1.0, "zuivering": 1.1809}
    # The Tariefplan PDF dates its sanering 2024 and never moves it.
    frozen = "Tarief gemeentelijke sanering (afvoer ) 2024: 1,6533 €/m³ basistarief\n"
    assert _sanering_for_year(frozen, 2026) == {"afvoer": 1.6533}
    assert _sanering_for_year(frozen + text, 2026)["afvoer"] == 1.0


def test_a_tab_past_the_year_asked_for_is_not_a_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no 2026 tab, a 2027 tab was served as 2026's card."""
    from datetime import date

    from custom_components.be_water_prices.providers import pidpa

    class _FakeDate(date):
        @classmethod
        def today(cls) -> date:
            return date(2026, 9, 6)

    monkeypatch.setattr(pidpa, "date", _FakeDate)
    page = fixture_html("pidpa_geel_2026.html")
    assert page.count("-tab-2026") >= 1
    with pytest.raises(ExtractorError, match="2026"):
        pidpa.parse_commune_tariff(page.replace("-tab-2026", "-tab-2027"), commune_slug="geel")
