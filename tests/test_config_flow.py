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

import pytest

from custom_components.be_water_prices.providers._postcodes import (
    resolve as _resolve_postcode,
)
from custom_components.be_water_prices.providers._postcodes import (
    resolve_candidates as _resolve_candidates,
)


@pytest.mark.parametrize("raw", ["1_000", "１０００", "+1000", "1000.0", "01000", "10 00"])
def test_a_postcode_is_four_ascii_digits_and_nothing_else(raw: str) -> None:
    """int() took all of these, and the raw string is what the entry keeps."""
    assert _resolve_postcode(raw) is None
    assert _resolve_postcode(" 1000 ") == "vivaqua"


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
    # 2100 is Deurne, which is the city of Antwerp and Water-link's; the
    # province outside the city and its ring is Pidpa's.
    assert _resolve_postcode("2110") == "pidpa"
    assert _resolve_postcode("2300") == "pidpa"
    assert _resolve_postcode("2999") == "pidpa"


def test_other_flanders_postcodes_resolve_to_de_watergroep() -> None:
    # Vlaams-Brabant + Halle-Vilvoorde + Limburg.
    assert _resolve_postcode("1500") == "de_watergroep"
    assert _resolve_postcode("3000") == "de_watergroep"
    assert _resolve_postcode("3500") == "de_watergroep"


def test_farys_pockets_in_halle_vilvoorde_beat_the_range_rule() -> None:
    # Farys serves a handful of Halle-Vilvoorde communes that sit inside
    # the De Watergroep range. The carve-out has to win over the range or
    # these households are priced against the wrong operator.
    assert _resolve_postcode("1620") == "farys"  # Drogenbos
    assert _resolve_postcode("1653") == "farys"  # Dworp (Beersel)
    assert _resolve_postcode("1933") == "farys"  # Sterrebeek (Zaventem)
    # Neighbours inside the same range stay with De Watergroep.
    assert _resolve_postcode("1640") == "de_watergroep"
    assert _resolve_postcode("1740") == "de_watergroep"
    # A street-level split still goes to the chooser rather than the
    # carve-out.
    assert _resolve_postcode("1770") == "de_watergroep"


def test_brabant_wallon_is_split_between_inbw_and_swde() -> None:
    # The province is not inBW territory end to end: the ZDE gives inBW 20
    # of its 44 postcodes and SWDE the other 24, so the table decides and
    # a blanket range rule would mis-bill half the province.
    assert _resolve_postcode("1300") == "inbw"
    assert _resolve_postcode("1380") == "inbw"
    assert _resolve_postcode("1495") == "inbw"
    assert _resolve_postcode("1315") == "swde"
    assert _resolve_postcode("1421") == "swde"
    assert _resolve_postcode("1480") == "swde"
    # A number inside the range that no commune uses has no answer at all,
    # which sends the user to the manual picker instead of guessing.
    assert _resolve_postcode("1499") is None


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
    # Koksijde, De Panne, Veurne, Nieuwpoort and both Alveringem codes.
    for pc in ("8670", "8660", "8630", "8620", "8690", "8691"):
        assert _resolve_postcode(pc) == "aquaduin", pc


def test_middelkerke_and_bredene_are_not_aquaduin() -> None:
    # Aquaduin's own publication names Alveringem, De Panne, Nieuwpoort
    # and Veurne; neither Middelkerke nor Bredene is on that list.
    # Farys serves Middelkerke (8431/8433/8434 already resolve to it)
    # and De Watergroep serves Bredene.
    assert _resolve_postcode("8430") == "farys"
    assert _resolve_postcode("8450") == "de_watergroep"
    assert _resolve_candidates("8450") == ("de_watergroep",)


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


