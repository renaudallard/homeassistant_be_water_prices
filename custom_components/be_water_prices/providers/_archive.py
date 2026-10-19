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

"""Reading the project's card archive.

The ``archive`` branch holds one row per utility, commune and month, and
the texts each row was parsed from (see ``scripts/archive_cards.py``). The
coordinator reads a row when a refresh fails with nothing to serve.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from urllib.parse import quote

import aiohttp

from ..const import CARD_ARCHIVE_URL
from ._pdf import fetch_text
from .base import ExtractorError, TransientFetchError

# How many months back a read looks for a stored row, newest first and
# stopping at the first one it finds. The daily run files a row a month,
# so this month and the one before it cover an outage. The rest is for the
# utility the runner cannot reach, whose rows reach the branch only when
# someone runs the archiver from a residential address, and for a month
# the archiver spent locked out of a publication. A water tariff is
# annual, so the oldest card this can reach is last year's, the card every
# extractor already stands on in January, and the staleness clock still
# runs from the day it was captured.
ARCHIVE_MONTHS_BACK = 12


async def archived_row(
    session: aiohttp.ClientSession, utility: str, commune: str, month: date
) -> dict[str, Any] | None:
    """The row the project's card archive holds for that utility, commune
    and month, or None when it holds none or what it holds is not a row.

    Raises :class:`TransientFetchError` when the archive could not be
    asked (a network failure, a 5xx, a rate limit): that says nothing
    about whether the month is held."""
    url = f"{CARD_ARCHIVE_URL}/{utility}/{quote(commune, safe='')}/{month:%Y-%m}.json"
    try:
        body = await fetch_text(session, url)
    except TransientFetchError:
        raise
    except ExtractorError:
        return None
    try:
        row = json.loads(body)
    except ValueError:
        return None
    return row if isinstance(row, dict) else None
