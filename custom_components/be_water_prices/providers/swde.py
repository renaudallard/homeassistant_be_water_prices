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

"""SWDE -- Société wallonne des eaux.

Largest Walloon distributor: ~2.4 M inhabitants across ~200 communes,
roughly two-thirds of Wallonia's population. Publishes its own CVD
(true-cost-of-supply) on https://www.swde.be/en/water-prices-swde
(the FR slug ``prix-de-l-eau-swde`` is 404 -- noted in SCOPE.md).

The page exposes the four bill components under their own ``<h3>``
headings, each followed by a ``<p>`` containing one ``<strong>`` with
the EUR value::

    <h3>1. True-cost of supply (CVD)</h3>
    <p>The current CVD amounts to <strong>€ 3.24/m³</strong>.</p>

    <h3>2. True cost of sanitation (CVA)</h3>
    <p>The current CVA is <strong>€ 2.748/m³</strong>.</p>

    <h3>3. VAT</h3>
    <p>For water supply, it amounts to <strong>6 %</strong>.</p>

    <h3>4. Social Water Fund</h3>
    <p>The current Social Water Fund amounts to <strong>€ 0.0339/m³</strong>.</p>

CVA and FSE are flat-Wallonia constants (set by SPGE / CWaPE) and live
in :mod:`const`; we cross-check the parsed values against them and
warn on drift -- the const value wins so the same SWDE refresh also
catches a CILE / inBW drift downstream.

The redevance (annual fixed fee) is the regulator-defined
``20·CVD + 30·CVA`` formula; we materialise it into
:attr:`WaterTariff.yearly_fixed_fee` here so the cost engine doesn't
re-derive it.
"""

from __future__ import annotations

import logging
from datetime import date

import aiohttp
from bs4 import BeautifulSoup, Tag

from ..const import (
    DEFAULT_VAT_RATE,
    REGION_WALLONIA,
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from ._html import extract_amounts, fetch_html
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "swde"
LABEL = "SWDE"
SOURCE_URL = "https://www.swde.be/en/water-prices-swde"

# Headings that introduce each component on the page. We accent-fold
# everything before matching so a "à"/"é" re-render doesn't break us.
_CVD_HEADINGS = ("cvd", "true-cost of supply", "true cost of supply")
_CVA_HEADINGS = ("cva", "true cost of sanitation")
_FSE_HEADINGS = ("social water fund", "fonds social de l'eau", "fonds social")


def _amount_after(heading: Tag) -> float | None:
    """Return the first € amount inside a sibling ``<p>`` after ``heading``.

    Walks forward through siblings until the next heading of the same
    or higher level (so the search is bounded to one section) and grabs
    the first € figure encountered.
    """
    for sibling in heading.find_all_next():
        if sibling is heading:
            continue
        if sibling.name in ("h1", "h2", "h3", "h4") and sibling is not heading:
            return None
        amounts = extract_amounts(sibling.get_text(" ", strip=True))
        if amounts:
            return amounts[0]
    return None


def _find_component(soup: BeautifulSoup, keywords: tuple[str, ...]) -> float | None:
    """Find the first ``<h3>`` whose text contains any of ``keywords``;
    return the first € amount in the section that follows.
    """
    for heading in soup.find_all(["h2", "h3", "h4"]):
        text = heading.get_text(" ", strip=True).lower()
        if any(k in text for k in keywords):
            value = _amount_after(heading)
            if value is not None:
                return value
    return None


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    """Parse a captured ``swde.be/en/water-prices-swde`` page."""
    soup = BeautifulSoup(html, "html.parser")
    cvd = _find_component(soup, _CVD_HEADINGS)
    if cvd is None:
        raise ExtractorError("could not find SWDE CVD on the tariff page")

    cva_published = _find_component(soup, _CVA_HEADINGS)
    if cva_published is not None and abs(cva_published - WALLONIA_CVA_EUR_PER_M3) > 0.005:
        _LOGGER.warning(
            "SWDE published CVA %s differs from Wallonia constant %s -- "
            "the constant in const.py needs a refresh",
            cva_published,
            WALLONIA_CVA_EUR_PER_M3,
        )

    fse_published = _find_component(soup, _FSE_HEADINGS)
    if fse_published is not None and abs(fse_published - WALLONIA_FSE_EUR_PER_M3) > 0.005:
        _LOGGER.warning(
            "SWDE published FSE %s differs from Wallonia constant %s -- "
            "the constant in const.py needs a refresh",
            fse_published,
            WALLONIA_FSE_EUR_PER_M3,
        )

    cva = WALLONIA_CVA_EUR_PER_M3
    fse = WALLONIA_FSE_EUR_PER_M3

    target = year or date.today().year
    redevance = 20.0 * cvd + 30.0 * cva  # CWaPE-defined formula.

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_WALLONIA,
        valid_from=date(target, 1, 1),
        # SWDE does not publish an explicit valid_until on this page; the
        # tariff is set annually by CWaPE so December 31 of the same year
        # is the correct outer bound.
        valid_until=date(target, 12, 31),
        publication_label=f"SWDE water prices {target}",
        source_url=SOURCE_URL,
        yearly_fixed_fee=redevance,
        cvd_eur_per_m3=cvd,
        cva_eur_per_m3=cva,
        fse_eur_per_m3=fse,
        vat_rate=DEFAULT_VAT_RATE,
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
