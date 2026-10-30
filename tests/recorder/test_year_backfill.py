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

"""The year-to-date sensors' history written into a real recorder.

Home Assistant counts a new total sensor's statistics from its first value,
so a monthly graph of the year's cost started at the install. The rows the
backfill writes have to join what the recorder compiled from the live
sensor, which lies in how Home Assistant compiles, so these run on the real
recorder.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
    do_adhoc_statistics,
)

from custom_components.be_water_prices.const import CONF_UTILITY, DOMAIN
from custom_components.be_water_prices.coordinator import WaterCoordinator
from custom_components.be_water_prices.statistics import (
    _import_year_rows,
    async_backfill_year,
)

METER = "sensor.water_meter"
SID = "sensor.water_current_year_cost"
OLD = datetime(2025, 12, 31, 23, tzinfo=UTC)  # 1 January 2026, Brussels
NEW = datetime(2026, 12, 31, 23, tzinfo=UTC)  # 1 January 2027, Brussels


async def _live(
    hass: HomeAssistant, freezer: Any, when: datetime, value: float, reset: datetime
) -> None:
    """The sensor reports ``value`` at ``when`` and its five minutes compile."""
    freezer.move_to(when)
    hass.states.async_set(
        SID,
        str(value),
        {
            "device_class": "monetary",
            "state_class": "total",
            "unit_of_measurement": "€",
            "last_reset": reset.isoformat(),
        },
    )
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    do_adhoc_statistics(hass, start=when.replace(minute=when.minute - when.minute % 5, second=0))
    await async_wait_recording_done(hass)


def _rows(
    hass: HomeAssistant, start: datetime, period: Literal["hour", "month"] = "hour"
) -> list[tuple[Any, ...]]:
    got = statistics_during_period(
        hass, start, None, {SID}, period, None, {"sum", "state", "change"}
    )
    return [(r.get("state"), r.get("sum"), r.get("change")) for r in got.get(SID, [])]


async def _sensor(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()


async def test_a_fresh_sensor_counts_the_months_before_its_install(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Installed on 10 October with 300 billed, the sensor's own statistics
    start at that figure. Written from 1 January, the months add up to the
    sensor's figure, none of them below zero, and the live compile carries
    on from the imported total."""
    await _sensor(hass)
    h = datetime(2026, 10, 10, 8, tzinfo=UTC)
    await _live(hass, freezer, h + timedelta(minutes=1), 300.0, OLD)
    await _live(hass, freezer, h + timedelta(minutes=56), 300.5, OLD)
    await _live(hass, freezer, h + timedelta(hours=1, minutes=1), 300.6, OLD)
    totals = [(OLD + timedelta(days=31 * n), 30.0 * (n + 1)) for n in range(9)]
    totals += [(h - timedelta(hours=1), 299.9), (h, 300.5)]
    assert await _import_year_rows(hass, SID, "€", None, totals, OLD)
    await async_wait_recording_done(hass)
    await _live(hass, freezer, h + timedelta(hours=1, minutes=56), 301.0, OLD)
    months = await hass.async_add_executor_job(_rows, hass, OLD - timedelta(days=1), "month")
    changes = [change for _state, _sum, change in months]
    assert min(changes) >= 0.0, months
    assert sum(changes) == pytest.approx(301.0), months
    hours = await hass.async_add_executor_job(_rows, hass, h)
    assert [r[1] for r in hours] == pytest.approx([300.5, 301.0]), hours


