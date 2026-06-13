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

"""Farys (TMVW) -- biggest remaining Flemish operator (~1.5 M, ~22 % of Flanders).

Farys's `farys.be/nl/watertarieven` page is JS-rendered: the static
HTML carries only a 290-option commune dropdown and no rates. Selecting
a commune fires a Drupal AJAX form POST to
`/nl/watertarieven?ajax_form=1`; the response is a JSON envelope
containing an `insert` command whose `data` field is HTML markup with
the full per-commune integrale waterprijs structure (drinkwater +
gemeentelijke + bovengemeentelijke saneringsbijdragen, with the
standard VMM 50/30/20 + 10/6/4 vastrecht/korting split).

The extractor calls that endpoint directly with the commune ID baked
in (Gent-centrum = 25071 by default) and parses the rates out of the
`insert` command's HTML payload. The `form_build_id` field is
optional; the endpoint accepts a POST without it.

Per-commune data is real and varies (Gent-centrum's drinkwater is
3.0058 EUR/m³ in 2026, other communes differ). Gent-centrum is the
default since Gent is Farys's namesake city and the largest commune
in its Oost-Vl. heartland; users override via the OptionsFlow
commune dropdown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from .._phantom_blocklists import (
    FARYS_UNSERVABLE_IDS as _UNSERVABLE_COMMUNE_IDS,
)
from .._phantom_blocklists import (
    FARYS_UNSERVABLE_LABELS as _UNSERVABLE_COMMUNE_LABELS,
)
from ..const import REGION_FLANDERS
from ._flanders import build_flanders_tariff
from ._pdf import USER_AGENT, _http_error, fetch_text, to_float
from .base import CommuneOption, ExtractorError, TransientFetchError, WaterExtractor, WaterTariff

# Re-exported so ``async_migrate_entry`` and the test suite can read
# them under their existing names without knowing about the dep-free
# leaf module.
__all__ = ("EXTRACTOR", "_UNSERVABLE_COMMUNE_IDS", "_UNSERVABLE_COMMUNE_LABELS")

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "farys"
LABEL = "Farys"
ENDPOINT_URL = "https://www.farys.be/nl/watertarieven?ajax_form=1"
PAGE_URL = "https://www.farys.be/nl/watertarieven"
DEFAULT_MUNICIPALITY_ID = "25071"  # Gent-centrum
DEFAULT_MUNICIPALITY_LABEL = "Gent-centrum"
_CURRENT_PAGE_NID = "20471"

# Match "Basistarief drinkwater (per m³) € N,NNNN" and the matching
# comforttarief / sanering rows. The HTML has the labels broken across
# inline tags but the text-collapsed version is stable.
_BASIS_DRINKWATER_RE = re.compile(
    r"Basistarief\s+drinkwater\s*\(per\s*m³\)\s*€\s*([\d]+,\d{3,5})", re.IGNORECASE
)
_COMFORT_DRINKWATER_RE = re.compile(
    r"Comforttarief\s+drinkwater\s*\(per\s*m³\)\s*€\s*([\d]+,\d{3,5})", re.IGNORECASE
)
_BASIS_GEMEENTELIJK_RE = re.compile(
    r"Basistarief\s+gemeentelijke[^€]+€\s*([\d]+,\d{3,5})", re.IGNORECASE | re.DOTALL
)
_BASIS_BOVENGEMEENTELIJK_RE = re.compile(
    r"Basistarief\s+bovengemeentelijke[^€]+€\s*([\d]+,\d{3,5})", re.IGNORECASE | re.DOTALL
)


def _extract_html_payload(ajax_response_text: str) -> str:
    """Pull the `insert`-command `data` field out of a Drupal AJAX response."""
    try:
        commands = json.loads(ajax_response_text)
    except json.JSONDecodeError as err:
        raise ExtractorError(f"Farys AJAX response is not JSON: {err}") from err
    # A Drupal AJAX response is a list of command dicts. A well-formed
    # JSON value that is not that shape (an error envelope, a bare string
    # or number) would otherwise raise a raw AttributeError / TypeError;
    # report it as a parse failure instead.
    if not isinstance(commands, list):
        raise ExtractorError("Farys AJAX response was not a command list")
    for cmd in commands:
        if isinstance(cmd, dict) and cmd.get("command") == "insert":
            data = cmd.get("data")
            if isinstance(data, str) and "Basistarief" in data:
                return data
    raise ExtractorError("Farys AJAX response has no insert command with tariff data")


def _amount(text: str, pattern: re.Pattern[str], label: str) -> float:
    match = pattern.search(text)
    if match is None:
        raise ExtractorError(f"Farys: could not find {label} in the AJAX HTML payload")
    return to_float(match.group(1))


def parse_tariff(
    ajax_response_text: str,
    *,
    year: int | None = None,
    municipality_label: str = DEFAULT_MUNICIPALITY_LABEL,
) -> WaterTariff:
    """Parse a captured Farys AJAX response (JSON envelope of HTML)."""
    html_payload = _extract_html_payload(ajax_response_text)
    soup = BeautifulSoup(html_payload, "html.parser")
    text = soup.get_text(" ", strip=True)

    basis = _amount(text, _BASIS_DRINKWATER_RE, "drinkwater basistarief")
    comfort = _amount(text, _COMFORT_DRINKWATER_RE, "drinkwater comforttarief")
    if abs(comfort - 2.0 * basis) > 0.01:
        raise ExtractorError(
            f"Farys comforttarief {comfort} is not 2× basistarief {basis} (VMM 2× rule)"
        )
    sanering_gem = _amount(text, _BASIS_GEMEENTELIJK_RE, "gemeentelijke saneringsbijdrage")
    sanering_bov = _amount(
        text, _BASIS_BOVENGEMEENTELIJK_RE, "bovengemeentelijke saneringsbijdrage"
    )

    target = year or date.today().year
    return build_flanders_tariff(
        utility_id=UTILITY_ID,
        year=target,
        publication_label=f"Farys watertarieven {target} ({municipality_label})",
        source_url=PAGE_URL,
        basis=basis,
        comfort=comfort,
        sanering_gemeentelijk=sanering_gem,
        sanering_bovengemeentelijk=sanering_bov,
    )


async def _post_for_commune(session: aiohttp.ClientSession, commune_id: str) -> str:
    payload = {
        "switcher": "WaterRateInformation",
        "municipality": commune_id,
        "current_page_nid": _CURRENT_PAGE_NID,
        "form_id": "farys_municipalities_switcher_form",
        "_triggering_element_name": "municipality",
    }
    try:
        async with session.post(
            ENDPOINT_URL,
            data=payload,
            headers={
                "User-Agent": USER_AGENT,
                "X-Requested-With": "XMLHttpRequest",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status >= 400:
                raise _http_error(ENDPOINT_URL, resp.status)
            return await resp.text()
    except (aiohttp.ClientError, TimeoutError) as err:
        raise TransientFetchError(f"network error fetching Farys AJAX endpoint: {err}") from err


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    text = await _post_for_commune(session, DEFAULT_MUNICIPALITY_ID)
    return await asyncio.to_thread(parse_tariff, text)


async def fetch_for_commune(session: aiohttp.ClientSession, commune: str) -> WaterTariff:
    text = await _post_for_commune(session, commune)
    return await asyncio.to_thread(parse_tariff, text, municipality_label=commune)


# Each <option> is "<postcode> - <commune> (<gemeente>)" with value =
# numeric ID. We store the numeric ID as the option's id and the full
# label as its display string.
_OPTION_RE = re.compile(
    r'<option[^>]*value="(\d+)"[^>]*>\s*([^<]+?)\s*</option>',
    re.IGNORECASE | re.DOTALL,
)


# Phantom entries in Farys's dropdown -- the AJAX endpoint returns a
# response without an "insert" command for these, so picking them
# crashes with "no insert command with tariff data". These are split
# postcodes where DWG is the actual operator for that street/parish;
# the entry is left in Farys's UI but the back-end has no data.
# Drop them from list_communes so users can't pick them; the resolver
# falls back to Farys for the postcode (the dominant operator on the
# Farys-served half), and users in the DWG half manual-override on
# reconfigure. The data lives in ``_phantom_blocklists`` (imported at
# the top of this module) so the maintenance script can read it
# without pulling the providers package in.


async def list_communes(session: aiohttp.ClientSession) -> tuple[CommuneOption, ...]:
    """Discover all 290+ Farys communes by scraping the watertarieven dropdown.

    Drops the 23 phantom entries Farys's UI lists without backing tariff
    data (see ``_UNSERVABLE_COMMUNE_LABELS``).
    """
    html = await fetch_text(session, PAGE_URL)
    communes: list[CommuneOption] = []
    seen: set[str] = set()
    for match in _OPTION_RE.finditer(html):
        commune_id = match.group(1)
        label = match.group(2).strip()
        if not label or commune_id in seen or label in _UNSERVABLE_COMMUNE_LABELS:
            continue
        seen.add(commune_id)
        communes.append(CommuneOption(id=commune_id, label=label))
    if not communes:
        raise ExtractorError("could not discover any Farys communes from the watertarieven page")
    return tuple(communes)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
    fetch_for_commune=fetch_for_commune,
    list_communes=list_communes,
)
