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

"""A refresh that outlives its entry must not re-create the stale Repair."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from custom_components.be_water_prices.coordinator import RecorderUnavailable
from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
from tests.test_ha_coordinator import _fresh_tariff

_GET = "custom_components.be_water_prices.coordinator.get"
_YTD = "custom_components.be_water_prices.coordinator._recorder_ytd_m3"
_FULL_YEAR = "custom_components.be_water_prices.coordinator._recorder_full_year_m3"


def _metered_entry(hass: HomeAssistant) -> MockConfigEntry:
    hass.states.async_set("sensor.water_meter", "100")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_WATER_METER_SENSOR: "sensor.water_meter",
        },
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    return entry


def _extractor(fetch: Any) -> WaterExtractor:
    return WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=fetch)


async def test_no_repair_card_for_an_entry_that_is_no_longer_loaded(hass: HomeAssistant) -> None:
    """The Repair fix flow's refresh runs in the flow's task, which unload does not cancel."""

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        stale = replace(coordinator.data, snapshot_stale=True)

        # Loaded: the card is raised, as before.
        coordinator._sync_repair_issue(stale)
        registry = ir.async_get(hass)
        assert registry.async_get_issue(DOMAIN, coordinator.stale_issue_id) is not None
        registry.async_delete(DOMAIN, coordinator.stale_issue_id)

        # Gone: a late refresh must not bring it back.
        entry.mock_state(hass, ConfigEntryState.NOT_LOADED)
        coordinator._sync_repair_issue(stale)
        assert registry.async_get_issue(DOMAIN, coordinator.stale_issue_id) is None


async def test_a_late_refresh_does_not_write_the_store_of_a_removed_entry(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Removal deletes the YTD file; the fetch in flight brought it back."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    gate = asyncio.Event()
    calls = 0

    async def _fetch(_session: Any) -> WaterTariff:
        nonlocal calls
        calls += 1
        if calls > 1:
            await gate.wait()
        return _fresh_tariff()

    entry = _metered_entry(hass)
    store_key = f"{DOMAIN}.{entry.entry_id}.ytd"
    with (
        patch(_GET, return_value=_extractor(_fetch)),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert store_key in hass_storage

        refresh = hass.loop.create_task(coordinator.async_refresh())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        assert calls == 2 and not gate.is_set()

        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert store_key not in hass_storage

        hass.states.async_set("sensor.water_meter", "107")
        gate.set()
        await refresh
        await hass.async_block_till_done()

    assert store_key not in hass_storage


async def test_a_late_refresh_does_not_raise_the_projection_card_after_unload(
    hass: HomeAssistant,
) -> None:
    """The stale card was gated in the previous round; the projection card was not."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    entered = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def _full_year(_hass: Any, _meter: str, _year: int) -> float | None:
        nonlocal calls
        calls += 1
        if calls == 1:
            # Leaves the memo unset, so the next refresh asks again.
            raise RecorderUnavailable("db busy")
        entered.set()
        await release.wait()
        return 131.0

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    entry = _metered_entry(hass)
    with (
        patch(_GET, return_value=_extractor(_fetch)),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
        patch(_FULL_YEAR, new=_full_year),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        task = hass.async_create_task(coordinator.async_refresh())
        await asyncio.wait_for(entered.wait(), 5)
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert entry.state is ConfigEntryState.NOT_LOADED
        release.set()
        await task
        await hass.async_block_till_done()

    assert ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id) is None


