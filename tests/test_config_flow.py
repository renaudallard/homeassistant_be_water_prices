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
    assert _resolve_postcode("4000") == "swde"
    assert _resolve_postcode("5000") == "swde"
    assert _resolve_postcode("7000") == "swde"
    assert _resolve_postcode("7999") == "swde"


def test_antwerp_postcodes_resolve_to_pidpa() -> None:
    assert _resolve_postcode("2000") == "pidpa"
    assert _resolve_postcode("2300") == "pidpa"
    assert _resolve_postcode("2999") == "pidpa"


def test_other_flanders_postcodes_resolve_to_de_watergroep() -> None:
    # Vlaams-Brabant + Halle-Vilvoorde + Limburg.
    assert _resolve_postcode("1500") == "de_watergroep"
    assert _resolve_postcode("3000") == "de_watergroep"
    assert _resolve_postcode("3500") == "de_watergroep"


def test_unresolved_postcodes_return_none() -> None:
    # Brabant Wallon (inBW, v0.4) and West-/Oost-Vl (Farys, deferred).
    assert _resolve_postcode("1300") is None
    assert _resolve_postcode("1499") is None
    assert _resolve_postcode("8000") is None
    assert _resolve_postcode("9000") is None


def test_invalid_postcodes_return_none() -> None:
    assert _resolve_postcode("abcd") is None
    assert _resolve_postcode("") is None
