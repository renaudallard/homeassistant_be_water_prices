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


def test_non_brussels_postcodes_dont_resolve_in_v01() -> None:
    # v0.2 fills in Flanders (1500-3999), v0.3 fills in Wallonia (4000+).
    assert _resolve_postcode("2000") is None
    assert _resolve_postcode("9000") is None
    assert _resolve_postcode("4000") is None


def test_invalid_postcodes_return_none() -> None:
    assert _resolve_postcode("abcd") is None
    assert _resolve_postcode("") is None
