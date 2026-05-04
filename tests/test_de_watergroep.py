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

"""De Watergroep extractor against the captured 2026 news article."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers.de_watergroep import (
    EXTRACTOR,
    parse_tariff,
)
from tests import fixture_html


def test_extractor_metadata() -> None:
    assert EXTRACTOR.id == "de_watergroep"
    assert EXTRACTOR.label == "De Watergroep"
    assert EXTRACTOR.region == "flanders"


def test_parses_2026_basistarief() -> None:
    t = parse_tariff(fixture_html("dewatergroep_2026.html"), year=2026)
    assert t.basis_eur_per_m3 == 2.9521
    assert t.comfort_eur_per_m3 == 5.9042  # 2× basis


def test_uses_drinkwater_only_vastrecht() -> None:
    # The news article only covers the drinkwater leg, so vastrecht is
    # 50 EUR / 10 EUR-per-persoon (not the full 100/20 integrale fee).
    t = parse_tariff(fixture_html("dewatergroep_2026.html"), year=2026)
    assert t.yearly_fixed_fee == 50.0
    assert t.yearly_fixed_fee_per_resident_discount == 10.0
    # Sanering stays at 0 -- per-commune data is a v0.4 polish.
    assert t.sanering_gemeentelijk_eur_per_m3 == 0.0
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 0.0


def test_raises_on_missing_basistarief_phrase() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("<html><body>nothing here</body></html>", year=2026)
