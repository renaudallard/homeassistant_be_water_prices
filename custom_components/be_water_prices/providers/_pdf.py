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

"""Shared helpers for fetching and reading PDF tariff cards.

Vendored subset of the sibling ``be_electricity_prices`` integration's
``providers/_pdf.py``: keeps only what water extractors need (sign /
formula parsing, hourly month resolution, layout-aligned PDF reading
are electricity-only).
"""

from __future__ import annotations

import asyncio
import calendar
import json
import logging
import re
import unicodedata
from datetime import date
from io import BytesIO
from pathlib import Path

import aiohttp
import pypdf

from .base import ExtractorError, TransientFetchError

_LOGGER = logging.getLogger(__name__)


def _http_error(url: str, status: int) -> ExtractorError:
    """Map an HTTP status to the right error class.

    5xx (server error) and 429 (rate limited) are transient upstream
    conditions; 4xx (moved / forbidden / gone) usually means the page
    changed and is a real failure worth reporting.
    """
    message = f"HTTP {status} fetching {url}"
    if status >= 500 or status == 429:
        return TransientFetchError(message)
    return ExtractorError(message)


def _read_version() -> str:
    manifest = Path(__file__).resolve().parent.parent / "manifest.json"
    try:
        return str(json.loads(manifest.read_text(encoding="utf-8")).get("version", "0"))
    except (OSError, ValueError):
        return "0"


USER_AGENT = f"Home Assistant be_water_prices/{_read_version()}"

# Hard ceiling on a fetched response body. Real tariff PDFs and HTML
# pages are well under a megabyte; this only bounds memory if an upstream
# server -- or a MitM -- returns an arbitrarily large body.
MAX_RESPONSE_BYTES = 16 * 1024 * 1024


def _guard_content_length(resp: aiohttp.ClientResponse, url: str) -> None:
    """Reject a response whose declared length is over the cap."""
    length = resp.content_length
    if length is not None and length > MAX_RESPONSE_BYTES:
        raise ExtractorError(
            f"response from {url} declares {length} bytes, over the {MAX_RESPONSE_BYTES}-byte limit"
        )


async def _read_capped(resp: aiohttp.ClientResponse, url: str) -> bytes:
    """Read the body, refusing anything past ``MAX_RESPONSE_BYTES``.

    Content-Length is only a hint (absent on a chunked response, and a
    hostile server can lie), so the cap is also enforced on the bytes
    actually read.
    """
    _guard_content_length(resp, url)
    payload = bytearray()
    async for chunk in resp.content.iter_chunked(65536):
        payload.extend(chunk)
        if len(payload) > MAX_RESPONSE_BYTES:
            raise ExtractorError(f"response from {url} exceeded {MAX_RESPONSE_BYTES} bytes")
    return bytes(payload)


async def _read_text_capped(resp: aiohttp.ClientResponse, url: str) -> str:
    """Read a text body under the size cap, decoding leniently.

    Streams via :func:`_read_capped` so a chunked or Content-Length-less
    body cannot blow past the cap (``resp.text()`` reads unbounded), and
    decodes with the declared charset, falling back to UTF-8 with
    ``errors="replace"`` so a charset-mislabelled page yields a parseable
    string instead of raising an uncaught ``UnicodeDecodeError``.
    """
    payload = await _read_capped(resp, url)
    return payload.decode(resp.charset or "utf-8", errors="replace")


def _is_pdf_payload(payload: bytes) -> bool:
    """Return True if the bytes look like a PDF.

    PDFs start with ``%PDF``; some publishers prepend a UTF-8 BOM.
    """
    if payload.startswith(b"%PDF"):
        return True
    return payload.startswith(b"\xef\xbb\xbf%PDF")


async def fetch_pdf_text(session: aiohttp.ClientSession, url: str) -> str:
    """Download ``url`` and return concatenated extracted text."""
    try:
        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status >= 400:
                raise _http_error(url, resp.status)
            payload = await _read_capped(resp, url)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise TransientFetchError(f"network error fetching {url}: {err}") from err

    if not _is_pdf_payload(payload):
        snippet = payload[:80]
        raise ExtractorError(f"expected a PDF at {url}, payload starts with {snippet!r}")
    return await asyncio.to_thread(extract_pdf_text, payload)


def extract_pdf_text(payload: bytes) -> str:
    try:
        reader = pypdf.PdfReader(BytesIO(payload))
        pages = list(reader.pages)
        chunks: list[str] = []
        failures = 0
        for idx, page in enumerate(pages):
            text = page.extract_text()
            if text is None:
                _LOGGER.warning("pypdf returned None for page %d/%d", idx + 1, len(pages))
                failures += 1
                continue
            chunks.append(text)
        if pages and failures == len(pages):
            raise ExtractorError("PDF parse error: every page failed to decode")
        return "\n".join(chunks)
    except ExtractorError:
        raise
    except Exception as err:
        raise ExtractorError(f"PDF parse error: {err}") from err


def extract_pdf_text_layout(payload: bytes) -> str:
    """Extract PDF text via pdfplumber, preserving table layout."""
    try:
        import pdfplumber

        with pdfplumber.open(BytesIO(payload)) as pdf:
            return "\n".join((page.dedupe_chars().extract_text() or "") for page in pdf.pages)
    except Exception as err:
        raise ExtractorError(f"PDF layout parse error: {err}") from err


