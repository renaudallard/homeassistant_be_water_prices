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

"""Unit tests for the vendored PDF helpers (pure functions only)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from custom_components.be_water_prices.providers import _pdf
from custom_components.be_water_prices.providers._pdf import (
    fold_accents,
    to_float,
)
from custom_components.be_water_prices.providers.base import ExtractorError, TransientFetchError


class _FakeContent:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def iter_chunked(self, _n: int) -> AsyncIterator[bytes]:
        for chunk in self._chunks:
            yield chunk


class _FakeResp:
    def __init__(
        self, chunks: list[bytes], content_length: int | None, charset: str | None = None
    ) -> None:
        self.content = _FakeContent(chunks)
        self.content_length = content_length
        self.charset = charset


def test_to_float_handles_belgian_comma() -> None:
    assert to_float("15,93") == 15.93
    assert to_float("0.102") == 0.102


@pytest.mark.parametrize("digits", ["9" * 309, "1" + "0" * 400 + ",5"])
def test_to_float_refuses_a_number_past_the_double_range(digits: str) -> None:
    """Infinity passed the Flemish 2x checks and reached the sensors."""
    with pytest.raises(ExtractorError, match="finite"):
        to_float(digits)


def test_to_float_strips_unicode_separators() -> None:
    # NBSP-separated thousands: Belgian PDFs use this for "5 029" etc.
    assert to_float("5 029,5") == 5029.5


def test_fold_accents_lowercases_and_strips_diacritics() -> None:
    assert fold_accents("Août 2026") == "aout 2026"
    assert fold_accents("Décembre") == "decembre"


def test_guard_content_length_rejects_declared_oversize() -> None:
    resp = _FakeResp([], content_length=_pdf.MAX_RESPONSE_BYTES + 1)
    with pytest.raises(ExtractorError):
        _pdf._guard_content_length(resp, "https://example.test")  # type: ignore[arg-type]


def test_guard_content_length_allows_unknown_or_small() -> None:
    _pdf._guard_content_length(_FakeResp([], content_length=None), "u")  # type: ignore[arg-type]
    _pdf._guard_content_length(_FakeResp([], content_length=1024), "u")  # type: ignore[arg-type]


async def test_read_capped_rejects_oversized_body(monkeypatch: Any) -> None:
    # A lying / absent Content-Length must still be caught on the bytes read.
    monkeypatch.setattr(_pdf, "MAX_RESPONSE_BYTES", 10)
    resp = _FakeResp([b"x" * 6, b"y" * 6], content_length=None)
    with pytest.raises(ExtractorError):
        await _pdf._read_capped(resp, "https://example.test")  # type: ignore[arg-type]


async def test_read_capped_returns_small_body() -> None:
    resp = _FakeResp([b"%PDF", b"-1.7 rest"], content_length=13)
    assert await _pdf._read_capped(resp, "u") == b"%PDF-1.7 rest"  # type: ignore[arg-type]


async def test_read_text_capped_decodes_leniently() -> None:
    # 0xE9 is Latin-1 'é'; a body mislabelled as UTF-8 must not raise.
    resp = _FakeResp([b"caf\xe9 75,00 euro"], content_length=14, charset="utf-8")
    text = await _pdf._read_text_capped(resp, "u")  # type: ignore[arg-type]
    # ASCII content survives; the bad byte is replaced, not fatal.
    assert "75,00 euro" in text


async def test_read_text_capped_uses_declared_charset() -> None:
    resp = _FakeResp(["café".encode("latin-1")], content_length=4, charset="latin-1")
    text = await _pdf._read_text_capped(resp, "u")  # type: ignore[arg-type]
    assert text == "café"


async def test_read_text_capped_falls_back_on_unknown_charset() -> None:
    # An unrecognized charset label (vendor token / typo) must not raise a
    # LookupError; fall back to UTF-8 rather than escaping unclassified.
    resp = _FakeResp([b"75,00 euro"], content_length=10, charset="utf8mb4")
    text = await _pdf._read_text_capped(resp, "u")  # type: ignore[arg-type]
    assert "75,00 euro" in text


def test_a_bom_prefixed_pdf_is_read_rather_than_silently_empty() -> None:
    """The BOM was accepted and then handed to a reader that cannot skip it.

    pdfplumber looks for %PDF at byte zero, so tolerating
    the prefix without removing it turned a clear "not a PDF" into an
    empty extraction and a parser failure blamed on the regex.
    """
    from custom_components.be_water_prices.providers._pdf import (
        _is_pdf_payload,
        _strip_bom,
        extract_pdf_text_layout,
    )
    from tests import fixture_bytes

    real = fixture_bytes("aquaduin_2026.pdf")
    with_bom = b"\xef\xbb\xbf" + real
    assert _is_pdf_payload(with_bom)
    assert _strip_bom(with_bom) == real
    assert extract_pdf_text_layout(with_bom) == extract_pdf_text_layout(real)


def test_a_blank_line_ahead_of_the_signature_is_not_a_refusal() -> None:
    """Some servers emit a newline before %PDF; pdfplumber reads the file once it is gone."""
    from tests import fixture_bytes

    assert _pdf._is_pdf_payload(b"\r\n%PDF-1.4")
    text = _pdf.extract_pdf_text_layout(b"\n" + fixture_bytes("aquaduin_2026.pdf"))
    assert "Tarieven" in text or "tarieven" in text


def test_a_pdf_with_no_text_layer_says_so() -> None:
    """An empty extraction has to be an error, not an empty string."""
    import pytest

    from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
    from custom_components.be_water_prices.providers.base import ExtractorError

    blank = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
        b"trailer<</Root 1 0 R>>"
    )
    with pytest.raises(ExtractorError):
        extract_pdf_text_layout(blank)


def test_an_argless_exception_still_produces_a_message() -> None:
    """str() of an argless exception is "", which truncates the message.

    That message reaches users through the last_error attribute and the
    stale-snapshot Repair card, where it ended at the colon.
    """
    from custom_components.be_water_prices.providers._pdf import error_text

    assert error_text(TimeoutError()) == "TimeoutError"
    assert error_text(ValueError("boom")) == "boom"


class _RedirectedResp(_FakeResp):
    """A 200 that arrived through a redirect."""

    def __init__(self, final: str, body: bytes = b"hello") -> None:
        from yarl import URL

        super().__init__([body], content_length=len(body))
        self.status = 200
        self.url = URL(final)
        self.history = (object(),)
        self.content_type = "text/plain"


class _StatusResp(_FakeResp):
    """A direct answer with the given status."""

    def __init__(self, status: int, body: bytes = b"<html>") -> None:
        super().__init__([body], content_length=len(body))
        self.status = status
        self.history = ()
        self.content_type = "text/html"


class _FakeSession:
    def __init__(self, resp: _FakeResp) -> None:
        self._resp = resp

    def get(self, *_a: object, **_k: object) -> _FakeSession:
        return self

    async def __aenter__(self) -> _RedirectedResp:
        return self._resp

    async def __aexit__(self, *_a: object) -> None:
        return None


async def test_a_redirect_off_the_requested_site_is_refused(caplog: Any) -> None:
    """The href checks validate the link, aiohttp then follows a 30x anywhere.

    The target stays out of the message, which lands in last_error and
    the diagnostics dump; a LAN appliance or a captive portal is nobody's
    business there. An https target on another site is the case the
    https check cannot catch.
    """
    session = _FakeSession(_RedirectedResp("http://127.0.0.1:8123/admin"))
    with pytest.raises(ExtractorError, match="redirected off https") as err:
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]
    assert "127.0.0.1" not in str(err.value)
    with pytest.raises(ExtractorError, match="redirected"):
        await _pdf.fetch_pdf_text_layout(session, "https://water-link.be/x.pdf")  # type: ignore[arg-type]
    session = _FakeSession(_RedirectedResp("https://evil.test/x"))
    with pytest.raises(ExtractorError, match="redirected off-site") as err:
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]
    assert "evil.test" not in str(err.value)
    assert "evil.test" in caplog.text


async def test_a_redirect_that_drops_https_is_refused() -> None:
    session = _FakeSession(_RedirectedResp("http://water-link.be/x"))
    with pytest.raises(ExtractorError, match="redirected off https"):
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]


async def test_a_redirect_within_the_site_is_followed() -> None:
    session = _FakeSession(_RedirectedResp("https://www.water-link.be/x"))
    assert await _pdf.fetch_text(session, "https://water-link.be/x") == "hello"  # type: ignore[arg-type]


@pytest.mark.parametrize("status", [300, 304, 404])
async def test_an_answer_outside_2xx_is_an_http_error(status: int) -> None:
    """A 3xx the client did not follow is a moved page, not a body to parse."""
    for fetch in (_pdf.fetch_text, _pdf.fetch_pdf_text_layout):
        session = _FakeSession(_StatusResp(status))
        with pytest.raises(ExtractorError, match=f"HTTP {status}") as err:
            await fetch(session, "https://water-link.be/x")  # type: ignore[arg-type]
        assert not isinstance(err.value, TransientFetchError)


async def test_a_5xx_on_the_pdf_fetch_is_transient() -> None:
    session = _FakeSession(_StatusResp(503))
    with pytest.raises(TransientFetchError, match="HTTP 503"):
        await _pdf.fetch_pdf_text_layout(session, "https://water-link.be/x.pdf")  # type: ignore[arg-type]


async def test_a_non_pdf_answer_is_named_by_type_not_quoted() -> None:
    """The first bytes of a stranger's page must not reach last_error."""
    resp = _RedirectedResp("https://water-link.be/x.pdf", body=b"INTERNAL token=abc")
    resp.history = ()
    with pytest.raises(ExtractorError) as err:
        await _pdf.fetch_pdf_text_layout(_FakeSession(resp), "https://water-link.be/x.pdf")  # type: ignore[arg-type]
    assert "token=abc" not in str(err.value)
    assert "text/plain" in str(err.value)


