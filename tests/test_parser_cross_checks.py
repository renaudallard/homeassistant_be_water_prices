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

import functools

import pytest

from custom_components.be_water_prices.providers import (
    agso_knokke,
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


@functools.cache
def _pdf(name: str) -> str:
    return extract_pdf_text_layout(fixture_bytes(name))


def test_aquaduin_comfort_must_be_twice_the_basis() -> None:
    text = _pdf("aquaduin_2026.pdf").replace("11,9816", "9,9999")
    with pytest.raises(ExtractorError, match="2× basistarief"):
        aquaduin.parse_tariff(text, year=2026)


def test_agso_comfort_must_be_twice_the_basis() -> None:
    """Read by position alone, a swapped basis / comfort cell shipped 2x silently."""
    page = fixture_html("agso_knokke_2026.html")
    assert page.count("€ 4,6590") == 1
    with pytest.raises(ExtractorError, match="2× basistarief"):
        agso_knokke.parse_tariff(page.replace("€ 4,6590", "€ 4,0000"), year=2026)


def test_agso_swapped_cells_do_not_ship_a_doubled_rate() -> None:
    page = fixture_html("agso_knokke_2026.html")
    assert page.count("€ 2,3295") == 1 and page.count("€ 4,6590") == 1
    swapped = (
        page.replace("€ 2,3295", "€ X").replace("€ 4,6590", "€ 2,3295").replace("€ X", "€ 4,6590")
    )
    with pytest.raises(ExtractorError, match="2× basistarief"):
        agso_knokke.parse_tariff(swapped, year=2026)


def test_farys_comfort_must_be_twice_the_basis() -> None:
    # The VAT-inclusive figure moves with the rate, so only the 2x rule is broken.
    raw = fixture_html("farys_gent_2026.json")
    assert raw.count("6,0116") == 1 and raw.count("6,3723") == 1
    raw = raw.replace("6,0116", "5,5000").replace("6,3723", "5,8300")
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
    # The printed total moves with the water rate, so only the 2x rule is broken.
    text = _pdf("water_link_2026.pdf")
    assert text.count("3,3384") >= 1 and text.count("9,4112") >= 1
    text = text.replace("3,3384", "3,0000").replace("9,4112", "9,0728")
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
    row below it and publish 0,0020 EUR/m3, a thousandth of the rate.

    The row is refused now rather than read as free, so what this pins is
    that the refusal is what happens: reaching past the boundary would
    hand back a plausible number instead.
    """
    page = fixture_html("dewatergroep_halle_2026.html")
    cell = '\n€ 1,9572<br /><span class="small">(incl. € 2,0746)</span>'
    assert page.count(cell) == 1, "the per-m3 Afvoer cell moved; the test needs updating"
    html = page.replace(cell, "")

    with pytest.raises(ExtractorError, match="printed no gemeentelijke saneringsbijdrage"):
        de_watergroep.parse_commune_tariff(html, year=2026, commune_label="Halle")
