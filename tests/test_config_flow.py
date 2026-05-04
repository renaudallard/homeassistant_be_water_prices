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
    # 4000-7999 covers Liège, Namur, Luxembourg, Hainaut where SWDE is
    # the dominant operator. CILE / INASEP / régies refinements land in
    # v0.4 from the Géoportail Wallonie ZDE GeoPackage.
    assert _resolve_postcode("4000") == "swde"
    assert _resolve_postcode("5000") == "swde"
    assert _resolve_postcode("7000") == "swde"
    assert _resolve_postcode("7999") == "swde"


def test_flanders_postcodes_unresolved_in_v03() -> None:
    # v0.2 (Flemish core) will fill 1500-3999 + 8000-9999.
    assert _resolve_postcode("2000") is None
    assert _resolve_postcode("9000") is None
    # Brabant Wallon (1300-1499) is also unresolved -- inBW lands in v0.4.
    assert _resolve_postcode("1300") is None
    assert _resolve_postcode("1499") is None


def test_invalid_postcodes_return_none() -> None:
    assert _resolve_postcode("abcd") is None
    assert _resolve_postcode("") is None