def _pdf_with_stream(dictionary: bytes, body: bytes, keyword: bytes = b"stream\n") -> bytes:
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        dictionary + keyword + body + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    for index, obj in enumerate(objs, 1):
        out += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
    out += b"trailer<</Root 1 0 R>>\n%%EOF\n"
    return bytes(out)


def test_a_deflate_bomb_is_refused_before_pdfminer_inflates_it() -> None:
    """Seventy megabytes of spaces travel as seventy kilobytes."""
    import zlib

    body = zlib.compress(b"BT /F1 12 Tf 10 100 Td (hello) Tj ET\n" + b" " * (70 << 20), 9)
    payload = _pdf_with_stream(b"<</Length %d/Filter/FlateDecode>>" % len(body), body)
    assert len(payload) < 200_000
    with pytest.raises(ExtractorError, match="inflate past"):
        _pdf.extract_pdf_text_layout(payload)


def test_a_filter_chain_is_refused() -> None:
    """A doubly-deflated stream is invisible to a single inflate pass."""
    import zlib

    body = zlib.compress(zlib.compress(b"BT (hello) Tj ET", 9), 9)
    payload = _pdf_with_stream(b"<</Length %d/Filter[/FlateDecode/FlateDecode]>>" % len(body), body)
    with pytest.raises(ExtractorError, match="filter chain"):
        _pdf.extract_pdf_text_layout(payload)


