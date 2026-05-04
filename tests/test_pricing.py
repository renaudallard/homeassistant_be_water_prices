"""Cost-computation tests (Brussels and Wallonia branches)."""

from __future__ import annotations

from datetime import date

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
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


def _swde_2026() -> WaterTariff:
    cvd = 3.24
    return WaterTariff(
        utility="swde",
        region="wallonia",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="2026",
        source_url="https://example.invalid/",
        yearly_fixed_fee=20 * cvd + 30 * WALLONIA_CVA_EUR_PER_M3,
        cvd_eur_per_m3=cvd,
        cva_eur_per_m3=WALLONIA_CVA_EUR_PER_M3,
        fse_eur_per_m3=WALLONIA_FSE_EUR_PER_M3,
        vat_rate=0.06,
    )


def test_brussels_linear_matches_handcalc() -> None:
    # 80 m³ * € 5,35 (VAT-incl total per m³) + € 40,23 fixed = € 468,23.
    assert compute_annual_cost(_vivaqua_2026(), consumption_m3=80, persons=1) == 468.23


def test_brussels_zero_consumption_only_pays_redevance() -> None:
    assert compute_annual_cost(_vivaqua_2026(), consumption_m3=0, persons=1) == 40.23


def test_wallonia_zero_consumption_pays_full_redevance() -> None:
    # 20·CVD + 30·CVA = 64.80 + 82.44 = 147.24 ex-VAT, ×1.06 = 156.0744 → 156.07.
    assert compute_annual_cost(_swde_2026(), consumption_m3=0, persons=1) == 156.07


def test_wallonia_thirty_m3_uses_only_first_block() -> None:
    # First-block per-m³ ex-VAT = 0.5·3.24 + 2.748 + 0.0339 = 4.4019.
    # 30 × 4.4019 + 147.24 = 132.057 + 147.24 = 279.297 ex-VAT, ×1.06 = 296.05.
    assert compute_annual_cost(_swde_2026(), consumption_m3=30, persons=1) == 296.05


def test_wallonia_eighty_m3_crosses_into_second_block() -> None:
    # 30 × 4.4019 + 50 × 6.0219 + 147.24 = 132.057 + 301.095 + 147.24
    # = 580.392 ex-VAT, ×1.06 = 615.21552 → 615.22.
    assert compute_annual_cost(_swde_2026(), consumption_m3=80, persons=1) == 615.22


def test_wallonia_returns_none_when_cvd_unset() -> None:
    bad = WaterTariff(
        utility="custom",
        region="wallonia",
        valid_from=date(2026, 1, 1),
        valid_until=None,
        publication_label="x",
        source_url="https://example.invalid/",
        yearly_fixed_fee=147.24,
        # cvd defaults to 0.0 -- shaped for Wallonia but not parsable.
    )
    assert compute_annual_cost(bad, 80, 1) is None


def test_returns_none_for_unwired_region() -> None:
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
