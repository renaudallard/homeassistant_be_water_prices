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
are electricity-only). A card published as a picture is fetched here too,
through the same guards, though only the card archiver ever reads one.
"""

from __future__ import annotations

import asyncio
import codecs
import ipaddress
import logging
import math
import os
import re
import subprocess
import sys
import unicodedata
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from typing import Any

import aiohttp
from yarl import URL

from ..const import FETCH_BUDGET_S, INTEGRATION_VERSION
from . import _pdf_guard
from .base import ExtractorError, TransientFetchError

_LOGGER = logging.getLogger(__name__)


def error_text(err: BaseException) -> str:
    """A message for ``err`` that is never empty.

    ``str()`` of an exception raised with no arguments is "", which
    leaves the surrounding message ending at its colon -- and that
    message reaches users through the last_error attribute and the
    stale-snapshot Repair card.
    """
    return str(err) or type(err).__name__


def _http_error(url: str, status: int) -> ExtractorError:
    """Map an HTTP status to the right error class.

    5xx (server error) and 429 (rate limited) are transient upstream
    conditions; 4xx (moved / forbidden / gone) usually means the page
    changed and is a real failure worth reporting. So is a 3xx that
    reaches the caller: one the client could not follow, or one it was
    told not to.
    """
    message = f"HTTP {status} fetching {url}"
    if status >= 500 or status == 429:
        return TransientFetchError(message)
    return ExtractorError(message)


USER_AGENT = f"Home Assistant be_water_prices/{INTEGRATION_VERSION}"

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


def _site(host: str) -> str:
    """The registrable part of ``host``: its last two labels."""
    return ".".join(host.lower().rstrip(".").split(".")[-2:])


# Where a site sends a page it no longer has: its home page, the bare
# language root included ("/fr/"), or a page saying so. VIVAQUA answers a
# removed page with a redirect to "/", AGSO Knokke-Heist with one to
# "/pagenotfound"; both then answer 200.
_HOME_PATH = re.compile(r"^/(?:[a-z]{2}/?)?$")
_NOT_FOUND_PATH = re.compile(r"page-?not-?found|/404(?:\.|/|$)", re.IGNORECASE)


# How many redirects one fetch follows. Every page, card and answer came
# straight back when each utility and the archive were fetched on 6
# November 2026, 31 requests and not one redirect, so five leave room for
# a move to https, a trailing slash and a language root on the way to a
# page. The statuses are the ones aiohttp follows.
MAX_REDIRECTS = 5
_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _local_address(host: str) -> bool:
    """Whether ``host`` is an address literal outside the public internet.

    That is private, loopback, link-local, reserved, shared (carrier NAT)
    and unspecified space, which ``is_global`` leaves out, and multicast,
    which it counts in. An IPv4 address written as IPv6 is judged as the
    IPv4 address it is.
    """
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        address = address.ipv4_mapped
    return not address.is_global or address.is_multicast


def _redirect_target(url: str, current: URL, location: str) -> URL:
    """Where a redirect from ``current`` points, checked before it is asked for.

    ``url`` is what the fetch was asked for. The target must not drop from
    https, must stay on the site ``url`` names, and must not be an address
    literal outside the public internet: the site check already refuses
    every address for a named site, and this covers a fetch asked of an
    address. Staying on the site and on https are what the href checks of
    the extractors exist for, so a redirect holds to them too. The target
    goes to the debug log only, since it can be a LAN appliance or a
    captive portal and the message lands in last_error and the diagnostics
    dump.
    """
    try:
        parsed = URL(location)
        target = parsed if parsed.scheme else current.join(parsed)
    except ValueError:
        raise ExtractorError(f"{url} redirected to an address that does not parse") from None
    if target.scheme not in ("http", "https") or (
        current.scheme == "https" and target.scheme != "https"
    ):
        _LOGGER.debug("%s redirected to %s", url, target)
        raise ExtractorError(f"{url} redirected off https; refusing to follow it")
    host = target.host or ""
    if _site(host) != _site(URL(url).host or ""):
        _LOGGER.debug("%s redirected to %s", url, target)
        raise ExtractorError(f"{url} redirected off-site; refusing to follow it")
    if _local_address(host):
        _LOGGER.debug("%s redirected to %s", url, target)
        raise ExtractorError(f"{url} redirected to a private address; refusing to follow it")
    return target


def _guard_moved(url: str, final: URL) -> None:
    """Refuse the home page or not-found page a redirect from ``url`` ended on.

    Every hop stayed on the site, so the path is safe to name. Read as a
    card, that page failed the parser, which then took the blame for a page
    the utility had moved.
    """
    if _NOT_FOUND_PATH.search(final.path) or (
        _HOME_PATH.match(final.path) and not _HOME_PATH.match(URL(url).path or "/")
    ):
        raise ExtractorError(f"{url} redirected to {final.path}: the page moved or was removed")


@asynccontextmanager
async def _get(
    session: aiohttp.ClientSession, url: str, *, timeout: float, **kwargs: Any
) -> AsyncIterator[aiohttp.ClientResponse]:
    """GET ``url`` and hold its 2xx answer, following redirects by hand.

    aiohttp follows a redirect before its caller sees the answer, so a
    check on the URL it landed on came after the request to the target
    had been sent: a host this integration fetches could have Home
    Assistant send a GET, with a path and query of its choosing, to any
    address on its network. Here each Location is checked by
    :func:`_redirect_target` before anything is sent to it, at most
    :data:`MAX_REDIRECTS` of them, all within the one ``timeout`` in
    seconds. An answer outside 2xx raises :func:`_http_error`, a 3xx with
    no Location included, and a redirect onto a moved page raises too.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    target: str | URL = url
    for hops in range(MAX_REDIRECTS + 1):
        left = deadline - loop.time()
        if left <= 0:
            raise TimeoutError
        async with session.get(
            target, allow_redirects=False, timeout=aiohttp.ClientTimeout(total=left), **kwargs
        ) as resp:
            location = None
            if resp.status in _REDIRECT_STATUSES:
                location = resp.headers.get("Location") or resp.headers.get("URI")
            if location is None:
                if not 200 <= resp.status < 300:
                    raise _http_error(url, resp.status)
                if hops:
                    _guard_moved(url, URL(target))
                yield resp
                return
        target = _redirect_target(url, URL(target), location)
    raise ExtractorError(f"{url} redirected more than {MAX_REDIRECTS} times; refusing to follow it")


