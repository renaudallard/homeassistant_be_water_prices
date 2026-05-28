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

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.pidpa import (
    EXTRACTOR,
    parse_commune_tariff,
    parse_tariff,
)
from tests import fixture_bytes, fixture_html


def _pdf_text() -> str:
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
        # The fixture inlines 2018-2026; 2099 is not a tab.
        parse_commune_tariff(html, commune_slug="geel", year=2099)


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
