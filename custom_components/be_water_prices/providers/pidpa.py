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

Two ingestion paths:

  1. **Default**: a multi-year "Tariefplan" PDF (covering 2025-2030)
     at ``/sites/default/files/2024-05/Tariefplan_2025-2030_simulatie_type_gezin.pdf``.
     Drinkwater rates per year + a saneringsbijdragen paragraph that
     was published in May 2024; subsequent rate revisions only land
     on the per-commune HTML pages, so the PDF leg drifts mid-cycle.

  2. **Per-commune** (when a commune is selected via the OptionsFlow):
     the per-commune page at ``/ons-aanbod/je-gemeente/<slug>``
     carries one ``<table>`` per year (2018-2026) inside a tabbed
     widget. Pidpa serves uniform rates province-wide today, but the
     numbers there are the *current* published values rather than the
     2024-frozen PDF projection. Picking a commune is therefore a
     way to opt into the up-to-date numbers.

PDF rows::

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

Per-commune HTML rows (one 5x5 table per year, all years inlined and
toggled via CSS)::

    | (header)  | Drinkwater | Gem. afvoer  | Bov. zuivering | Integrale |
    | Vastrecht | 50 ex/53 in| 30 / 31,8    | 20 / 21,2      | 100 / 106 |
    | Korting   | 10 / 10,6  | 6  / 6,36    | 4  / 4,24      | 20  / 21,2|
    | Basis /m3 | N,NNNN ex  | N,NNNN ex    | N,NNNN ex      | sum       |
    | Comfort/m3| 2N,NNN     | 2N,NNN       | 2N,NNN         | sum       |

The year tab is identified by ``id="tabid-<hash>-tab-<year>"``; the
huishoudelijk outer tab by ``id="tabid-<hash>-tab-0"``. We pick the
``Basistarief`` row's ex-VAT amount in the Drinkwater / Afvoer /
Zuivering columns.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup, Tag

from ..const import (
    DEFAULT_VAT_RATE,
    FLANDERS_KORTING_TOTAL_PER_PERSON,
    FLANDERS_VASTRECHT_TOTAL,
    REGION_FLANDERS,
)
from ._html import fetch_html
from ._pdf import fetch_pdf_text_layout, to_float
from .base import CommuneOption, ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "pidpa"
LABEL = "Pidpa"
SOURCE_URL = (
    "https://www.pidpa.be/sites/default/files/2024-05/Tariefplan_2025-2030_simulatie_type_gezin.pdf"
)
COMMUNE_URL_FMT = "https://www.pidpa.be/ons-aanbod/je-gemeente/{slug}"
SITEMAP_URL = "https://www.pidpa.be/sitemap.xml"

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


# --- per-commune ingestion ---------------------------------------------------

# Cell text on the per-commune table looks like
# "2,1888 euro excl. btw - 2,3201 euro incl. btw" with optional whitespace
# (incl. NBSP) between the number and the unit; we anchor on the ex-VAT half.
_EX_VAT_AMOUNT_RE = re.compile(r"(\d+,\d+)\s*euro\s*excl", re.IGNORECASE)
# Pidpa publishes its commune slugs in the public sitemap.xml.
_COMMUNE_SLUG_RE = re.compile(r"https://www\.pidpa\.be/ons-aanbod/je-gemeente/([a-z][a-z0-9-]*)<")


def _ex_vat_amount(text: str) -> float:
    match = _EX_VAT_AMOUNT_RE.search(text)
    if match is None:
        raise ExtractorError(f"could not parse ex-VAT amount from {text!r}")
    return to_float(match.group(1))


def _is_huishoudelijk_year_tab(div: Tag, *, year: int) -> bool:
    """Return True when ``div`` is the Huishoudelijke ``year`` tariff tab.

    The page nests two layers of ``tariff-tab-content`` divs:
        outer  id="tabid-<h>-tab-0"   (Huishoudelijk)
        inner  id="tabid-<h>-tab-{year}"
    We accept ``div`` as the inner tab and walk up to confirm the
    outer is the huishoudelijk tab.
    """
    if not str(div.get("id") or "").endswith(f"-tab-{year}"):
        return False
    cur: Tag | None = div
    for _ in range(5):
        cur = cur.parent if cur is not None else None
        if cur is None or cur.name is None:
            break
        classes = list(cur.get("class") or [])
        if "tariff-tab-content" in classes and str(cur.get("id") or "").endswith("-tab-0"):
            return True
    return False