# The codecs a text body is decoded with. Every page and answer declared
# UTF-8 or no charset at all, which is read as UTF-8, when each utility
# and the archive were fetched on 6 November 2026; the other three are
# the labels an older page uses instead. A label counts by the codec it
# names, so "UTF8" and "windows-1252" pass with the canonical spellings.
# Decoded with whatever a server declared, "punycode" took time quadratic
# in the body, on the event loop: a few hundred kilobytes froze Home
# Assistant for seconds.
_TEXT_CODECS = frozenset({"utf-8", "iso8859-1", "cp1252", "ascii"})


def _text_codec(charset: str | None) -> str:
    """The codec for a body declared as ``charset``, UTF-8 for any other."""
    try:
        name = codecs.lookup(charset or "utf-8").name
    except (LookupError, ValueError):
        # An unknown label (a typo, or a vendor token like "utf8mb4"), or
        # one with a NUL in it.
        return "utf-8"
    return name if name in _TEXT_CODECS else "utf-8"


async def _read_text_capped(resp: aiohttp.ClientResponse, url: str) -> str:
    """Read a text body under the size cap, decoding leniently.

    Streams via :func:`_read_capped` so a chunked or Content-Length-less
    body cannot blow past the cap (``resp.text()`` reads unbounded), and
    decodes with the declared charset when it is one of
    :data:`_TEXT_CODECS` and as UTF-8 otherwise, with ``errors="replace"``
    so a charset-mislabelled page yields a parseable string instead of
    raising an uncaught ``UnicodeDecodeError``.
    """
    payload = await _read_capped(resp, url)
    return payload.decode(_text_codec(resp.charset), errors="replace")


