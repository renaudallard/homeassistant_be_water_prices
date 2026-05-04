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

Two ingestion paths:

  - **Default fetch** (no commune configured) hits the operator-wide
    news article ``over-de-watergroep/nieuws/tarieven-<year>``. That
    page only carries the drinkwater basistarief; sanering stays at 0
    so the projected-cost sensor under-states the real bill.

  - **Per-commune fetch** GETs ``/Tarief/UpdateDetailTariefJaar/<year>``
    with a ``dwg_l=<GUID>`` cookie. The response is the full per-commune
    integrale waterprijs page: drinkwater + gemeentelijke +
    bovengemeentelijke saneringsbijdragen with the standard VMM
    50/30/20 + 10/6/4 vastrecht/korting structure.

Commune list discovery: scrape the dropdown on
``/nl-be/drinkwater/tarieven`` (700+ ``<option>`` entries with GUID
values).

Comforttarief is exactly ``2 ×`` the basistarief by VMM mandate; we
materialise it.
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import (
    DEFAULT_VAT_RATE,
    FLANDERS_KORTING_DRINKWATER_PER_PERSON,
    FLANDERS_KORTING_TOTAL_PER_PERSON,
    FLANDERS_VASTRECHT_DRINKWATER,
    FLANDERS_VASTRECHT_TOTAL,
    REGION_FLANDERS,
)
from ._html import fetch_html
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

# Per-commune endpoint rows (after stripping HTML to whitespace text):
#   "Basistarief per m³ Waterverbruik drinkwater € 2,9251 (incl. ...)
#    Afvoer van afvalwater € 1,9572 (incl. ...) Zuivering van
#    afvalwater € 1,7019 (incl. ...)"
_BASIS_DRINKWATER_RE = re.compile(
    r"Basistarief\s+per\s+m³.*?Waterverbruik\s+drinkwater\s*€\s*([\d]+,\d{3,5})",
    re.IGNORECASE | re.DOTALL,
)
_BASIS_AFVOER_RE = re.compile(
    r"Basistarief\s+per\s+m³.*?Afvoer\s+van\s+afvalwater\s*€\s*([\d]+,\d{3,5})",
    re.IGNORECASE | re.DOTALL,
)
_BASIS_ZUIVERING_RE = re.compile(
    r"Basistarief\s+per\s+m³.*?Zuivering\s+van\s+afvalwater\s*€\s*([\d]+,\d{3,5})",
    re.IGNORECASE | re.DOTALL,
)


def parse_news_tariff(html: str, year: int) -> WaterTariff:
    """Parse the news-article fallback (drinkwater leg only).

    Used when no commune is configured; sanering stays at 0 because
    the news article does not carry per-commune sewerage rates.
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
    """Parse the per-commune AJAX response (full integrale waterprijs)."""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(" ", strip=True)

    drinkwater = _BASIS_DRINKWATER_RE.search(text)
    afvoer = _BASIS_AFVOER_RE.search(text)
    zuivering = _BASIS_ZUIVERING_RE.search(text)
    if drinkwater is None or afvoer is None or zuivering is None:
        raise ExtractorError(
            "could not parse De Watergroep per-commune basis rows "
            f"(drinkwater={drinkwater is not None}, "
            f"afvoer={afvoer is not None}, zuivering={zuivering is not None})"
        )
    basis = to_float(drinkwater.group(1))
    san_gem = to_float(afvoer.group(1))
    san_bov = to_float(zuivering.group(1))

    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=f"De Watergroep tarieven {year} ({commune_label})",
        source_url=COMMUNE_DETAIL_URL_FMT.format(year=year),
        yearly_fixed_fee=FLANDERS_VASTRECHT_TOTAL,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_TOTAL_PER_PERSON,
        basis_eur_per_m3=basis,
        comfort_eur_per_m3=2.0 * basis,  # VMM-mandated 2× rule
        sanering_gemeentelijk_eur_per_m3=san_gem,
        sanering_bovengemeentelijk_eur_per_m3=san_bov,
        vat_rate=DEFAULT_VAT_RATE,
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
        return parse_commune_tariff(text, year=year, commune_label=_DEFAULT_COMMUNE_LABEL)
    except ExtractorError as default_err:
        _LOGGER.info(
            "De Watergroep default-commune fetch failed (%s); falling back to news article",
            default_err,
        )
        try:
            html = await fetch_html(session, NEWS_URL_FMT.format(year=target))
            return parse_news_tariff(html, year=target)
        except ExtractorError as err:
            _LOGGER.info(
                "De Watergroep %d article unavailable (%s); trying %d", target, err, target - 1
            )
            html = await fetch_html(session, NEWS_URL_FMT.format(year=target - 1))
            return parse_news_tariff(html, year=target - 1)


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
    return parse_commune_tariff(text, year=year, commune_label=commune)


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