def test_an_unexpected_filter_is_refused() -> None:
    payload = _pdf_with_stream(b"<</Length 5/Filter/ASCII85Decode>>", b"87cUR")
    with pytest.raises(ExtractorError, match="ASCII85Decode"):
        _pdf.extract_pdf_text_layout(payload)


def _stored_block(raw: bytes) -> bytes:
    """One stored deflate block: ``raw`` travels verbatim, "endstream" included."""
    size = len(raw)
    return b"\x00" + size.to_bytes(2, "little") + (0xFFFF ^ size).to_bytes(2, "little") + raw


def _pdf_with_xref(
    dictionary: bytes,
    body: bytes,
    keyword: bytes,
    trailer: bytes = b"",
    extra: tuple[bytes, ...] = (),
) -> bytes:
    """The same one-stream document with a cross-reference table, so pdfminer
    reads /Length like it does on a real card rather than scanning. The
    ``trailer`` bytes go into the trailer dictionary and the ``extra``
    objects are numbered from 6."""
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        dictionary + keyword + body + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
        *extra,
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for index, obj in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer<</Root 1 0 R/Size %d%s>>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objs) + 1,
        trailer,
        xref,
    )
    return bytes(out)


@pytest.mark.parametrize(
    "keyword",
    [
        b"stream\r",
        b"stream \n",
        b"stream\x0c\n",
        b"stream(\r\n",
        b"stream junk on the line\n",
    ],
)
def test_every_keyword_line_pdfminer_reads_past_is_counted(
    keyword: bytes, monkeypatch: Any
) -> None:
    """pdfminer starts the data after the first CR or LF, whatever precedes it.

    The guard once tolerated only blanks before the line end, so a form
    feed or any other byte there left the stream uncounted while pdfminer
    inflated it in full. Each shape is checked against pdfminer itself.
    """
    import io
    import zlib

    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser

    monkeypatch.setattr(_pdf, "MAX_INFLATED_BYTES", 1 << 20)
    content = b" " * (2 << 20)
    body = zlib.compress(content, 9)
    payload = _pdf_with_xref(b"<</Length %d/Filter/FlateDecode>>" % len(body), body, keyword)
    stream = PDFDocument(PDFParser(io.BytesIO(payload))).getobj(4)
    assert len(stream.get_data()) == len(content)
    with pytest.raises(ExtractorError, match="inflate past"):
        _pdf.guard_pdf_streams(payload)


