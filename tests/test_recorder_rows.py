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

"""How the daily statistics buckets are summed into a year's consumption."""

from __future__ import annotations

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.util import dt as dt_util

from custom_components.be_water_prices import coordinator as co

_ROWS = "custom_components.be_water_prices.coordinator._recorder_daily_rows"


def _row(day: date, *, change: float, state: float, total: float) -> dict[str, Any]:
    return {
        "start": dt_util.start_of_local_day(day).timestamp(),
        "change": change,
        "sum": total,
        "state": state,
    }


async def test_a_day_whose_change_is_the_whole_register_is_not_water() -> None:
    """A meter that dipped to 0 and came back shows its register as one day's change.

    Home Assistant treats the dip as a reset and adds the recovered
    reading to the sum. Billed into the year it became the high-water
    mark and pinned both sensors until January.
    """
    rows = [
        _row(date(2026, 3, 1), change=0.3, state=4000.3, total=100.3),
        _row(date(2026, 3, 2), change=4050.0, state=4050.0, total=4150.3),
        _row(date(2026, 3, 3), change=0.2, state=4050.2, total=4150.5),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        total = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 1, 1), date(2026, 3, 3))  # type: ignore[arg-type]
    assert total == 0.5


async def test_ordinary_days_are_summed_in_full() -> None:
    rows = [
        _row(date(2026, 3, 1), change=0.3, state=4000.3, total=100.3),
        _row(date(2026, 3, 2), change=0.4, state=4000.7, total=100.7),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        total = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 1, 1), date(2026, 3, 2))  # type: ignore[arg-type]
    assert round(total, 3) == 0.7


async def test_a_metered_year_carrying_the_register_as_one_day_is_not_offered() -> None:
    rows = [
        _row(date(2025, 12, 15), change=0.3, state=3000.0, total=50.0),
        _row(date(2026, 6, 1), change=3100.0, state=3100.0, total=3150.0),
        _row(date(2026, 12, 15), change=0.2, state=3100.2, total=3150.2),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await co._recorder_full_year_m3(None, "sensor.m", 2026) is None  # type: ignore[arg-type]


async def test_a_dip_that_recovers_across_midnight_nets_to_the_water_used() -> None:
    """A `total` meter that rebooted at 23:59 and came back at 00:01.

    The drop and the recovery land in two buckets. Dropping only the
    negative half billed the whole register into the year.
    """
    rows = [
        _row(date(2026, 3, 1), change=0.3, state=4000.3, total=100.3),
        _row(date(2026, 3, 2), change=-4050.0, state=0.0, total=-3949.7),
        _row(date(2026, 3, 3), change=4050.2, state=4050.2, total=100.5),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        total = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 1, 1), date(2026, 3, 3))  # type: ignore[arg-type]
    assert round(total, 3) == 0.5


async def test_a_genuine_swap_is_dropped_whole() -> None:
    rows = [
        _row(date(2026, 3, 1), change=0.3, state=4000.3, total=100.3),
        _row(date(2026, 3, 2), change=-4000.0, state=0.3, total=-3899.7),
        _row(date(2026, 3, 3), change=0.4, state=0.7, total=-3899.3),
        _row(date(2026, 3, 4), change=0.5, state=1.2, total=-3898.8),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        total = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 1, 1), date(2026, 3, 4))  # type: ignore[arg-type]
    # The drop swallows the day after it; the water from then on counts.
    assert round(total, 3) == 0.8


async def test_a_negative_day_does_not_walk_a_reset_into_the_year() -> None:
    """One -0.1 day let the whole register in: 0.5 m3 became 4050.8."""
    glitched = [
        _row(date(2026, 3, 1), change=0.3, state=4000.3, total=4000.3),
        _row(date(2026, 3, 2), change=-0.1, state=4000.2, total=4000.2),
        _row(date(2026, 3, 3), change=4050.4, state=4050.4, total=4050.4),
        _row(date(2026, 3, 4), change=0.2, state=4050.6, total=4050.6),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=glitched)):
        total = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 3, 1), date(2026, 3, 4))  # type: ignore[arg-type]
    assert total < 5.0
    assert round(total, 3) == 0.5


async def test_a_re_based_counter_does_not_bill_its_whole_register() -> None:
    """A pulse counter handed the real meter reading climbs, it does not reset."""
    rows = [
        _row(date(2026, 2, 14), change=0.3, state=45.2, total=45.2),
        _row(date(2026, 2, 15), change=1189.6, state=1234.8, total=1234.8),
        _row(date(2026, 2, 16), change=0.3, state=1235.1, total=1235.1),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        total = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 1, 1), date(2026, 2, 16))  # type: ignore[arg-type]
    assert round(total, 3) == 0.6


async def test_a_heavy_but_believable_day_is_still_counted() -> None:
    """A pool fill is real water; only a register-sized day is refused."""
    rows = [
        _row(date(2026, 6, 1), change=0.3, state=300.3, total=300.3),
        _row(date(2026, 6, 2), change=60.0, state=360.3, total=360.3),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        total = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 1, 1), date(2026, 6, 2))  # type: ignore[arg-type]
    assert round(total, 3) == 60.3


async def test_a_bucket_dated_after_the_window_is_not_billed_into_it() -> None:
    """The query comes back one day long; on 31 December that day is next year."""
    rows = [
        _row(date(2026, 3, 1), change=0.3, state=100.3, total=100.3),
        _row(date(2026, 3, 2), change=0.4, state=100.7, total=100.7),
        _row(date(2026, 3, 3), change=0.5, state=101.2, total=101.2),
    ]
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        asked = await co._recorder_ytd_m3(None, "sensor.m", date(2026, 1, 1), date(2026, 3, 2))  # type: ignore[arg-type]
    assert round(asked, 3) == 0.7
