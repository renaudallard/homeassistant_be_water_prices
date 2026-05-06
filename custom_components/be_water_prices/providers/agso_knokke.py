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

"""AGSO Knokke-Heist -- water utility for the single Knokke-Heist commune (~33 k pop).

Source: https://www.agsoknokke-heist.be/waterbedrijf/tarieven/tarieven-kleinverbruikers

The page exposes two tables side-by-side, one for the previous year
and one for the current year; the next-year section is introduced by a
``Wijziging per 1 januari <YYYY>`` heading. Each table follows the
canonical "Integrale waterprijs" layout::

    Tariefschijf       | Basis (huishoudelijk) | comfort | tot 1.000m³ | >1.000m³ | Vast recht | Korting/dom
    Drinkwater         | € 2,3295              | € 4,6590| € 2,7073    | € 2,1658 | € 50,00    | -€ 10,00
    Afvoer afvalwater  | € 1,9572              | € 3,9144| € 2,2173    | € 2,2173 | € 30,00    | -€ 6,00
    Zuivering afvalwater| € 1,7019             | € 3,4038| € 1,9281    | € 1,9281 | € 20,00    | -€ 4,00
    Integrale prijs ex-BTW | €5,9886           | ...

Picks the table whose "Integrale waterprijs" total is the highest --
operators only ever index up year-on-year, so that's a robust proxy
for "the latest year present" without relying on a fragile heading
match. Falls back to the only table when just one is published.

Stores all three components separately (drinkwater + afvoer +
zuivering) the way Pidpa does, so the ``water_basis_rate`` /
``water_sanering_rate`` sensors decompose cleanly. Uses the standard
VMM vastrecht (50/30/20 + 10/6/4) materialised from
:mod:`const`.
"""

from __future__ import annotations

import logging
from datetime import date

import aiohttp
from bs4 import BeautifulSoup, Tag

from ..const import REGION_FLANDERS
from ._flanders import build_flanders_tariff
from ._html import extract_amounts, fetch_html
from .base import ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "agso_knokke"
LABEL = "AGSO Knokke-Heist"
SOURCE_URL = "https://www.agsoknokke-heist.be/waterbedrijf/tarieven/tarieven-kleinverbruikers"


def _row_first_amount(table: Tag, row_label: str) -> float | None:
    """Return the basis (column index 1) amount for the row whose first
    cell contains ``row_label`` (case-insensitive substring match).
    """
    needle = row_label.lower()
    for tr in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in tr.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        if needle in cells[0].lower():
            amounts = extract_amounts(cells[1])
            if amounts:
                return amounts[0]
    return None


def _table_integrale_basis(table: Tag) -> float | None:
    """Return the integrale basis ex-BTW total used to rank tables."""
    return _row_first_amount(table, "integrale")


def _parse_one(table: Tag, year: int) -> WaterTariff | None:
    drinkwater = _row_first_amount(table, "drinkwater")
    afvoer = _row_first_amount(table, "afvoer")
    zuivering = _row_first_amount(table, "zuivering")
    if drinkwater is None or afvoer is None or zuivering is None:
        return None
    # Comforttarief = 2× basis per VMM. Verify drinkwater specifically;
    # the other two are computed by the cost engine.
    return build_flanders_tariff(
        utility_id=UTILITY_ID,
        year=year,
        publication_label=f"AGSO Knokke-Heist tarieven {year}",
        source_url=SOURCE_URL,
        basis=drinkwater,
        comfort=2.0 * drinkwater,
        sanering_gemeentelijk=afvoer,
        sanering_bovengemeentelijk=zuivering,
    )


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    """Parse a captured AGSO Knokke-Heist tarieven page."""
    soup = BeautifulSoup(html, "html.parser")
    tables = soup.find_all("table")
    if not tables:
        raise ExtractorError("could not locate AGSO Knokke tariff tables")

    # Pick the table with the highest integrale basis -- the page typically
    # shows previous + current year, and tariffs only index up year-on-year.
    ranked: list[tuple[float, Tag]] = []
    for table in tables:
        score = _table_integrale_basis(table)
        if score is not None:
            ranked.append((score, table))
    if not ranked:
        raise ExtractorError("none of the AGSO Knokke tables carry an Integrale waterprijs row")
    ranked.sort(key=lambda x: x[0], reverse=True)
    chosen = ranked[0][1]

    target = year or date.today().year
    parsed = _parse_one(chosen, target)
    if parsed is None:
        raise ExtractorError(
            "AGSO Knokke chosen table missing drinkwater / afvoer / zuivering rows"
        )
    return parsed


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    html = await fetch_html(session, SOURCE_URL)
    return parse_tariff(html)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
)
