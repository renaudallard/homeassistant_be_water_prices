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

"""The stream guard's work on one card is bounded, not only its memory.

It ran inside Home Assistant before the reader child started, and it was
linear in the file but at up to six seconds a megabyte on a Raspberry Pi:
a card of stream keywords held the interpreter for minutes. It runs in the
reader child now, and its loops are still held to a step limit there.
"""

from __future__ import annotations

import time
import zlib

import pytest

from custom_components.be_water_prices.providers import _pdf, _pdf_guard
from custom_components.be_water_prices.providers.base import ExtractorError
from tests import fixture_bytes

_LIMIT = _pdf_guard.MAX_GUARD_STEPS


def test_keywords_each_followed_by_a_zlib_header_are_refused() -> None:
    """Four megabytes of this took 23 seconds of CPU and passed: every
    keyword started a decompressor that failed at once and was counted as
    having inflated nothing."""
    payload = b"%PDF-1.4\n" + b"stream\nx\x9c" * ((4 << 20) // 9)
    with pytest.raises(ExtractorError, match="inflate past"):
        _pdf.guard_pdf_streams(payload)


@pytest.mark.parametrize(
    "payload",
    [
        b"%PDF-1.4\n1 0 obj\n" + b"stream\n" * (_LIMIT + 1),
        b"%PDF-1.4\n" + (b"stream\n" + zlib.compress(b"")) * (_LIMIT // 2 + 1),
        b"%PDF-1.4\n" + b"obj " * (_LIMIT + 1) + b"stream\nAA",
        b"%PDF-1.4\n" + (b"1 0 obj<<" + b"[]" * 2000 + b">>stream\nAA") * (_LIMIT // 4000 + 1),
        b"%PDF-1.4\n1 0 obj<</S(" + b"\\x" * (_LIMIT + 1) + b")>>stream\nAA",
        b"%PDF-1.4\n1 0 obj<<" + b"/F 1" * (_LIMIT + 1) + b">>stream\nAA",
    ],
    ids=["keywords", "empty streams", "obj tokens", "brackets", "string escapes", "flag keys"],
)
def test_every_loop_of_the_guard_counts_against_one_limit(payload: bytes) -> None:
    with pytest.raises(ExtractorError, match=f"more than {_LIMIT} steps"):
        _pdf.guard_pdf_streams(payload)


def test_a_stream_that_inflates_and_then_fails_is_counted(monkeypatch: pytest.MonkeyPatch) -> None:
    """One call into the decompressor was allowed the whole budget, so a
    bomb with a broken checksum inflated nearly that much and then failed,
    counted as nothing, and the next keyword could do it again."""
    monkeypatch.setattr(_pdf_guard, "MAX_INFLATED_BYTES", 1 << 20)
    bomb = zlib.compress(b" " * (900 << 10), 9)[:-4] + b"\0\0\0\0"
    payload = b"%PDF-1.4\n" + (b"stream\n" + bomb + b"\n") * 2
    with pytest.raises(ExtractorError, match="inflate past"):
        _pdf.guard_pdf_streams(payload)


def test_the_real_cards_take_a_few_hundred_steps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Aquaduin's card takes the most, 182."""
    monkeypatch.setattr(_pdf_guard, "MAX_GUARD_STEPS", 400)
    for name in ("aquaduin_2026.pdf", "water_link_2026.pdf", "pidpa_tariefplan_2025-2030.pdf"):
        _pdf.guard_pdf_streams(fixture_bytes(name))


def test_the_guard_runs_in_the_reader_child_and_not_here() -> None:
    """A single scan of a crafted 16 MiB card held the interpreter for
    seconds, and Home Assistant's event loop stalled for as long, whatever
    thread the guard ran in. The reader's child spends that time now, and
    the thread that asked for the text only waits for it."""
    payload = b"%PDF-1.4\n1 0 obj<<" + b"/#" * (8 << 20) + b"/Encrypt>>stream\nAA"
    started = time.thread_time()
    with pytest.raises(ExtractorError, match="PDF is encrypted"):
        _pdf.extract_pdf_text_layout(payload)
    assert time.thread_time() - started < 0.25
