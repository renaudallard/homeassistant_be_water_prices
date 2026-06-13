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

"""HTML scraping helpers for water-utility tariff pages.

Most Belgian water utilities publish their numerical tariff card on a
plain HTML page, often in a ``<table>``. This module provides:

* :func:`fetch_html` -- async GET wrapper that mirrors :func:`_pdf.fetch_text`
  but returns the raw HTML body. (Re-exported from ``_pdf`` so a future
  caller does not need to import both modules.)
* :func:`parse_simple_table` -- extracts a ``<table>`` whose first
  column is a label and whose remaining columns are values, returning a
  ``list[list[str]]`` of rows. Skips empty cells and folds whitespace.
* :func:`find_table` -- locate the first ``<table>`` element whose
  rendered text contains every keyword in ``must_contain`` (case-
  insensitive, accent-folded). Useful when a page has more than one
  table and only one is the year you want.
* :func:`extract_amounts` -- regex helper that pulls every ``€ 12,34``
  amount from a string in document order.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import Any

import aiohttp
from bs4 import BeautifulSoup, Tag

from ._pdf import fetch_text, fold_accents, to_float

# Re-export so callers can import everything they need from this module.
fetch_html = fetch_text
__all__ = [
    "BeautifulSoup",
    "extract_amounts",
    "fetch_and_parse",
    "fetch_html",
    "find_table",
    "parse_simple_table",
    "to_float",
]


async def fetch_and_parse[T](
    session: aiohttp.ClientSession,
    url: str,
    parser: Callable[..., T],
    *args: Any,
    verify_ssl: bool = True,
    **kwargs: Any,
) -> T:
    """GET ``url`` then run ``parser`` on the body off the event loop.

    HTML parsing (BeautifulSoup, or a heavy regex over a large page) is
    pure-CPU work. Running it inline after the ``await`` would block the
    asyncio loop for tens of milliseconds on a real tariff page, which
    HA's blocking-I/O guard cannot detect. Hand it to a worker thread,
    mirroring the PDF helpers, which already offload extraction.
    """
    html = await fetch_html(session, url, verify_ssl=verify_ssl)
    return await asyncio.to_thread(parser, html, *args, **kwargs)


_AMOUNT_BEFORE_EURO = re.compile(r"€\s*([0-9]+(?:[.,][0-9]+)?)")
_AMOUNT_AFTER_EURO = re.compile(r"([0-9]+(?:[.,][0-9]+)?)\s*€")


def extract_amounts(text: str) -> list[float]:
    """Return every euro amount found in ``text``, in order.

    Accepts both Dutch / English layout (``€ 12,34``) and French layout
    (``12,34 €``); some Walloon utility pages publish the symbol after
    the number. Hits are de-duplicated by start position so a string
    like "€ 12,34 €" reports 12.34 once.
    """
    seen: dict[int, float] = {}
    for pattern in (_AMOUNT_BEFORE_EURO, _AMOUNT_AFTER_EURO):
        for m in pattern.finditer(text):
            seen[m.start(1)] = to_float(m.group(1))
    return [seen[k] for k in sorted(seen)]


def find_table(
    soup: BeautifulSoup,
    must_contain: tuple[str, ...],
) -> Tag | None:
    """Return the first ``<table>`` whose rendered text contains every
    keyword in ``must_contain``. Comparison is case-insensitive and
    accent-folded so "Janvier 2026" matches "janvier 2026".
    """
    needles = tuple(fold_accents(k) for k in must_contain)
    for table in soup.find_all("table"):
        haystack = fold_accents(table.get_text(" ", strip=True))
        if all(n in haystack for n in needles):
            return table
    return None


def parse_simple_table(table: Tag) -> list[list[str]]:
    """Extract a ``<table>`` as a list of rows of stripped cell text.

    Empty rows (all cells blank after stripping) are dropped. Every cell
    is whitespace-folded to a single space.
    """
    rows: list[list[str]] = []
    for tr in table.find_all("tr"):
        cells = [
            re.sub(r"\s+", " ", td.get_text(" ", strip=True)) for td in tr.find_all(["td", "th"])
        ]
        if any(c for c in cells):
            rows.append(cells)
    return rows
