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

"""De Watergroep -- 167 communes / ~3.3 M customers (~49.5 % of Flanders).

Two ingestion paths share the same cookie-driven endpoint:

  - **Default fetch** (no commune configured) hits the cookie-driven
    ``/Tarief/UpdateDetailTariefJaar/<year>`` endpoint with the Halle
    GUID and labels the snapshot ``"Halle (DWG-served default)"``.
    That gives the full integrale waterprijs (drinkwater +
    gemeentelijke + bovengemeentelijke saneringsbijdragen) for one
    representative DWG-served commune. If the AJAX endpoint is down
    we fall through to the news article
    ``over-de-watergroep/nieuws/tarieven-<year>`` which only carries
    the drinkwater leg (sanering = 0) so the integration keeps
    producing *some* tariff.

  - **Per-commune fetch** GETs the same endpoint with the user-picked
    ``dwg_l=<GUID>`` cookie and returns the integrale waterprijs for
    that commune, with the standard VMM 50/30/20 + 10/6/4
    vastrecht/korting structure.

Commune list discovery: scrape the dropdown on
``/nl-be/drinkwater/tarieven`` (700+ ``<option>`` entries with GUID
values).

Comforttarief is exactly ``2 ×`` the basistarief by VMM mandate; we
materialise it.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import (
    DEFAULT_VAT_RATE,
    FLANDERS_KORTING_DRINKWATER_PER_PERSON,
    FLANDERS_VASTRECHT_DRINKWATER,
    REGION_FLANDERS,
)
from ._flanders import build_flanders_tariff
from ._html import fetch_and_parse, fetch_html
from ._pdf import USER_AGENT, to_float
from .base import CommuneOption, ExtractorError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "de_watergroep"
LABEL = "De Watergroep"
NEWS_URL_FMT = "https://www.dewatergroep.be/nl-be/over-de-watergroep/nieuws/tarieven-{year}"
COMMUNE_LIST_URL = "https://www.dewatergroep.be/nl-be/drinkwater/tarieven"
COMMUNE_DETAIL_URL_FMT = "https://www.dewatergroep.be/Tarief/UpdateDetailTariefJaar/{year}"

# Default commune for the no-commune fallback. Halle (postcode 1500) is
# a representative DWG-served commune in Vlaams-Brabant; we use it
# because the cookie-driven endpoint returns the *full* integrale
# waterprijs (drinkwater + gemeentelijke + bovengemeentelijke) where
# the news-article path only has the drinkwater leg. Saneringsbijdragen
# in Flanders vary by commune, so this still under- or over-estimates
# slightly for users who don't pick their commune in OptionsFlow, but
# the average error is ~25 EUR/year vs. the news article's ~200 EUR.
_DEFAULT_COMMUNE_GUID = "{B16A143A-49E6-4CE5-A241-1AA09BFC406A}"
_DEFAULT_COMMUNE_LABEL = "Halle (DWG-served default)"

# News article wording: "2,9521 euro voor 1.000 liter".
_BASIS_NEWS_RE = re.compile(
    r"([\d]+,\s*\d{3,5})\s*euro\s+voor\s+1[.,]?000\s+liter",
    re.IGNORECASE,
)

# Rows inside the "Basistarief per m³" block. After
# _basis_per_m3_block() trims the surrounding sections out, these
# regexes match against just that block -- so a missing Basistarief
# Afvoer row cannot silently bleed into the Comforttarief Afvoer row
# (DOTALL + non-greedy used to walk past the empty basis label and
# match the comfort one, returning ~2x the correct rate).
_DRINKWATER_RE = re.compile(
    r"Waterverbruik\s+drinkwater\s*€\s*([\d]+,\d{3,5})",
    re.IGNORECASE,
)
_AFVOER_RE = re.compile(
    r"Afvoer\s+van\s+afvalwater\s*€\s*([\d]+,\d{3,5})",
    re.IGNORECASE,
)
_ZUIVERING_RE = re.compile(
    r"Zuivering\s+van\s+afvalwater\s*€\s*([\d]+,\d{3,5})",
    re.IGNORECASE,
)


def _basis_per_m3_block(text: str) -> str | None:
    """Return the slice of ``text`` belonging to the Basistarief per m³ table.

    Bounded on the right by the next section marker (Basistarief per
    liter, Comforttarief, ...) so the row regexes can't reach into the
    Comforttarief block and silently match its Afvoer / Zuivering rows
    when the Basistarief block has an empty row.

    If the anchor appears more than once on the page (e.g. DWG ever
    inlines an explainer / comparison heading above the live table),
    the first occurrence whose slice contains the data row
    "Waterverbruik drinkwater" wins. A pure-heading first hit no
    longer truncates the parser to navigation text.
    """
    pos = 0
    while True:
        start = text.find("Basistarief per m³", pos)
        if start < 0:
            return None
        next_markers = (
            text.find("Basistarief per liter", start + 1),
            text.find("Comforttarief", start + 1),
        )
        end = min((m for m in next_markers if m > 0), default=len(text))
        block = text[start:end]
        if "Waterverbruik drinkwater" in block:
            return block
        pos = start + 1


def parse_news_tariff(html: str, year: int) -> WaterTariff:
    """Parse the news-article fallback (drinkwater leg only).

    Used as a deeper fallback when the cookie-driven per-commune
    endpoint is unreachable; sanering stays at 0 because the news
    article does not carry per-commune sewerage rates.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    match = _BASIS_NEWS_RE.search(text)
    if match is None:
        raise ExtractorError(
            f"could not locate De Watergroep basistarief for {year} on the news article"
        )
    basis = to_float(match.group(1))
    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=f"De Watergroep tarieven {year} (drinkwater leg only)",
        source_url=NEWS_URL_FMT.format(year=year),
        yearly_fixed_fee=FLANDERS_VASTRECHT_DRINKWATER,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_DRINKWATER_PER_PERSON,
        basis_eur_per_m3=basis,
        comfort_eur_per_m3=2.0 * basis,  # VMM-mandated 2× rule
        vat_rate=DEFAULT_VAT_RATE,
    )


