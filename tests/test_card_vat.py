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

"""Each card is billed at the VAT rate it gives.

A Flemish card prints its vastrecht beside the same figure with VAT, a
Walloon page states its rate in words, and each test moves that on a real
card and checks the parse follows. A parser still billing the default
would read 6 %.
"""

from __future__ import annotations

import functools

import pytest

from custom_components.be_water_prices.const import DEFAULT_VAT_RATE
from custom_components.be_water_prices.providers import (
    agso_knokke,
    aquaduin,
    de_watergroep,
    farys,
    inasep,
    pidpa,
    swde,
    water_link,
)
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers._walloon_simple import parse_tariff
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    vat_from_pair,
    vat_from_percent,
)
from tests import fixture_bytes, fixture_html


@functools.cache
def _pdf(name: str) -> str:
    return extract_pdf_text_layout(fixture_bytes(name))


def _swap(text: str, old: str, new: str, count: int) -> str:
    """``text`` with every ``old`` replaced, after checking there are
    ``count`` of them: a replacement that misses is a test of nothing."""
    assert text.count(old) == count, (old, text.count(old))
    return text.replace(old, new)


def test_the_rate_is_what_the_figures_give() -> None:
    assert vat_from_pair("x", 100.0, 106.0) == 0.06
    assert vat_from_pair("x", 50.0, 60.5) == 0.21
    assert vat_from_percent("x", 6) == 0.06
    for ex, incl in ((100.0, 100.0), (100.0, 150.0), (0.0, 6.0)):
        with pytest.raises(ExtractorError):
            vat_from_pair("x", ex, incl)


def test_pidpa() -> None:
    page = _swap(fixture_html("pidpa_geel_2026.html"), "106 euro incl", "109 euro incl", 2)
    assert pidpa.parse_commune_tariff(page, commune_slug="geel", year=2026).vat_rate == 0.09


def test_farys_holds_every_pair_to_the_rate_its_vastrecht_gives() -> None:
    """Its rows print each figure with VAT too; a vastrecht twin that gives
    another rate leaves every other row disagreeing with it."""
    raw = fixture_html("farys_gent_2026.json")
    assert farys.parse_tariff(raw, year=2026).vat_rate == 0.06
    moved = _swap(raw, "\\u0026euro; 53,0000", "\\u0026euro; 54,5000", 1)
    with pytest.raises(ExtractorError, match="do not agree"):
        farys.parse_tariff(moved, year=2026)


def test_de_watergroep() -> None:
    page = _swap(fixture_html("dewatergroep_halle_2026.html"), "(incl. € 53)", "(incl. € 54,5)", 2)
    t = de_watergroep.parse_commune_tariff(page, year=2026, commune_label="Halle")
    assert t.vat_rate == 0.075


def test_water_link() -> None:
    text = _swap(_pdf("water_link_2026.pdf"), "100,0000 106,0000", "100,0000 109,0000", 1)
    assert water_link.parse_tariff(text, year=2026).vat_rate == 0.09


def test_agso_knokke() -> None:
    page = _swap(fixture_html("agso_knokke_2026.html"), "€ 106,00", "€ 109,00", 2)
    assert agso_knokke.parse_tariff(page, year=2026).vat_rate == 0.09


def test_aquaduin() -> None:
    text = _swap(_pdf("aquaduin_2026.pdf"), "50,00 euro/jaar 53,00", "50,00 euro/jaar 56,00", 1)
    assert aquaduin.parse_tariff(text, year=2026).vat_rate == 0.09


def test_a_walloon_page_is_billed_at_the_rate_it_states() -> None:
    page = _swap(fixture_html("inasep_2026.html"), "TVA : 6 %", "TVA : 9 %", 2)
    assert inasep.parse_tariff(page, year=2026).vat_rate == 0.09


def test_a_walloon_page_stating_two_rates_is_refused() -> None:
    page = fixture_html("ieg_2026.html")
    page = _swap(page, "TVA de 6%", "TVA de 6% (TVA : 21 % sur les travaux)", 3)
    with pytest.raises(ExtractorError, match="more than one VAT rate"):
        parse_tariff(
            page, utility_id="ieg", source_url="https://example.invalid/", label_prefix="X"
        )


def test_swde_states_its_rate_in_english() -> None:
    page = _swap(
        fixture_html("swde_2026.html"), "amounts to <strong>6 %", "amounts to <strong>9 %", 1
    )
    assert swde.parse_tariff(page, year=2026).vat_rate == 0.09


def test_a_page_that_states_no_rate_gets_the_default() -> None:
    """IDEN prints its three figures ex-VAT and never says at what rate."""
    from custom_components.be_water_prices.providers import iden

    page = fixture_html("iden_2026.html")
    assert iden.parse_tariff(page, year=2026).vat_rate == DEFAULT_VAT_RATE
