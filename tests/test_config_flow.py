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

from custom_components.be_water_prices.providers._postcodes import (
    resolve as _resolve_postcode,
)
from custom_components.be_water_prices.providers._postcodes import (
    resolve_candidates as _resolve_candidates,
)


def test_brussels_postcodes_resolve_to_vivaqua() -> None:
    assert _resolve_postcode("1000") == "vivaqua"
    assert _resolve_postcode("1180") == "vivaqua"
    assert _resolve_postcode("1299") == "vivaqua"


def test_walloon_postcodes_resolve_to_swde() -> None:
    # SWDE serves the bulk of Wallonia; spot-check a few core postcodes
    # the ZDE table maps to it.
    assert _resolve_postcode("5000") == "swde"  # Namur centre
    assert _resolve_postcode("7000") == "swde"  # Mons
    assert _resolve_postcode("6700") == "swde"  # Arlon


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


def test_liege_core_resolves_to_cile() -> None:
    # The ZDE-derived table has CILE for the entire Liège core (4000-4099)
    # plus a number of communes further out. SWDE picks up where CILE stops.
    assert _resolve_postcode("4000") == "cile"
    assert _resolve_postcode("4099") == "cile"
    # 4500-area is mixed -- some communes are CILE, others SWDE -- so we
    # don't pin a specific assertion here; the table is the source of truth.


def test_walloon_per_postcode_table_resolves_small_intercommunales() -> None:
    # The ZDE table assigns each Walloon postcode to its actual operator.
    # These spot-check the small intercommunales we have extractors for.
    assert _resolve_postcode("5070") == "inasep"  # Fosses-la-Ville
    assert _resolve_postcode("5640") == "aiem"  # Mettet
    assert _resolve_postcode("5360") == "aiec"  # Hamois
    assert _resolve_postcode("7700") == "ieg"  # Mouscron
    # Régies-served postcode (e.g. Bouillon 6830) returns None so the user
    # picks manually rather than getting a wrong-default to SWDE.
    assert _resolve_postcode("6830") is None  # Bouillon (régie)


def test_knokke_heist_postcodes_resolve_to_agso() -> None:
    assert _resolve_postcode("8300") == "agso_knokke"
    assert _resolve_postcode("8301") == "agso_knokke"


def test_aquaduin_westkust_postcodes_resolve_to_aquaduin() -> None:
    # Koksijde, De Panne, Veurne, Nieuwpoort, Bredene, Middelkerke.
    for pc in ("8670", "8660", "8630", "8620", "8450", "8430"):
        assert _resolve_postcode(pc) == "aquaduin", pc


def test_west_oost_vlaanderen_postcodes_resolve_to_farys() -> None:
    # Farys covers most of 8000-9999; AGSO Knokke, Aquaduin and DWG
    # carve-outs win first. Postcodes below are unambiguously Farys
    # (not on DWG's commune list).
    assert _resolve_postcode("9000") == "farys"  # Gent
    assert _resolve_postcode("8000") == "farys"  # Brugge
    assert _resolve_postcode("9300") == "farys"  # Aalst
    # Carve-out: 8300 stays AGSO, 8670 stays Aquaduin (tested separately above).
    assert _resolve_postcode("8300") == "agso_knokke"
    assert _resolve_postcode("8670") == "aquaduin"


def test_dwg_carveout_in_west_oost_vlaanderen_resolves_to_de_watergroep() -> None:
    # 119 DWG-served postcodes scattered inside the otherwise-Farys
    # 8000-9999 block. Spot-check a few major ones spanning the
    # geographic range.
    assert _resolve_postcode("8500") == "de_watergroep"  # Kortrijk
    assert _resolve_postcode("8530") == "de_watergroep"  # Harelbeke
    assert _resolve_postcode("8800") == "de_watergroep"  # Roeselare
    assert _resolve_postcode("8790") == "de_watergroep"  # Waregem
    assert _resolve_postcode("8900") == "de_watergroep"  # Dikkebus (Ieper)
    assert _resolve_postcode("9100") == "de_watergroep"  # Nieuwkerken-Waas
    assert _resolve_postcode("9112") == "de_watergroep"  # Sinaai
    assert _resolve_postcode("9120") == "de_watergroep"  # Beveren
    assert _resolve_postcode("9160") == "de_watergroep"  # Lokeren
    assert _resolve_postcode("9900") == "de_watergroep"  # Eeklo
    assert _resolve_postcode("9990") == "de_watergroep"  # Maldegem


