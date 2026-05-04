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

Per-commune data is real and varies (Aalst's drinkwater is 3.0058
EUR/m³ in 2026, other communes differ). Per-commune selection in the
OptionsFlow lands later -- for now Gent-centrum is the default since
Gent is Farys's namesake city and the largest commune in its Oost-Vl.
heartland.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date

import aiohttp
from bs4 import BeautifulSoup

from ..const import (
    DEFAULT_VAT_RATE,
    FLANDERS_KORTING_TOTAL_PER_PERSON,
    FLANDERS_VASTRECHT_TOTAL,
    REGION_FLANDERS,
)
from ._pdf import USER_AGENT, to_float
from .base import ExtractorError, WaterExtractor, WaterTariff

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
    for cmd in commands:
        if cmd.get("command") == "insert":
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
    return WaterTariff(
        utility=UTILITY_ID,
        region=REGION_FLANDERS,
        valid_from=date(target, 1, 1),
        valid_until=date(target, 12, 31),
        publication_label=f"Farys watertarieven {target} ({municipality_label})",
        source_url=PAGE_URL,
        yearly_fixed_fee=FLANDERS_VASTRECHT_TOTAL,
        yearly_fixed_fee_per_resident_discount=FLANDERS_KORTING_TOTAL_PER_PERSON,
        basis_eur_per_m3=basis,
        comfort_eur_per_m3=comfort,
        sanering_gemeentelijk_eur_per_m3=sanering_gem,
        sanering_bovengemeentelijk_eur_per_m3=sanering_bov,
        vat_rate=DEFAULT_VAT_RATE,
    )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    payload = {
        "switcher": "WaterRateInformation",
        "municipality": DEFAULT_MUNICIPALITY_ID,
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
                raise ExtractorError(f"HTTP {resp.status} fetching {ENDPOINT_URL}")
            text = await resp.text()
    except aiohttp.ClientError as err:
        raise ExtractorError(f"network error fetching Farys AJAX endpoint: {err}") from err
    return parse_tariff(text)


EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_FLANDERS,
    fetch=fetch,
)
