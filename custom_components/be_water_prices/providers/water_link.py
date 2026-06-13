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
commune (Antwerpen sits lower at 1.3345 €/m³, the four ring communes
at 1.9572 €/m³). The PDF carries one BASISTARIEF row per commune --
five in 2026: Antwerpen, Beveren-Kruibeke-Zwijndrecht, Edegem, Hove,
Mortsel. The extractor stores **Antwerpen's rates** as the default
since it is the city Water-link is named after and accounts for the
bulk of customers; ring-commune users pick their commune via the
OptionsFlow to get the right sanering rate.

Comforttarief is exactly 2× basis per VMM mandate; we cross-check.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp

from ..const import REGION_FLANDERS
from ._flanders import build_flanders_tariff
from ._pdf import fetch_pdf_text_layout, to_float
from .base import CommuneOption, ExtractorError, TransientFetchError, WaterExtractor, WaterTariff

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

    return build_flanders_tariff(
        utility_id=UTILITY_ID,
        year=target,
        publication_label=f"Water-link tarieven huishoudelijk {target} ({commune})",
        source_url=SOURCE_URL_FMT.format(year=target),
        basis=water_basis,
        comfort=water_comfort,
        sanering_gemeentelijk=afvoer_basis,
        sanering_bovengemeentelijk=zuivering_basis,
    )


async def _fetch_pdf_text(session: aiohttp.ClientSession) -> tuple[str, int]:
    """Fetch this year's PDF, falling back to last year. Returns (text, year)."""
    target = date.today().year
    try:
        return await fetch_pdf_text_layout(session, SOURCE_URL_FMT.format(year=target)), target
    except ExtractorError as err:
        if isinstance(err, TransientFetchError):
            # A transient blip (5xx / 429 / timeout) on this year's URL must
            # propagate so live_check classifies it as TRANSIENT instead of
            # being masked by silently serving last year's prices.
            raise
        _LOGGER.info("Water-link %d PDF unavailable (%s); trying %d", target, err, target - 1)
        text = await fetch_pdf_text_layout(session, SOURCE_URL_FMT.format(year=target - 1))
        return text, target - 1


def _maybe_extend_valid_until(tariff: WaterTariff, parsed_year: int) -> WaterTariff:
    """Push valid_until forward to March 31 of the *current* year when
    we fell back to last year's PDF, so the snapshot_stale Repair does
    not fire on Jan 1 just because Water-link is a few weeks late with
    the new card.
    """
    from dataclasses import replace

    current = date.today().year
    if parsed_year >= current:
        return tariff
    return replace(tariff, valid_until=date(current, 3, 31))


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    text, year = await _fetch_pdf_text(session)
    return _maybe_extend_valid_until(parse_tariff(text, year=year), year)


async def fetch_for_commune(session: aiohttp.ClientSession, commune: str) -> WaterTariff:
    text, year = await _fetch_pdf_text(session)
    return _maybe_extend_valid_until(parse_tariff(text, year=year, commune=commune), year)


# Anchored on the start-of-line: each commune row in the PDF starts at
# column 0 followed by 5 EUR amounts (Water, Afvoer, Zuivering, Total ex,
# Total incl). We extract the commune name token (everything before the
# first run of digits-comma) and use it as both the id and the label.
_COMMUNE_LINE_RE = re.compile(
    r"^([A-Z][A-Za-zÀ-ÿ\- ]+?)\s+\d+,\d{3,5}\s+\d+,\d{3,5}\s+\d+,\d{3,5}\s+\d+,\d{3,5}\s+\d+,\d{3,5}\s*$",
    re.MULTILINE,
)


async def list_communes(session: aiohttp.ClientSession) -> tuple[CommuneOption, ...]:
    """Discover the communes Water-link serves from the BASISTARIEF block."""
    text, _ = await _fetch_pdf_text(session)
    cut = text.find("BASISTARIEF")
    end = text.find("COMFORTTARIEF", cut) if cut >= 0 else -1
    if cut < 0 or end < 0:
        raise ExtractorError("could not isolate the Water-link BASISTARIEF block")
    block = text[cut:end]
    communes: list[CommuneOption] = []
    seen: set[str] = set()
    for match in _COMMUNE_LINE_RE.finditer(block):
        name = match.group(1).strip()
        if name in seen or name.lower() in {"basistarief", "comforttarief"}:
            continue
        seen.add(name)
        communes.append(CommuneOption(id=name, label=name))
    if not communes:
        raise ExtractorError("Water-link BASISTARIEF block had no commune rows")
    return tuple(communes)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
    fetch_for_commune=fetch_for_commune,
    list_communes=list_communes,
)
