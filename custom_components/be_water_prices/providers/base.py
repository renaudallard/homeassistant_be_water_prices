"""Per-utility extractor protocol and shared dataclasses.

Each utility module under ``providers/`` exposes a top-level
``EXTRACTOR: WaterExtractor`` whose ``fetch`` callable returns one
:class:`WaterTariff`. The coordinator combines that with the user's
configured consumption / household size to produce the projected annual
cost. No EUR values live in Python source -- everything in
:class:`WaterTariff` comes from a live fetch of the utility's own
publication.

All EUR figures stored in :class:`WaterTariff` are **ex-VAT**. Belgian
water is residentially taxed at 6 %; ``vat_rate`` carries the rate so
the cost engine can re-apply it once at the end. Utilities that publish
VAT-incl numbers (VIVAQUA does) divide by ``1 + vat_rate`` at parse
time.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from typing import Protocol

import aiohttp


@dataclass(frozen=True, kw_only=True)
class WaterTariff:
    """One year's worth of tariff data for one utility."""

    utility: str
    region: str  # see const.REGIONS
    valid_from: date
    valid_until: date | None
    publication_label: str
    source_url: str

    yearly_fixed_fee: float  # EUR/year, ex-VAT
    # Flanders applies a per-resident discount on the vastrecht (-10 EUR per
    # gedomicilieerde, max 5 persons). Other regions leave at 0.
    yearly_fixed_fee_per_resident_discount: float = 0.0

    # Volumetric components (EUR/m³, ex-VAT). Shape differs by region:
    #   Flanders : basis (block 1) + comfort (block 2)
    #   Brussels : linear single rate (basis = comfort = None)
    #   Wallonia : linear single rate via cvd_eur_per_m3 + national CVA + FSE;
    #              tier math (50 % CVD on the first 30 m³) lives in pricing
    basis_eur_per_m3: float | None = None
    comfort_eur_per_m3: float | None = None
    linear_eur_per_m3: float | None = None

    # Sewerage / wastewater (EUR/m³, ex-VAT). Names follow the regional
    # vocabulary; only the ones relevant to the utility's region are non-zero.
    sanering_bovengemeentelijk_eur_per_m3: float = 0.0  # Flanders, to Aquafin
    sanering_gemeentelijk_eur_per_m3: float = 0.0  # Flanders, to commune
    cvd_eur_per_m3: float = 0.0  # Wallonia, per-distributor
    cva_eur_per_m3: float = 0.0  # Wallonia, national (SPGE constant)
    fse_eur_per_m3: float = 0.0  # Wallonia, national (Fonds Social de l'Eau)

    vat_rate: float = 0.06


WaterTariffFetcher = Callable[[aiohttp.ClientSession], Awaitable[WaterTariff]]


@dataclass(frozen=True, kw_only=True)
class WaterExtractor:
    """Registry entry for one utility."""

    id: str
    label: str
    region: str
    fetch: WaterTariffFetcher


class WaterExtractorProtocol(Protocol):
    """Each utility module must expose a top-level ``EXTRACTOR`` of this shape."""

    EXTRACTOR: WaterExtractor


class ExtractorError(Exception):
    """Raised when a utility's source cannot be fetched or parsed."""
