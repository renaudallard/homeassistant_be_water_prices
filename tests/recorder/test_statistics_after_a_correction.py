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

"""What Home Assistant's statistics make of a year-to-date figure that falls.

The YTD sensors are totals with a last_reset, and the compiler opens a new
cycle whenever last_reset moves, adding the whole new figure to the sum. A
correction within the year has to stay in its cycle so the sum nets to the
corrected figure; only a restarted year may open a new one. Both are pinned
here against the real compiler, with the sensor deciding the reset.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import get_last_statistics
from homeassistant.core import HomeAssistant
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.be_water_prices.const import CONF_UTILITY
from custom_components.be_water_prices.sensor import SENSORS, WaterSensor

_ENTITY = "sensor.vivaqua_current_year_cost"
_ATTRS = {
    "device_class": "monetary",
    "state_class": "total",
    "unit_of_measurement": "EUR",
}


def _sensor() -> WaterSensor:
    coordinator = SimpleNamespace(
        entry=SimpleNamespace(entry_id="e1", title="VIVAQUA", data={CONF_UTILITY: "vivaqua"}),
        data=None,
    )
    desc = next(d for d in SENSORS if d.key == "current_year_cost")
    return WaterSensor(coordinator, desc)  # type: ignore[arg-type]


async def _compile(
    hass: HomeAssistant, freezer: Any, rounds: list[tuple[float, datetime | None]]
) -> float:
    """Publish each figure through the sensor an hour apart and compile it.

    Returns the long-term sum the compiler ends on. Each figure lands in
    the last five minutes of an hour, which is the run that compiles the
    hour as well.
    """
    sensor = _sensor()
    moment = datetime(2026, 10, 4, 7, 55, tzinfo=dt_util.get_default_time_zone())
    for value, started in rounds:
        sensor.coordinator.data = SimpleNamespace(
            current_year_cost_eur=value, ytd_started_at=started
        )
        with patch.object(WaterSensor, "async_write_ha_state"):
            sensor._handle_coordinator_update()
        freezer.move_to(moment)
        reset = sensor.last_reset
        assert reset is not None
        hass.states.async_set(_ENTITY, str(value), {**_ATTRS, "last_reset": reset.isoformat()})
        await async_wait_recording_done(hass)
        do_adhoc_statistics(hass, start=moment.astimezone(dt_util.UTC))
        await async_wait_recording_done(hass)
        moment += timedelta(hours=1)
    last = await get_instance(hass).async_add_executor_job(
        get_last_statistics, hass, 1, _ENTITY, True, {"sum"}
    )
    return float(last[_ENTITY][0]["sum"])


@pytest.mark.asyncio
async def test_a_correction_in_the_year_nets_the_sum_to_the_true_figure(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Turning on the social tariff in October rebuilds the bill lower.

    The sensor used to move its reset on any fall, so the compiler added
    the rebuilt 87.51 on top of the 437.56 already summed, and the
    statistics showed 525.56 EUR for a year that cost 88.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()

    total = await _compile(
        hass, freezer, [(0.0, None), (437.56, None), (87.51, None), (88.0, None)]
    )

    assert total == pytest.approx(88.0)


@pytest.mark.asyncio
async def test_a_restarted_year_still_opens_a_new_cycle(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """A confirmed swap restarts the figure at 0, and what the year had
    summed before it stays in the sum instead of being given back.

    This is a meter the recorder holds no statistics for, whose year then
    really carries on from the new register. With statistics behind it the
    next tick brings the year back inside the new cycle and the sum counts
    the water before the swap twice, as the README says.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()
    swap = datetime(2026, 10, 4, 9, 30, tzinfo=dt_util.get_default_time_zone())

    total = await _compile(hass, freezer, [(0.0, None), (40.0, None), (0.0, swap), (2.0, swap)])

    assert total == pytest.approx(42.0)