def _find_year_table(soup: BeautifulSoup, *, year: int) -> Tag | None:
    for div in soup.find_all("div", class_="tariff-tab-content"):
        if not _is_huishoudelijk_year_tab(div, year=year):
            continue
        for table in div.find_all("table"):
            if "Integrale waterprijs" in table.get_text(" ", strip=True):
                return table
    return None


def parse_commune_tariff(html: str, *, commune_slug: str, year: int | None = None) -> WaterTariff:
    """Parse a captured Pidpa per-commune page for ``year``."""
    target = year or date.today().year
    soup = BeautifulSoup(html, "html.parser")
    table = _find_year_table(soup, year=target)
    if table is None:
        raise ExtractorError(
            f"could not locate Pidpa huishoudelijk {target} table for commune {commune_slug!r}"
        )

    rows = table.find_all("tr")
    # Layout (5 rows): header / vastrecht / korting / basistarief / comforttarief.
    if len(rows) < 5:
        raise ExtractorError(
            f"Pidpa per-commune table for {commune_slug!r} has {len(rows)} rows; expected 5"
        )
    basis_cells = rows[3].find_all(["th", "td"])
    comfort_cells = rows[4].find_all(["th", "td"])
    # 5 columns: label / drinkwater / afvoer / zuivering / integrale.
    if len(basis_cells) < 4 or len(comfort_cells) < 4:
        raise ExtractorError(f"Pidpa per-commune table columns malformed for {commune_slug!r}")

    drink_basis = _ex_vat_amount(basis_cells[1].get_text(" ", strip=True))
    afvoer_basis = _ex_vat_amount(basis_cells[2].get_text(" ", strip=True))
    zuivering_basis = _ex_vat_amount(basis_cells[3].get_text(" ", strip=True))
    drink_comfort = _ex_vat_amount(comfort_cells[1].get_text(" ", strip=True))
    if abs(drink_comfort - 2.0 * drink_basis) > 0.01:
        raise ExtractorError(
            f"Pidpa drinkwater comforttarief {drink_comfort} is not 2× basistarief"
            f" {drink_basis} for {commune_slug!r} {target} (VMM 2× rule)"
        )

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(target, 1, 1),
        valid_until=date(target, 12, 31),
        publication_label=f"Pidpa per-commune tarieven {target} ({commune_slug})",
        source_url=COMMUNE_URL_FMT.format(slug=commune_slug),
        yearly_fixed_fee=FLANDERS_VASTRECHT_TOTAL,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_TOTAL_PER_PERSON,
        basis_eur_per_m3=drink_basis,
        comfort_eur_per_m3=drink_comfort,
        sanering_gemeentelijk_eur_per_m3=afvoer_basis,
        sanering_bovengemeentelijk_eur_per_m3=zuivering_basis,
        vat_rate=DEFAULT_VAT_RATE,
    )


async def fetch_for_commune(session: aiohttp.ClientSession, commune: str) -> WaterTariff:
    html = await fetch_html(session, COMMUNE_URL_FMT.format(slug=commune))
    return parse_commune_tariff(html, commune_slug=commune)


def _slug_to_label(slug: str) -> str:
    """Title-case each hyphenated token; the user picks from the dropdown so
    cosmetic Dutch capitalisation glitches (Op / Den / De / Sint) don't matter.
    """
    return "-".join(token.capitalize() for token in slug.split("-"))


async def list_communes(session: aiohttp.ClientSession) -> tuple[CommuneOption, ...]:
    """Discover the Pidpa-served communes from the public sitemap."""
    sitemap = await fetch_html(session, SITEMAP_URL)
    slugs = sorted(set(_COMMUNE_SLUG_RE.findall(sitemap)))
    if not slugs:
        raise ExtractorError("Pidpa sitemap returned no commune slugs")
    return tuple(CommuneOption(id=s, label=_slug_to_label(s)) for s in slugs)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
    fetch_for_commune=fetch_for_commune,
    list_communes=list_communes,
)
