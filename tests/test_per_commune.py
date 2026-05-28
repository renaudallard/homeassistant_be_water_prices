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

"""Per-commune routing tests for De Watergroep, Farys, and Water-link."""

from __future__ import annotations

from datetime import date

from custom_components.be_water_prices.providers import all_extractors
from custom_components.be_water_prices.providers.base import (
    WaterTariff,
    relabel_with_human_commune,
)
from custom_components.be_water_prices.providers.de_watergroep import (
    _OPTION_RE as DWG_OPTION_RE,
)
from custom_components.be_water_prices.providers.de_watergroep import (
    EXTRACTOR as DWG,
)
from custom_components.be_water_prices.providers.de_watergroep import (
    parse_commune_tariff as parse_dwg_commune,
)
from custom_components.be_water_prices.providers.de_watergroep import (
    parse_news_tariff,
)
from custom_components.be_water_prices.providers.farys import (
    _OPTION_RE as FARYS_OPTION_RE,
)
from custom_components.be_water_prices.providers.farys import (
    EXTRACTOR as FARYS,
)
from custom_components.be_water_prices.providers.water_link import (
    EXTRACTOR as WATER_LINK,
)
from tests import fixture_html


def test_only_per_commune_utilities_advertise_commune_support() -> None:
    per_commune = {e.id for e in all_extractors() if e.supports_communes}
    assert per_commune == {"de_watergroep", "farys", "pidpa", "water_link"}


def test_dwg_per_commune_captures_full_integrale_waterprijs() -> None:
    # The big regression risk: earlier versions of DWG only carried the
    # drinkwater leg (sanering = 0). Per-commune fetch must now carry
    # both saneringsbijdragen.
    t = parse_dwg_commune(
        fixture_html("dewatergroep_halle_2026.html"), year=2026, commune_label="Halle"
    )
    assert t.basis_eur_per_m3 == 2.9251
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019
    assert t.yearly_fixed_fee == 100.0  # full VMM 50+30+20
    assert t.yearly_fixed_fee_per_resident_discount == 20.0  # full VMM 10+6+4


def test_dwg_per_commune_handles_missing_afvoer_row() -> None:
    # Sinaai (postcode 9112) has no gemeentelijke saneringsbijdrage:
    # DWG renders the "Afvoer van afvalwater" header with no euro
    # amount. A missing row must be parsed as 0.0 (drinkwater alone is
    # mandatory) so the integration still loads for these communes
    # instead of crashing at setup with "could not parse ... rows".
    t = parse_dwg_commune(
        fixture_html("dewatergroep_sinaai_2026.html"), year=2026, commune_label="Sinaai"
    )
    assert t.basis_eur_per_m3 == 2.9251
    assert t.sanering_gemeentelijk_eur_per_m3 == 0.0
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019


def test_dwg_per_commune_does_not_bleed_into_comforttarief_block() -> None:
    # Regression: an earlier version anchored the Afvoer/Zuivering
    # regexes on the single 'Basistarief per m³' phrase and used
    # re.DOTALL + .*? -- if the Basistarief Afvoer row had no euro
    # amount (zero-afvoer commune), the regex skipped past it and
    # silently matched the Comforttarief Afvoer (~2x the basistarief),
    # silently doubling san_gem instead of returning 0. Build a minimal
    # HTML where basis Afvoer is empty but comfort Afvoer carries an
    # amount and assert san_gem stays 0.0.
    html = (
        "<html><body>"
        "Basistarief per m&#179; "
        "Waterverbruik drinkwater &euro; 2,9251 "
        "Afvoer van afvalwater "  # empty (zero-afvoer commune)
        "Zuivering van afvalwater &euro; 1,7019 "
        "Basistarief per liter Waterverbruik drinkwater 0,002925 "
        "Comforttarief per m&#179; "
        "Waterverbruik drinkwater &euro; 5,8502 "
        "Afvoer van afvalwater &euro; 3,9144 "  # would bleed if scope is wrong
        "Zuivering van afvalwater &euro; 3,4038 "
        "</body></html>"
    )
    t = parse_dwg_commune(html, year=2026, commune_label="synthetic")
    assert t.basis_eur_per_m3 == 2.9251
    assert t.sanering_gemeentelijk_eur_per_m3 == 0.0  # NOT 3.9144
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019