def test_invalid_postcodes_return_none() -> None:
    assert _resolve_postcode("abcd") is None
    assert _resolve_postcode("") is None
    assert _resolve_postcode(None) is None


def test_dwg_only_after_farys_filter_includes_new_carve_outs() -> None:
    # Once Farys's phantom dropdown entries are filtered out, 8432
    # Leffinge and 9571 Hemelveerdegem become DWG-only and belong in
    # the carve-out (Farys's old "we list it" claim was a mirage).
    assert _resolve_postcode("8432") == "de_watergroep"
    assert _resolve_postcode("9571") == "de_watergroep"


def test_resolve_candidates_returns_single_for_unambiguous() -> None:
    # Bulk of postcodes are unambiguous -- one operator serves them.
    assert _resolve_candidates("1000") == ("vivaqua",)
    assert _resolve_candidates("9000") == ("farys",)
    assert _resolve_candidates("9112") == ("de_watergroep",)
    assert _resolve_candidates("4000") == ("cile",)


def test_resolve_candidates_returns_multiple_for_split_postcodes() -> None:
    # The 8 real splits are postcodes where two or three operators
    # genuinely share the postcode at street level; the config flow
    # asks the user to pick.
    assert _resolve_candidates("1770") == ("de_watergroep", "farys")  # Liedekerke
    assert _resolve_candidates("8020") == ("farys", "de_watergroep")  # Oostkamp
    assert _resolve_candidates("8400") == ("farys", "de_watergroep")  # Oostende
    assert _resolve_candidates("8450") == ("aquaduin", "de_watergroep")  # Bredene
    assert _resolve_candidates("8490") == ("farys", "de_watergroep")  # Jabbeke
    assert _resolve_candidates("9080") == ("farys", "de_watergroep")  # Lochristi
    assert _resolve_candidates("9550") == ("farys", "de_watergroep")  # Herzele
    assert _resolve_candidates("9570") == ("farys", "de_watergroep")  # Lierde


def test_resolve_returns_dominant_candidate_for_splits() -> None:
    # ``resolve`` is the legacy single-operator wrapper; for split
    # postcodes it returns the first candidate (preserves the
    # range-based default so old callers still get the operator the
    # resolver would have picked before split-awareness landed).
    assert _resolve_postcode("1770") == "de_watergroep"
    assert _resolve_postcode("8020") == "farys"
    assert _resolve_postcode("8450") == "aquaduin"


def test_resolve_candidates_empty_for_invalid() -> None:
    assert _resolve_candidates("abcd") == ()
    assert _resolve_candidates("") == ()
    assert _resolve_candidates(None) == ()
    assert _resolve_candidates("6830") == ()  # Bouillon (régie, unsupported)


def test_split_postcodes_first_candidate_matches_legacy_resolution() -> None:
    """The first entry in every _SPLIT_POSTCODES tuple is documented
    as the operator the legacy range-rule resolver would have picked
    -- i.e., what _resolve_single returns for that postcode. The two
    tables are hand-maintained; without this assertion a future
    refresh that moves a postcode into _DWG_POSTCODES_FLANDERS could
    silently flip _resolve_single's answer while _SPLIT_POSTCODES
    still hard-codes the old dominant operator.
    """
    from custom_components.be_water_prices.providers._postcodes import (
        _SPLIT_POSTCODES,
        _resolve_single,
    )

    for pc, candidates in _SPLIT_POSTCODES.items():
        legacy = _resolve_single(int(pc))
        assert candidates[0] == legacy, (
            f"_SPLIT_POSTCODES[{pc!r}][0] is {candidates[0]!r} "
            f"but _resolve_single({pc}) returns {legacy!r}"
        )
