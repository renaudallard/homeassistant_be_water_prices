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
    *,
    social_tariff: bool = False,
) -> float | None:
    """Return the projected VAT-incl annual cost in EUR.

    Returns ``None`` when the tariff is shaped for a region whose math
    is not yet wired in.

    **Brussels** (linear): ``consumption × (linear + sanering) + redevance``,
    then VAT. ``persons`` unused.

    **Wallonia** (tier): the first 30 m³ pay ``0.5·CVD + 0·CVA + FSE``
    per m³ (CVA is exempt on the residential first block by CWaPE
    rule), above 30 m³ pays the full ``CVD + CVA + FSE``, plus the
    redevance that the extractor already materialised from
    ``20·CVD + 30·CVA`` into :attr:`WaterTariff.yearly_fixed_fee`.
    Verified against inBW's own published facture (100 m³ → 584.26 EUR
    matches to the cent only with the CVA-exempt-on-block-1 rule).
    ``persons`` unused.

    **Flanders** (block): basis volume = ``30 + 30·persons`` m³ (capped at
    persons=5 by the config flow). Inside the basis volume each m³ pays
    ``basis + sanering_boven + sanering_gemeente``; above it each m³
    pays the comforttarief which is exactly ``2 ×`` each of those
    components -- this 2× rule is mandated by VMM and applies uniformly,
    so the calc engine doubles the sanering values rather than the
    extractors carrying duplicate fields. The annual vastrecht is
    ``yearly_fixed_fee − persons × yearly_fixed_fee_per_resident_discount``,
    floored at zero. ``social_tariff=True`` applies the VMM 80 %
    reduction on the post-calc total.
    """
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
        first_block = min(consumption_m3, 30.0) * (0.5 * cvd + fse)
        rest = max(0.0, consumption_m3 - 30.0) * (cvd + cva + fse)
        ex_vat = first_block + rest + tariff.yearly_fixed_fee
        return round(ex_vat * (1.0 + tariff.vat_rate), 2)

    if tariff.region == "flanders":
        if tariff.basis_eur_per_m3 is None:
            return None
        comfort = tariff.comfort_eur_per_m3 or (2.0 * tariff.basis_eur_per_m3)
        san = tariff.sanering_bovengemeentelijk_eur_per_m3 + tariff.sanering_gemeentelijk_eur_per_m3
        basis_volume = 30.0 + 30.0 * persons
        consumed_basis = min(consumption_m3, basis_volume)
        consumed_comfort = max(0.0, consumption_m3 - basis_volume)
        per_m3_basis = tariff.basis_eur_per_m3 + san
        per_m3_comfort = comfort + 2.0 * san
        volumetric = consumed_basis * per_m3_basis + consumed_comfort * per_m3_comfort
        vastrecht = max(
            0.0,
            tariff.yearly_fixed_fee - persons * tariff.yearly_fixed_fee_per_resident_discount,
        )
        ex_vat = volumetric + vastrecht
        total = ex_vat * (1.0 + tariff.vat_rate)
        if social_tariff:
            total *= 0.20  # VMM social tariff = 80% reduction on the post-calc total.
        return round(total, 2)

    return None
