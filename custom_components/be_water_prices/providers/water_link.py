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

"""Water-link -- Antwerp city + ring (~200 k customers).

Source PDF (one per year):
    https://water-link.be/sites/default/files/<YYYY>-01/<YYYY>%20HH.pdf

The watertarieven HTML page exposes 22 unlabelled rate tables and is
brittle to parse for "the current year"; the PDF link surfaced through
the same page (in the "Publicaties" sidebar) is much cleaner. It carries
one canonical set of rates for the year, broken down per commune::

    Tarieven Water-Link
    Geldig vanaf 1 januari 2026
                                          Water  Afvoer  Zuivering  Totaal
    Vastrecht per wooneenheid             50,0   30,0    20,0       100,0
    Korting per gedomicilieerde inwoner   -10,0  -6,0    -4,0       -20,0
    ...
    Integrale waterprijs basistarief
        Antwerpen                         1.6692 1.3345  1.7019     4.7056
        Beveren-Kruibeke-Zwijndrecht      1.6692 1.9572  1.7019     5.3283
        Edegem                            1.6692 1.9572  1.7019     5.3283
        ...

Drinkwater (Water column) and zuivering (Zuivering column) are uniform
across the service area; only the gemeentelijke afvoer differs by
commune (Antwerpen sits lower at 1.3345 €/m³, the surrounding ring
communes at 1.9572 €/m³). The extractor stores **Antwerpen's rates**
as the default since Antwerpen is the city Water-link is named after
and accounts for the bulk of customers; users in the ring communes
(Beveren-Kruibeke-Zwijndrecht, Edegem, Hemiksem, Hove, Mortsel, Ranst,
Schoten, Zwijndrecht-Burcht) see a slight under-estimate on the
sanering rate. Per-commune precision lands when the OptionsFlow grows
a commune selector for Water-link entries.

Comforttarief is exactly 2× basis per VMM mandate; we cross-check.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp

from ..const import (
    DEFAULT_VAT_RATE,
    FLANDERS_KORTING_TOTAL_PER_PERSON,
    FLANDERS_VASTRECHT_TOTAL,
    REGION_FLANDERS,
)
from ._pdf import fetch_pdf_text_layout, to_float
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "water_link"
LABEL = "Water-link"
SOURCE_URL_FMT = "https://water-link.be/sites/default/files/{year}-01/{year}%20HH.pdf"

# Commune to anchor the rate row on. Antwerpen is the largest customer
# block; ring communes share the same drinkwater/zuivering numbers but
# carry a higher gemeentelijke afvoer rate (1.9572 vs Antwerpen's 1.3345
# in 2026).
_DEFAULT_COMMUNE = "Antwerpen"

# Anchored on "<commune>  N,NNNN  N,NNNN  N,NNNN  N,NNNN  N,NNNN" -- five
# columns: Water, Afvoer, Zuivering, Totaal-ex-BTW, Totaal-incl-BTW.
_RATE_ROW_RE_FMT = r"^{commune}\s+([\d]+,\d{{3,5}})\s+([\d]+,\d{{3,5}})\s+([\d]+,\d{{3,5}})"


def _parse_commune_row(
    text: str, commune: str, after_marker: str
) -> tuple[float, float, float] | None:
    """Find the first occurrence of ``after_marker`` then the next line
    that starts with ``commune`` followed by 3 EUR amounts. Returns
    ``(water, afvoer, zuivering)`` or ``None`` if not found.
    """
    cut = text.find(after_marker)
    if cut < 0:
        return None
    pattern = re.compile(_RATE_ROW_RE_FMT.format(commune=re.escape(commune)), re.MULTILINE)
    match = pattern.search(text, cut)
    if match is None:
        return None
    return (to_float(match.group(1)), to_float(match.group(2)), to_float(match.group(3)))


def parse_tariff(
    text: str, year: int | None = None, commune: str = _DEFAULT_COMMUNE
) -> WaterTariff:
    """Parse a captured Water-link huishoudelijk PDF."""
    target = year or date.today().year
    basis = _parse_commune_row(text, commune, "BASISTARIEF")
    comfort = _parse_commune_row(text, commune, "COMFORTTARIEF")
    if basis is None:
        raise ExtractorError(f"could not find Water-link basistarief row for {commune!r}")
    if comfort is None:
        raise ExtractorError(f"could not find Water-link comforttarief row for {commune!r}")

    water_basis, afvoer_basis, zuivering_basis = basis
    water_comfort = comfort[0]
    if abs(water_comfort - 2.0 * water_basis) > 0.01:
        raise ExtractorError(
            f"Water-link comforttarief {water_comfort} is not 2× basistarief {water_basis} (VMM 2× rule)"
        )

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(target, 1, 1),
        valid_until=date(target, 12, 31),
        publication_label=f"Water-link tarieven huishoudelijk {target} ({commune})",
        source_url=SOURCE_URL_FMT.format(year=target),
        yearly_fixed_fee=FLANDERS_VASTRECHT_TOTAL,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_TOTAL_PER_PERSON,
        basis_eur_per_m3=water_basis,
        comfort_eur_per_m3=water_comfort,
        sanering_gemeentelijk_eur_per_m3=afvoer_basis,
        sanering_bovengemeentelijk_eur_per_m3=zuivering_basis,
        vat_rate=DEFAULT_VAT_RATE,
    )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    target = date.today().year
    try:
        text = await fetch_pdf_text_layout(session, SOURCE_URL_FMT.format(year=target))
        return parse_tariff(text, year=target)
    except ExtractorError as err:
        _LOGGER.info("Water-link %d PDF unavailable (%s); trying %d", target, err, target - 1)
        text = await fetch_pdf_text_layout(session, SOURCE_URL_FMT.format(year=target - 1))
        return parse_tariff(text, year=target - 1)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
)
