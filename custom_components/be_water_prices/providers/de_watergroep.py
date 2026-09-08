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
from ._pdf import USER_AGENT, _http_error, _read_text_capped, error_text, to_float
from .base import (
    CommuneOption,
    ExtractorError,
    TransientFetchError,
    WaterExtractor,
    WaterTariff,
    carry_prior_year_card,
)

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

# News article wording: "2,9521 euro voor 1.000 liter". The integer part
# is bounded (a price never has more than a few leading digits) so a long
# run of digits without the trailing "euro voor 1.000 liter" cannot make
# the unbounded "+" backtrack quadratically over attacker-sized input.
_BASIS_NEWS_RE = re.compile(
    r"(\d{1,7},\s*\d{3,5})\s*euro\s+voor\s+1[.,]?000\s+liter",
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

# What De Watergroep prints in place of an amount it cannot render. It is
# a statement that the number is unavailable, not that the leg is free,
# and reading it as 0.00 EUR/m3 under-states a bill by the whole leg:
# 3660 Opglabbeek billed 575.26 EUR a year instead of 782.73 on
# 2026-09-08. The two cases have to be told apart, so the sentence is
# matched next to the label it replaced.
_UNAVAILABLE = "de kostprijs kan momenteel niet getoond worden"
_AFVOER_UNAVAILABLE_RE = re.compile(rf"Afvoer\s+van\s+afvalwater\s*{_UNAVAILABLE}", re.IGNORECASE)
_ZUIVERING_UNAVAILABLE_RE = re.compile(
    rf"Zuivering\s+van\s+afvalwater\s*{_UNAVAILABLE}", re.IGNORECASE
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

    The article is prose, not the tariff card, and the two have been
    seen to disagree: for 2026 it prints 2,9521 euro per 1.000 liter
    where the tariff endpoint and De Watergroep's own kraanwater page
    both print 2,9251. This path keeps a tariff on the board; it is
    not a cross-check for the endpoint, and its figure must not be
    used to "correct" the per-commune parser.
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

    A leg can be missing for two reasons and they are not the same. When
    De Watergroep cannot render the amount it says so in words, and
    reading that as 0.00 EUR/m3 drops the whole leg from the bill: on
    2026-09-08 that billed 3660 Opglabbeek 575.26 EUR a year against
    782.73. That case raises, so the coordinator keeps the last good
    snapshot and the stale-snapshot Repair goes up.

    A bare label with no amount and no such sentence still counts as a
    commune that levies nothing. Sinaai used to be cited here as the
    example of one, wrongly: it publishes EUR 1,9114 today, and the
    committed fixture had merely caught the page in the same state. There
    is no confirmed example either way, so the reading is left as it was
    rather than swapped for a guess.

    Drinkwater stays required: without it there is no tariff at all.
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
    for pattern, leg in (
        (_AFVOER_UNAVAILABLE_RE, "gemeentelijke"),
        (_ZUIVERING_UNAVAILABLE_RE, "bovengemeentelijke"),
    ):
        if pattern.search(block) is not None:
            raise ExtractorError(
                f"De Watergroep says the {leg} saneringsbijdrage cannot be shown "
                f"for this commune; refusing to bill it as 0.00 EUR/m3"
            )
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
        return await _newest_commune_card(session, _DEFAULT_COMMUNE_GUID, _DEFAULT_COMMUNE_LABEL)
    except ExtractorError as default_err:
        if isinstance(default_err, TransientFetchError):
            # A transient blip (5xx / 429 / timeout) must propagate so
            # live_check / fixture_drift classify it as TRANSIENT, rather
            # than silently degrading to the drinkwater-only news article
            # (a ~200 EUR/year under-estimate).
            raise
        _LOGGER.info(
            "De Watergroep default-commune fetch failed (%s); falling back to news article",
            default_err,
        )
        try:
            return await fetch_and_parse(
                session, NEWS_URL_FMT.format(year=target), parse_news_tariff, year=target
            )
        except TransientFetchError:
            # A blip on this year's article is an outage, not a missing
            # article; serving last year's rate for it would hide the
            # outage from the live check as well.
            raise
        except ExtractorError as err:
            _LOGGER.info(
                "De Watergroep %d article unavailable (%s); trying %d", target, err, target - 1
            )
            prior = await fetch_and_parse(
                session, NEWS_URL_FMT.format(year=target - 1), parse_news_tariff, year=target - 1
            )
            return carry_prior_year_card(prior, target)


# The year switcher in the answer marks the tab it served, whichever year
# the URL asked for.
_SERVED_YEAR_RE = re.compile(
    r'UpdateDetailTariefJaar/(20\d\d)/hh-tarieven"\s+class="active"\s+'
    r'title="Huishoudelijke tarieven (20\d\d)"'
)


def _served_year(text: str, asked: int) -> int:
    """The year the answer's active tab names, else the year asked for.

    Asked for a year it has not published, the endpoint can answer with
    the newest card it has, and stamping that with the year in the URL
    served last year's card as this year's, never stale.
    """
    match = _SERVED_YEAR_RE.search(text)
    if match is None or match.group(1) != match.group(2):
        return asked
    return int(match.group(1))


async def _fetch_commune_ajax(
    session: aiohttp.ClientSession, commune: str, year: int | None = None
) -> tuple[str, int]:
    """GET the UpdateDetailTariefJaar AJAX response for ``commune``.

    Returns the response body and the year it carries: the one its
    active tab names, or failing that the one the URL asked for. Raised
    errors are :class:`ExtractorError`; the caller decides what to
    label the parsed tariff with.
    """
    target = year or date.today().year
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
            # The commune cookie must not travel to wherever a redirect
            # points; a moved endpoint is a failure to look at.
            allow_redirects=False,
        ) as resp:
            if not 200 <= resp.status < 300:
                raise _http_error(url, resp.status)
            text = await _read_text_capped(resp, url)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise TransientFetchError(
            f"network error fetching De Watergroep AJAX endpoint: {error_text(err)}"
        ) from err
    if not text.strip() or "Basistarief" not in text:
        raise ExtractorError(
            f"De Watergroep returned an empty body for commune {commune!r} "
            "(probably an invalid GUID)"
        )
    return text, _served_year(text, target)


async def _commune_card(
    session: aiohttp.ClientSession, commune: str, label: str, target: int, year: int | None = None
) -> WaterTariff:
    text, served = await _fetch_commune_ajax(session, commune, year)
    tariff = await asyncio.to_thread(parse_commune_tariff, text, year=served, commune_label=label)
    return carry_prior_year_card(tariff, target)


async def _newest_commune_card(
    session: aiohttp.ClientSession, commune: str, label: str
) -> WaterTariff:
    """This year's card for ``commune``, or last year's until 31 March.

    In January the endpoint for the new year can fail outright until the
    card is published; last year's endpoint still answers, and its card
    stands in like every other utility's prior-year fallback.
    """
    target = date.today().year
    try:
        return await _commune_card(session, commune, label, target)
    except TransientFetchError:
        raise
    except ExtractorError as err:
        _LOGGER.info("De Watergroep %d card unavailable (%s); trying %d", target, err, target - 1)
        return await _commune_card(session, commune, label, target, year=target - 1)


async def fetch_for_commune(session: aiohttp.ClientSession, commune: str) -> WaterTariff:
    """Per-commune fetch via the cookie-driven UpdateDetailTariefJaar endpoint."""
    return await _newest_commune_card(session, commune, commune)


# The gap between the tag and the label is not padded with \\s* on
# either side: three ways to split the same run of whitespace makes
# the engine enumerate every split when the match fails, which is
# cubic in the length of that run. .strip() below does the same job
# in linear time.
_OPTION_RE = re.compile(
    r'<option[^>]*value="(\{[0-9A-Fa-f-]+\})"[^>]*>([^<]*)</option>',
    re.IGNORECASE | re.DOTALL,
)


def _parse_commune_options(html: str) -> list[CommuneOption]:
    communes: list[CommuneOption] = []
    seen: set[str] = set()
    for match in _OPTION_RE.finditer(html):
        guid = match.group(1)
        label = match.group(2).strip()
        if not label or guid in seen:
            continue
        seen.add(guid)
        communes.append(CommuneOption(id=guid, label=label))
    return communes


async def list_communes(session: aiohttp.ClientSession) -> tuple[CommuneOption, ...]:
    """Discover the 700 De Watergroep communes by scraping the dropdown."""
    html = await fetch_html(session, COMMUNE_LIST_URL)
    # Same reasoning as farys.list_communes: this is reached from the
    # config flow on the event loop, and every other parse in this
    # module goes through a thread.
    communes = await asyncio.to_thread(_parse_commune_options, html)
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