async def test_a_setup_that_fails_after_the_first_refresh_takes_its_cards_down(
    hass: HomeAssistant,
) -> None:
    """The failed-setup path forgot the coordinator but left both cards behind."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    yesterday = date.today() - timedelta(days=1)

    async def _stale(_session: Any) -> WaterTariff:
        return replace(_fresh_tariff(), valid_until=yesterday)

    entry = _metered_entry(hass)
    with (
        patch(_GET, return_value=_extractor(_stale)),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
        patch(_FULL_YEAR, new=AsyncMock(return_value=131.0)),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=RuntimeError("boom")
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id) is False
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR

    registry = ir.async_get(hass)
    assert registry.async_get_issue(DOMAIN, f"snapshot_stale_{entry.entry_id}") is None
    assert registry.async_get_issue(DOMAIN, f"projection_outdated_{entry.entry_id}") is None


async def test_the_coordinator_a_reload_replaced_no_longer_raises_cards(
    hass: HomeAssistant,
) -> None:
    """After a reload the entry is loaded again, but with another coordinator."""

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    with (
        patch(_GET, return_value=_extractor(_fetch)),
        patch(_YTD, new=AsyncMock(return_value=None)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        previous = hass.data[DOMAIN][entry.entry_id]
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()
        assert entry.state is ConfigEntryState.LOADED
        assert hass.data[DOMAIN][entry.entry_id] is not previous

        previous._sync_repair_issue(replace(previous.data, snapshot_stale=True))
    assert ir.async_get(hass).async_get_issue(DOMAIN, previous.stale_issue_id) is None


async def test_a_replaced_coordinator_does_not_follow_the_new_meter(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A late refresh resolved the meter and re-subscribed a dead coordinator to it.

    One reading later its debounced save wrote the deleted YTD file back.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.meter_a", "100")
    hass.states.async_set("sensor.meter_b", "500")
    gate = asyncio.Event()
    calls = 0

    async def _fetch(_session: Any) -> WaterTariff:
        nonlocal calls
        calls += 1
        if calls == 2:
            await gate.wait()
        return _fresh_tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_WATER_METER_SENSOR: "sensor.meter_a"},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    store_key = f"{DOMAIN}.{entry.entry_id}.ytd"
    with (
        patch(_GET, return_value=_extractor(_fetch)),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        old = hass.data[DOMAIN][entry.entry_id]

        # Retry on the stale card: a refresh in the flow's task, with a slow fetch.
        refresh = hass.loop.create_task(old.async_refresh())
        for _ in range(6):
            await asyncio.sleep(0)
        assert calls == 2 and not gate.is_set()

        # Meanwhile the meter is changed in Options: the listener reloads.
        hass.config_entries.async_update_entry(
            entry,
            options={CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_WATER_METER_SENSOR: "sensor.meter_b"},
        )
        for _ in range(300):
            if (
                entry.state is ConfigEntryState.LOADED
                and hass.data[DOMAIN].get(entry.entry_id) is not old
            ):
                break
            await asyncio.sleep(0.01)
        assert hass.data[DOMAIN][entry.entry_id] is not old
        assert old._meter_unsub is None

        gate.set()
        await refresh
        await hass.async_block_till_done()
        assert not old._owns_the_entry()
        assert old._meter_unsub is None, "the replaced coordinator re-subscribed to the new meter"

        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert store_key not in hass_storage

        hass.states.async_set("sensor.meter_b", "510")
        await hass.async_block_till_done()
        async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=40))
        await hass.async_block_till_done()
    assert store_key not in hass_storage


async def test_the_replaced_coordinator_is_retired_while_the_new_setup_runs(
    hass: HomeAssistant,
) -> None:
    """Read from the entry's state alone, the old coordinator still owned the entry
    while the reload's new setup was in progress and the bucket not yet filled."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    gate = asyncio.Event()
    calls = 0

    async def _fetch(_session: Any) -> WaterTariff:
        nonlocal calls
        calls += 1
        if calls >= 2:
            await gate.wait()
        return _fresh_tariff()

    entry = _metered_entry(hass)
    with (
        patch(_GET, return_value=_extractor(_fetch)),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        old = hass.data[DOMAIN][entry.entry_id]

        refresh = hass.loop.create_task(old.async_refresh())
        for _ in range(6):
            await asyncio.sleep(0)
        assert calls == 2

        reload = hass.loop.create_task(hass.config_entries.async_reload(entry.entry_id))
        for _ in range(300):
            if calls == 3:
                break
            await asyncio.sleep(0.01)
        assert calls == 3
        assert entry.state is ConfigEntryState.SETUP_IN_PROGRESS
        assert entry.entry_id not in hass.data.get(DOMAIN, {})
        assert not old._owns_the_entry()

        # The old refresh lands inside the window: no owner save, no card.
        hass.states.async_set("sensor.water_meter", "130")
        gate.set()
        await refresh
        assert old._cycle_dirty
        old._sync_repair_issue(replace(old.data, snapshot_stale=True))
        assert ir.async_get(hass).async_get_issue(DOMAIN, old.stale_issue_id) is None
        await reload
        await hass.async_block_till_done()
