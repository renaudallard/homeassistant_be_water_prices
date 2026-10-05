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

"""What Home Assistant's statistics do with a meter that misreads high.

A misread just before midnight that is corrected just after it puts the
spike in one day's bucket and the correction in the next. Under a tenth
of the register the compiler lets the sum fall, and past it the
correction is a new cycle that adds the whole register. Either way the
reader has to take the spike back out of the day before, and leave that
day alone when the register falls below where it stood before.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.be_water_prices.coordinator import (
    _admitted_changes,
    _recorder_daily_rows,
    _recorder_ytd_m3,
)

_METER = "sensor.water_meter"


async def _record(
    hass: HomeAssistant, freezer: Any, state_class: str, plan: list[tuple[datetime, float]]
) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()
    attrs = {"device_class": "water", "state_class": state_class, "unit_of_measurement": "m³"}
    tz = dt_util.get_default_time_zone()
    for local, state in plan:
        # Brussels time, which is only the default once it has been set.
        moment = local.replace(tzinfo=tz)
        freezer.move_to(moment)
        hass.states.async_set(_METER, str(state), attrs)
        await async_wait_recording_done(hass)
        do_adhoc_statistics(hass, start=moment.astimezone(dt_util.UTC))
        await async_wait_recording_done(hass)


@pytest.mark.parametrize("spike", [5.0, 60.0])
@pytest.mark.asyncio
async def test_a_spike_corrected_after_midnight_is_not_water(
    recorder_mock: None, hass: HomeAssistant, freezer: Any, spike: float
) -> None:
    """5 m3 lets the sum fall the next day, 60 m3 is a reset to the compiler.

    The spike was admitted with its day and only the bucket after it was
    refused, so the year and the rolling year both carried it, and the
    year's mark pinned it until January.
    """
    await _record(
        hass,
        freezer,
        "total_increasing",
        [
            # 27 February is the compiler's zero point and 28 February gives
            # 1 March a sum to be measured against.
            (datetime(2026, 2, 27, 23, 55), 499.8),
            (datetime(2026, 2, 28, 23, 55), 500.0),
            (datetime(2026, 3, 1, 23, 55), 500.3),
            (datetime(2026, 3, 2, 23, 55), 500.7 + spike),
            (datetime(2026, 3, 3, 0, 55), 500.7),
            (datetime(2026, 3, 3, 23, 55), 501.0),
            (datetime(2026, 3, 4, 23, 55), 501.2),
        ],
    )

    total = await _recorder_ytd_m3(hass, _METER, date(2026, 3, 1), date(2026, 3, 4))

    # 500.0 -> 501.2, and the day after the correction keeps its 0.2.
    assert total == pytest.approx(1.2)


@pytest.mark.parametrize("state_class", ["total", "total_increasing"])
@pytest.mark.asyncio
async def test_a_meter_that_reads_0_at_midnight_keeps_the_day_before(
    recorder_mock: None, hass: HomeAssistant, freezer: Any, state_class: str
) -> None:
    """A register that falls below where it stood is a dip, not a spike.

    Capping the day before at the climb to the dip value would bill it
    nothing.
    """
    await _record(
        hass,
        freezer,
        state_class,
        [
            (datetime(2026, 2, 26, 23, 55), 999.6),
            (datetime(2026, 2, 27, 23, 55), 999.8),
            (datetime(2026, 2, 28, 23, 55), 1000.0),
            (datetime(2026, 3, 1, 23, 55), 1000.3),
            # Still at 0 when 2 March closes, back the next morning.
            (datetime(2026, 3, 2, 10, 0), 1000.5),
            (datetime(2026, 3, 2, 23, 55), 0.0),
            (datetime(2026, 3, 3, 10, 0), 1000.7),
            (datetime(2026, 3, 3, 23, 55), 1001.0),
        ],
    )

    rows = await _recorder_daily_rows(hass, _METER, date(2026, 2, 28), date(2026, 3, 3))
    admitted, _refused = _admitted_changes(
        rows, _METER, date(2026, 2, 28), date(2026, 3, 3), level=logging.DEBUG
    )
    days = {dt_util.as_local(dt_util.utc_from_timestamp(b)).date(): m3 for b, m3 in admitted if b}

    assert days[date(2026, 3, 1)] == pytest.approx(0.3)
