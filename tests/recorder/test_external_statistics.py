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

"""What the reader makes of a statistic another integration writes itself.

Niko Home Control imports its water under a "nhc2:..." id with each
hour's own volume as the state and the running total as the sum, which is
how core's suez_water writes it too. The state is not a register, so the
day's change must not be measured against it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pytest
from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
    async_add_external_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

_ID = "nhc2:water_meter_watervolume"


@pytest.mark.asyncio
async def test_an_external_statistic_is_read_from_its_sum(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Every day's water is counted, though each day ends on a state of 0."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2026, 3, 10, 12, 0, tzinfo=tz))
    metadata = StatisticMetaData(
        has_mean=False,
        has_sum=True,
        name="Water meter WaterVolume",
        source="nhc2",
        statistic_id=_ID,
        unit_of_measurement="L",
        unit_class="volume",
        mean_type=StatisticMeanType.NONE,
    )
    rows = []
    running = 0.0
    hour = datetime(2026, 2, 27, 0, 0, tzinfo=tz)
    while hour < datetime(2026, 3, 10, 0, 0, tzinfo=tz):
        # 10 L an hour from 07:00 to 21:59 and none overnight, so the
        # last hour of every day, the daily bucket's state, is 0.
        litres = 10.0 if 7 <= hour.hour <= 21 else 0.0
        running += litres
        rows.append(StatisticData(start=hour, state=litres, sum=running))
        hour += timedelta(hours=1)
    async_add_external_statistics(hass, metadata, rows)
    await async_wait_recording_done(hass)

    total, taken_back = await _recorder_ytd_m3(hass, _ID, date(2026, 3, 1), date(2026, 3, 9))

    assert total == pytest.approx(9 * 0.15)
    assert taken_back == 0.0