async def fetch_pdf_text_layout(session: aiohttp.ClientSession, url: str) -> str:
    """Layout-preserving variant of :func:`fetch_pdf_text`."""
    try:
        async with session.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as resp:
            if resp.status >= 400:
                raise _http_error(url, resp.status)
            payload = await _read_capped(resp, url)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise TransientFetchError(f"network error fetching {url}: {err}") from err
    if not _is_pdf_payload(payload):
        raise ExtractorError(f"expected a PDF at {url}, payload starts with {payload[:80]!r}")
    return await asyncio.to_thread(extract_pdf_text_layout, payload)


async def fetch_text(
    session: aiohttp.ClientSession,
    url: str,
    *,
    timeout: int = 20,
    verify_ssl: bool = True,
) -> str:
    """GET ``url`` and return the response body as text.

    ``verify_ssl=False`` skips TLS certificate verification for this
    request only; reserve it for utility servers whose hosts genuinely
    misconfigure their chain (e.g. inBW, where the GoDaddy intermediate
    is not sent by the server). The risk is bounded -- worst case is
    a MitM serving stale tariff numbers, no credentials are involved.
    """
    try:
        kwargs: dict[str, object] = {
            "headers": {"User-Agent": USER_AGENT},
            "timeout": aiohttp.ClientTimeout(total=timeout),
        }
        if not verify_ssl:
            kwargs["ssl"] = False
        async with session.get(url, **kwargs) as resp:  # type: ignore[arg-type]
            if resp.status >= 400:
                raise _http_error(url, resp.status)
            return await _read_text_capped(resp, url)
    except (aiohttp.ClientError, TimeoutError) as err:
        raise TransientFetchError(f"network error fetching {url}: {err}") from err


_NUMERIC_SEPARATORS = (
    " ",
    " ",  # NBSP
    " ",  # THIN SPACE
    " ",  # NARROW NO-BREAK SPACE
    " ",  # LINE SEPARATOR
)


def fold_accents(text: str) -> str:
    """Lowercase and strip Latin diacritics."""
    return "".join(
        c for c in unicodedata.normalize("NFKD", text.lower()) if not unicodedata.combining(c)
    )


def to_float(text: str) -> float:
    """Parse a Belgian / French decimal number ('15,93' or '0.102').

    Strips every Unicode space variant Belgian publications use as a
    thousands separator or unit padder before swapping the comma for a
    decimal point.
    """
    cleaned = text.strip()
    for sep in _NUMERIC_SEPARATORS:
        cleaned = cleaned.replace(sep, "")
    return float(cleaned.replace(",", "."))


_MONTH_NAMES: dict[str, int] = {
    "januari": 1,
    "februari": 2,
    "maart": 3,
    "april": 4,
    "mei": 5,
    "juni": 6,
    "juli": 7,
    "augustus": 8,
    "september": 9,
    "oktober": 10,
    "november": 11,
    "december": 12,
    "janvier": 1,
    "fevrier": 2,
    "février": 2,
    "mars": 3,
    "avril": 4,
    "juin": 6,
    "juillet": 7,
    "aout": 8,
    "août": 8,
    "septembre": 9,
    "octobre": 10,
    "novembre": 11,
    "decembre": 12,
    "décembre": 12,
    "january": 1,
    "february": 2,
    "march": 3,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "october": 10,
}


_VALID_KEYWORDS = ("geldig", "valable", "validit", "valid ")


def parse_valid_until(text: str) -> date | None:
    """Best-effort parse of a "valid until" date from a tariff card.

    Anchored on a validity keyword (``geldig``, ``valable``,
    ``validit``, ``valid``); only considers dates in a ~200-char window
    after one of those keywords. Tries spelled-out, numeric, and bare
    "<month> <year>" forms (the bare form returns the last day of the
    matched month). Returns ``None`` when nothing matches.
    """
    lower = text.lower()
    name_alt = "|".join(re.escape(m) for m in _MONTH_NAMES)
    spelled_re = re.compile(rf"\b(\d{{1,2}})\s+({name_alt})\s+(20\d{{2}})\b")
    numeric_re = re.compile(r"(?<!\d)(\d{1,2})/(\d{1,2})/(\d{2}(?:\d{2})?)(?!\d)")
    bare_month_re = re.compile(rf"\b({name_alt})\s+(20\d{{2}})\b")

    windows: list[str] = []
    for keyword in _VALID_KEYWORDS:
        start = 0
        while True:
            idx = lower.find(keyword, start)
            if idx < 0:
                break
            windows.append(lower[idx : idx + 200])
            start = idx + len(keyword)

    if not windows:
        return None

    max_year = date.today().year + 5

    def _accept(d: date) -> bool:
        return d.year <= max_year

    candidates: list[date] = []
    for window in windows:
        for match in spelled_re.finditer(window):
            day, month_name, year = match.group(1), match.group(2), match.group(3)
            try:
                cand = date(int(year), _MONTH_NAMES[month_name], int(day))
            except ValueError:
                continue
            if _accept(cand):
                candidates.append(cand)
        for match in numeric_re.finditer(window):
            day, month, year = match.group(1), match.group(2), match.group(3)
            try:
                year_i = int(year)
                if year_i < 100:
                    year_i += 2000
                cand = date(year_i, int(month), int(day))
            except ValueError:
                continue
            if _accept(cand):
                candidates.append(cand)

    if candidates:
        return max(candidates)

    for window in windows:
        for match in bare_month_re.finditer(window):
            month_name, year = match.group(1), match.group(2)
            try:
                month = _MONTH_NAMES[month_name]
                last_day = calendar.monthrange(int(year), month)[1]
                cand = date(int(year), month, last_day)
            except (KeyError, ValueError):
                continue
            if _accept(cand):
                candidates.append(cand)
    return max(candidates) if candidates else None
