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

"""The shared Flemish builder refuses a card that is not a price."""

from __future__ import annotations

import pytest

from custom_components.be_water_prices.providers._flanders import build_flanders_tariff
from custom_components.be_water_prices.providers.base import ExtractorError


def _build(**overrides: float) -> None:
    fields = {"basis": 2.0, "comfort": 4.0, "sanering_gemeentelijk": 1.0}
    fields.update(overrides)
    build_flanders_tariff(
        utility_id="farys",
        year=2026,
        publication_label="test",
        source_url="https://example.invalid/",
        sanering_bovengemeentelijk=1.5,
        **fields,
    )


@pytest.mark.parametrize("field", ["basis", "comfort"])
@pytest.mark.parametrize("value", [0.0, -2.0])
def test_a_drinkwater_rate_of_zero_or_less_is_refused(field: str, value: float) -> None:
    """0 is twice 0, so the 2x check let a card of nothing through."""
    with pytest.raises(ExtractorError, match="not a price"):
        _build(**{field: value, "comfort" if field == "basis" else "basis": value})


def test_a_negative_sanering_is_refused() -> None:
    with pytest.raises(ExtractorError, match="negative sanering"):
        _build(sanering_gemeentelijk=-0.5)


def test_a_zero_sanering_is_a_legitimate_card() -> None:
    """Aquaduin folds it into the basis and a commune can levy none."""
    _build(sanering_gemeentelijk=0.0)
