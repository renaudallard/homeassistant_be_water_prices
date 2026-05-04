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

"""Tests for the small Walloon intercommunales (IEG, AIEM, AIEC, CIESAC, IDEN)."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._walloon_simple import parse_cvd
from custom_components.be_water_prices.providers.aiec import EXTRACTOR as AIEC
from custom_components.be_water_prices.providers.aiec import parse_tariff as parse_aiec
from custom_components.be_water_prices.providers.aiem import EXTRACTOR as AIEM
from custom_components.be_water_prices.providers.aiem import parse_tariff as parse_aiem
from custom_components.be_water_prices.providers.ciesac import EXTRACTOR as CIESAC
from custom_components.be_water_prices.providers.ciesac import parse_tariff as parse_ciesac
from custom_components.be_water_prices.providers.iden import EXTRACTOR as IDEN
from custom_components.be_water_prices.providers.iden import parse_tariff as parse_iden
from custom_components.be_water_prices.providers.ieg import EXTRACTOR as IEG
from custom_components.be_water_prices.providers.ieg import parse_tariff as parse_ieg
from tests import fixture_html


def test_extractor_metadata_set_on_each() -> None:
    for extractor, expected_id, expected_label in [
        (IEG, "ieg", "IEG"),
        (AIEM, "aiem", "AIEM"),
        (AIEC, "aiec", "AIEC"),
        (CIESAC, "ciesac", "CIESAC"),
        (IDEN, "iden", "IDEN"),
    ]:
        assert extractor.id == expected_id
        assert extractor.label == expected_label
        assert extractor.region == "wallonia"


def test_each_utility_parses_its_2026_cvd() -> None:
    cases = [
        (parse_ieg, "ieg_2026.html", 2.38),
        (parse_aiem, "aiem_2026.html", 2.87),
        (parse_aiec, "aiec_callmepower_2026.html", 2.46),
        (parse_ciesac, "ciesac_callmepower_2026.html", 2.9),
        (parse_iden, "iden_callmepower_2026.html", 3.555),
    ]
    for parse_fn, fixture, expected_cvd in cases:
        t = parse_fn(fixture_html(fixture), year=2026)
        assert t.cvd_eur_per_m3 == expected_cvd, fixture
        assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
        assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3
        # Redevance materialised from 20·CVD + 30·CVA.
        assert round(t.yearly_fixed_fee, 2) == round(
            20 * expected_cvd + 30 * WALLONIA_CVA_EUR_PER_M3, 2
        )
        assert t.region == "wallonia"
        assert t.valid_from.year == 2026


def test_aiem_parser_skips_the_example_value_in_the_formula_text() -> None:
    # AIEM's page spells out "0,5 x CVD (soit 1,435€)" before listing the
    # actual current value. The parser anchors on "actuelle du CVD" so
    # the example value does not win.
    t = parse_aiem(fixture_html("aiem_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 2.87
    assert t.cvd_eur_per_m3 != 1.435


def test_parse_cvd_raises_on_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_cvd("<html><body>nothing about water here</body></html>")