def test_an_endstream_inside_the_data_does_not_end_the_count(monkeypatch: Any) -> None:
    """A stored block spells "endstream" long before the stream is done."""
    import zlib

    monkeypatch.setattr(_pdf, "MAX_INFLATED_BYTES", 1 << 20)
    content = b" " * (2 << 20)
    body = (
        b"\x78\x9c"
        + _stored_block(b"endstream ")
        + zlib.compress(content, 9)[2:-4]
        + zlib.adler32(b"endstream " + content).to_bytes(4, "big")
    )
    assert len(zlib.decompress(body)) == len(content) + 10
    payload = _pdf_with_stream(b"<</Length %d/Filter/FlateDecode>>" % len(body), body)
    with pytest.raises(ExtractorError, match="inflate past"):
        _pdf.guard_pdf_streams(payload)


def test_a_filter_held_in_another_object_is_refused() -> None:
    payload = _pdf_with_stream(b"<</Length 5/Filter 9 0 R>>", b"hello")
    with pytest.raises(ExtractorError, match="by reference"):
        _pdf.guard_pdf_streams(payload)


def _pdfminer_stream(payload: bytes) -> Any:
    """The content stream of a ``_pdf_with_xref`` document, as pdfminer reads it."""
    import io

    from pdfminer.pdfdocument import PDFDocument
    from pdfminer.pdfparser import PDFParser

    return PDFDocument(PDFParser(io.BytesIO(payload))).getobj(4)


def _filters_pdfminer_applies(payload: bytes) -> list[str]:
    return [f.name for f, _params in _pdfminer_stream(payload).get_filters()]


@pytest.mark.parametrize(
    "key", [b"/F", b"/F#69lter", b"/#46", b"/Fil#74er", b"/Fi#lter", b"/F#", b"/F# ", b"/##46"]
)
def test_every_spelling_of_the_filter_key_is_checked(key: bytes) -> None:
    """pdfminer takes /F for /Filter, reads "#xx" in a name as the byte it
    encodes and drops a "#" with no hex digit after it; the guard once knew
    only the long spelling."""
    payload = _pdf_with_xref(b"<</Length 11%s/ASCIIHexDecode>>" % key, b"48656c6c6f>", b"stream\n")
    assert _filters_pdfminer_applies(payload) == ["ASCIIHexDecode"]
    with pytest.raises(ExtractorError, match="ASCIIHexDecode"):
        _pdf.guard_pdf_streams(payload)


@pytest.mark.parametrize(
    "key", [b"/FontFile 7 0 R", b"/First 12", b"/F 4", b"/F1 5 0 R", b"/F1%c\n5 0 R"]
)
def test_names_that_only_start_like_the_filter_key_are_not_it(key: bytes) -> None:
    """A font file, an object stream's offset, an annotation's flags and a
    font named /F1 all begin with /F; none of them names a filter."""
    payload = _pdf_with_stream(b"<</Length 5%s/Filter/FlateDecode>>" % key, b"hello")
    _pdf.guard_pdf_streams(payload)


@pytest.mark.parametrize("value", [b"%c\n/ASCIIHexDecode", b" 9 %c\n0 R", b" 9 0 %c\nR"])
def test_a_comment_between_the_filter_key_and_its_value_is_refused(value: bytes) -> None:
    """pdfminer skips the comment and applies the filter behind it."""
    payload = _pdf_with_xref(b"<</Length 11/Filter%s>>" % value, b"48656c6c6f>", b"stream\n")
    if value.startswith(b"%"):
        assert _filters_pdfminer_applies(payload) == ["ASCIIHexDecode"]
    with pytest.raises(ExtractorError, match="cannot be checked"):
        _pdf.guard_pdf_streams(payload)


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (b"/F/#41SCIIHexDecode", "#41SCIIHexDecode filter"),
        (b"/Filter/#41SCIIHexDecode", "#41SCIIHexDecode filter"),
        (b"/Filter/FlateDecode 0 R/ASCIIHexDecode", "by reference"),
        (b"/F 6.0 0 R", "by reference"),
        (b"/F +6 0 R", "by reference"),
        (b"/F 6 -0 R", "by reference"),
        (b"/F 6 0.0 R", "by reference"),
        (b"/F 6 null R", "by reference"),
        (b"/F 6 /x R", "by reference"),
        (b"/F 6 /x %c\nR", "by reference"),
    ],
)
def test_a_filter_value_in_any_other_form_is_refused(value: bytes, message: str) -> None:
    """pdfminer reads an escaped filter name, and its R takes any two tokens
    before it as a reference, so the guard lets through only a plain name
    or an annotation's flags with nothing behind them that R could take."""
    payload = _pdf_with_xref(
        b"<</Length 11%s>>" % value, b"48656c6c6f>", b"stream\n", extra=(b"[/ASCIIHexDecode]",)
    )
    assert _filters_pdfminer_applies(payload) == ["ASCIIHexDecode"]
    with pytest.raises(ExtractorError, match=message):
        _pdf.guard_pdf_streams(payload)


