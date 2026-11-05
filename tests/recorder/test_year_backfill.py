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

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
    async_import_statistics,
    get_metadata,
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

from custom_components.be_water_prices import meter_reads
from custom_components.be_water_prices import statistics as statistics_module
from custom_components.be_water_prices.const import BACKFILL_YIELD_EVERY, CONF_UTILITY, DOMAIN
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
    hass: HomeAssistant,
    freezer: Any,
    when: datetime,
    value: float,
    reset: datetime,
    sid: str = SID,
) -> None:
    """The sensor reports ``value`` at ``when`` and its five minutes compile."""
    freezer.move_to(when)
    hass.states.async_set(
        sid,
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
    year's imported hours add to it rather than start below it. The live
    rows already carry on from it, so nothing has to move them."""
    await _sensor(hass)
    last = NEW - timedelta(hours=2)
    await _live(hass, freezer, last + timedelta(minutes=1), 400.0, OLD)
    await _live(hass, freezer, last + timedelta(minutes=56), 410.0, OLD)
    chain = (await hass.async_add_executor_job(_rows, hass, last))[-1][1]
    await _live(hass, freezer, NEW + timedelta(minutes=1), 3.0, NEW)
    await _live(hass, freezer, NEW + timedelta(hours=1, minutes=56), 5.0, NEW)
    freezer.move_to(NEW + timedelta(hours=2, minutes=1))
    instance = get_instance(hass)
    with patch.object(
        instance, "async_adjust_statistics", wraps=instance.async_adjust_statistics
    ) as adjust:
        assert await _import_year_rows(
            hass, SID, "€", None, [(NEW, 3.0), (NEW + timedelta(hours=1), 5.0)], NEW
        )
    adjust.assert_not_called()
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
        _ytd=SimpleNamespace(year=2026),
        _card_in_force=lambda tariff, year: tariff,
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
    """Written for a cycle that opens on 1 January once the sensor has live
    statistics to join it to; a sensor restarted on a meter swap since is
    left alone, and once written, nothing more is."""
    await _sensor(hass)
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
    attrs = {
        "device_class": "monetary",
        "state_class": "total",
        "unit_of_measurement": "€",
        "last_reset": dt_util.as_local(jan1).isoformat(),
    }
    swapped = dt_util.as_local(jan1 + timedelta(days=1)).isoformat()
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(ytd_consumption_m3=1.0, current_year_cost_eur=12.0)
    coordinator.async_year_by_hour = AsyncMock(
        side_effect=lambda hours: [(n / 10.0, n / 2.0) for n, _hour in enumerate(hours)]
    )
    entry.runtime_data = coordinator
    h = datetime(2026, 1, 2, 8, tzinfo=UTC)
    freezer.move_to(h + timedelta(minutes=1))
    hass.states.async_set(cost, "12.0", attrs)
    hass.states.async_set(volume, "1.0", {"last_reset": swapped})
    await hass.async_block_till_done()
    # Nothing compiled for the sensor yet: there is no total to join the year
    # to, so it is asked again.
    assert await async_backfill_year(hass, entry) is None
    await _live(hass, freezer, h + timedelta(minutes=1), 12.0, jan1, cost)
    await _live(hass, freezer, h + timedelta(minutes=56), 12.0, jan1, cost)
    freezer.move_to(h + timedelta(hours=1, minutes=3))
    written = await async_backfill_year(hass, entry)
    assert written
    await async_wait_recording_done(hass)

    def stats(sid: str) -> list[Any]:
        got = statistics_during_period(hass, jan1, None, {sid}, "hour", None, {"state", "sum"})
        return got.get(sid, [])

    rows = await hass.async_add_executor_job(stats, cost)
    assert len(rows) == written
    # 34 hours to 08:00 UTC, the last at 16.5 by async_year_by_hour, scaled
    # to the 12.0 the sensor showed at the end of it.
    assert [row["state"] for row in rows[:3]] == pytest.approx(
        [0.0, 0.5 / 1.375, 1.0 / 1.375], abs=1e-4
    )
    assert rows[-1]["state"] == pytest.approx(12.0)
    assert await hass.async_add_executor_job(stats, volume) == []
    assert await async_backfill_year(hass, entry) == 0


def _year_entry(hass: HomeAssistant) -> tuple[MockConfigEntry, str, str]:
    """An entry and its two year-to-date sensors, as the registry has them."""
    entry = MockConfigEntry(domain=DOMAIN, data={CONF_UTILITY: "vivaqua"})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    cost, volume = (
        registry.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}", suggested_object_id=f"v_{key}"
        ).entity_id
        for key in ("current_year_cost", "ytd_consumption")
    )
    return entry, cost, volume


async def _hour_of_litres(
    hass: HomeAssistant,
    freezer: Any,
    sids: tuple[str, str],
    when: datetime,
    m3: float,
    unit: str,
) -> None:
    """The two sensors report at ``when`` and its five minutes compile, the
    volume in ``unit``."""
    cost, volume = sids
    reset = dt_util.as_local(OLD).isoformat()
    freezer.move_to(when)
    hass.states.async_set(
        volume,
        str(m3 if unit == "m³" else m3 * 1000),
        {
            "device_class": "water",
            "state_class": "total",
            "unit_of_measurement": unit,
            "last_reset": reset,
        },
    )
    hass.states.async_set(
        cost,
        str(10.0 * m3),
        {
            "device_class": "monetary",
            "state_class": "total",
            "unit_of_measurement": "€",
            "last_reset": reset,
        },
    )
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    do_adhoc_statistics(hass, start=when.replace(minute=when.minute - when.minute % 5, second=0))
    await async_wait_recording_done(hass)


def _volume_rows(hass: HomeAssistant, sid: str) -> list[tuple[float, float, float]]:
    """The volume's hourly rows as the recorder keeps them."""
    got = statistics_during_period(
        hass, OLD, None, {sid}, "hour", {"volume": "m³"}, {"state", "sum", "change"}
    )
    return [(r["state"], r["sum"], r["change"]) for r in got.get(sid, [])]


async def _a_year_in(hass: HomeAssistant, freezer: Any, first_unit: str) -> tuple[Any, str]:
    """Installed on 2 January at 09:00 with 1 m3 used, the volume compiled
    first in ``first_unit`` and shown in litres from 09:56, then the year
    written two hours on: the entry and the volume's statistic id."""
    await _sensor(hass)
    entry, cost, volume = _year_entry(hass)
    h = datetime(2026, 1, 2, 8, tzinfo=UTC)
    await _hour_of_litres(hass, freezer, (cost, volume), h + timedelta(minutes=1), 1.0, first_unit)
    await _hour_of_litres(hass, freezer, (cost, volume), h + timedelta(minutes=56), 1.05, "L")
    for k in range(12):
        when = h + timedelta(hours=1, minutes=5 * k + 1)
        await _hour_of_litres(hass, freezer, (cost, volume), when, 1.1, "L")
    freezer.move_to(h + timedelta(hours=2, minutes=3))
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(ytd_consumption_m3=1.1, current_year_cost_eur=11.0)
    coordinator.async_year_by_hour = AsyncMock(
        side_effect=lambda hours: [
            (1.1 * (n + 1) / len(hours), 11.0 * (n + 1) / len(hours)) for n, _ in enumerate(hours)
        ]
    )
    entry.runtime_data = coordinator
    assert await async_backfill_year(hass, entry)
    await async_wait_recording_done(hass)
    # And the hour after it compiles, the volume at 1.11 m3.
    for k in range(12):
        when = h + timedelta(hours=2, minutes=5 * k + 1)
        await _hour_of_litres(hass, freezer, (cost, volume), when, 1.11, "L")
    return entry, volume


@pytest.mark.parametrize("left_out", ["volume", "both"])
async def test_a_sensor_the_recorder_does_not_record_is_left_out(
    recorder_mock: None, hass: HomeAssistant, freezer: Any, left_out: str
) -> None:
    """A sensor the recorder excludes never compiles the statistics its year
    joins, and waiting for them priced the year again every hour for good.
    It is left out: the other sensor's year is written, and with neither left
    the year is settled without being priced."""
    await _sensor(hass)
    entry, cost, volume = _year_entry(hass)
    excluded = {volume} if left_out == "volume" else {cost, volume}
    get_instance(hass).entity_filter = lambda entity_id: entity_id not in excluded
    h = datetime(2026, 1, 2, 8, tzinfo=UTC)
    await _hour_of_litres(hass, freezer, (cost, volume), h + timedelta(minutes=1), 1.0, "m³")
    await _hour_of_litres(hass, freezer, (cost, volume), h + timedelta(minutes=56), 1.05, "m³")
    freezer.move_to(h + timedelta(hours=1, minutes=3))
    coordinator = MagicMock()
    coordinator.data = SimpleNamespace(ytd_consumption_m3=1.05, current_year_cost_eur=10.5)
    coordinator.async_year_by_hour = AsyncMock(
        side_effect=lambda hours: [
            (1.05 * (n + 1) / len(hours), 10.5 * (n + 1) / len(hours)) for n, _ in enumerate(hours)
        ]
    )
    entry.runtime_data = coordinator
    written = await async_backfill_year(hass, entry)
    await async_wait_recording_done(hass)
    if left_out == "both":
        assert written == 0
        coordinator.async_year_by_hour.assert_not_awaited()
        return

    def cost_rows() -> list[Any]:
        got = statistics_during_period(hass, OLD, None, {cost}, "hour", None, {"sum"})
        return got.get(cost, [])

    rows = await hass.async_add_executor_job(cost_rows)
    assert written and len(rows) == written
    assert await hass.async_add_executor_job(_volume_rows, hass, volume) == []


async def test_a_year_shown_in_litres_is_written_in_cubic_metres(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """Home Assistant answers a read in the unit the sensor shows and converts
    the reset times with the figures, so in litres they came back fifty
    thousand years ahead, the import stopped half done and the next hour fell
    by the whole year. Kept in cubic metres, the year is written in them and
    the live total carries on from it."""
    _entry, volume = await _a_year_in(hass, freezer, "m³")
    meta = await hass.async_add_executor_job(lambda: get_metadata(hass, statistic_ids={volume}))
    assert meta[volume][1]["unit_of_measurement"] == "m³"
    rows = await hass.async_add_executor_job(_volume_rows, hass, volume)
    assert min(change for _state, _sum, change in rows) >= 0.0, rows
    assert rows[-1][1] == pytest.approx(1.11), rows
    assert rows[-1][2] == pytest.approx(0.01), rows


async def test_a_year_kept_in_litres_is_written_in_litres(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """A sensor shown in litres before its first compile keeps its statistics
    in litres. The year goes in as litres, and the statistic stays in them:
    written in cubic metres, every litre kept before it would have read as
    a cubic metre."""
    _entry, volume = await _a_year_in(hass, freezer, "L")
    meta = await hass.async_add_executor_job(lambda: get_metadata(hass, statistic_ids={volume}))
    assert meta[volume][1]["unit_of_measurement"] == "L"
    rows = await hass.async_add_executor_job(_volume_rows, hass, volume)
    # 35 hours, from midnight on 1 January to 11:00 on the 2nd, Brussels time.
    assert rows[0][0] == pytest.approx(1.1 / 35), rows
    assert min(change for _state, _sum, change in rows) >= 0.0, rows
    assert rows[-1][1] == pytest.approx(1.11), rows


async def test_a_compile_between_the_read_and_the_import_joins_the_year(
    recorder_mock: None, hass: HomeAssistant, freezer: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The recorder compiles every five minutes, whatever the backfill is doing.
    A row it compiled after the live rows were read and before the import
    reached its queue stayed on the old total, and the next hour then fell
    by the whole year: 1.0 after 300.5."""
    await _sensor(hass)
    h = datetime(2026, 10, 10, 8, tzinfo=UTC)
    await _live(hass, freezer, h + timedelta(minutes=1), 300.0, OLD)
    await _live(hass, freezer, h + timedelta(minutes=56), 300.5, OLD)
    await _live(hass, freezer, h + timedelta(hours=1, minutes=1), 300.6, OLD)
    totals = [(OLD + timedelta(days=31 * n), 30.0 * (n + 1)) for n in range(9)]
    totals += [(h - timedelta(hours=1), 299.9), (h, 300.5)]
    read = statistics_module._read
    compiled: list[bool] = []

    async def _then_compile(*args: Any) -> list[Any] | None:
        got = await read(*args)
        if not compiled and args[4] == "hour" and args[2] >= h:
            # The last read of the live rows, then the five minutes at 09:05.
            compiled.append(True)
            await _live(hass, freezer, h + timedelta(hours=1, minutes=6), 300.7, OLD)
        return got

    monkeypatch.setattr(statistics_module, "_read", _then_compile)
    assert await _import_year_rows(hass, SID, "€", None, totals, OLD)
    assert compiled
    await async_wait_recording_done(hass)
    await _live(hass, freezer, h + timedelta(hours=1, minutes=56), 301.0, OLD)
    hours = await hass.async_add_executor_job(_rows, hass, h)
    assert [r[1] for r in hours] == pytest.approx([300.5, 301.0]), hours


async def test_the_five_minute_rows_carry_on_from_the_year(
    recorder_mock: None, hass: HomeAssistant, freezer: Any
) -> None:
    """The year ends on the figure the sensor showed at the end of its last
    hour, not on the one at the moment it is written, so the five-minute
    rows the live compile wrote since then climb on from it instead of
    falling back first: -1.5 in one of them before."""
    await _sensor(hass)
    h = datetime(2026, 10, 10, 8, tzinfo=UTC)
    value = 300.0
    for k in range(17):
        await _live(hass, freezer, h + timedelta(minutes=5 * k + 1), value, OLD)
        value = round(value + 0.1, 2)
    # Written at 09:21 with the newest compiled hour ending at 09:00, the
    # year's figure standing at 301.6 by then.
    totals = [(OLD + timedelta(days=31 * n), 30.0 * (n + 1)) for n in range(9)]
    totals += [(h, 301.6)]
    assert await _import_year_rows(hass, SID, "€", None, totals, OLD)
    await async_wait_recording_done(hass)

    def short() -> list[tuple[Any, ...]]:
        got = statistics_during_period(
            hass, h, None, {SID}, "5minute", None, {"sum", "state", "change"}
        )
        return [(r["state"], r["sum"], r["change"]) for r in got.get(SID, [])]

    rows = await hass.async_add_executor_job(short)
    assert min(change for _state, _sum, change in rows) >= 0.0, rows
    assert [s - state for state, s, _change in rows] == pytest.approx([0.0] * len(rows)), rows
    assert rows[-1][1] == pytest.approx(301.6), rows
    # The hours hold the year to 09:00, at the 301.1 the sensor showed then.
    months = await hass.async_add_executor_job(_rows, hass, OLD - timedelta(days=1), "month")
    assert sum(change for _state, _sum, change in months) == pytest.approx(301.1), months


async def test_the_year_is_built_in_slices(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A whole year of hours held the event loop for a fifth of a second on
    a Raspberry Pi. It is built a long month at a time, the loop free to run
    whatever else is waiting in between."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    jan1 = dt_util.as_utc(dt_util.start_of_local_day(date(2026, 1, 1)))
    hours = [jan1 + timedelta(hours=n) for n in range(8760)]

    async def _rows(*_args: Any, period: str = "day", **_kwargs: Any) -> list[dict[str, float]]:
        step, count = (timedelta(hours=1), 8760) if period == "hour" else (timedelta(days=1), 365)
        return [
            {"start": (jan1 + step * n).timestamp(), "change": 0.01, "sum": 0.01 * n}
            for n in range(-1, count)
        ]

    monkeypatch.setattr(meter_reads, "_recorder_daily_rows", _rows)
    owner = SimpleNamespace(
        hass=hass,
        data=SimpleNamespace(ytd_consumption_m3=60.0, tariff=None),
        _meter_entity_id=METER,
        _ytd=SimpleNamespace(year=2026),
        _card_in_force=lambda tariff, year: tariff,
        _sees_a_register=lambda meter, state: False,
        _ytd_cost_from_m3=lambda tariff, m3, year, day: m3,
    )
    turns = 0
    building = True

    async def _waiting() -> None:
        nonlocal turns
        while building:
            turns += 1
            await asyncio.sleep(0)

    other = asyncio.ensure_future(_waiting())
    year = await WaterCoordinator.async_year_by_hour(owner, hours)  # type: ignore[arg-type]
    building = False
    await other
    assert year is not None
    assert year[-1][0] == pytest.approx(60.0)
    # Two passes over the hours, each giving way every 744 of them.
    assert turns >= 2 * (len(hours) // BACKFILL_YIELD_EVERY), turns
