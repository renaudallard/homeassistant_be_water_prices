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

"""Aquaduin (ex-IWVA) -- 6 communes Westkust (~80 k year-round residents).

Source: https://www.aquaduin.be/drinkwater/tarieven/overzicht-tarieven-<year>.pdf

Aquaduin publishes a clean numeric tariff card PDF -- the gold-standard
example flagged in SCOPE.md. The PDF lays out the residential block as::

    Basistarief 30 m³ + 30 m³ per gedomicilieerde persoon  5,9908 euro/m³  6,35
    Comforttarief > Basisverbruik (pro rata verrekend)     11,9816 euro/m³ 12

Plus the standard VMM vastrecht / korting (50/30/20 + 10/6/4 -- the
``20 %`` line in the PDF is the per-resident korting expressed as a
percentage of the matching vastrecht component).

The 5.9908 €/m³ is the **integrale waterprijs basistarief** (drinkwater
+ saneringsbijdragen combined into one per-m³ figure -- highest in
Flanders per SCOPE.md). The PDF does not split it into separate
drinkwater / sanering rows the way Pidpa or AGSO Knokke do. We store
it as ``basis_eur_per_m3`` with sanering = 0, which keeps the
volumetric math correct (per_m3_basis = basis + sanering still equals
the published 5.9908). The trade-off: the ``water_basis_rate`` sensor
shows the integrated rate rather than the drinkwater-only rate, which
is unavoidable without per-component publication.
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

UTILITY_ID = "aquaduin"
LABEL = "Aquaduin"
SOURCE_URL_FMT = "https://www.aquaduin.be/drinkwater/tarieven/overzicht-tarieven-{year}.pdf"

# Anchored on the PDF's exact wording -- "Basistarief 30 m³ + 30 m³ per
# gedomicilieerde persoon  N,NNNN euro/m³". The intervening "+ 30 m³ per
# gedomicilieerde persoon" string carries digits, so the gap regex uses
# ``.*?`` not ``[^0-9]*?``. Numeric format is 4-decimal Belgian (comma).
_BASIS_RE = re.compile(
    r"basistarief\s+30\s*m³.*?([\d]+,\d{3,5})\s*euro/m³",
    re.IGNORECASE | re.DOTALL,
)
_COMFORT_RE = re.compile(
    r"comforttarief.*?([\d]+,\d{3,5})\s*euro/m³",
    re.IGNORECASE | re.DOTALL,
)


def parse_tariff(text: str, year: int | None = None) -> WaterTariff:
    """Parse a captured Aquaduin tariff PDF (extracted via pdfplumber)."""
    target = year or date.today().year
    basis_m = _BASIS_RE.search(text)
    comfort_m = _COMFORT_RE.search(text)
    if basis_m is None:
        raise ExtractorError("could not locate Aquaduin basistarief in tariff PDF")
    basis = to_float(basis_m.group(1))
    comfort = to_float(comfort_m.group(1)) if comfort_m else 2.0 * basis
    if abs(comfort - 2.0 * basis) > 0.01:
        raise ExtractorError(
            f"Aquaduin comforttarief {comfort} is not 2× basistarief {basis} (VMM 2× rule)"
        )

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(target, 1, 1),
        valid_until=date(target, 12, 31),
        publication_label=f"Aquaduin overzicht tarieven {target}",
        source_url=SOURCE_URL_FMT.format(year=target),
        yearly_fixed_fee=FLANDERS_VASTRECHT_TOTAL,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_TOTAL_PER_PERSON,
        basis_eur_per_m3=basis,  # integrated drinkwater + sanering, see module docstring
        comfort_eur_per_m3=comfort,
        vat_rate=DEFAULT_VAT_RATE,
    )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    target = date.today().year
    try:
        text = await fetch_pdf_text_layout(session, SOURCE_URL_FMT.format(year=target))
        return parse_tariff(text, year=target)
    except ExtractorError as err:
        _LOGGER.info("Aquaduin %d PDF unavailable (%s); trying %d", target, err, target - 1)
        text = await fetch_pdf_text_layout(session, SOURCE_URL_FMT.format(year=target - 1))
        return parse_tariff(text, year=target - 1)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
)