def test_a_dictionary_with_both_filter_keys_is_refused() -> None:
    """pdfminer applies /F when both are there, whichever comes first."""
    payload = _pdf_with_xref(
        b"<</Length 11/Filter/FlateDecode/F/ASCIIHexDecode>>", b"48656c6c6f>", b"stream\n"
    )
    assert _filters_pdfminer_applies(payload) == ["ASCIIHexDecode"]
    with pytest.raises(ExtractorError, match="more than one filter"):
        _pdf.guard_pdf_streams(payload)


def test_a_filter_far_ahead_of_the_stream_keyword_is_still_read() -> None:
    """A dictionary longer than a few kilobytes once hid its own filter."""
    import zlib

    body = zlib.compress(zlib.compress(b"BT (hello) Tj ET", 9), 9)
    pad = b"A" * 8192
    payload = _pdf_with_stream(
        b"<</Filter[/FlateDecode/FlateDecode]/Length %d/Pad(%s)>>" % (len(body), pad), body
    )
    with pytest.raises(ExtractorError, match="filter chain"):
        _pdf.guard_pdf_streams(payload)


@pytest.mark.parametrize("name", [b"/Encrypt", b"/Encr#ypt"])
def test_an_encrypted_document_is_refused(name: bytes, monkeypatch: Any) -> None:
    """pdfminer opens a file with an empty user password unasked, deciphers
    its streams and inflates them, while the bytes on disk are noise to the
    guard. The file is encrypted here by hand (revision 2, RC4) so pdfminer
    is shown inflating the whole stream."""
    import hashlib
    import zlib

    from pdfminer.arcfour import Arcfour

    pad = bytes.fromhex("28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a")
    owner, docid, perms = b"\x01" * 32, b"\x02" * 16, -4
    key = hashlib.md5(pad + owner + perms.to_bytes(4, "little", signed=True) + docid).digest()[:5]
    user = Arcfour(key).encrypt(pad)
    object_key = hashlib.md5(key + (4).to_bytes(3, "little") + b"\0\0").digest()[:10]

    monkeypatch.setattr(_pdf, "MAX_INFLATED_BYTES", 1 << 20)
    content = b" " * (2 << 20)
    body = Arcfour(object_key).encrypt(zlib.compress(content, 9))
    payload = _pdf_with_xref(
        b"<</Length %d/Filter/FlateDecode>>" % len(body),
        body,
        b"stream\n",
        b"/ID[<%s><%s>]%s<</Filter/Standard/V 1/R 2/O<%s>/U<%s>/P %d>>"
        % (
            docid.hex().encode(),
            docid.hex().encode(),
            name,
            owner.hex().encode(),
            user.hex().encode(),
            perms,
        ),
    )
    assert len(_pdfminer_stream(payload).get_data()) == len(content)
    with pytest.raises(ExtractorError, match="encrypted"):
        _pdf.guard_pdf_streams(payload)


def test_an_obj_token_inside_the_dictionary_does_not_hide_the_filter() -> None:
    """The dictionary starts at "N G obj", not at the last "obj" spelled anywhere."""
    import zlib

    body = zlib.compress(zlib.compress(b"BT (hello) Tj ET", 9), 9)
    payload = _pdf_with_stream(
        b"<</Filter[/FlateDecode/FlateDecode]/K/obj/Length %d>>" % len(body), body
    )
    with pytest.raises(ExtractorError, match="filter chain"):
        _pdf.guard_pdf_streams(payload)


