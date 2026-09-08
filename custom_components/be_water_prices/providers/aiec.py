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

"""AIEC -- Association Intercommunale des Eaux du Condroz.

Tiny operator covering parts of the Condroz region. AIEC publishes its
card only as a picture: ``Prix.htm`` embeds a JPEG whose name carries
the day the card took effect (``Tarif-2026-04-1-<cms hash>.jpg``). The
rate inside it cannot be read without OCR, so the CVD still comes from
the Callmepower aggregator.

An aggregator can be wrong, and a drift check cannot tell: it compares
the parser against its own source, so a source that is itself wrong
reads as green. On 1 April 2026 AIEC moved its CVD to 3,050 and
Callmepower stayed on 2,460, under-stating an 80 m3 bill by 53 EUR a
year with nothing to show for it.

The picture's own date is readable even though its contents are not, so
:func:`fetch` holds the aggregator to it: a card dated after the one we
are about to serve means the aggregator is behind, and the fetch fails
rather than publishing a rate the operator has replaced. That puts it on
the paths that are watched, the same trade
:func:`_walloon_simple.warn_constant_drift` makes for the SPGE
constants.

Sources: https://callmepower.be/fr/eau/distributeurs/aiec
         http://www.eauxducondroz.be/Prix.htm
"""

from __future__ import annotations

import logging
import re
from datetime import date

import aiohttp

from ..const import REGION_WALLONIA
from ._html import fetch_html
from ._walloon_simple import build_extractor
from ._walloon_simple import parse_tariff as _parse_tariff
from .base import ExtractorError, TransientFetchError, WaterExtractor, WaterTariff

_LOGGER = logging.getLogger(__name__)

UTILITY_ID = "aiec"
LABEL = "AIEC"
SOURCE_URL = "https://callmepower.be/fr/eau/distributeurs/aiec"
_LABEL_PREFIX = "AIEC tarifs (via Callmepower)"


def parse_tariff(html: str, year: int | None = None) -> WaterTariff:
    return _parse_tariff(
        html,
        utility_id=UTILITY_ID,
        source_url=SOURCE_URL,
        label_prefix=_LABEL_PREFIX,
        year=year,
    )


# The operator's own page, and the card picture it embeds. The trailing
# hash is the CMS dedupe suffix, as on the Water-link and Aquaduin links.
OPERATOR_URL = "http://www.eauxducondroz.be/Prix.htm"
_CARD_IMAGE_RE = re.compile(
    r"Tarif-(20\d\d)-(\d{1,2})-(\d{1,2})[^\"'>]*\.jpe?g",
    re.IGNORECASE,
)


def published_card_date(html: str) -> date | None:
    """The day the newest card picture on AIEC's page took effect.

    ``None`` when the page carries no card whose name states a date, which
    is not a reason to refuse the aggregator: it is the state the page was
    in before AIEC started dating its pictures.
    """
    dates = []
    for match in _CARD_IMAGE_RE.finditer(html):
        try:
            dates.append(date(int(match.group(1)), int(match.group(2)), int(match.group(3))))
        except ValueError:
            continue
    return max(dates) if dates else None


def check_against_operator(tariff: WaterTariff, html: str) -> None:
    """Refuse an aggregator card the operator has already replaced."""
    published = published_card_date(html)
    if published is not None and published > tariff.valid_from:
        raise ExtractorError(
            f"AIEC published a card effective {published.isoformat()} but "
            f"{SOURCE_URL} still carries the one from "
            f"{tariff.valid_from.isoformat()} (CVD {tariff.cvd_eur_per_m3}); "
            f"the rate cannot be read from {OPERATOR_URL}, which publishes it "
            "as a picture"
        )


async def fetch(session: aiohttp.ClientSession) -> WaterTariff:
    tariff = await _fetch_aggregator(session)
    try:
        html = await fetch_html(session, OPERATOR_URL)
    except TransientFetchError:
        raise
    except ExtractorError as err:
        # The operator's page is the cross-check, not the rate. Losing it
        # is worth a log line, not the card.
        _LOGGER.info("could not read %s to date-check the AIEC card: %s", OPERATOR_URL, err)
        return tariff
    check_against_operator(tariff, html)
    return tariff


_fetch_aggregator = build_extractor(
    utility_id=UTILITY_ID,
    label=LABEL,
    source_url=SOURCE_URL,
    publication_label_prefix=_LABEL_PREFIX,
).fetch

EXTRACTOR = WaterExtractor(
    id=UTILITY_ID,
    label=LABEL,
    region=REGION_WALLONIA,
    fetch=fetch,
)
