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

"""Every Flemish card is billed on the vastrecht and korting it prints.

Each test raises the zuivering leg's vastrecht on a real card from 20 to
25 EUR, its total from 100 to 105 where the card prints one, and checks
the parse follows. A parser still billing a constant would read 100.
"""

from __future__ import annotations

import functools

import pytest

from custom_components.be_water_prices.providers import (
    agso_knokke,
    aquaduin,
    de_watergroep,
    farys,
    pidpa,
    water_link,
)
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.base import ExtractorError
from tests import fixture_bytes, fixture_html


@functools.cache
def _pdf(name: str) -> str:
    return extract_pdf_text_layout(fixture_bytes(name))


def _swap(text: str, old: str, new: str, count: int, *, first: bool = False) -> str:
    """``text`` with every ``old`` replaced, or only the first, after
    checking there are ``count`` of them: a replacement that misses is a
    test of nothing."""
    assert text.count(old) == count, (old, text.count(old))
    return text.replace(old, new, 1 if first else -1)


def test_pidpa_page() -> None:
    page = fixture_html("pidpa_geel_2026.html")
    # The first of each is the household table's zuivering vastrecht and
    # its total; the later ones are the korting total and the eigen
    # waterwinner table.
    page = _swap(page, "20 euro excl. btw", "25 euro excl. btw", 3, first=True)
    page = _swap(page, "100 euro excl. btw", "105 euro excl. btw", 2, first=True)
    t = pidpa.parse_commune_tariff(page, commune_slug="geel", year=2026)
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (105.0, 20.0)


def test_pidpa_page_legs_that_do_not_make_the_total_are_refused() -> None:
    page = fixture_html("pidpa_geel_2026.html")
    page = _swap(page, "100 euro excl. btw", "105 euro excl. btw", 2, first=True)
    with pytest.raises(ExtractorError, match="do not add up to the 105"):
        pidpa.parse_commune_tariff(page, commune_slug="geel", year=2026)


def test_pidpa_tariefplan() -> None:
    text = _swap(
        _pdf("pidpa_tariefplan_2025-2030.pdf"), "Zuivering € 20,00", "Zuivering € 25,00", 1
    )
    t = pidpa.parse_tariff(text, year=2026)
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (105.0, 20.0)


def test_farys() -> None:
    raw = fixture_html("farys_gent_2026.json")
    raw = _swap(raw, "\\u0026euro; 20,0000", "\\u0026euro; 25,0000", 1)
    raw = _swap(raw, "\\u0026euro; 21,2000", "\\u0026euro; 26,5000", 1)
    t = farys.parse_tariff(raw, year=2026)
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (105.0, 20.0)


def test_de_watergroep() -> None:
    page = fixture_html("dewatergroep_halle_2026.html")
    page = _swap(page, "€ 20<br/>", "€ 25<br/>", 2)
    t = de_watergroep.parse_commune_tariff(page, year=2026, commune_label="Halle")
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (105.0, 20.0)


def test_de_watergroep_without_its_vastrecht_table_is_refused() -> None:
    page = fixture_html("dewatergroep_halle_2026.html")
    page = _swap(page, "Korting op vastrecht", "Korting", 2)
    with pytest.raises(ExtractorError, match="vastrecht / korting table"):
        de_watergroep.parse_commune_tariff(page, year=2026, commune_label="Halle")


def test_water_link() -> None:
    text = _swap(
        _pdf("water_link_2026.pdf"),
        "50,0000 30,0000 20,0000 100,0000 106,0000",
        "50,0000 30,0000 25,0000 105,0000 111,3000",
        1,
    )
    t = water_link.parse_tariff(text, year=2026)
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (105.0, 20.0)


def test_agso_knokke() -> None:
    page = fixture_html("agso_knokke_2026.html")
    page = _swap(page, '">€ 20,00</p>', '">€ 25,00</p>', 2)
    page = _swap(page, "<b>€   100,00</b>", "<b>€   105,00</b>", 2)
    t = agso_knokke.parse_tariff(page, year=2026)
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (105.0, 20.0)


def test_aquaduin() -> None:
    text = _swap(
        _pdf("aquaduin_2026.pdf"),
        "afvalwater (per jaar en per abonnee) 20,00",
        "afvalwater (per jaar en per abonnee) 25,00",
        1,
    )
    text = _swap(text, "20,00%", "25,00%", 1)
    t = aquaduin.parse_tariff(text, year=2026)
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (105.0, 26.25)