def test_streams_that_share_their_bytes_are_refused() -> None:
    """Every keyword starts a stream whose blocks run to the end of the file.

    Each start would inflate the whole tail again, for nothing but a few
    bytes of output, so the pass would be quadratic in the file.
    """
    unit = _stored_block(b"stream\n\x78\x9c") + _stored_block(b"") * 200
    payload = b"%PDF-1.4\nstream\n\x78\x9c" + unit * 64
    with pytest.raises(ExtractorError, match="overlap"):
        _pdf.guard_pdf_streams(payload)


def test_many_stream_keywords_are_scanned_in_linear_time() -> None:
    """A megabyte of keywords with no stream behind them, and every
    "endstream" of two hundred images, once cost a rescan of the whole
    prefix each. Neither reaches the decompressor, so ten thousand tiny
    Flate streams check that each one stops at its own end instead of
    feeding it the rest of the file."""
    import time
    import zlib

    junk = b"%PDF-1.4\n1 0 obj\n" + b"stream\n" * 150_000
    image = b"<</Type/XObject/Subtype/Image/Filter/DCTDecode/Length 65536>>stream\n"
    image += b"\xff\xd8\xff\xe0" + bytes(range(256)) * 256 + b"\nendstream\n"
    images = b"%PDF-1.4\n" + b"".join(b"%d 0 obj\n" % n + image for n in range(1, 201))
    body = zlib.compress(b"BT (x) Tj ET")
    flate = b"<</Length %d/Filter/FlateDecode>>stream\n" % len(body) + body + b"\nendstream\n"
    flates = b"%PDF-1.4\n" + b"".join(b"%d 0 obj\n" % n + flate for n in range(1, 10_001))
    started = time.perf_counter()
    _pdf.guard_pdf_streams(junk)
    _pdf.guard_pdf_streams(images)
    _pdf.guard_pdf_streams(flates)
    assert time.perf_counter() - started < 5


def test_the_real_cards_pass_the_stream_guard() -> None:
    from tests import fixture_bytes

    for name in ("aquaduin_2026.pdf", "water_link_2026.pdf", "pidpa_tariefplan_2025-2030.pdf"):
        _pdf.guard_pdf_streams(fixture_bytes(name))


def _deflated_spaces(mib: int) -> bytes:
    """A zlib stream of ``mib`` MiB of spaces, built without holding them.

    After a full flush the compressor starts afresh, so every further
    megabyte compresses to the same bytes and is repeated rather than
    compressed again; the checksum is worked out on the side.
    """
    import zlib

    chunk = b" " * (1 << 20)
    compressor = zlib.compressobj(9)
    first = compressor.compress(chunk) + compressor.flush(zlib.Z_FULL_FLUSH)
    again = compressor.compress(chunk) + compressor.flush(zlib.Z_FULL_FLUSH)
    tail = compressor.flush()[:-4]
    check = 1
    for _ in range(mib):
        check = zlib.adler32(chunk, check)
    return first + again * (mib - 1) + tail + check.to_bytes(4, "big")


def _bombs() -> list[tuple[bytes, bytes, tuple[bytes, ...]]]:
    """Streams the text pass lets through that inflate to twice the reader's
    memory ceiling: a run of R taking the checked filter off pdfminer's
    stack, the same run turning an annotation's flags into a reference, an
    "N G obj" in a string moving the dictionary's start past its filter,
    and a hex wrapping that hides the deflate header from the pass."""
    import zlib

    inner = _deflated_spaces(2 * _pdf.PDF_READER_MEMORY_BYTES >> 20)
    twice = zlib.compress(inner, 9)
    hexed = inner.hex().encode() + b">"
    return [
        (b"<</Length %d/Filter/FlateDecode/X/Y/Z R R[/FlateDecode/FlateDecode]>>", twice, ()),
        (b"<</Length %d/F 6/X/Y/Z R R>>", twice, (b"[/FlateDecode/FlateDecode]",)),
        (b"<</Filter[/FlateDecode/FlateDecode]/Length %d/X(9 0 obj)>>", twice, ()),
        (b"<</Length %d/F/FlateDecode/X/Y/Z R R[/ASCIIHexDecode/FlateDecode]>>", hexed, ()),
    ]