def test_postcode_range_boundaries_are_inclusive() -> None:
    # Pin the inclusive upper bounds of each Flemish range so an
    # off-by-one regression in _resolve_single (e.g. `<= 1999` to
    # `< 1999`) is caught: 1999 must stay DWG, 2999 must stay Pidpa,
    # 9999 must stay Farys.
    assert _resolve_postcode("1999") == "de_watergroep"
    assert _resolve_postcode("3999") == "de_watergroep"
    assert _resolve_postcode("2999") == "pidpa"
    assert _resolve_postcode("9999") == "farys"
    # Lower bounds of each range.
    assert _resolve_postcode("1000") == "vivaqua"
    assert _resolve_postcode("1300") == "inbw"
    assert _resolve_postcode("1500") == "de_watergroep"
    assert _resolve_postcode("2000") == "water_link"
    # 2100 is Deurne, a district of the city of Antwerp and Water-link's;
    # 2110 Wijnegem is the first postcode above the city block that Pidpa
    # really serves, so it is the one that pins this range's start.
    assert _resolve_postcode("2110") == "pidpa"
    assert _resolve_postcode("3000") == "de_watergroep"


def test_internal_range_edges_are_pinned_against_shadowing_regressions() -> None:
    # Pinning user-facing ranges only catches regressions on edges that
    # are NOT shadowed by a higher-priority range above. Internal
    # boundaries -- e.g. extending DWG's 1500-1999 to 2000 -- would be
    # silently masked by the water_link 2000-2070 branch firing first
    # under the same `_resolve_postcode` call. Pin the internal edges
    # explicitly so a range-extension regression surfaces.
    # Water-link / Pidpa split inside the Antwerp province (2000-2070
    # vs 2100-2999):
    assert _resolve_postcode("2070") == "water_link"
    # 2071 is the first non-water_link postcode in the Antwerp
    # province; it must deterministically resolve to Pidpa via the
    # 2000-2999 fallthrough. Allowing None here would defeat the
    # shadowing-regression detection -- a range narrowing that drops
    # 2071-2099 from Pidpa coverage would silently pass.
    assert _resolve_postcode("2071") == "pidpa"
    # Walloon table coverage cliff (postcodes outside the table return
    # None so the manual picker fires):
    assert _resolve_postcode("6830") is None  # Bouillon régie -- not shipped
    # AGSO Knokke carve-outs sit inside the Farys 8000-9999 range:
    assert _resolve_postcode("8300") == "agso_knokke"
    assert _resolve_postcode("8301") == "agso_knokke"
    # Aquaduin carve-out neighbours: the whole Middelkerke block is
    # Farys, and Alveringem is the Aquaduin edge.
    assert _resolve_postcode("8430") == "farys"
    assert _resolve_postcode("8431") == "farys"
    assert _resolve_postcode("8691") == "aquaduin"


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


def test_every_table_answer_is_a_registered_extractor() -> None:
    """The resolver's tables hold literal ids; the flow calls get() on them at once."""
    from custom_components.be_water_prices.providers import _postcodes, all_extractors

    ids = {extractor.id for extractor in all_extractors()}
    answers = set(_postcodes._PER_POSTCODE.values())
    answers |= {c for candidates in _postcodes._SPLIT_POSTCODES.values() for c in candidates}
    answers |= {
        _postcodes.resolve(str(pc)) for pc in range(1000, 10000) if _postcodes.resolve(str(pc))
    }
    assert answers <= ids, sorted(answers - ids)


def test_water_link_2070_is_offered_its_own_commune_not_antwerpen() -> None:
    """2070 is Zwijndrecht/Burcht: the card bills it in the ring group."""
    from custom_components.be_water_prices.config_flow import _commune_for_postcode

    assert _resolve_postcode("2070") == "water_link"
    assert _commune_for_postcode("water_link", "2070") == "Beveren-Kruibeke-Zwijndrecht"
    # The city core keeps the operator default.
    assert _commune_for_postcode("water_link", "2000") is None
    # Operators that say nothing about postcodes are unaffected.
    assert _commune_for_postcode("farys", "9000") is None
    assert _commune_for_postcode("water_link", None) is None


