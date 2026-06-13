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
from datetime import date
from typing import Any

import pytest

from custom_components.be_water_prices.providers import _pdf
from custom_components.be_water_prices.providers._pdf import (
    fold_accents,
    parse_valid_until,
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


def test_parse_valid_until_spelled_out_dutch() -> None:
    assert parse_valid_until("Geldig tot 30 april 2026") == date(2026, 4, 30)


def test_parse_valid_until_numeric_french() -> None:
    assert parse_valid_until("Valable jusqu'au 31/12/2026") == date(2026, 12, 31)


def test_parse_valid_until_returns_none_without_keyword() -> None:
    assert parse_valid_until("Random date 30 april 2026 unrelated") is None


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
