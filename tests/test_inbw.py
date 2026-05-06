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

"""inBW extractor against the captured 2026 fixture."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.pricing import compute_annual_cost
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.inbw import parse_tariff
from tests import fixture_html


def test_parses_2026_components() -> None:
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert t.cvd_eur_per_m3 == 2.6
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3
    # Redevance = 20·CVD + 30·CVA = 52 + 82.44 = 134.44
    assert round(t.yearly_fixed_fee, 2) == round(20 * 2.6 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)


def test_matches_published_facture_for_100_m3() -> None:
    # The inBW page itself shows 584.261 € TVAC for a 100 m³ sample bill.
    # Our cost engine has to reproduce that to the cent or we know either
    # the parser or the Wallonia tier math is off.
    t = parse_tariff(fixture_html("inbw_2026.html"), year=2026)
    assert compute_annual_cost(t, 100, 1) == 584.26


def test_raises_when_table_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>no table here</body></html>")
