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
    fields = {
        "basis": 2.0,
        "comfort": 4.0,
        "vastrecht": 100.0,
        "korting": 20.0,
        "vat_rate": 0.06,
        "sanering_gemeentelijk": 1.0,
    }
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


def test_a_rate_outside_the_plausibility_window_is_not_a_card() -> None:
    """A decimal that moved satisfies the 2x identity just as well."""
    with pytest.raises(ExtractorError, match=r"basis rate 0\.29251 is outside"):
        build_flanders_tariff(
            utility_id="x",
            year=2026,
            publication_label="x",
            source_url="https://example.invalid/",
            basis=0.29251,
            comfort=0.58502,
            vastrecht=100.0,
            korting=20.0,
            vat_rate=0.06,
        )
    # A believable basis with a comfort rate that is not.
    with pytest.raises(ExtractorError, match=r"comfort rate 29\.251 is outside"):
        build_flanders_tariff(
            utility_id="x",
            year=2026,
            publication_label="x",
            source_url="https://example.invalid/",
            basis=2.9251,
            comfort=29.251,
            vastrecht=100.0,
            korting=20.0,
            vat_rate=0.06,
        )


def test_every_real_flemish_rate_sits_inside_the_window() -> None:
    """Water-link's 1,6692 and Aquaduin's integrated 5,9908 are the extremes."""
    from custom_components.be_water_prices.providers._flanders import (
        _MAX_PLAUSIBLE_RATE,
        _MIN_PLAUSIBLE_RATE,
    )

    for basis in (1.6692, 2.1888, 2.3295, 2.9251, 3.0058, 5.9908):
        assert _MIN_PLAUSIBLE_RATE <= basis <= _MAX_PLAUSIBLE_RATE
        assert _MIN_PLAUSIBLE_RATE <= 2.0 * basis <= _MAX_PLAUSIBLE_RATE


@pytest.mark.parametrize(
    ("vastrecht", "korting", "match"),
    [
        (10.0, 2.0, r"vastrecht 10\.0 is outside"),
        (1000.0, 200.0, r"vastrecht 1000\.0 is outside"),
        (100.0, 0.0, "not a discount"),
        (100.0, -20.0, "not a discount"),
        (100.0, 200.0, "not a discount"),
    ],
)
def test_a_vastrecht_or_korting_that_is_not_one_is_refused(
    vastrecht: float, korting: float, match: str
) -> None:
    """Both are read off the card, so a cell read a decimal out or from the
    wrong column is refused rather than billed."""
    with pytest.raises(ExtractorError, match=match):
        _build(vastrecht=vastrecht, korting=korting)


def test_the_card_figures_are_what_is_billed() -> None:
    """Nothing stands in for what the card prints."""
    t = build_flanders_tariff(
        utility_id="x",
        year=2027,
        publication_label="x",
        source_url="https://example.invalid/",
        basis=2.0,
        comfort=4.0,
        vastrecht=106.0,
        korting=21.0,
        vat_rate=0.06,
    )
    assert (t.yearly_fixed_fee, t.yearly_fixed_fee_per_resident_discount) == (106.0, 21.0)


def test_the_legs_must_add_up_to_the_printed_total() -> None:
    from custom_components.be_water_prices.providers._flanders import leg_total

    assert leg_total("x", 2026, "vastrecht", (50.0, 30.0, 20.0), 100.0) == 100.0
    assert leg_total("x", 2026, "vastrecht", (50.0, 30.0, 20.0)) == 100.0
    with pytest.raises(ExtractorError, match="do not add up to the 106"):
        leg_total("x", 2026, "vastrecht", (50.0, 30.0, 20.0), 106.0)
