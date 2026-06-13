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
Source: https://www.inasep.be/prix-de-leau-et-evolution

The page lists the three components under a ``Tarifs YYYY`` heading,
e.g.::

    Tarifs 2026
    Coût-Vérité Distribution (CVD) = 3,6734 €/m³ depuis le 27 avril 2026
    Coût-Vérité Assainissement (CVA) = 2,748 €/m³ depuis le 1er janvier 2026
    Fonds social de l'Eau = 0,0339 €/m³ depuis le 1er janvier 2026

CVA and FSE are the SPGE flat-Wallonia constants and are
cross-checked. The parser anchors the CVD on the literal phrase
``Coût-Vérité Distribution (CVD)`` so the unrelated euros amounts
elsewhere on the page (annual-impact figures, per-glass examples)
can't win.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import REGION_WALLONIA
from ._html import fetch_and_parse
from ._pdf import to_float
from ._walloon_simple import build_tariff
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "inasep"
LABEL = "INASEP"
SOURCE_URL = "https://www.inasep.be/prix-de-leau-et-evolution"

# The page renders ``Coût-Vérité Distribution (CVD) = 3,6734 €/m³``,
# but bs4's text extraction drops the accents on some passes and the
# superscript ``³`` becomes a plain ``3``. Tolerate accent-stripped
# spellings of "Coût" and "Vérité".
_CVD_RE = re.compile(
    r"Co[ûu]t.{0,3}V[ée]rit[ée]\s+Distribution\s*\(CVD\)\s*=?\s*([\d]+,\d{3,5})\s*€",
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
    return build_tariff(
        utility_id=UTILITY_ID,
        cvd=cvd,
        source_url=SOURCE_URL,
        publication_label=f"INASEP votre eau au coût-vérité {target}",
        year=target,
    )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    return await fetch_and_parse(session, SOURCE_URL, parse_tariff)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_WALLONIA,
    fetch=fetch,
)
