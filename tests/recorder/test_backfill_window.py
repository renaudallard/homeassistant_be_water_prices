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

"""The price backfill against Home Assistant's own statistics compiler.

The recorder writes an hour's long-term rows itself, ten seconds after
the hour, and inserts them without an upsert. A row the backfill
imported first made that insert fail, and the recorder rolled back the
whole period for every entity in the house.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    async_import_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder.tasks import CompileMissingStatisticsTask
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
from custom_components.be_water_prices.coordinator import CoordinatorData
from custom_components.be_water_prices.sensor import EUR_PER_M3
from custom_components.be_water_prices.statistics import async_backfill_prices
from tests.test_ha_coordinator import _fresh_tariff

_TEMPERATURE = "sensor.temperature"


async def _backfill_after(
    hass: HomeAssistant, freezer: Any, compiled: int, backfill_at: timedelta
) -> tuple[datetime, list[Any]]:
    """Compile ``compiled`` five-minute periods of an hour, then backfill.

    Returns the hour the periods were compiled in and the rows handed to
    the recorder, which still go through to it.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    assert await async_setup_component(hass, "sensor", {})
    await hass.async_block_till_done()

    entry = MockConfigEntry(domain=DOMAIN, data={CONF_UTILITY: "vivaqua"})
    entry.add_to_hass(hass)
    basis = (
        er.async_get(hass)
        .async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_basis_rate", suggested_object_id="v_basis_rate"
        )
        .entity_id
    )
    coordinator = MagicMock()
    coordinator.data = CoordinatorData(
        tariff=_fresh_tariff(),
        fetched_at=datetime.now(UTC),
        snapshot_age_hours=0.0,
        snapshot_stale=False,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    # Past the run marker the recorder stamps on a new database.
    zero = dt_util.utcnow().replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    for period in range(compiled):
        moment = zero + timedelta(minutes=5 * period)
        freezer.move_to(moment)
        hass.states.async_set(
            basis, "2.5", {"state_class": "measurement", "unit_of_measurement": EUR_PER_M3}
        )
        hass.states.async_set(
            _TEMPERATURE, "20.0", {"state_class": "measurement", "unit_of_measurement": "°C"}
        )
        await async_wait_recording_done(hass)
        do_adhoc_statistics(hass, start=moment)
        await async_wait_recording_done(hass)

    imported: list[Any] = []

    def _import(*args: Any, **kwargs: Any) -> None:
        imported.append(args[2])
        async_import_statistics(*args, **kwargs)

    freezer.move_to(zero + backfill_at)
    with patch(
        "homeassistant.components.recorder.statistics.async_import_statistics",
        side_effect=_import,
    ):
        assert await async_backfill_prices(hass, entry, start=zero - timedelta(hours=3)) > 0
    await async_wait_recording_done(hass)
    return zero, imported


async def _temperature_hours(hass: HomeAssistant, start: datetime, hours: int) -> list[float]:
    stats = await hass.async_add_executor_job(
        statistics_during_period,
        hass,
        start,
        start + timedelta(hours=hours),
        {_TEMPERATURE},
        "hour",
        None,
        {"mean"},
    )
    return [row["mean"] for row in stats.get(_TEMPERATURE, [])]


@pytest.mark.asyncio
async def test_the_backfill_stays_behind_the_recorder(
    recorder_mock: None,
    hass: HomeAssistant,
    freezer: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A backfill at hh:00:05 must leave the hour that just ended alone.

    That hour is compiled at hh:00:10. Importing its row first cost every
    entity its hourly statistics for it, the temperature here included.
    """
    zero, imported = await _backfill_after(
        hass, freezer, compiled=11, backfill_at=timedelta(hours=1, seconds=5)
    )

    # The recorder's own compile of the hour, ten seconds after it.
    freezer.move_to(zero + timedelta(hours=1, seconds=10))
    do_adhoc_statistics(hass, start=zero + timedelta(minutes=55))
    await async_wait_recording_done(hass)

    assert "Blocked attempt to insert duplicated statistic rows" not in caplog.text
    assert await _temperature_hours(hass, zero, 1) == [20.0]
    # The last compiled hour ends at zero, and nothing was written past it.
    assert imported[-1][-1]["start"] + timedelta(hours=1) <= zero


@pytest.mark.asyncio
async def test_the_backfill_leaves_the_downtime_to_the_catch_up(
    recorder_mock: None,
    hass: HomeAssistant,
    freezer: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Every hour the host was off is the recorder's to compile at startup.

    The catch-up only runs once Home Assistant has started, after the
    setup-time backfill. Ending an hour before the clock is not enough
    here: only the recorder's own run marker says where it stopped.
    """
    zero, imported = await _backfill_after(
        hass, freezer, compiled=6, backfill_at=timedelta(hours=3, minutes=1)
    )

    get_instance(hass).queue_task(CompileMissingStatisticsTask())
    await async_wait_recording_done(hass)

    assert "Blocked attempt to insert duplicated statistic rows" not in caplog.text
    assert await _temperature_hours(hass, zero, 3) == [20.0, 20.0, 20.0]
    assert imported[-1][-1]["start"] + timedelta(hours=1) <= zero
