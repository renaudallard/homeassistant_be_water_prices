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

"""INASEP -- Intercommunale Namuroise de Services Publics.

Wallonia (Namur sud), 10 communes, ~38 600 abonnés (~80-100 k people).
Source: https://www.inasep.be/votre-eau-au-cout-verite

The page exposes the components in plain prose, e.g.::

    Le CVD varie en fonction du distributeur d'eau. A l'INASEP, il
    est de 2,9952 €/m3
    janvier 2026, le CVA est de 2,748 €/m3 ...
    chaque consommateur wallon paye une petite contribution 0,0339 €/m3

CVA and FSE are the SPGE flat-Wallonia constants and are
cross-checked. The parser anchors the CVD on the literal phrase
``A l'INASEP, il est de`` so an unrelated euros amount elsewhere on
the page can't win.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import (
    DEFAULT_VAT_RATE,
    REGION_WALLONIA,
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from ._html import fetch_html
from ._pdf import to_float
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "inasep"
LABEL = "INASEP"
SOURCE_URL = "https://www.inasep.be/votre-eau-au-cout-verite"

# INASEP renders ``A l'INASEP, il est de 2,9952 €/m³`` -- but the
# right-single-quote is U+2019 (’) and the ``³`` superscript becomes a
# plain ``3`` after one of the bs4 normalisation passes. Tolerate both
# the ASCII and the Unicode quote, plus space-then-3 instead of m³.
_CVD_RE = re.compile(
    r"A\s+l['’’]INASEP[^0-9]*?([\d]+,\d{3,5})\s*€",
    re.IGNORECASE | re.DOTALL,
)


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    """Parse a captured INASEP page."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    text = soup.get_text(" ", strip=True)
    match = _CVD_RE.search(text)
    if match is None:
        raise ExtractorError("could not find INASEP CVD on the tariff page")
    cvd = to_float(match.group(1))

    target = year or date.today().year
    cva = WALLONIA_CVA_EUR_PER_M3
    fse = WALLONIA_FSE_EUR_PER_M3
    redevance = 20.0 * cvd + 30.0 * cva

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_WALLONIA,
        valid_from=date(target, 1, 1),
        valid_until=date(target, 12, 31),
        publication_label=f"INASEP votre eau au coût-vérité {target}",
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