def test_a_bomb_the_stream_guard_misses_dies_in_the_reader_child() -> None:
    """No pattern follows pdfminer's parser everywhere, so the guard passes
    these, and each one used to be inflated in Home Assistant's own memory.
    The reader's child runs out of its ceiling instead, and this process
    never grows."""
    import resource

    for dictionary, body, extra in _bombs():
        payload = _pdf_with_xref(dictionary % len(body), body, b"stream\n", extra=extra)
        _pdf.guard_pdf_streams(payload)
        before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        with pytest.raises(ExtractorError, match="PDF layout parse error") as err:
            _pdf.extract_pdf_text_layout(payload)
        grown = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss - before
        assert "allocate" in str(err.value) or "MemoryError" in str(err.value)
        # ru_maxrss counts KiB on Linux.
        assert grown < 8 * 1024


def test_the_reader_childs_warnings_never_reach_this_process(monkeypatch: Any) -> None:
    """pdfminer logs a warning quoting the operand for each operator it
    cannot use, and with no handler those went to the child's stderr, which
    this process read whole: two megabytes from this one small page, and
    pages can share the stream. Only the child's one failure line may come
    back."""
    import subprocess
    import zlib

    content = zlib.compress((b"(" + b"A" * 10000 + b") w\n") * 200, 9)
    payload = _pdf_with_xref(
        b"<</Length %d/Filter/FlateDecode>>" % len(content), content, b"stream\n"
    )
    _pdf.guard_pdf_streams(payload)
    received: list[int] = []
    run = subprocess.run

    def recording_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        child = run(*args, **kwargs)
        received.append(len(child.stderr))
        return child

    monkeypatch.setattr(subprocess, "run", recording_run)
    with pytest.raises(ExtractorError, match="no extractable text layer"):
        _pdf.extract_pdf_text_layout(payload)
    assert received == [0]


# Each card is read twice, and the Pidpa card alone takes most of the
# suite's 30 s per test on a Raspberry Pi.
@pytest.mark.timeout(120)
@pytest.mark.parametrize(
    "name", ["aquaduin_2026.pdf", "water_link_2026.pdf", "pidpa_tariefplan_2025-2030.pdf"]
)
def test_the_real_cards_read_in_the_child_as_they_did_in_process(name: str) -> None:
    import io

    import pdfplumber

    from tests import fixture_bytes

    payload = fixture_bytes(name)
    with pdfplumber.open(io.BytesIO(payload)) as pdf:
        expected = "\n".join((p.dedupe_chars().extract_text() or "") for p in pdf.pages)
    assert _pdf.extract_pdf_text_layout(payload) == expected


