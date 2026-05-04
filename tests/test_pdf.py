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

from datetime import date

from custom_components.be_water_prices.providers._pdf import (
    fold_accents,
    parse_valid_until,
    to_float,
)


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