_UTF8_BOM = b"\xef\xbb\xbf"


def _strip_bom(payload: bytes) -> bytes:
    """Drop a leading UTF-8 BOM, or blank line, in front of %PDF.

    pdfplumber looks for %PDF at byte zero, so the BOM has
    to come off rather than merely be tolerated: accepting it and then
    handing the reader bytes it cannot open turned a clear "this is not
    a PDF" into an empty extraction with no error at all. A newline
    ahead of the signature, which some servers emit, is the same case.
    """
    if payload.startswith(_UTF8_BOM):
        payload = payload[len(_UTF8_BOM) :]
    return payload.lstrip(b"\r\n\t ")


def _is_pdf_payload(payload: bytes) -> bool:
    """Return True if the bytes look like a PDF.

    PDFs start with ``%PDF``; some publishers prepend a UTF-8 BOM.
    """
    return _strip_bom(payload).startswith(b"%PDF")


def guard_pdf_streams(payload: bytes) -> None:
    """Run the stream guard in this process, a refusal as an
    :class:`ExtractorError`. Home Assistant never does: the reader child runs
    it on every card it reads (:func:`extract_pdf_text_layout`). This is the
    same check for the tests and for a tool that wants it without the
    reader."""
    try:
        _pdf_guard.guard_pdf_streams(payload)
    except _pdf_guard.Refused as err:
        raise ExtractorError(str(err)) from None


# What the PDF reader may allocate, and how long it may take. pdfplumber
# runs in a child process held to these, since the stream guard cannot
# follow pdfminer's parser everywhere and a bomb it misses would otherwise
# be inflated in Home Assistant's own memory. RLIMIT_DATA, not RLIMIT_AS,
# is the limit: Linux counts the heap and anonymous mappings against it
# since 4.7, and it is near what the reader really uses. That holds only
# for the allocator the child starts with: Home Assistant OS and Container
# export PYTHONMALLOC=mimalloc, which reserves a 1 GiB arena at start that
# Linux counts against the limit before the reader has read a byte, so the
# child is pinned to the C library's malloc whatever this process runs
# under. Measured that way on the three cards on file and on a 2 MB card
# with eight pages of tables, the smallest limit each one still reads
# under was 28, 28, 52 and 56 MiB, so the ceiling leaves several times the
# largest. The time limit leaves a minute of the fetch budget for the
# downloads ahead of the parse; the Pidpa card, the slowest on file,
# reads in 17 s on a Raspberry Pi 4 under that malloc.
PDF_READER_MEMORY_BYTES = 256 * 1024 * 1024
PDF_READER_TIMEOUT_S = FETCH_BUDGET_S - 60

# The child reads the PDF on stdin, runs the stream guard on it, loaded
# from the file named on its command line, and writes its text on stdout.
# A card the guard refuses ends it with GUARD_REFUSED and the refusal's
# first line on stderr; any other failure with one line naming the
# exception type and the first line of its message, never the traceback,
# which can quote the page.
# Nothing else may reach that pipe: pdfminer logs a warning for each
# operator it cannot use, quoting the operand, and with no handler set they
# go to stderr, which this process reads whole and the memory ceiling does
# not bound, so a few kilobytes of bad operators shared by many pages would
# grow Home Assistant by hundreds of megabytes. The child keeps a private
# copy of the descriptor for its one line and points its own stderr at the
# null device.
# Without the resource module it runs uncapped rather than not at all, and
# under a hard limit already below the ceiling it keeps that.
# The status the reader child ends with when the stream guard refuses the
# card, set apart from the 1 of any other failure.
GUARD_REFUSED = 4
_READER = """\
import os
import sys

report = os.fdopen(os.dup(2), "w")
null = os.open(os.devnull, os.O_WRONLY)
os.dup2(null, 2)
os.close(null)
try:
    import resource

    resource.setrlimit(resource.RLIMIT_DATA, (int(sys.argv[1]),) * 2)
except (ImportError, OSError, ValueError):
    pass
refused = None
try:
    import importlib.util

    spec = importlib.util.spec_from_file_location("pdf_guard", sys.argv[2])
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    payload = sys.stdin.buffer.read()
    try:
        guard.guard_pdf_streams(payload)
    except guard.Refused as err:
        refused = (str(err).splitlines() or ["refused"])[0]
    if refused is None:
        import io

        import pdfplumber

        with pdfplumber.open(io.BytesIO(payload)) as pdf:
            text = "\\n".join((page.dedupe_chars().extract_text() or "") for page in pdf.pages)
except BaseException as err:
    lines = str(err).splitlines()
    name = type(err).__name__
    report.write((f"{name}: {lines[0]}" if lines else name)[:200] + "\\n")
    report.flush()
    sys.exit(1)
if refused is not None:
    report.write(refused[:200] + "\\n")
    report.flush()
    sys.exit(int(sys.argv[3]))
sys.stdout.buffer.write(text.encode("utf-8", "surrogatepass"))
"""


