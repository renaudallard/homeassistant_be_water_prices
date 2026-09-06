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
