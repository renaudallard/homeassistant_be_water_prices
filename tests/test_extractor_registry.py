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

"""Smoke test that every registered extractor exposes the expected metadata."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers import EXTRACTORS, get

# (utility_id) -> (label, region). The registry is the source of truth at
# runtime; this table is what the tests compare against. Adding a new
# extractor without an entry here trips test_registry_matches_expected_set.
_EXPECTED: dict[str, tuple[str, str]] = {
    "vivaqua": ("VIVAQUA", "brussels"),
    "de_watergroep": ("De Watergroep", "flanders"),
    "pidpa": ("Pidpa", "flanders"),
    "aquaduin": ("Aquaduin", "flanders"),
    "agso_knokke": ("AGSO Knokke-Heist", "flanders"),
    "water_link": ("Water-link", "flanders"),
    "farys": ("Farys", "flanders"),
    "swde": ("SWDE", "wallonia"),
    "inbw": ("inBW", "wallonia"),
    "cile": ("CILE", "wallonia"),
    "inasep": ("INASEP", "wallonia"),
    "ieg": ("IEG", "wallonia"),
    "aiem": ("AIEM", "wallonia"),
    "aiec": ("AIEC", "wallonia"),
    "ciesac": ("CIESAC", "wallonia"),
    "iden": ("IDEN", "wallonia"),
}


@pytest.mark.parametrize("utility_id", sorted(_EXPECTED))
def test_extractor_metadata(utility_id: str) -> None:
    expected_label, expected_region = _EXPECTED[utility_id]
    extractor = get(utility_id)
    assert extractor.id == utility_id
    assert extractor.label == expected_label
    assert extractor.region == expected_region


def test_registry_matches_expected_set() -> None:
    assert set(EXTRACTORS) == set(_EXPECTED)
