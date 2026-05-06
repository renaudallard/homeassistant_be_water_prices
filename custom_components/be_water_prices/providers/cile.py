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

"""CILE -- Compagnie Intercommunale Liégeoise des Eaux.

Liège region, ~560 k inhabitants across 24 communes -- the largest
Walloon distributor after SWDE. Source:
https://www.cile.be/facturation/le-prix-de-leau

The page exposes a clean 4-row table::

    Poste            | Tarif au 1er janvier <YYYY>
    C.V.A            | 2,7480 €/m³
    C.V.D            | 3,5552 €/m³
    Fonds social     | 0,0339 €/m³
    TVA 6%           |

CVA / FSE come from the SPGE flat-Wallonia constants (and are
cross-checked here -- a divergence > 0.005 €/m³ between page and
constant logs a warning).

Stores tariff like SWDE / inBW: ``cvd_eur_per_m3`` for the
distributor's value, ``cva_eur_per_m3`` and ``fse_eur_per_m3`` for
the national ones, and ``yearly_fixed_fee = 20·CVD + 30·CVA``
materialised from the regulator's redevance formula. The Wallonia
branch of :func:`pricing.compute_annual_cost` handles the tier math.
"""

from __future__ import annotations

import logging
from datetime import date

import aiohttp
from bs4 import BeautifulSoup, Tag

from ..const import (
    REGION_WALLONIA,
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from ._html import extract_amounts, fetch_html
from ._walloon_simple import build_tariff
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "cile"
LABEL = "CILE"
SOURCE_URL = "https://www.cile.be/facturation/le-prix-de-leau"


def _row_amount(table: Tag, row_label: str) -> float | None:
    needle = row_label.lower()
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        if needle in cells[0].lower():
            amounts = extract_amounts(cells[-1])
            if amounts:
                return amounts[0]
    return None


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    """Parse a captured ``cile.be/facturation/le-prix-de-leau`` page."""
    soup = BeautifulSoup(html, "html.parser")
    table = soup.find("table")
    if not isinstance(table, Tag):
        raise ExtractorError("could not locate CILE tariff table")

    cvd = _row_amount(table, "c.v.d")
    if cvd is None:
        raise ExtractorError("could not find CILE CVD row")

    cva_published = _row_amount(table, "c.v.a")
    if cva_published is not None and abs(cva_published - WALLONIA_CVA_EUR_PER_M3) > 0.005:
        _LOGGER.warning(
            "CILE published CVA %s differs from Wallonia constant %s",
            cva_published,
            WALLONIA_CVA_EUR_PER_M3,
        )
    fse_published = _row_amount(table, "fonds social")
    if fse_published is not None and abs(fse_published - WALLONIA_FSE_EUR_PER_M3) > 0.001:
        _LOGGER.warning(
            "CILE published FSE %s differs from Wallonia constant %s",
            fse_published,
            WALLONIA_FSE_EUR_PER_M3,
        )

    target = year or date.today().year
    return build_tariff(
        utility_id=UTILITY_ID,
        cvd=cvd,
        source_url=SOURCE_URL,
        publication_label=f"CILE prix de l'eau {target}",
        year=target,
    )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    html = await fetch_html(session, SOURCE_URL)
    return parse_tariff(html)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_WALLONIA,
    fetch=fetch,
)
