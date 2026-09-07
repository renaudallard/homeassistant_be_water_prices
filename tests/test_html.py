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

"""Unit tests for the bs4-based HTML helpers."""

from __future__ import annotations

from custom_components.be_water_prices.providers._html import extract_amounts


def test_extract_amounts_handles_belgian_decimals() -> None:
    assert extract_amounts("Cost is € 5,35 per m³ and € 40,23 per year") == [5.35, 40.23]


def test_extract_amounts_handles_dot_decimals() -> None:
    assert extract_amounts("€ 0.102 / kWh") == [0.102]


def test_extract_amounts_reads_a_space_grouped_thousand_as_one_amount() -> None:
    # "€ 1 234,56" used to stop at the separator and report 1.0.
    assert extract_amounts("€ 1 234,56") == [1234.56]
    assert extract_amounts("€ 1 234,56") == [1234.56]
    assert extract_amounts("1 234,56 €") == [1234.56]


def test_extract_amounts_does_not_glue_a_year_onto_the_amount() -> None:
    assert extract_amounts("Tarif 2025 100,00 €") == [100.0]


def test_extract_amounts_reports_a_double_signed_amount_once() -> None:
    assert extract_amounts("€ 12,34 €") == [12.34]


def test_extract_amounts_returns_empty_for_no_match() -> None:
    assert extract_amounts("no euros here, just pesos $5.00") == []


def test_extract_amounts_keeps_a_discount_negative() -> None:
    """ "-€ 4,00" and "€ -6,00" are discounts; a dash between blanks is punctuation."""
    assert extract_amounts("Korting -€ 4,00 en € -6,00 en -7,25 €") == [-4.0, -6.0, -7.25]
    assert extract_amounts("Tarief 2025 - € 10,00") == [10.0]
