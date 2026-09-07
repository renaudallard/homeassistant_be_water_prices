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
from custom_components.be_water_prices.providers.base import ExtractorError


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

    pdfplumber and pypdf both look for %PDF at byte zero, so tolerating
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


class _FakeSession:
    def __init__(self, resp: _RedirectedResp) -> None:
        self._resp = resp

    def get(self, *_a: object, **_k: object) -> _FakeSession:
        return self

    async def __aenter__(self) -> _RedirectedResp:
        return self._resp

    async def __aexit__(self, *_a: object) -> None:
        return None


async def test_a_redirect_off_the_requested_site_is_refused() -> None:
    """The href checks validate the link, aiohttp then follows a 30x anywhere."""
    session = _FakeSession(_RedirectedResp("http://127.0.0.1:8123/admin"))
    with pytest.raises(ExtractorError, match="redirected"):
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]
    with pytest.raises(ExtractorError, match="redirected"):
        await _pdf.fetch_pdf_text_layout(session, "https://water-link.be/x.pdf")  # type: ignore[arg-type]


async def test_a_redirect_that_drops_https_is_refused() -> None:
    session = _FakeSession(_RedirectedResp("http://water-link.be/x"))
    with pytest.raises(ExtractorError, match="dropping https"):
        await _pdf.fetch_text(session, "https://water-link.be/x")  # type: ignore[arg-type]


async def test_a_redirect_within_the_site_is_followed() -> None:
    session = _FakeSession(_RedirectedResp("https://www.water-link.be/x"))
    assert await _pdf.fetch_text(session, "https://water-link.be/x") == "hello"  # type: ignore[arg-type]


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


@pytest.mark.parametrize("keyword", [b"stream\r", b"stream \n", b"stream"])
def test_a_bomb_behind_an_unusual_stream_keyword_is_refused(
    keyword: bytes, monkeypatch: Any
) -> None:
    """pdfminer starts the data after a bare CR, a trailing blank, or at once."""
    import zlib

    monkeypatch.setattr(_pdf, "MAX_INFLATED_BYTES", 1 << 20)
    body = zlib.compress(b" " * (2 << 20), 9)
    payload = _pdf_with_stream(b"<</Length %d/Filter/FlateDecode>>" % len(body), body, keyword)
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
    prefix each."""
    import time

    junk = b"%PDF-1.4\n1 0 obj\n" + b"stream\n" * 150_000
    image = b"<</Type/XObject/Subtype/Image/Filter/DCTDecode/Length 65536>>stream\n"
    image += b"\xff\xd8\xff\xe0" + bytes(range(256)) * 256 + b"\nendstream\n"
    images = b"%PDF-1.4\n" + b"".join(b"%d 0 obj\n" % n + image for n in range(1, 201))
    started = time.perf_counter()
    _pdf.guard_pdf_streams(junk)
    _pdf.guard_pdf_streams(images)
    assert time.perf_counter() - started < 5


def test_the_real_cards_pass_the_stream_guard() -> None:
    from tests import fixture_bytes

    for name in ("aquaduin_2026.pdf", "water_link_2026.pdf", "pidpa_tariefplan_2025-2030.pdf"):
        _pdf.guard_pdf_streams(fixture_bytes(name))


async def test_read_text_capped_survives_a_codec_without_a_replace_handler() -> None:
    """idna passes the codec lookup and then refuses errors="replace"."""
    resp = _FakeResp([b"caf\xc3\xa9 75,00 euro"], content_length=None, charset="idna")
    assert await _pdf._read_text_capped(resp, "u") == "café 75,00 euro"  # type: ignore[arg-type]