def parse_commune_tariff(
    html: str,
    *,
    year: int,
    commune_label: str,
) -> WaterTariff:
    """Parse the per-commune AJAX response (full integrale waterprijs).

    Some communes don't levy a gemeentelijke or bovengemeentelijke
    saneringsbijdrage (Sinaai is the canonical example: DWG renders the
    "Afvoer van afvalwater" / "Zuivering van afvalwater" labels with
    no euro amount). Treat a missing row as 0 instead of raising so
    these communes still produce a tariff. Drinkwater stays required:
    without it there's no tariff at all.
    """
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)
    block = _basis_per_m3_block(text)
    if block is None:
        raise ExtractorError("could not locate De Watergroep 'Basistarief per m³' section")

    drinkwater = _DRINKWATER_RE.search(block)
    if drinkwater is None:
        raise ExtractorError("could not parse De Watergroep per-commune drinkwater basistarief")
    basis = to_float(drinkwater.group(1))
    afvoer = _AFVOER_RE.search(block)
    zuivering = _ZUIVERING_RE.search(block)
    san_gem = to_float(afvoer.group(1)) if afvoer is not None else 0.0
    san_bov = to_float(zuivering.group(1)) if zuivering is not None else 0.0

    return build_flanders_tariff(
        utility_id=UTILITY_ID,
        year=year,
        publication_label=f"De Watergroep tarieven {year} ({commune_label})",
        source_url=COMMUNE_DETAIL_URL_FMT.format(year=year),
        basis=basis,
        comfort=2.0 * basis,  # VMM-mandated 2× rule
        sanering_gemeentelijk=san_gem,
        sanering_bovengemeentelijk=san_bov,
    )


# Backwards-compat alias for tests pinned to the old name.
parse_tariff = parse_news_tariff


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    """No-commune fallback fetch.

    Returns the full integrale waterprijs by hitting the cookie-driven
    per-commune endpoint with a known DWG-served default commune
    (Halle, postcode 1500). Falls back to the news-article ingestion
    (drinkwater leg only, sanering = 0) if the per-commune endpoint
    raises so the integration keeps producing *some* tariff rather
    than going completely dark.
    """
    target = date.today().year
    try:
        text, year = await _fetch_commune_ajax(session, _DEFAULT_COMMUNE_GUID)
        return await asyncio.to_thread(
            parse_commune_tariff, text, year=year, commune_label=_DEFAULT_COMMUNE_LABEL
        )
    except ExtractorError as default_err:
        _LOGGER.info(
            "De Watergroep default-commune fetch failed (%s); falling back to news article",
            default_err,
        )
        try:
            return await fetch_and_parse(
                session, NEWS_URL_FMT.format(year=target), parse_news_tariff, year=target
            )
        except ExtractorError as err:
            _LOGGER.info(
                "De Watergroep %d article unavailable (%s); trying %d", target, err, target - 1
            )
            return await fetch_and_parse(
                session, NEWS_URL_FMT.format(year=target - 1), parse_news_tariff, year=target - 1
            )


async def _fetch_commune_ajax(session: aiohttp.ClientSession, commune: str) -> tuple[str, int]:
    """GET the UpdateDetailTariefJaar AJAX response for ``commune``.

    Returns the response body and the year used in the URL. Raised
    errors are :class:`ExtractorError`; the caller decides what to
    label the parsed tariff with.
    """
    target = date.today().year
    url = COMMUNE_DETAIL_URL_FMT.format(year=target)
    try:
        async with session.get(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Cookie": f"dwg_l={commune}",
                "X-Requested-With": "XMLHttpRequest",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status >= 400:
                raise ExtractorError(f"HTTP {resp.status} fetching {url}")
            text = await resp.text()
    except aiohttp.ClientError as err:
        raise ExtractorError(f"network error fetching De Watergroep AJAX endpoint: {err}") from err
    if not text.strip() or "Basistarief" not in text:
        raise ExtractorError(
            f"De Watergroep returned an empty body for commune {commune!r} "
            "(probably an invalid GUID)"
        )
    return text, target


async def fetch_for_commune(session: aiohttp.ClientSession, commune: str) -> WaterTariff:
    """Per-commune fetch via the cookie-driven UpdateDetailTariefJaar endpoint."""
    text, year = await _fetch_commune_ajax(session, commune)
    return await asyncio.to_thread(parse_commune_tariff, text, year=year, commune_label=commune)


_OPTION_RE = re.compile(
    r'<option[^>]*value="(\{[0-9A-Fa-f-]+\})"[^>]*>\s*([^<]+?)\s*</option>',
    re.IGNORECASE | re.DOTALL,
)


async def list_communes(session: aiohttp.ClientSession) -> tuple[CommuneOption, ...]:
    """Discover the 700 De Watergroep communes by scraping the dropdown."""
    html = await fetch_html(session, COMMUNE_LIST_URL)
    communes: list[CommuneOption] = []
    seen: set[str] = set()
    for match in _OPTION_RE.finditer(html):
        guid = match.group(1)
        label = match.group(2).strip()
        if not label or guid in seen:
            continue
        seen.add(guid)
        communes.append(CommuneOption(id=guid, label=label))
    if not communes:
        raise ExtractorError("could not discover any De Watergroep communes from the dropdown")
    return tuple(communes)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
    fetch_for_commune=fetch_for_commune,
    list_communes=list_communes,
)
