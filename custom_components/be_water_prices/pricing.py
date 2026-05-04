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
    # ``persons`` is unused on the Brussels branch; the Flanders branch
    # added in v0.2 reads it for the basis-block volume (30 + 30·persons).
    _ = persons
    """Return the projected VAT-incl annual cost in EUR.

    v0.1 implements the Brussels (linear) branch. Flanders block math
    and Wallonia tier math arrive in v0.2 / v0.3. Returns ``None`` when
    the tariff is shaped for a region whose math is not yet wired in.
    """
    if tariff.region == "brussels":
        if tariff.linear_eur_per_m3 is None:
            return None
        per_m3 = tariff.linear_eur_per_m3 + (
            tariff.sanering_bovengemeentelijk_eur_per_m3 + tariff.sanering_gemeentelijk_eur_per_m3
        )
        ex_vat = consumption_m3 * per_m3 + tariff.yearly_fixed_fee
        return round(ex_vat * (1.0 + tariff.vat_rate), 2)
    return None
