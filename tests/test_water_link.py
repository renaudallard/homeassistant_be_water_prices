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

"""Water-link extractor against the captured 2026 PDF."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import ExtractorError
from custom_components.be_water_prices.providers._pdf import extract_pdf_text_layout
from custom_components.be_water_prices.providers.water_link import EXTRACTOR, parse_tariff
from tests import fixture_bytes


def _pdf_text() -> str:
    return extract_pdf_text_layout(fixture_bytes("water_link_2026.pdf"))


def test_extractor_metadata() -> None:
    assert EXTRACTOR.id == "water_link"
    assert EXTRACTOR.label == "Water-link"
    assert EXTRACTOR.region == "flanders"


def test_parses_2026_antwerpen_default() -> None:
    t = parse_tariff(_pdf_text(), year=2026)
    assert t.basis_eur_per_m3 == 1.6692
    assert t.comfort_eur_per_m3 == 3.3384  # = 2× basis
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.3345  # Antwerpen-specific
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019
    assert t.yearly_fixed_fee == 100.0
    assert t.yearly_fixed_fee_per_resident_discount == 20.0


def test_parses_2026_ring_commune_overrides_sanering() -> None:
    # Edegem, Hove, Mortsel etc. carry the higher 1.9572 afvoer rate.
    t = parse_tariff(_pdf_text(), year=2026, commune="Edegem")
    assert t.basis_eur_per_m3 == 1.6692  # uniform
    assert t.sanering_gemeentelijk_eur_per_m3 == 1.9572  # ring rate
    assert t.sanering_bovengemeentelijk_eur_per_m3 == 1.7019  # uniform


def test_raises_when_pdf_text_is_garbage() -> None:
    with pytest.raises(ExtractorError):
        parse_tariff("nothing here", year=2026)
