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


def _de_watergroep_2026() -> WaterTariff:
    return WaterTariff(
        utility="de_watergroep",
        region="flanders",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="2026",
        source_url="https://example.invalid/",
        yearly_fixed_fee=50.0,  # drinkwater leg only
        yearly_fixed_fee_per_resident_discount=10.0,
        basis_eur_per_m3=2.9521,
        comfort_eur_per_m3=5.9042,
        # Sanering=0; this tariff intentionally only models the drinkwater leg.
        vat_rate=0.06,
    )


def _pidpa_2026() -> WaterTariff:
    return WaterTariff(
        utility="pidpa",
        region="flanders",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="2026",
        source_url="https://example.invalid/",
        yearly_fixed_fee=100.0,
        yearly_fixed_fee_per_resident_discount=20.0,
        basis_eur_per_m3=2.0848,
        comfort_eur_per_m3=4.1696,
        sanering_gemeentelijk_eur_per_m3=1.6533,
        sanering_bovengemeentelijk_eur_per_m3=1.1809,
        vat_rate=0.06,
    )


def test_flanders_de_watergroep_drinkwater_only_handcalc() -> None:
    # 72m³, 2 persons, drinkwater leg only:
    # vastrecht = 50 - 2·10 = 30; basis_volume = 30+30·2 = 90; all 72 in basis
    # volumetric = 72 · 2.9521 = 212.55; ex-VAT = 30 + 212.55 = 242.55
    # incl-VAT = 242.55 · 1.06 = 257.10
    assert compute_annual_cost(_de_watergroep_2026(), 72, 2) == 257.1


def test_flanders_pidpa_full_bill_handcalc() -> None:
    # 80m³, 1 person:
    # basis_vol = 60, consumed_basis = 60, consumed_comfort = 20
    # per_m3_basis = 2.0848 + 1.6533 + 1.1809 = 4.919
    # per_m3_comfort = 4.1696 + 2·(1.6533 + 1.1809) = 9.838
    # volumetric = 60·4.919 + 20·9.838 = 295.14 + 196.76 = 491.90
    # vastrecht = 100 - 1·20 = 80; ex-VAT = 491.90 + 80 = 571.90
    # incl-VAT = 571.90 · 1.06 = 606.214 → 606.21
    assert compute_annual_cost(_pidpa_2026(), 80, 1) == 606.21


def test_flanders_pidpa_all_basis_with_four_persons() -> None:
    # basis_vol = 30 + 30·4 = 150; 100m³ all in basis
    # per_m3_basis = 4.919; volumetric = 100·4.919 = 491.90
    # vastrecht = 100 - 4·20 = 20; ex-VAT = 491.90 + 20 = 511.90
    # incl-VAT = 511.90 · 1.06 = 542.614 → 542.61
    assert compute_annual_cost(_pidpa_2026(), 100, 4) == 542.61


def test_flanders_social_tariff_applies_eighty_percent_reduction() -> None:
    full = compute_annual_cost(_pidpa_2026(), 80, 1)
    discounted = compute_annual_cost(_pidpa_2026(), 80, 1, social_tariff=True)
    assert full is not None and discounted is not None
    assert discounted == round(full * 0.20, 2)


def test_flanders_vastrecht_floors_at_zero_for_huge_household() -> None:
    # An extreme: persons=10 would give negative vastrecht under naive math.
    # The MIN/MAX_PERSONS clamp lives in the config flow; the math itself
    # floors at 0 so a misconfigured options dict can't produce a negative bill.
    cost_clamped = compute_annual_cost(_pidpa_2026(), 0, 10)
    assert cost_clamped == 0.0


def test_returns_none_for_unwired_region() -> None:
    bogus = WaterTariff(
        utility="other",
        region="atlantis",  # not in REGIONS
        valid_from=date(2026, 1, 1),
        valid_until=None,
        publication_label="x",
        source_url="https://example.invalid/",
        yearly_fixed_fee=50.0,
        basis_eur_per_m3=2.95,
    )
    assert compute_annual_cost(bogus, 80, 2) is None
