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

"""AGSO Knokke-Heist extractor against the captured fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.agso_knokke import EXTRACTOR, parse_tariff
from tests import fixture_html


def test_extractor_metadata() -> None:
    assert EXTRACTOR.id == "agso_knokke"
    assert EXTRACTOR.label == "AGSO Knokke-Heist"
    assert EXTRACTOR.region == "flanders"


def test_picks_the_higher_year_table() -> None:
    # The fixture has both 2025 and 2026 tables; we pick the table with
    # the highest "Integrale waterprijs" total -- the 2026 one.
    t = parse_tariff(fixture_html("agso_knokke_2026.html"), year=2026)
    assert t.basis_eur_per_m3 == 2.3295  # drinkwater 2026
    assert t.comfort_eur_per_m3 == 2.0 * 2.3295  # VMM 2× rule
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572  # afvoer 2026
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019  # zuivering 2026
    assert t.yearly_fixed_fee == 100.0  # standard VMM 50+30+20
    assert t.yearly_fixed_fee_per_resident_discount == 20.0  # 10+6+4


def test_raises_when_table_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>")
