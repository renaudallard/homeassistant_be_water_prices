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

"""Shared builder for Flemish operators publishing the integrale waterprijs.

Flanders has a uniform tariff layout: a vastrecht per wooneenheid and a
korting on it per registered resident, each split over the drinkwater,
gemeentelijke and bovengemeentelijke legs, a basis + comfort water rate,
and per-commune saneringsbijdragen (gemeentelijk + bovengemeentelijk).
Every card prints its vastrecht and korting, and each parser reads them,
along with the VAT rate its vastrecht and the VAT-inclusive twin printed
beside it give.

Operators that publish only an integrated drinkwater rate (Aquaduin)
leave the sanering arguments at their default of ``0.0``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from ..const import REGION_FLANDERS
from .base import ExtractorError, WaterTariff

# What a Flemish drinkwater rate can plausibly be, in EUR/m3 ex-VAT. The
# seven operators sit between 1.67 (Water-link) and 5.99 (Aquaduin, which
# folds its sanering into the basis), so this is wide enough to hold a
# decade of indexation and narrow enough to catch a decimal that moved or
# a cell read from the wrong column. The same window already existed for
# the Walloon CVD, and in scripts/live_check.py for every region, but the
# live check runs in CI once a day and this runs in the user's own Home
# Assistant on every fetch.
_MIN_PLAUSIBLE_RATE = 0.5
_MAX_PLAUSIBLE_RATE = 20.0

# What a Flemish vastrecht can plausibly be, in EUR a year ex-VAT: 100 on
# every card since the decree set it, so a figure a tenth or ten times
# that is a misread rather than a new tariff.
_MIN_PLAUSIBLE_VASTRECHT = 20.0
_MAX_PLAUSIBLE_VASTRECHT = 500.0


def leg_total(
    utility_id: str, year: int, label: str, legs: Sequence[float], printed: float | None = None
) -> float:
    """The sum of the three legs of a fixed charge, held to the card's own
    total where it prints one: a leg read from the wrong cell then no
    longer adds up."""
    total = sum(legs)
    if printed is not None and abs(total - printed) > 0.005:
        raise ExtractorError(
            f"{utility_id} {year}: {label} legs {' + '.join(f'{v:g}' for v in legs)} "
            f"do not add up to the {printed:g} the card prints"
        )
    return total


def build_flanders_tariff(
    *,
    utility_id: str,
    year: int,
    publication_label: str,
    source_url: str,
    basis: float,
    comfort: float,
    vastrecht: float,
    korting: float,
    vat_rate: float,
    sanering_gemeentelijk: float = 0.0,
    sanering_bovengemeentelijk: float = 0.0,
) -> WaterTariff:
    """Build a Flemish :class:`WaterTariff` from the figures its card prints.

    A rate of zero or less is not a rate: the 2x check passed 0 against
    0 and a card of nothing went to the sensors. Nor is a rate outside
    the plausibility window: a comfort rate that is exactly twice its
    basis satisfies the VMM identity whether both are right or both are
    a decimal place out. A sanering component of zero is legitimate
    (Aquaduin folds it into the basis, and a commune can levy none), a
    negative one is not. The vastrecht has to sit in its own window, and
    the korting per resident has to be above zero and no more than the
    vastrecht it is taken off.
    """
    if basis <= 0 or comfort <= 0:
        raise ExtractorError(
            f"{utility_id} {year}: drinkwater rate {basis} / {comfort} is not a price"
        )
    for rate, label in ((basis, "basis"), (comfort, "comfort")):
        if not _MIN_PLAUSIBLE_RATE <= rate <= _MAX_PLAUSIBLE_RATE:
            raise ExtractorError(
                f"{utility_id} {year}: {label} rate {rate} is outside "
                f"[{_MIN_PLAUSIBLE_RATE}, {_MAX_PLAUSIBLE_RATE}] EUR/m3"
            )
    if not _MIN_PLAUSIBLE_VASTRECHT <= vastrecht <= _MAX_PLAUSIBLE_VASTRECHT:
        raise ExtractorError(
            f"{utility_id} {year}: vastrecht {vastrecht} is outside "
            f"[{_MIN_PLAUSIBLE_VASTRECHT}, {_MAX_PLAUSIBLE_VASTRECHT}] EUR a year"
        )
    if not 0 < korting <= vastrecht:
        raise ExtractorError(
            f"{utility_id} {year}: korting {korting} per resident is not a discount "
            f"on a vastrecht of {vastrecht}"
        )
    if sanering_gemeentelijk < 0 or sanering_bovengemeentelijk < 0:
        raise ExtractorError(
            f"{utility_id} {year}: negative sanering {sanering_gemeentelijk} / "
            f"{sanering_bovengemeentelijk}"
        )
    return WaterTariff(
        utility=utility_id,
        region=REGION_FLANDERS,
        valid_from=date(year, 1, 1),
        valid_until=date(year, 12, 31),
        publication_label=publication_label,
        source_url=source_url,
        yearly_fixed_fee=vastrecht,
        yearly_fixed_fee_per_resident_discount=korting,
        basis_eur_per_m3=basis,
        comfort_eur_per_m3=comfort,
        sanering_gemeentelijk_eur_per_m3=sanering_gemeentelijk,
        sanering_bovengemeentelijk_eur_per_m3=sanering_bovengemeentelijk,
        vat_rate=vat_rate,
    )
