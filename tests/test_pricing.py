"""Cost-computation tests (Brussels branch only in v0.1)."""

from __future__ import annotations

from datetime import date

from custom_components.be_water_prices.pricing import compute_annual_cost
from custom_components.be_water_prices.providers.base import WaterTariff


def _vivaqua_2026() -> WaterTariff:
    return WaterTariff(
        utility="vivaqua",
        region="brussels",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="2026",
        source_url="https://example.invalid/",
        yearly_fixed_fee=40.23 / 1.06,
        linear_eur_per_m3=2.62 / 1.06,
        sanering_gemeentelijk_eur_per_m3=2.73 / 1.06,
        vat_rate=0.06,
    )


def test_brussels_linear_matches_handcalc() -> None:
    # 80 m³ * € 5,35 (VAT-incl total per m³) + € 40,23 fixed = € 468,23
    cost = compute_annual_cost(_vivaqua_2026(), consumption_m3=80, persons=1)
    assert cost == 468.23


def test_brussels_zero_consumption_only_pays_redevance() -> None:
    cost = compute_annual_cost(_vivaqua_2026(), consumption_m3=0, persons=1)
    assert cost == 40.23


def test_returns_none_for_unsupported_region() -> None:
    flanders = WaterTariff(
        utility="other",
        region="flanders",
        valid_from=date(2026, 1, 1),
        valid_until=None,
        publication_label="x",
        source_url="https://example.invalid/",
        yearly_fixed_fee=50.0,
        basis_eur_per_m3=2.95,
    )
    assert compute_annual_cost(flanders, 80, 2) is None
