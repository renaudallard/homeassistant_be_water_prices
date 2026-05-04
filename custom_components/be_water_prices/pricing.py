"""Pure cost-projection helpers (no Home Assistant imports).

Kept separate from :mod:`coordinator` so the math is unit-testable from
a plain venv. The coordinator imports :func:`compute_annual_cost` and
applies it to the user's stored options.
"""

from __future__ import annotations

from .providers.base import WaterTariff


def compute_annual_cost(
    tariff: WaterTariff,
    consumption_m3: float,
    persons: int,
) -> float | None:
    """Return the projected VAT-incl annual cost in EUR.

    Returns ``None`` when the tariff is shaped for a region whose math
    is not yet wired in (v0.2 will add the Flanders branch).

    Brussels: linear single rate plus annual redevance. Persons unused.

    Wallonia: 7-tier residential structure simplified to the residential
    cap (≤ 30 m³). The first 30 m³ pay 50 % of CVD on top of CVA + FSE;
    consumption above 30 m³ pays the full CVD. The redevance carried by
    :attr:`WaterTariff.yearly_fixed_fee` is the regulator-defined
    ``20·CVD + 30·CVA`` formula already collapsed into one number by the
    extractor (so this branch does not re-derive it).
    """
    _ = persons  # Flanders block math (v0.2) reads this; other branches don't.

    if tariff.region == "brussels":
        if tariff.linear_eur_per_m3 is None:
            return None
        per_m3 = tariff.linear_eur_per_m3 + (
            tariff.sanering_bovengemeentelijk_eur_per_m3 + tariff.sanering_gemeentelijk_eur_per_m3
        )
        ex_vat = consumption_m3 * per_m3 + tariff.yearly_fixed_fee
        return round(ex_vat * (1.0 + tariff.vat_rate), 2)

    if tariff.region == "wallonia":
        cvd = tariff.cvd_eur_per_m3
        if cvd <= 0:
            return None
        cva = tariff.cva_eur_per_m3
        fse = tariff.fse_eur_per_m3
        first_block = min(consumption_m3, 30.0) * (0.5 * cvd + cva + fse)
        rest = max(0.0, consumption_m3 - 30.0) * (cvd + cva + fse)
        ex_vat = first_block + rest + tariff.yearly_fixed_fee
        return round(ex_vat * (1.0 + tariff.vat_rate), 2)

    return None
