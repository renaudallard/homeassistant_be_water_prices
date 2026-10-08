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

"""A billed meter the recorder holds no statistic for.

Renaming a meter moves its statistics to the new id and leaves the old
one, still named in the options, with none. A sensor with no state class
never has any. Both answer every query with the same zero as a year that
has used no water, and the fold has to tell the two apart against the
real recorder, not a stand-in for it.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from custom_components.be_water_prices.coordinator import WaterCoordinator
from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
from tests.test_ha_coordinator import _fresh_tariff

_METER = "sensor.water_meter"
_RENAMED = "sensor.water_meter_main"
_TOTAL = {"device_class": "water", "state_class": "total_increasing", "unit_of_measurement": "m³"}
_NO_STATE_CLASS = {"device_class": "water", "unit_of_measurement": "m³"}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):  # type: ignore[no-untyped-def]
    """These tests load the integration, so the recorder goes up first."""
    yield


async def _fetch(_session: Any) -> WaterTariff:
    return _fresh_tariff()


def _import_hours(hass: HomeAssistant, start: datetime, hours: int, base: float, m3: float) -> None:
    """Give the meter ``hours`` hourly statistics drawing ``m3`` in all."""
    per_hour = m3 / hours
    async_import_statistics(
        hass,
        {
            "has_sum": True,
            "mean_type": StatisticMeanType.NONE,
            "name": None,
            "source": "recorder",
            "statistic_id": _METER,
            "unit_class": "volume",
            "unit_of_measurement": "m³",
        },
        [
            {
                "start": start + timedelta(hours=h),
                "state": base + per_hour * (h + 1),
                "sum": per_hour * (h + 1),
            }
            for h in range(hours)
        ],
    )


async def _setup(hass: HomeAssistant) -> tuple[MockConfigEntry, WaterCoordinator]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_WATER_METER_SENSOR: _METER},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, entry.runtime_data


async def _tick(hass: HomeAssistant, coordinator: WaterCoordinator) -> None:
    await coordinator.async_refresh()
    await hass.async_block_till_done()


@pytest.fixture
def _vivaqua() -> Any:
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.statistics.async_maybe_backfill_once",
            new=AsyncMock(return_value=None),
        ),
    ):
        yield


@pytest.mark.usefixtures("_vivaqua")
async def test_a_renamed_meter_keeps_its_year(hass: HomeAssistant, freezer: Any) -> None:
    """The old id has no statistic left, and its zero is not the year's.

    Once the live path had rolled the year over, the first tick after the
    rename took that zero as the recorder's first answer and the second
    found it confirmed by itself: the year went to 0 m3 and a fees-only
    bill, and the Store saved it.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    tz = dt_util.get_default_time_zone()
    registry = er.async_get(hass)
    assert (
        registry.async_get_or_create(
            "sensor", "meter", "1", suggested_object_id="water_meter"
        ).entity_id
        == _METER
    )

    freezer.move_to(datetime(2026, 12, 31, 20, 0, tzinfo=tz))
    hass.states.async_set(_METER, "1000.0", _TOTAL)
    _import_hours(hass, datetime(2026, 12, 1, tzinfo=tz), 24 * 30, 900.0, 7.2)
    await async_wait_recording_done(hass)
    _entry, coordinator = await _setup(hass)
    # The setup tick framed the meter on the recorder's answer and asks it
    # once more on the next tick, which comes before the year turns.
    await _tick(hass, coordinator)

    # The live path rolls the year over, so nothing has asked the
    # recorder about the new one.
    freezer.move_to(datetime(2027, 1, 1, 0, 10, tzinfo=tz))
    hass.states.async_set(_METER, "1000.1", _TOTAL)
    await hass.async_block_till_done()
    assert coordinator._ytd.year == 2027
    assert coordinator._ytd.recorder_hwm is None

    _import_hours(hass, datetime(2027, 1, 1, tzinfo=tz), 24 * 44, 1000.0, 30.0)
    await async_wait_recording_done(hass)
    freezer.move_to(datetime(2027, 2, 14, 12, 0, tzinfo=tz))
    hass.states.async_set(_METER, "1030.0", _TOTAL)
    await hass.async_block_till_done()
    await _tick(hass, coordinator)
    assert coordinator.data is not None
    year = coordinator.data.ytd_consumption_m3
    assert year is not None and round(year, 1) == 29.9

    registry.async_update_entity(_METER, new_entity_id=_RENAMED)
    hass.states.async_remove(_METER)
    hass.states.async_set(_RENAMED, "1030.0", _TOTAL)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)

    for day in (15, 16):
        freezer.move_to(datetime(2027, 2, day, 12, 0, tzinfo=tz))
        await _tick(hass, coordinator)
        assert coordinator.data.ytd_consumption_m3 == year

    stored = await coordinator._store.async_load()
    assert stored is not None
    assert stored["m3"] == year
    assert stored["cost"] == coordinator.data.current_year_cost_eur
    assert stored["recorder_hwm"] is None


@pytest.mark.usefixtures("_vivaqua")
async def test_a_meter_with_no_statistics_anchors_and_keeps_its_year(
    hass: HomeAssistant, freezer: Any
) -> None:
    """A sensor with no state class starts its year and does not lose it.

    The fresh install anchors on the zero, as it must, or the year would
    stay unknown for good. A reading that then steps back a little leaves
    the frame stale, the next tick asks the recorder, and its zero, met
    for the second time, took the year down to nothing.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    tz = dt_util.get_default_time_zone()
    freezer.move_to(datetime(2027, 3, 1, 10, 0, tzinfo=tz))
    hass.states.async_set(_METER, "500.0", _NO_STATE_CLASS)
    _entry, coordinator = await _setup(hass)
    assert coordinator.data is not None
    assert coordinator.data.ytd_consumption_m3 == 0.0
    assert coordinator._ytd.offset_m3 == 500.0

    for day, reading in ((2, "505.0"), (3, "520.0")):
        freezer.move_to(datetime(2027, 3, day, 10, 0, tzinfo=tz))
        hass.states.async_set(_METER, reading, _NO_STATE_CLASS)
        await hass.async_block_till_done()
        await _tick(hass, coordinator)
    assert coordinator.data.ytd_consumption_m3 == 20.0

    hass.states.async_set(_METER, "512.0", _NO_STATE_CLASS)
    await hass.async_block_till_done()
    freezer.move_to(datetime(2027, 3, 4, 10, 0, tzinfo=tz))
    await _tick(hass, coordinator)

    assert coordinator.data.ytd_consumption_m3 == 20.0
    stored = await coordinator._store.async_load()
    assert stored is not None
    assert stored["m3"] == 20.0
    assert stored["recorder_hwm"] is None
