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

"""Cost-computation tests (Brussels and Wallonia branches)."""

from __future__ import annotations

from datetime import date

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.pricing import compute_annual_cost, compute_ytd_cost
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


def test_negative_consumption_is_clamped_to_zero() -> None:
    # Defensive: compute_annual_cost should never produce a negative bill
    # for a malformed consumption input. Same redevance as the zero case.
    assert compute_annual_cost(_vivaqua_2026(), consumption_m3=-5, persons=1) == 40.23
    assert compute_annual_cost(_swde_2026(), consumption_m3=-5, persons=1) == 156.07


def test_wallonia_zero_consumption_pays_full_redevance() -> None:
    # 20·CVD + 30·CVA = 64.80 + 82.44 = 147.24 ex-VAT, ×1.06 = 156.0744 → 156.07.
    assert compute_annual_cost(_swde_2026(), consumption_m3=0, persons=1) == 156.07


def test_wallonia_thirty_m3_uses_only_first_block() -> None:
    # First-block per-m³ ex-VAT = 0.5·CVD + FSE = 0.5·3.24 + 0.0339 = 1.6539
    # (CVA is exempt on the residential first 30 m³ per CWaPE).
    # 30 × 1.6539 + redevance(147.24) = 49.617 + 147.24 = 196.857 ex-VAT
    # ×1.06 = 208.668 → 208.67.
    assert compute_annual_cost(_swde_2026(), consumption_m3=30, persons=1) == 208.67


def test_wallonia_eighty_m3_crosses_into_second_block() -> None:
    # 30 × 1.6539 + 50 × (3.24 + 2.748 + 0.0339) + 147.24
    # = 49.617 + 50·6.0219 + 147.24
    # = 49.617 + 301.095 + 147.24 = 497.952 ex-VAT, ×1.06 = 527.829 → 527.83.
    assert compute_annual_cost(_swde_2026(), consumption_m3=80, persons=1) == 527.83


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


def test_flanders_social_tariff_zero_consumption_still_pays_vastrecht_share() -> None:
    # 0 m³ on social tariff: only the (1-person) vastrecht of 80 EUR ex-VAT,
    # ×1.06 VAT, ×0.20 social reduction → 16.96.
    assert compute_annual_cost(_pidpa_2026(), 0, 1, social_tariff=True) == 16.96


def test_flanders_consumption_exactly_at_basis_volume_boundary() -> None:
    # 1 person, basis_volume = 60 m³. Exactly 60 m³ must bill all-basis
    # with nothing leaking onto the comfort tier.
    # per_m3_basis = 2.0848 + 1.6533 + 1.1809 = 4.9190
    # volumetric = 60 · 4.9190 = 295.14
    # vastrecht  = 100 - 1·20 = 80
    # ex-VAT = 375.14; ×1.06 = 397.6484 → 397.65.
    assert compute_annual_cost(_pidpa_2026(), 60, 1) == 397.65


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


# ---------------------------------------------------------------------------
# YTD (compute_ytd_cost) tests
# ---------------------------------------------------------------------------


def test_ytd_on_jan_1_with_no_consumption_is_zero() -> None:
    # Brand new year, 0 m³ consumed, ~0 days elapsed → 0 EUR (no fees yet).
    assert compute_ytd_cost(_vivaqua_2026(), 0, 1, 0.0) == 0.0


def test_ytd_jan_1_with_consumption_bills_volumetric_no_redevance() -> None:
    # Some water consumed, but ~0 days elapsed: volumetric only, fee_factor=0.
    # Brussels: 5 · (2.62/1.06 + 2.73/1.06) ·1.06 = 5 · 5.35 = 26.75.
    assert compute_ytd_cost(_vivaqua_2026(), 5, 1, 0.0) == 26.75


def test_ytd_at_year_end_matches_annual_projection() -> None:
    # Full year elapsed, full annual consumption -> identical to annual.
    annual = compute_annual_cost(_vivaqua_2026(), 80, 1)
    ytd = compute_ytd_cost(_vivaqua_2026(), 80, 1, 1.0)
    assert annual == ytd


def test_ytd_brussels_pro_rates_redevance_at_half_year() -> None:
    # 40 m³ consumed by mid-year (fee_factor = 0.5).
    # ex-VAT = 40·(5.35/1.06) + 0.5·(40.23/1.06) = 220.8632...
    # ×1.06 = 234.115; banker's rounding → 234.12.
    assert compute_ytd_cost(_vivaqua_2026(), 40, 1, 0.5) == 234.12


def test_ytd_wallonia_pro_rates_redevance_only() -> None:
    # SWDE 80 m³, mid-year:
    # first_block = 30 · 1.6539 = 49.617
    # rest        = 50 · 6.0219 = 301.095
    # redevance·0.5 = 0.5 · 147.24 = 73.62
    # ex-VAT = 49.617 + 301.095 + 73.62 = 424.332; ×1.06 = 449.79
    assert compute_ytd_cost(_swde_2026(), 80, 1, 0.5) == 449.79


def test_ytd_flanders_full_consumption_pro_rated_vastrecht() -> None:
    # Pidpa, 80 m³, 1 person, mid-year:
    # volumetric (same as annual) = 491.90 ex-VAT
    # vastrecht = max(0, 100 - 1·20) · 0.5 = 40
    # ex-VAT = 491.90 + 40 = 531.90; ×1.06 = 563.81
    assert compute_ytd_cost(_pidpa_2026(), 80, 1, 0.5) == 563.81


def test_ytd_year_elapsed_fraction_clamps_to_unit_interval() -> None:
    # Negative or >1 fractions are silently clamped so a clock-skew bug
    # in the coordinator can't produce a nonsense bill.
    low = compute_ytd_cost(_vivaqua_2026(), 0, 1, -0.5)
    high = compute_ytd_cost(_vivaqua_2026(), 0, 1, 1.5)
    assert low == 0.0
    assert high == compute_ytd_cost(_vivaqua_2026(), 0, 1, 1.0)


def test_ytd_returns_none_for_unwired_region() -> None:
    bogus = WaterTariff(
        utility="other",
        region="atlantis",
        valid_from=date(2026, 1, 1),
        valid_until=None,
        publication_label="x",
        source_url="https://example.invalid/",
        yearly_fixed_fee=50.0,
        basis_eur_per_m3=2.95,
    )
    assert compute_ytd_cost(bogus, 40, 1, 0.5) is None
