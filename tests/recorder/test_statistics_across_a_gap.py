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

"""What Home Assistant's statistics do with a gap in the meter.

The fold decides whether a reading far above the recorder's figure is a
spike to be taken back or a meter returning from an outage with real
water. That decision rested for a while on the recorder being permanently
short by whatever was drawn while nobody was looking, which is not what
Home Assistant does: it carries the running sum forward and attributes the
whole increase to the bucket where the meter comes back.

The assumption is load-bearing and lives in another project, so it is
pinned here against the real compiler rather than remembered.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

_METER = "sensor.water_meter"
_ATTRS = {
    "device_class": "water",
    "state_class": "total_increasing",
    "unit_of_measurement": "m³",
}


async def _summed_change(
    hass: HomeAssistant, freezer: Any, rounds: list[tuple[int, str, bool]], window_h: int
) -> float:
    """Play ``rounds`` at the meter and sum the change from ``window_h`` on.

    Each round is (hour, state, whether anything compiled). A round that
    compiles nothing stands for the host being off rather than the meter
    merely reading unavailable while the host keeps working.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()
    zero = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)

    for hour, state, compiled in rounds:
        moment = zero + timedelta(hours=hour)
        freezer.move_to(moment)
        hass.states.async_set(_METER, state, _ATTRS)
        await async_wait_recording_done(hass)
        if compiled:
            do_adhoc_statistics(hass, start=moment)
            await async_wait_recording_done(hass)

    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        zero + timedelta(hours=window_h),
        zero + timedelta(hours=24),
        {_METER},
        "5minute",
        None,
        {"sum", "state", "change"},
    )
    return sum(row.get("change") or 0.0 for row in stats.get(_METER, []))


@pytest.mark.asyncio
async def test_water_drawn_while_the_meter_was_away_is_not_lost(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """The bucket the meter returns in carries the whole outage.

    Believing otherwise cost the fold its own defence: it handed the
    recorder an allowance to be short, and that allowance came straight off
    the guard that takes a spike back, so a year could carry one to January.
    """
    total = await _summed_change(
        hass,
        freezer,
        [
            (0, "100.0", True),
            (1, "100.5", True),
            # Away for two hours, and the household draws 40 m3.
            (2, STATE_UNAVAILABLE, True),
            (3, STATE_UNAVAILABLE, True),
            (4, "140.5", True),
        ],
        window_h=0,
    )

    assert total == pytest.approx(40.5)  # 100.0 -> 140.5, every drop of it


@pytest.mark.asyncio
async def test_the_host_being_off_loses_nothing_either(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Nothing compiles at all while the host is down, and it still adds up.

    This is the case the allowance was really written for: no bucket exists
    for any of the gap, so it looks like the statistics were never taken.
    They are taken on the far side instead.
    """
    total = await _summed_change(
        hass,
        freezer,
        [
            (0, "100.0", True),
            (1, "105.0", True),
            # The host is off: nothing compiles for either hour.
            (2, STATE_UNAVAILABLE, False),
            (3, STATE_UNAVAILABLE, False),
            (4, "135.0", True),
        ],
        window_h=2,
    )

    # The window opens at hour 2 with the meter at 105 and closes at 135.
    assert total == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_a_gap_spanning_the_window_lands_wholly_inside_it(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Which is why an outage across 1 January bills December into January.

    A period's consumption is measured against the last statistic compiled
    before it, so when nothing compiled on either side of the boundary the
    first bucket inside the window reaches back past it. Nothing in the
    data says when during the gap the water flowed, so the figure carries
    it and the README says so under Known limitations.
    """
    total = await _summed_change(
        hass,
        freezer,
        [
            (0, "100.0", True),
            # The host goes off here, at 105, and the meter keeps climbing.
            (1, "105.0", True),
            (2, STATE_UNAVAILABLE, False),
            (3, STATE_UNAVAILABLE, False),
            # It comes back inside the window at 135.
            (4, "135.0", True),
        ],
        window_h=3,
    )

    # The window opens at hour 3, by which time the meter was already past
    # 105, yet the whole 30 m3 since the last compiled statistic lands here.
    assert total == pytest.approx(30.0)
