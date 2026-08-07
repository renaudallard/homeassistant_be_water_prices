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

"""SWDE extractor against the captured 2026 fixture."""

from __future__ import annotations

import logging

import pytest

from custom_components.be_water_prices.const import (
    WALLONIA_CVA_EUR_PER_M3,
    WALLONIA_FSE_EUR_PER_M3,
)
from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.swde import parse_tariff
from tests import fixture_html


def test_parses_2026_components() -> None:
    t = parse_tariff(fixture_html("swde_2026.html"), year=2026)

    assert t.cvd_eur_per_m3 == 3.24
    # CVA / FSE are stored from const.py (not the page) but we still verify
    # the page hadn't drifted: the parser warns on drift above 0.005 for the
    # CVA and 0.001 for the much smaller FSE, and the constants are the
    # source of truth.
    assert t.cva_eur_per_m3 == WALLONIA_CVA_EUR_PER_M3
    assert t.fse_eur_per_m3 == WALLONIA_FSE_EUR_PER_M3

    # Redevance is the regulator-defined 20·CVD + 30·CVA.
    assert round(t.yearly_fixed_fee, 2) == round(20 * 3.24 + 30 * WALLONIA_CVA_EUR_PER_M3, 2)

    assert t.utility == "swde"
    assert t.region == "wallonia"
    assert t.valid_from.year == 2026
    assert t.valid_until is not None and t.valid_until.year == 2026
    # Wallonia uses CVD-based pricing; the Flanders / Brussels rate slots
    # stay None.
    assert t.linear_eur_per_m3 is None
    assert t.basis_eur_per_m3 is None
    assert t.comfort_eur_per_m3 is None
    assert t.vat_rate == 0.06
    assert t.source_url.startswith("https://www.swde.be/")


def test_raises_when_cvd_missing() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>")


def test_fse_drift_is_reported(caplog: pytest.LogCaptureFixture) -> None:
    """A moved Fonds Social must be reported by the SWDE refresh.

    SWDE is the drift sentinel for the shared SPGE constants, but the FSE
    is only ~0.03 EUR/m3, so the default 0.005 tolerance would have let a
    15% move through unreported.
    """
    html = fixture_html("swde_2026.html").replace("€ 0.0339/m³", "€ 0.0375/m³")
    with caplog.at_level(logging.WARNING):
        parse_tariff(html, year=2026)
    assert "SWDE FSE" in caplog.text


def test_cvd_section_without_a_figure_does_not_borrow_the_cva() -> None:
    """A CVD section that stops carrying € must fail, not read the CVA.

    The section scan used to run on past its own section and into the next
    one's wrapper, so dropping the € from the CVD prose silently answered
    with the CVA from the following section, which is a plausible enough
    rate that nothing downstream would reject it.
    """
    html = fixture_html("swde_2026.html").replace("€ 3.24/m³", "EUR 3.24/m3")
    with pytest.raises(ExtractorError):
        parse_tariff(html, year=2026)