def extract_pdf_text_layout(payload: bytes) -> str:
    """Extract PDF text via pdfplumber, preserving table layout.

    The reader runs in a child process under :data:`PDF_READER_MEMORY_BYTES`
    and :data:`PDF_READER_TIMEOUT_S`, so a stream the guard let through
    that inflates without end costs the child, not Home Assistant, and a
    card that keeps the reader busy is killed rather than holding a thread
    for as long as it likes. The time limit is per parse and shorter than
    the fetch budget, but an extractor that falls back to last year's card
    after a timeout starts a second parse, which can still be running when
    the budget gives up on the fetch. The child runs the stream guard
    first, from :mod:`._pdf_guard`, so none of the guard's scans hold this
    process's interpreter, and a card it refuses gets the guard's own
    message. The child imports pdfplumber from wherever this process found
    it, Home Assistant's deps directory included, through PYTHONPATH, and
    never from its working directory.
    """
    payload = _strip_bom(payload)
    # Home Assistant runs from its config directory and starts with -P so
    # that a stray module there cannot shadow an import. The child is
    # started the same way, and an empty or "." entry in the path handed
    # down would put that directory back, so those are left out.
    path = os.pathsep.join(entry for entry in sys.path if entry not in ("", "."))
    try:
        child = subprocess.run(
            [
                sys.executable,
                "-P",
                "-c",
                _READER,
                str(PDF_READER_MEMORY_BYTES),
                _pdf_guard.__file__,
                str(GUARD_REFUSED),
            ],
            input=payload,
            capture_output=True,
            timeout=PDF_READER_TIMEOUT_S,
            env={**os.environ, "PYTHONPATH": path, "PYTHONMALLOC": "malloc"},
            check=False,
        )
    except subprocess.TimeoutExpired as err:
        raise ExtractorError(
            f"the PDF took longer than {PDF_READER_TIMEOUT_S} s to read; gave up on it"
        ) from err
    except (OSError, ValueError) as err:
        raise ExtractorError(f"could not start the PDF reader: {error_text(err)}") from err
    if child.returncode < 0:
        raise ExtractorError(f"the PDF reader was killed by signal {-child.returncode}")
    if child.returncode:
        lines = child.stderr.decode("utf-8", "replace").strip().splitlines()
        if child.returncode == GUARD_REFUSED and lines:
            raise ExtractorError(lines[-1])
        reason = lines[-1] if lines else f"exit status {child.returncode}"
        raise ExtractorError(f"PDF layout parse error: {reason}")
    text = child.stdout.decode("utf-8", "surrogatepass")
    if not text.strip():
        # A PDF with no text layer, or none we can reach. Returning ""
        # sends the parser off to fail on a missing row, which points
        # the maintainer at the regex rather than at the document.
        raise ExtractorError("PDF carried no extractable text layer")
    return text


# A memo of fetched texts, keyed by URL for a page and by ``layout\0<url>``
# for a rendered PDF, that a caller can install around a block: the card
# archiver and the live check read every utility in one walk, and the
# archiver's replay serves a stored month's texts from it with no network.
# Off by default; the coordinator wants a live read on every tick.
_TEXT_MEMO: ContextVar[dict[str, str] | None] = ContextVar("_TEXT_MEMO", default=None)