def test_the_reader_child_finds_pdfplumber_where_this_process_did(monkeypatch: Any) -> None:
    """Home Assistant installs pdfplumber in a deps directory it adds to its
    own path, where a fresh interpreter would not look. A child started with
    -S has no site-packages either, so it reads the card only through the
    path handed down to it."""
    import subprocess

    from tests import fixture_bytes

    run = subprocess.run

    def run_without_site(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        return run([args[0], "-S", *args[1:]], **kwargs)

    monkeypatch.delenv("PYTHONPATH", raising=False)
    monkeypatch.setattr(subprocess, "run", run_without_site)
    assert "Overzicht tarieven" in _pdf.extract_pdf_text_layout(fixture_bytes("aquaduin_2026.pdf"))


def test_a_card_that_keeps_the_reader_busy_is_given_up_on(monkeypatch: Any) -> None:
    """The fetch budget stopped waiting, but the parse thread ran on."""
    from tests import fixture_bytes

    monkeypatch.setattr(_pdf, "PDF_READER_TIMEOUT_S", 0.01)
    with pytest.raises(ExtractorError, match=r"took longer than 0\.01 s"):
        _pdf.extract_pdf_text_layout(fixture_bytes("aquaduin_2026.pdf"))


def test_a_reader_that_cannot_start_or_dies_is_an_extractor_error(monkeypatch: Any) -> None:
    import sys

    from tests import fixture_bytes

    payload = fixture_bytes("aquaduin_2026.pdf")
    monkeypatch.setattr(_pdf, "_READER", "import os; os.abort()")
    with pytest.raises(ExtractorError, match="killed by signal"):
        _pdf.extract_pdf_text_layout(payload)
    monkeypatch.setattr(_pdf, "_READER", "raise SystemExit(3)")
    with pytest.raises(ExtractorError, match="exit status 3"):
        _pdf.extract_pdf_text_layout(payload)
    monkeypatch.setattr(sys, "executable", "/nonexistent/python")
    with pytest.raises(ExtractorError, match="could not start the PDF reader"):
        _pdf.extract_pdf_text_layout(payload)


def test_a_reader_failure_names_the_error_and_not_the_traceback() -> None:
    """Only the child's one line on stderr, the exception type and the
    first line of its message, reaches last_error."""
    with pytest.raises(ExtractorError) as err:
        _pdf.extract_pdf_text_layout(b"%PDF-1.4 garbage")
    assert str(err.value) == (
        "PDF layout parse error: PdfminerException: No /Root object! - Is this really a PDF?"
    )


async def test_read_text_capped_survives_a_codec_without_a_replace_handler() -> None:
    """idna passes the codec lookup and then refuses errors="replace"."""
    resp = _FakeResp([b"caf\xc3\xa9 75,00 euro"], content_length=None, charset="idna")
    assert await _pdf._read_text_capped(resp, "u") == "café 75,00 euro"  # type: ignore[arg-type]


class _CountingSession(_FakeSession):
    """Counts the requests it was asked for."""

    def __init__(self, resp: _FakeResp) -> None:
        super().__init__(resp)
        self.requests = 0

    def get(self, *_a: object, **_k: object) -> _CountingSession:
        self.requests += 1
        return self


def _direct(body: bytes, content_type: str) -> _RedirectedResp:
    resp = _RedirectedResp("https://water-link.be/x", body=body)
    resp.history = ()
    resp.content_type = content_type
    return resp


async def test_the_text_memo_serves_a_repeat_read_without_a_second_request() -> None:
    """Inside memoise_text_fetches a URL is fetched once and served from the
    store after that; outside the block every call asks the server."""
    session = _CountingSession(_direct(b"hello", "text/html"))
    store: dict[str, str] = {}
    with _pdf.memoise_text_fetches(store):
        assert await _pdf.fetch_text(session, "https://water-link.be/x") == "hello"  # type: ignore[arg-type]
        assert await _pdf.fetch_text(session, "https://water-link.be/x") == "hello"  # type: ignore[arg-type]
    assert session.requests == 1
    assert store == {"https://water-link.be/x": "hello"}
    assert await _pdf.fetch_text(session, "https://water-link.be/x") == "hello"  # type: ignore[arg-type]
    assert session.requests == 2


async def test_a_stored_text_is_served_without_any_request() -> None:
    """A store seeded ahead of the block answers without touching the
    session at all: how the archiver replays a month it kept."""
    session = _CountingSession(_direct(b"live", "text/html"))
    with _pdf.memoise_text_fetches({"https://water-link.be/x": "stored"}):
        assert await _pdf.fetch_text(session, "https://water-link.be/x") == "stored"  # type: ignore[arg-type]
    assert session.requests == 0


async def test_render_pdf_is_the_seam_for_bytes_that_came_another_way() -> None:
    """Bytes a provider obtained outside the reader render through the same
    hook the reader uses; without a hook they render in a thread."""
    seen: list[tuple[str, str, bytes]] = []

    async def hook(variant: str, url: str, payload: bytes, renderer: Any) -> str:
        seen.append((variant, url, payload))
        return "hooked"

    with _pdf.render_through(hook):
        assert await _pdf.render_pdf("layout", "u", b"%PDF x", lambda p: "rendered") == "hooked"
    assert seen == [("layout", "u", b"%PDF x")]
    assert await _pdf.render_pdf("layout", "u", b"%PDF x", lambda p: "rendered") == "rendered"


async def test_render_hook_sees_the_bytes_and_decides_the_text() -> None:
    """Inside render_through the PDF reader hands its validated bytes and
    its own renderer to the hook and takes the hook's text; the memo keeps
    that text under the reader's own key."""
    session = _CountingSession(_direct(b"%PDF-1.4 card", "application/pdf"))
    seen: list[tuple[str, str, bytes]] = []

    async def hook(variant: str, url: str, payload: bytes, renderer: Any) -> str:
        seen.append((variant, url, payload))
        assert renderer is _pdf.extract_pdf_text_layout
        return "from the hook"

    store: dict[str, str] = {}
    with _pdf.memoise_text_fetches(store), _pdf.render_through(hook):
        text = await _pdf.fetch_pdf_text_layout(session, "https://water-link.be/x")  # type: ignore[arg-type]
        again = await _pdf.fetch_pdf_text_layout(session, "https://water-link.be/x")  # type: ignore[arg-type]
    assert text == again == "from the hook"
    assert seen == [("layout", "https://water-link.be/x", b"%PDF-1.4 card")]
    assert session.requests == 1
    assert store == {"layout\0https://water-link.be/x": "from the hook"}