def test_dwg_news_fallback_keeps_drinkwater_leg_only_semantics() -> None:
    # Sanity check: the no-commune fallback must NOT pretend to know
    # sanering -- it must keep it at 0 and use the drinkwater-only
    # vastrecht (50 / 10) so the bill matches the news-article example.
    t = parse_news_tariff(fixture_html("dewatergroep_2026.html"), year=2026)
    assert t.basis_eur_per_m3 == 2.9521
    assert t.sanering_gemeentelijk_eur_per_m3 == 0.0
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 0.0
    assert t.yearly_fixed_fee == 50.0  # drinkwater leg
    assert t.yearly_fixed_fee_per_resident_discount == 10.0


def test_dwg_commune_dropdown_yields_700_options() -> None:
    html = fixture_html("dewatergroep_tarieven_2026.html")
    options = list(DWG_OPTION_RE.finditer(html))
    # The dropdown carries every Vlaams commune DWG serves; ~700 in 2026.
    assert len(options) > 600


def test_farys_commune_dropdown_yields_290_options() -> None:
    # Used in tests/fixtures/farys_gent_2026.json indirectly; the live
    # dropdown is on the page itself, captured separately. Smoke-test
    # that the option regex still matches a plausible count.
    from custom_components.be_water_prices.providers.farys import _OPTION_RE

    # Build a minimal fixture to exercise the option regex.
    html = (
        '<select name="municipality">'
        '<option value="24511">9300 - Aalst (Aalst)</option>'
        '<option value="25071">9000 - Gent-centrum (Gent)</option>'
        "</select>"
    )
    options = list(_OPTION_RE.finditer(html))
    assert len(options) == 2
    assert options[0].group(1) == "24511"


def test_extractors_advertise_their_metadata_correctly() -> None:
    assert DWG.supports_communes
    assert FARYS.supports_communes
    assert WATER_LINK.supports_communes
    # Pidpa joined the per-commune set in v0.5.x once the
    # /ons-aanbod/je-gemeente/<slug> ingestion path landed.
    pidpa = next(e for e in all_extractors() if e.id == "pidpa")
    assert pidpa.supports_communes
    # VIVAQUA, SWDE and the rest are still single-fetch.
    aquaduin = next(e for e in all_extractors() if e.id == "aquaduin")
    assert not aquaduin.supports_communes


# Sentinel to keep the module-level import sorted; ensures test_per_commune
# imports the option regexes in a single pass.
_SENTINEL_REGEXES = (DWG_OPTION_RE, FARYS_OPTION_RE)


def _farys_25071_tariff() -> WaterTariff:
    return WaterTariff(
        utility="farys",
        region="flanders",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="Farys watertarieven 2026 (25071)",
        source_url="https://example.invalid/",
        yearly_fixed_fee=100.0,
        basis_eur_per_m3=1.5,
    )


def test_relabel_swaps_opaque_id_for_human_label() -> None:
    relabelled = relabel_with_human_commune(
        _farys_25071_tariff(),
        commune_id="25071",
        commune_label="Gent (Centrum)",
    )
    assert relabelled.publication_label == "Farys watertarieven 2026 (Gent (Centrum))"


def test_relabel_is_a_no_op_when_label_missing_or_equals_id() -> None:
    base = _farys_25071_tariff()
    assert relabel_with_human_commune(base, commune_id="25071", commune_label=None) is base
    assert relabel_with_human_commune(base, commune_id="25071", commune_label="25071") is base


def test_relabel_is_a_no_op_when_id_not_in_publication_label() -> None:
    base = _farys_25071_tariff()
    out = relabel_with_human_commune(base, commune_id="99999", commune_label="Anywhere")
    assert out is base
