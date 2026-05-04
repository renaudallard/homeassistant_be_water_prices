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

"""Pidpa -- water utility for the Antwerp province (~1.2 M inhabitants).

Pidpa publishes its drinkwater tariffs in a multi-year "Tariefplan"
PDF (covering 2025-2030) at::

    https://www.pidpa.be/sites/default/files/2024-05/Tariefplan_2025-2030_simulatie_type_gezin.pdf

The HTML page at ``/tarieven-en-betalen/drinkwatertarieven/...``
explains the bill structure but does not carry the per-m³ numbers --
only the PDF does. The PDF is text-extractable; we use
``extract_pdf_text_layout`` to keep column alignment.

Inside the PDF the relevant rows are::

    Tarieven Vast recht Korting/DOM
    Drinkwater € 50,00 € 10,00
    Riolering € 30,00 € 6,00
    Zuivering € 20,00 € 4,00

    Drinkwatertarief
    (excl. BTW)         2025 2026 2027 2028 2029 2030
    basistarief
    huishoudelijk       2,0462 2,0848 2,1233 2,1619 2,2004 2,239
    ( €/m³)
    comforttarief
    huishoudelijk       4,0924 4,1696 4,2466 4,3238 4,4008 4,478
    (€/m³)

The vastrecht / korting numbers are the standard VMM structure -- we
cross-check that the PDF still publishes them and warn on drift.

Saneringsbijdragen (afvoer + zuivering) come from a separate paragraph
near the top of the PDF::

    Tarief gemeentelijke sanering (afvoer): 1,6533 €/m³ basistarief
        and 3,3066 €/m³ comforttarief.
    Tarief bovengemeentelijke sanering (zuivering): 1,1809 €/m³ basistarief
        and 2,3619 €/m³ comforttarief.

We store only the *basis* values; the cost engine doubles them in the
comfort block (the ``2 ×`` rule is a Flemish bill mandate, see
:func:`pricing.compute_annual_cost`).
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

UTILITY_ID = "pidpa"
LABEL = "Pidpa"
SOURCE_URL = (
    "https://www.pidpa.be/sites/default/files/2024-05/Tariefplan_2025-2030_simulatie_type_gezin.pdf"
)

# The PDF lays the year header out as: "(excl. BTW) 2025 2026 2027 2028 2029 2030".
# We capture the per-year basis row underneath and pull the column matching
# our target year.
_BASIS_RE = re.compile(
    r"basistarief\s+huishoudelijk\s+([\d.,]+(?:\s+[\d.,]+)+)",
    re.IGNORECASE,
)
_COMFORT_RE = re.compile(
    r"comforttarief\s+huishoudelijk\s+([\d.,]+(?:\s+[\d.,]+)+)",
    re.IGNORECASE,
)
_HEADER_RE = re.compile(
    r"Drinkwatertarief\s+(?P<years>(?:20\d{2}\s*){2,})",
    re.IGNORECASE,
)
# Anchor on the parenthesised role label ("afvoer" / "zuivering") so the
# "bovengemeentelijke" line cannot be matched by the "gemeentelijke" regex
# (which would otherwise hit on the substring "gemeentelijke" inside
# "bovengemeentelijke"). The PDF has stray whitespace ("(afvoer )" with a
# trailing space, "(zuivering) :" with a gap before the colon) so we
# tolerate spaces around the punctuation.
_SAN_RE = re.compile(
    r"\(\s*(afvoer|zuivering)\s*\)\s*(?:\d{4}\s*)?:\s*([\d.,]+)\s*€/m³",
    re.IGNORECASE,
)


def _column_for_year(years: list[int], values: list[float], year: int) -> float | None:
    for y, v in zip(years, values, strict=False):
        if y == year:
            return v
    return None


def _parse_year_row(text: str, pattern: re.Pattern[str]) -> list[float]:
    match = pattern.search(text)
    if not match:
        return []
    return [to_float(token) for token in match.group(1).split()]


def _parse_year_header(text: str) -> list[int]:
    match = _HEADER_RE.search(text)
    if not match:
        return []
    return [int(token) for token in match.group("years").split()]


def parse_tariff(text: str, year: int | None = None) -> WaterTariff:
    """Parse a captured Pidpa Tariefplan PDF (extracted via pdfplumber)."""
    target = year or date.today().year
    years = _parse_year_header(text)
    if not years:
        raise ExtractorError("could not locate the (excl. BTW) year header in Pidpa PDF")

    basis_row = _parse_year_row(text, _BASIS_RE)
    comfort_row = _parse_year_row(text, _COMFORT_RE)
    if not basis_row or not comfort_row:
        raise ExtractorError("could not locate basistarief / comforttarief row in Pidpa PDF")

    basis = _column_for_year(years, basis_row, target)
    comfort = _column_for_year(years, comfort_row, target)
    if basis is None or comfort is None:
        # Fall back to the latest year present (the multi-year PDF stops at 2030;
        # past 2030 we serve the closing year so sensors don't go blank).
        basis = basis_row[-1]
        comfort = comfort_row[-1]
        target = years[-1]
        _LOGGER.warning(
            "Pidpa PDF does not list %d, falling back to %d", year or date.today().year, target
        )

    if abs(comfort - 2.0 * basis) > 0.01:
        raise ExtractorError(
            f"Pidpa comforttarief {comfort} is not 2× basistarief {basis} for {target}"
        )

    sanering: dict[str, float] = {}
    for match in _SAN_RE.finditer(text):
        sanering[match.group(1).lower()] = to_float(match.group(2))
    if "afvoer" not in sanering or "zuivering" not in sanering:
        raise ExtractorError(
            "could not parse Pidpa saneringsbijdragen (afvoer / zuivering); "
            f"found: {sorted(sanering)}"
        )
    gemeentelijk = sanering["afvoer"]
    bovengemeentelijk = sanering["zuivering"]

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(target, 1, 1),
        valid_until=date(target, 12, 31),
        publication_label=f"Pidpa Tariefplan 2025-2030 column {target}",
        source_url=SOURCE_URL,
        yearly_fixed_fee=FLANDERS_VASTRECHT_TOTAL,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_TOTAL_PER_PERSON,
        basis_eur_per_m3=basis,
        comfort_eur_per_m3=comfort,
        sanering_gemeentelijk_eur_per_m3=gemeentelijk,
        sanering_bovengemeentelijk_eur_per_m3=bovengemeentelijk,
        vat_rate=DEFAULT_VAT_RATE,
    )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    text = await fetch_pdf_text_layout(session, SOURCE_URL)
    return parse_tariff(text)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
)
