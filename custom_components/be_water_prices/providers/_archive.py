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
coordinator reads a row when a refresh fails with nothing to serve; an
extractor whose card is a picture reads the text the archive's OCR made of
it, since nothing in Home Assistant decodes a picture.

Both obey the entry's ``card_archive`` option, which the coordinator
states for the duration of a fetch with :func:`archive_reads`.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, timedelta
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
# A text is stored under the SHA-256 of its own bytes, which is what makes
# it checkable: anything else in a row's ``text`` is not followed.
_TEXT_PATH_RE = re.compile(r"texts/([0-9a-f]{64})\.txt")

_ARCHIVE_READS: ContextVar[bool] = ContextVar("_ARCHIVE_READS", default=True)


@contextmanager
def archive_reads(enabled: bool) -> Iterator[None]:
    """Allow or forbid reading the archive inside this block."""
    token = _ARCHIVE_READS.set(enabled)
    try:
        yield
    finally:
        _ARCHIVE_READS.reset(token)


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


async def _archived_text(session: aiohttp.ClientSession, path: str) -> str | None:
    """A stored text, or None when it is missing or not what its name says."""
    match = _TEXT_PATH_RE.fullmatch(path)
    if match is None:
        return None
    try:
        text = await fetch_text(session, f"{CARD_ARCHIVE_URL}/{path}")
    except TransientFetchError:
        raise
    except ExtractorError:
        return None
    if hashlib.sha256(text.encode("utf-8")).hexdigest() != match.group(1):
        return None
    return text


async def archived_picture_text(
    session: aiohttp.ClientSession, utility: str, url: str, today: date
) -> str | None:
    """What the archive's OCR read off the picture at ``url``.

    The newest row of the utility's default card, this month's first, then
    back a month at a time until one is found. That row names the picture
    it read when the archive has read the one the page shows now; a row
    that names another picture, or none, means it has not, and an older
    row cannot have either. None with ``card_archive`` off.

    Raises :class:`TransientFetchError` when the archive could not be asked.
    """
    if not _ARCHIVE_READS.get():
        return None
    month = today
    for _ in range(ARCHIVE_MONTHS_BACK):
        row = await archived_row(session, utility, "default", month)
        if row is not None:
            break
        # The first of the month, a day back: the last day of the one
        # before, whatever its length.
        month = month.replace(day=1) - timedelta(days=1)
    else:
        return None
    sources = row.get("_sources")
    if not isinstance(sources, list):
        return None
    for source in sources:
        if (
            isinstance(source, dict)
            and source.get("variant") == "ocr"
            and source.get("url") == url
            and isinstance(source.get("text"), str)
        ):
            return await _archived_text(session, source["text"])
    return None