@contextmanager
def memoise_text_fetches(store: dict[str, str]) -> Iterator[None]:
    """Serve repeat reads of one URL from ``store`` inside this block."""
    token = _TEXT_MEMO.set(store)
    try:
        yield
    finally:
        _TEXT_MEMO.reset(token)


async def memoised_text(key: str, fetch: Callable[[], Awaitable[str]]) -> str:
    """``fetch`` once per ``key`` inside memoise_text_fetches, every time
    outside it. The key is the URL for a page, ``layout\0<url>`` for a
    rendered PDF and ``ocr\0<url>`` for a picture read; a provider that
    asks one endpoint for several answers (a commune in a cookie, or in a
    form field) puts what it asked for in the key, since the URL alone
    would serve the first commune's answer to every other."""
    memo = _TEXT_MEMO.get()
    if memo is not None and key in memo:
        return memo[key]
    text = await fetch()
    if memo is not None:
        memo[key] = text
    return text


# How downloaded PDF bytes become text, when someone other than the reader
# wants a say. The card archiver keys the render on the bytes' hash, so a
# card that has not changed since it was stored is neither rendered nor
# parsed again, and it keeps the bytes it has not seen before. None, the
# default everywhere in Home Assistant, renders in a worker thread.
RenderHook = Callable[[str, str, bytes, Callable[[bytes], str]], Awaitable[str]]
_RENDER_HOOK: ContextVar[RenderHook | None] = ContextVar("_RENDER_HOOK", default=None)


@contextmanager
def render_through(hook: RenderHook) -> Iterator[None]:
    """Route every PDF render inside this block through ``hook``."""
    token = _RENDER_HOOK.set(hook)
    try:
        yield
    finally:
        _RENDER_HOOK.reset(token)


async def render_pdf(variant: str, url: str, payload: bytes, render: Callable[[bytes], str]) -> str:
    """Turn validated PDF bytes into text, through the render hook when one
    is installed and in a worker thread otherwise. A provider that receives
    a card some other way than through the reader must call this too, or
    the archiver never sees the bytes."""
    hook = _RENDER_HOOK.get()
    if hook is None:
        return await asyncio.to_thread(render, payload)
    return await hook(variant, url, payload, render)


async def fetch_pdf_text_layout(session: aiohttp.ClientSession, url: str) -> str:
    """Download ``url`` and return its text with the table layout kept."""

    async def read() -> str:
        try:
            async with _get(session, url, headers={"User-Agent": USER_AGENT}, timeout=30) as resp:
                content_type = resp.content_type
                payload = await _read_capped(resp, url)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TransientFetchError(f"network error fetching {url}: {error_text(err)}") from err
        if not _is_pdf_payload(payload):
            # The content type, not the first bytes: whatever answered is not
            # the tariff card, and quoting it put a stranger's page into a
            # sensor attribute and the diagnostics dump.
            raise ExtractorError(f"the answer from {url} ({content_type}) has no PDF signature")
        return await render_pdf("layout", url, payload, extract_pdf_text_layout)

    return await memoised_text(f"layout\0{url}", read)


# A card some utility publishes only as a picture. Nothing in Home Assistant
# decodes one: the card archiver reads it with an OCR engine and keeps the
# text, and an installation reads that text back from the archive. The
# reader is installed by the archiver and the scripts that walk with it;
# None, the default everywhere in Home Assistant, means ask the archive.
PictureReader = Callable[[str, bytes], Awaitable[str]]
_PICTURE_READER: ContextVar[PictureReader | None] = ContextVar("_PICTURE_READER", default=None)
# Pictures read and refused inside a walk, for the archiver to report.
_UNREAD_PICTURES: ContextVar[list[str] | None] = ContextVar("_UNREAD_PICTURES", default=None)
# What a JPEG and a PNG start with; anything else is not the card.
_PICTURE_SIGNATURES = (b"\xff\xd8\xff", b"\x89PNG\r\n\x1a\n")