async def test_a_new_year_continues_last_years_total(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """The live total holds last year's figure at 31 December; the new
    year's imported hours add to it rather than start below it."""
    await _sensor(hass)
    last = NEW - timedelta(hours=2)
    await _live(hass, freezer, last + timedelta(minutes=1), 400.0, OLD)
    await _live(hass, freezer, last + timedelta(minutes=56), 410.0, OLD)
    chain = (await hass.async_add_executor_job(_rows, hass, last))[-1][1]
    await _live(hass, freezer, NEW + timedelta(minutes=1), 3.0, NEW)
    await _live(hass, freezer, NEW + timedelta(hours=1, minutes=56), 5.0, NEW)
    freezer.move_to(NEW + timedelta(hours=2, minutes=1))
    assert await _import_year_rows(
        hass, SID, "€", None, [(NEW, 3.0), (NEW + timedelta(hours=1), 5.0)], NEW
    )
    await async_wait_recording_done(hass)
    got = await hass.async_add_executor_job(_rows, hass, last)
    assert [r[2] for r in got[1:]] == pytest.approx([3.0, 2.0]), got
    assert got[-1][1] == pytest.approx(chain + 5.0), got
    await _live(hass, freezer, NEW + timedelta(hours=2, minutes=56), 5.5, NEW)
    got = await hass.async_add_executor_job(_rows, hass, last)
    assert got[-1][2] == pytest.approx(0.5), got


def _meter(hass: HomeAssistant, start: datetime, litres: list[float]) -> None:
    """The meter's hourly statistics from ``start``, ``litres`` an hour."""
    total = 0.0
    rows = []
    for n, used in enumerate(litres):
        total += used / 1000.0
        rows.append(StatisticData(start=start + timedelta(hours=n), state=50.0 + total, sum=total))
    async_import_statistics(
        hass,
        StatisticMetaData(
            mean_type=StatisticMeanType.NONE,
            has_sum=True,
            name=None,
            source="recorder",
            statistic_id=METER,
            unit_class="volume",
            unit_of_measurement="m³",
        ),
        rows,
    )


async def test_the_year_follows_the_meter_hour_by_hour(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Two days of the year at 10 L an hour by day and none at night: it climbs
    in the hours the meter did, ends on the m³ the year publishes, and each
    hour is priced with its own day's fees."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    freezer.move_to(datetime(2026, 1, 3, 0, 30, tzinfo=dt_util.get_default_time_zone()))
    jan1 = dt_util.as_utc(dt_util.start_of_local_day(date(2026, 1, 1)))
    # A day of history before the year, so that 1 January is not the
    # statistic's first bucket, which the reader trims.
    litres = [10.0 if 7 <= n % 24 <= 21 else 0.0 for n in range(72)]
    _meter(hass, jan1 - timedelta(days=1), litres)
    await async_wait_recording_done(hass)
    owner = SimpleNamespace(
        hass=hass,
        data=SimpleNamespace(ytd_consumption_m3=0.6, tariff=None),
        _meter_entity_id=METER,
        _sees_a_register=lambda meter, state: True,
        _ytd_cost_from_m3=lambda tariff, m3, year, day: 3.0 * m3 + day.day,
    )
    hours = [jan1 + timedelta(hours=n) for n in range(48)]
    year = await WaterCoordinator.async_year_by_hour(owner, hours)  # type: ignore[arg-type]
    assert year is not None
    # The meter recorded 0.3 m3; the year publishes 0.6, so each hour counts
    # double.
    assert year[6] == (pytest.approx(0.0), pytest.approx(1.0))
    assert year[7][0] == pytest.approx(0.02)
    assert year[23] == (pytest.approx(0.3), pytest.approx(0.9 + 1.0))
    assert year[24][1] == pytest.approx(0.9 + 2.0)
    assert year[-1][0] == pytest.approx(0.6)


async def test_the_year_is_written_once(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Written for a cycle that opens on 1 January; a sensor restarted on a
    meter swap since is left alone, and once written, nothing more is."""
    await _sensor(hass)
    freezer.move_to(datetime(2026, 1, 2, 10, 30, tzinfo=UTC))
    jan1 = dt_util.as_utc(dt_util.start_of_local_day(date(2026, 1, 1)))
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_UTILITY: "vivaqua"})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    cost, volume = (
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}", suggested_object_id=f"v_{key}"
        ).entity_id
        for key in ("current_year_cost", "ytd_consumption")
    )
    hass.states.async_set(cost, "12.0", {"last_reset": dt_util.as_local(jan1).isoformat()})
    swapped = dt_util.as_local(jan1 + timedelta(days=1)).isoformat()
    hass.states.async_set(volume, "1.0", {"last_reset": swapped})
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(ytd_consumption_m3=1.0, current_year_cost_eur=12.0)
    coordinator.async_year_by_hour = AsyncMock(
        side_effect=lambda hours: [(n / 10.0, n / 2.0) for n, _hour in enumerate(hours)]
    )
    entry.runtime_data = coordinator
    written = await async_backfill_year(hass, entry)
    assert written
    await async_wait_recording_done(hass)

    def stats(sid: str) -> list[Any]:
        got = statistics_during_period(hass, jan1, None, {sid}, "hour", None, {"state", "sum"})
        return got.get(sid, [])

    rows = await hass.async_add_executor_job(stats, cost)
    assert len(rows) == written
    assert [row["state"] for row in rows[:3]] == pytest.approx([0.0, 0.5, 1.0])
    assert await hass.async_add_executor_job(stats, volume) == []
    assert await async_backfill_year(hass, entry) == 0