def test_the_2070_default_is_worth_the_difference_it_claims() -> None:
    """Antwerpen 1,3345 against the ring group's 1,9572 is 66 EUR a year."""
    from custom_components.be_water_prices.pricing import compute_annual_cost
    from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
    from custom_components.be_water_prices.providers.water_link import parse_tariff
    from tests import fixture_bytes

    text = extract_pdf_text_layout(fixture_bytes("water_link_2026.pdf"))
    antwerpen = compute_annual_cost(parse_tariff(text, year=2026, commune="Antwerpen"), 80, 1)
    ring = compute_annual_cost(
        parse_tariff(text, year=2026, commune="Beveren-Kruibeke-Zwijndrecht"), 80, 1
    )
    assert antwerpen is not None and ring is not None
    assert round(ring - antwerpen, 2) == 66.01


def test_the_three_pidpa_communes_off_the_default_rate_are_pre_selected() -> None:
    """Pidpa is not one rate province-wide; three of 63 communes are lower."""
    from custom_components.be_water_prices.config_flow import _commune_for_postcode

    for postcode, slug in (("2560", "nijlen"), ("2160", "wommelgem"), ("2460", "kasterlee")):
        assert _resolve_postcode(postcode) == "pidpa", postcode
        assert _commune_for_postcode("pidpa", postcode) == slug
    # A commune that really is on the default rate is left alone.
    assert _commune_for_postcode("pidpa", "2440") is None


def test_the_city_of_antwerp_is_water_link_not_pidpa() -> None:
    """Every district above the 2000-2070 block, plus three ring communes.

    water-link.be lists all twelve under "Water in jouw woonplaats", none
    of their municipalities appears in Pidpa's own commune list, and
    Water-link's card carries a billing row for Antwerpen, Edegem, Hove
    and Mortsel. Left on the Pidpa range they were billed 121.09 EUR a
    year too much (55.08 for the three ring communes).
    """
    for postcode in (
        "2099",
        "2100",  # Deurne
        "2140",  # Borgerhout
        "2150",  # Borsbeek
        "2170",  # Merksem
        "2180",  # Ekeren
        "2540",  # Hove
        "2600",  # Berchem
        "2610",  # Wilrijk
        "2640",  # Mortsel
        "2650",  # Edegem
        "2660",  # Hoboken
    ):
        assert _resolve_postcode(postcode) == "water_link", postcode
    # Water-link is active in these three and bills none of them: they
    # carry no row on its card and Pidpa lists all three.
    for postcode in ("2520", "2620", "2900"):  # Ranst, Hemiksem, Schoten
        assert _resolve_postcode(postcode) == "pidpa", postcode


def test_the_farys_communes_off_the_default_card_are_pre_selected() -> None:
    """Five of Farys's 266 communes are not on the Gent-centrum card."""
    from custom_components.be_water_prices.config_flow import _commune_for_postcode

    for postcode, commune in (
        ("1620", "25906"),  # Drogenbos, gemeentelijke 1,4903
        ("1930", "25926"),  # Zaventem and Nossegem, drinkwater 2,9251
        ("1932", "25931"),  # Sint-Stevens-Woluwe
        ("1933", "25936"),  # Sterrebeek
    ):
        assert _resolve_postcode(postcode) == "farys", postcode
        assert _commune_for_postcode("farys", postcode) == commune
    # A commune that really is on the default card is left alone.
    assert _commune_for_postcode("farys", "1730") is None


def test_the_zaventem_default_is_worth_the_difference_it_claims() -> None:
    """The tussenkomst is 0,0807 off the drinkwater leg, 8.55 EUR a year."""
    from custom_components.be_water_prices.pricing import compute_annual_cost
    from custom_components.be_water_prices.providers.farys import parse_tariff
    from tests import fixture_html

    gent = compute_annual_cost(parse_tariff(fixture_html("farys_gent_2026.json"), year=2026), 80, 1)
    zaventem = compute_annual_cost(
        parse_tariff(fixture_html("farys_zaventem_2026.json"), year=2026), 80, 1
    )
    assert gent is not None and zaventem is not None
    assert round(gent - zaventem, 2) == 8.55


def test_the_pidpa_default_no_longer_claims_to_be_province_wide() -> None:
    from custom_components.be_water_prices.providers import pidpa

    assert "province-wide" not in pidpa._DEFAULT_COMMUNE_LABEL
    assert "province-wide" not in (pidpa.__doc__ or "")
