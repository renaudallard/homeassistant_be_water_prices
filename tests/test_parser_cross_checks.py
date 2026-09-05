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

"""Every parser's arithmetic cross-check, with a page that breaks it.

Each of these guards exists because a published page can be edited in
one place and not another, and the result is a number that looks
perfectly ordinary. None of them was exercised by anything: removing all
nine left the suite green, because the committed fixtures are internally
consistent and a fixture-only test can never see the difference.

So each test doctors one figure and asserts the parser refuses, rather
than shipping the plausible wrong answer.
"""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import (
    aquaduin,
    de_watergroep,
    farys,
    inbw,
    pidpa,
    vivaqua,
    water_link,
)
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.base import ExtractorError
from tests import fixture_bytes, fixture_html


def _pdf(name: str) -> str:
    return extract_pdf_text_layout(fixture_bytes(name))


def test_aquaduin_comfort_must_be_twice_the_basis() -> None:
    text = _pdf("aquaduin_2026.pdf").replace("11,9816", "9,9999")
    with pytest.raises(ExtractorError, match="2× basistarief"):
        aquaduin.parse_tariff(text, year=2026)


def test_farys_comfort_must_be_twice_the_basis() -> None:
    raw = fixture_html("farys_gent_2026.json").replace("6,0116", "5,5000")
    with pytest.raises(ExtractorError, match="2× basistarief"):
        farys.parse_tariff(raw, year=2026)


def test_pidpa_pdf_comfort_must_be_twice_the_basis() -> None:
    text = _pdf("pidpa_tariefplan_2025-2030.pdf").replace("4,1696", "3,9999")
    with pytest.raises(ExtractorError, match="2× basistarief"):
        pidpa.parse_tariff(text, year=2026)


def test_pidpa_commune_comfort_must_be_twice_the_basis() -> None:
    html = fixture_html("pidpa_geel_2026.html").replace("4,3776", "4,0000")
    with pytest.raises(ExtractorError, match="2× basistarief"):
        pidpa.parse_commune_tariff(html, commune_slug="geel", year=2026)


def test_water_link_comfort_must_be_twice_the_basis() -> None:
    text = _pdf("water_link_2026.pdf").replace("3,3384", "3,0000")
    with pytest.raises(ExtractorError, match="2× basistarief"):
        water_link.parse_tariff(text, year=2026)


def test_vivaqua_variable_rows_must_reconstruct_the_total() -> None:
    html = fixture_html("vivaqua_linear_2026.html").replace("2,62", "1,50")
    with pytest.raises(ExtractorError, match="variable supply"):
        vivaqua.parse_tariff(html, year=2026)


def test_vivaqua_fixed_rows_must_reconstruct_the_total() -> None:
    html = fixture_html("vivaqua_linear_2026.html").replace("19,69", "15,00")
    with pytest.raises(ExtractorError, match="fixed supply"):
        vivaqua.parse_tariff(html, year=2026)


def test_inbw_first_block_must_be_half_the_full_cvd() -> None:
    html = fixture_html("inbw_2026.html").replace("1,300", "1,900")
    with pytest.raises(ExtractorError, match=r"0\.5×"):
        inbw.parse_tariff(html, year=2026)


def test_inbw_redevance_must_be_twenty_times_the_cvd() -> None:
    html = fixture_html("inbw_2026.html").replace("52,000", "60,000")
    with pytest.raises(ExtractorError, match="20·CVD"):
        inbw.parse_tariff(html, year=2026)


def test_de_watergroep_block_does_not_reach_into_the_per_litre_table() -> None:
    """The section boundary is the only thing keeping the units straight.

    A commune whose per-m3 Afvoer row loses its amount -- a regex
    regression, a markup tweak -- would otherwise match the per-litre
    row below it and publish 0,0020 EUR/m3 in place of 0,0.
    """
    page = fixture_html("dewatergroep_halle_2026.html")
    cell = '\n€ 1,9572<br /><span class="small">(incl. € 2,0746)</span>'
    assert page.count(cell) == 1, "the per-m3 Afvoer cell moved; the test needs updating"
    html = page.replace(cell, "")

    tariff = de_watergroep.parse_commune_tariff(html, year=2026, commune_label="Halle")
    # 0.0020 is the per-LITRE Afvoer figure from the table below.
    assert tariff.sanering_gemeentelijk_eur_per_m3 == 0.0
    assert tariff.sanering_gemeentelijk_eur_per_m3 != 0.002
