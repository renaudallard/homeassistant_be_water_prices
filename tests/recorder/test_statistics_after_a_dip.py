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

"""What Home Assistant's statistics do with a meter that briefly reads 0.

A total_increasing meter that drops by more than a tenth is a new cycle
to the compiler, which then adds the whole recovered register to the
sum. The reader has to refuse that change and still find the day's
water, so both halves are pinned here against the real compiler.
"""

from __future__ import annotations

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

from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

_METER = "sensor.water_meter"
_ATTRS = {
    "device_class": "water",
    "state_class": "total_increasing",
    "unit_of_measurement": "m³",
}


@pytest.mark.asyncio
async def test_the_day_a_meter_reads_0_keeps_its_water(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Only the register's climb over the day is billed, and all of it.

    Refusing the day whole left every later answer short by what it drew
    while still climbing, which the fold read as a whole history and took
    the year down to.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()
    tz = dt_util.get_default_time_zone()

    for moment, state in (
        # 27 February is the compiler's zero point and 28 February gives
        # 1 March a sum to be measured against.
        (datetime(2026, 2, 27, 23, 55, tzinfo=tz), "999.8"),
        (datetime(2026, 2, 28, 23, 55, tzinfo=tz), "1000.0"),
        (datetime(2026, 3, 1, 23, 55, tzinfo=tz), "1000.3"),
        # 0.2 before the meter reads 0, back two hours later, 0.2 after.
        (datetime(2026, 3, 2, 10, 0, tzinfo=tz), "1000.5"),
        (datetime(2026, 3, 2, 10, 5, tzinfo=tz), "0"),
        (datetime(2026, 3, 2, 12, 0, tzinfo=tz), "1000.5"),
        (datetime(2026, 3, 2, 23, 55, tzinfo=tz), "1000.7"),
        (datetime(2026, 3, 3, 23, 55, tzinfo=tz), "1001.0"),
    ):
        freezer.move_to(moment)
        hass.states.async_set(_METER, state, _ATTRS)
        await async_wait_recording_done(hass)
        do_adhoc_statistics(hass, start=moment.astimezone(dt_util.UTC))
        await async_wait_recording_done(hass)

    total, _ = await _recorder_ytd_m3(hass, _METER, date(2026, 3, 1), date(2026, 3, 3))

    assert total == pytest.approx(1.0)  # 1000.0 -> 1001.0, every drop of it
