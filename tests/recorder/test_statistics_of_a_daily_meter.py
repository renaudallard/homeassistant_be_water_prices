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

"""What Home Assistant's statistics hold for a meter that resets each day.

A daily utility meter goes back to 0 at midnight, so its state at the end
of each day is that day's water rather than a register. Read as a
register, every day claimed its whole register and only the climb from
the day before was kept.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
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

_METER = "sensor.water_today"
_ATTRS = {
    "device_class": "water",
    "state_class": "total_increasing",
    "unit_of_measurement": "m³",
}


@pytest.mark.asyncio
async def test_a_meter_that_resets_daily_is_read_from_its_sum(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Every day's water is counted, whichever day used more."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()
    tz = dt_util.get_default_time_zone()

    # 26 February is the compiler's zero point and 27 February gives
    # 28 February a sum to be measured against.
    day = date(2026, 2, 26)
    for used in (0.1, 0.1, 0.2, 0.15, 0.3, 0.1):
        midnight = datetime.combine(day, datetime.min.time(), tz)
        for moment, state in (
            (midnight + timedelta(minutes=5), 0.0),
            (midnight + timedelta(hours=12), used / 2),
            (midnight + timedelta(hours=23, minutes=55), used),
        ):
            freezer.move_to(moment)
            hass.states.async_set(_METER, str(state), _ATTRS)
            await async_wait_recording_done(hass)
            do_adhoc_statistics(hass, start=moment.astimezone(dt_util.UTC))
            await async_wait_recording_done(hass)
        day += timedelta(days=1)

    answer = await _recorder_ytd_m3(
        hass, _METER, date(2026, 2, 28), date(2026, 3, 3), register=False
    )

    assert answer is not None
    assert answer[0] == pytest.approx(0.2 + 0.15 + 0.3 + 0.1)
