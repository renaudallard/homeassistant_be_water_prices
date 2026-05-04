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

"""Postcode resolver tests.

The full HA-driven config flow (``hass`` fixture, etc.) lands in v0.2
once the test environment grows ``pytest-homeassistant-custom-component``.
For now we only assert the pure resolver function: it is the only piece
of config-flow logic that has to be right before HA hands us a session.
"""

from __future__ import annotations

from custom_components.be_water_prices.providers._postcodes import resolve as _resolve_postcode


def test_brussels_postcodes_resolve_to_vivaqua() -> None:
    assert _resolve_postcode("1000") == "vivaqua"
    assert _resolve_postcode("1180") == "vivaqua"
    assert _resolve_postcode("1299") == "vivaqua"


def test_walloon_postcodes_resolve_to_swde() -> None:
    # 4000-4099 carved out for CILE; 4100+ is SWDE.
    assert _resolve_postcode("4100") == "swde"
    # 5000 is INASEP-uncurated → SWDE (the curated INASEP set covers Namur sud only).
    assert _resolve_postcode("5000") == "swde"
    assert _resolve_postcode("7000") == "swde"
    assert _resolve_postcode("7999") == "swde"


def test_antwerp_city_core_resolves_to_water_link() -> None:
    # 2000-2070 is the Antwerp city core where Water-link operates.
    assert _resolve_postcode("2000") == "water_link"
    assert _resolve_postcode("2030") == "water_link"
    assert _resolve_postcode("2070") == "water_link"


def test_rest_of_antwerp_province_resolves_to_pidpa() -> None:
    assert _resolve_postcode("2100") == "pidpa"
    assert _resolve_postcode("2300") == "pidpa"
    assert _resolve_postcode("2999") == "pidpa"


def test_other_flanders_postcodes_resolve_to_de_watergroep() -> None:
    # Vlaams-Brabant + Halle-Vilvoorde + Limburg.
    assert _resolve_postcode("1500") == "de_watergroep"
    assert _resolve_postcode("3000") == "de_watergroep"
    assert _resolve_postcode("3500") == "de_watergroep"


def test_brabant_wallon_postcodes_resolve_to_inbw() -> None:
    assert _resolve_postcode("1300") == "inbw"
    assert _resolve_postcode("1380") == "inbw"
    assert _resolve_postcode("1499") == "inbw"


def test_liege_core_resolves_to_cile_rest_to_swde() -> None:
    assert _resolve_postcode("4000") == "cile"
    assert _resolve_postcode("4099") == "cile"
    assert _resolve_postcode("4100") == "swde"
    assert _resolve_postcode("4500") == "swde"


def test_inasep_namur_sud_postcodes_resolve_to_inasep() -> None:
    assert _resolve_postcode("5060") == "inasep"
    assert _resolve_postcode("5500") == "inasep"
    # 5800 is outside the curated INASEP set → falls through to SWDE.
    assert _resolve_postcode("5800") == "swde"


def test_knokke_heist_postcodes_resolve_to_agso() -> None:
    assert _resolve_postcode("8300") == "agso_knokke"
    assert _resolve_postcode("8301") == "agso_knokke"


def test_aquaduin_westkust_postcodes_resolve_to_aquaduin() -> None:
    # Koksijde, De Panne, Veurne, Nieuwpoort, Bredene, Middelkerke.
    for pc in ("8670", "8660", "8630", "8620", "8450", "8430"):
        assert _resolve_postcode(pc) == "aquaduin", pc


def test_west_oost_vlaanderen_postcodes_resolve_to_farys() -> None:
    # Farys covers most of 8000-9999; AGSO Knokke and Aquaduin carve-outs win first.
    assert _resolve_postcode("9000") == "farys"  # Gent
    assert _resolve_postcode("8500") == "farys"
    assert _resolve_postcode("8000") == "farys"
    # Carve-out: 8300 stays AGSO, 8670 stays Aquaduin (tested separately above).
    assert _resolve_postcode("8300") == "agso_knokke"
    assert _resolve_postcode("8670") == "aquaduin"


def test_invalid_postcodes_return_none() -> None:
    assert _resolve_postcode("abcd") is None
    assert _resolve_postcode("") is None