@contextmanager
def read_pictures_through(reader: PictureReader) -> Iterator[None]:
    """Route every picture inside this block through ``reader``."""
    token = _PICTURE_READER.set(reader)
    try:
        yield
    finally:
        _PICTURE_READER.reset(token)


def reads_pictures() -> bool:
    """Whether a picture reader is installed here."""
    return _PICTURE_READER.get() is not None


@contextmanager
def collect_unread_pictures(store: list[str]) -> Iterator[None]:
    """Collect into ``store`` what :func:`note_unread_picture` reports here."""
    token = _UNREAD_PICTURES.set(store)
    try:
        yield
    finally:
        _UNREAD_PICTURES.reset(token)


def note_unread_picture(message: str) -> None:
    """Report a picture whose reading was refused, when someone collects."""
    store = _UNREAD_PICTURES.get()
    if store is not None:
        store.append(message)


async def fetch_picture_text(
    session: aiohttp.ClientSession,
    url: str,
    archived: Callable[[], Awaitable[str | None]],
) -> str | None:
    """The text of the card pictured at ``url``.

    A text already read in this walk, or seeded by a replay, is served as
    it is. With a picture reader installed the picture is downloaded and
    handed to it; without one, ``archived`` gives the archive's reading,
    or None when the archive has none.
    """
    key = f"ocr\0{url}"
    memo = _TEXT_MEMO.get()
    if memo is not None and key in memo:
        return memo[key]
    reader = _PICTURE_READER.get()
    if reader is None:
        return await archived()
    installed = reader

    async def read() -> str:
        try:
            async with _get(session, url, headers={"User-Agent": USER_AGENT}, timeout=30) as resp:
                content_type = resp.content_type
                payload = await _read_capped(resp, url)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TransientFetchError(f"network error fetching {url}: {error_text(err)}") from err
        if not payload.startswith(_PICTURE_SIGNATURES):
            raise ExtractorError(f"the answer from {url} ({content_type}) is not a JPEG or a PNG")
        try:
            return await installed(url, payload)
        except ExtractorError:
            raise
        except Exception as err:  # an OCR engine fails in ways of its own
            raise ExtractorError(f"could not read the picture at {url}: {error_text(err)}") from err

    return await memoised_text(key, read)


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

    async def read() -> str:
        # Off for every hop when it is off, as it was for every redirect
        # aiohttp followed.
        tls: dict[str, Any] = {} if verify_ssl else {"ssl": False}
        try:
            async with _get(
                session, url, headers={"User-Agent": USER_AGENT}, timeout=timeout, **tls
            ) as resp:
                return await _read_text_capped(resp, url)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise TransientFetchError(f"network error fetching {url}: {error_text(err)}") from err

    return await memoised_text(url, read)


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


# What a Dutch tariff card says it applies from: "Geldig vanaf 1 januari
# 2026" on Water-link's, "Overzicht tarieven per 1 januari 2026" on
# Aquaduin's. Both used to be dated from the file name or the page URL
# alone, so a link left pointing at an older card was served as this
# year's and never looked stale.
_CARD_YEAR_RE = re.compile(
    r"(?:geldig\s+vanaf|tarieven\s+per)\s+1\s+januari\s+(20\d\d)",
    re.IGNORECASE,
)


def stated_card_year(text: str) -> int | None:
    """The year a Dutch card states it applies from, if it states one."""
    match = _CARD_YEAR_RE.search(text)
    return int(match.group(1)) if match is not None else None


def to_float(text: str) -> float:
    """Parse a Belgian / French decimal number ('15,93' or '0.102').

    Strips every Unicode space variant Belgian publications use as a
    thousands separator or unit padder before swapping the comma for a
    decimal point.
    """
    cleaned = text.strip()
    for sep in _NUMERIC_SEPARATORS:
        cleaned = cleaned.replace(sep, "")
    value = float(cleaned.replace(",", "."))
    if not math.isfinite(value):
        # A run of a few hundred digits parses to infinity, which then
        # passes every ratio check (inf / inf is nan, and nan compares
        # false) and reaches sensors that refuse to publish it.
        raise ExtractorError(f"{text.strip()!r} is not a finite number")
    return value
