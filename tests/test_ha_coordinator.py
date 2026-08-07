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

"""HA-driven coordinator tests.

Cover the cached-fallback path on extractor failure and the
Repair-issue lifecycle (created on stale snapshot, cleared on
fresh fetch and on entry unload).
"""

from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from custom_components.be_water_prices.coordinator import RecorderUnavailable
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    WaterExtractor,
    WaterTariff,
)
from custom_components.be_water_prices.repairs import async_create_fix_flow


def _fresh_tariff(valid_until: date | None = None) -> WaterTariff:
    return WaterTariff(
        utility="vivaqua",
        region="brussels",
        valid_from=date(2026, 1, 1),
        valid_until=valid_until if valid_until is not None else date(2030, 12, 31),
        publication_label="VIVAQUA test 2026",
        source_url="https://example.invalid/",
        yearly_fixed_fee=40.23 / 1.06,
        linear_eur_per_m3=2.62 / 1.06,
        sanering_gemeentelijk_eur_per_m3=2.73 / 1.06,
    )


async def _setup_entry(hass: HomeAssistant, fetch_callable: Any) -> MockConfigEntry:
    # The integration is Belgium-only and production code reads HA-local
    # time (the valid_until staleness check uses dt_util.now().date()).
    # The harness defaults to US/Pacific which silently flips date
    # comparisons during the ~8 h overnight window where the system UTC
    # date is one day ahead of Pacific, making CI flaky around midnight
    # UTC. Pin to Europe/Brussels so the test clock matches the user's.
    await hass.config.async_set_time_zone("Europe/Brussels")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(
        id="vivaqua",
        label="VIVAQUA",
        region="brussels",
        fetch=fetch_callable,
    )
    with patch(
        "custom_components.be_water_prices.coordinator.get",
        return_value=fake,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@pytest.mark.asyncio
async def test_successful_fetch_does_not_raise_repair_issue(hass: HomeAssistant) -> None:
    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    entry = await _setup_entry(hass, _fetch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data is not None
    assert coordinator.data.snapshot_stale is False

    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is None


@pytest.mark.asyncio
async def test_expired_valid_until_raises_repair_issue(hass: HomeAssistant) -> None:
    yesterday = date.today() - timedelta(days=1)

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff(valid_until=yesterday)

    entry = await _setup_entry(hass, _fetch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data is not None
    assert coordinator.data.snapshot_stale is True

    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id)
    assert issue is not None
    assert issue.severity == ir.IssueSeverity.WARNING
    assert issue.translation_key == "snapshot_stale"


@pytest.mark.asyncio
async def test_repair_issue_clears_when_next_fetch_is_fresh(hass: HomeAssistant) -> None:
    yesterday = date.today() - timedelta(days=1)
    fetch_results = [_fresh_tariff(valid_until=yesterday), _fresh_tariff()]

    async def _fetch(_session: Any) -> WaterTariff:
        return fetch_results.pop(0)

    entry = await _setup_entry(hass, _fetch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is not None

    # Manually trigger a second refresh to consume the fresh fixture.
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert coordinator.data is not None
    assert coordinator.data.snapshot_stale is False
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is None


@pytest.mark.asyncio
async def test_extractor_error_serves_cached_snapshot(hass: HomeAssistant) -> None:
    fetch_results: list[Any] = [_fresh_tariff(), ExtractorError("HTTP 503 from upstream")]

    async def _fetch(_session: Any) -> WaterTariff:
        result = fetch_results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    entry = await _setup_entry(hass, _fetch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    first = coordinator.data
    assert first is not None
    assert first.last_error == ""

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    second = coordinator.data
    assert second is not None
    # Cached snapshot is served; tariff identity preserved.
    assert second.tariff is first.tariff
    assert "HTTP 503" in second.last_error


@pytest.mark.asyncio
async def test_repair_fix_flow_triggers_coordinator_refresh(hass: HomeAssistant) -> None:
    yesterday = date.today() - timedelta(days=1)
    fetch_results = [_fresh_tariff(valid_until=yesterday), _fresh_tariff()]

    async def _fetch(_session: Any) -> WaterTariff:
        return fetch_results.pop(0)

    entry = await _setup_entry(hass, _fetch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id)
    assert issue is not None and issue.is_fixable

    # Walk the fix flow: open it, then submit the confirmation step.
    flow = await async_create_fix_flow(hass, coordinator.stale_issue_id, issue.data)
    flow.hass = hass
    # The RepairsFlowManager sets these before the first step in production.
    flow.handler = DOMAIN
    flow.issue_id = coordinator.stale_issue_id
    result = await flow.async_step_init()
    assert result["type"] == "form"
    # The form forwards the issue's placeholders so {utility} etc. render
    # instead of literal braces.
    assert result["description_placeholders"]["utility"] == "VIVAQUA"
    result = await flow.async_step_init({})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    # The fix flow consumed the second (fresh) fetch result, so the
    # snapshot is no longer stale and the issue cleared itself.
    assert coordinator.data is not None
    assert coordinator.data.snapshot_stale is False
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is None


@pytest.mark.asyncio
async def test_one_absurd_reading_does_not_pin_the_year(hass: HomeAssistant) -> None:
    """A lone garbage spike must not become the year's high-water mark.

    The mark only ever climbs, so accepting a spike pins both the volume
    and the cost to it until January, and it gets persisted on the way.
    A real catch-up repeats the reading and is accepted on the next one.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # A 32-bit sentinel escaping the meter integration.
        hass.states.async_set("sensor.water_meter", "4294967.295")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # Normal readings resume and the mark is still where it was.
        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0

        # A genuine catch-up repeats itself and is taken on the second read.
        hass.states.async_set("sensor.water_meter", "400")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0
        hass.states.async_set("sensor.water_meter", "401")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 321.0


@pytest.mark.asyncio
async def test_recorder_fallback_does_not_publish_below_the_live_mark(
    hass: HomeAssistant,
) -> None:
    """A mid-year dropout must not republish a lower year-to-date volume.

    The recorder's daily total trails the live meter, so serving it raw
    after a dropout drops the m3 figure. That sensor is a TOTAL with a
    Jan 1 last_reset, and the statistics engine reads a same-cycle
    decrease as a reset, re-adding the whole figure to the long-term sum.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=20.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # Draw up to 130: baseline 80, live mark 130, so YTD is 50.
        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 50.0

        # The meter drops out and the recorder's daily total trails it.
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        recorder.return_value = 49.7
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 50.0


@pytest.mark.asyncio
async def test_removing_the_entry_deletes_its_ytd_store(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The persisted YTD anchor must not outlive the entry that wrote it.

    Unload only flushes the anchor. Without a removal hook the file stayed
    in .storage forever, and re-adding the same utility restored a baseline
    belonging to the deleted entry.
    """
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        await hass.config.async_set_time_zone("Europe/Brussels")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        store_key = f"{DOMAIN}.{entry.entry_id}.ytd"
        assert store_key in hass_storage

        assert await hass.config_entries.async_remove(entry.entry_id)
        await hass.async_block_till_done()
        assert store_key not in hass_storage


@pytest.mark.asyncio
async def test_repair_fix_flow_keeps_the_issue_when_still_stale(hass: HomeAssistant) -> None:
    """A retry that did not help must leave the Repair card in place.

    Completing the flow makes the Repairs manager delete the issue, so a
    still-stale snapshot would silently lose its card until the next daily
    tick recreated it, a day later.
    """
    yesterday = date.today() - timedelta(days=1)

    async def _fetch(_session: Any) -> WaterTariff:
        # Every fetch stays stale, so the retry cannot clear the issue.
        return _fresh_tariff(valid_until=yesterday)

    entry = await _setup_entry(hass, _fetch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    issue_reg = ir.async_get(hass)
    issue = issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id)
    assert issue is not None

    flow = await async_create_fix_flow(hass, coordinator.stale_issue_id, issue.data)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = coordinator.stale_issue_id
    await flow.async_step_init()
    result = await flow.async_step_init({})
    await hass.async_block_till_done()

    assert result["type"] == "abort"
    assert result["reason"] == "still_stale"
    # The abort text names the utility, so it needs the issue's
    # placeholders forwarded just like the form step above.
    assert result["description_placeholders"]["utility"] == "VIVAQUA"
    assert coordinator.data is not None
    assert coordinator.data.snapshot_stale is True
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is not None


@pytest.mark.asyncio
async def test_meter_state_change_updates_ytd_live(hass: HomeAssistant) -> None:
    """A water draw (meter state change) updates YTD cost/consumption live.

    The daily recorder query is stubbed to anchor a Jan 1 baseline; from
    there the running total must track the meter without another query.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    # Seed the meter before setup so _compute_ytd captures the baseline.
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        # baseline = live(100) - recorder_ytd(20) == reading at Jan 1.
        assert coordinator._ytd.offset_m3 == 80.0
        assert coordinator.data.ytd_consumption_m3 == 20.0
        cost_before = coordinator.data.current_year_cost_eur
        assert cost_before is not None

        # Draw 5 m³: 100 -> 105. YTD jumps 20 -> 25 with no recorder call.
        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0
        assert coordinator.data.current_year_cost_eur > cost_before

        # A same-value re-report (here an attribute-only state event) must
        # not republish: the data object is left untouched, so no redundant
        # recorder row is written for every sensor.
        unchanged = coordinator.data
        hass.states.async_set("sensor.water_meter", "105", {"updated": 1})
        await hass.async_block_till_done()
        assert coordinator.data is unchanged

        # A flapping meter (unavailable) must not blank the running total.
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0


async def test_meter_events_do_not_starve_the_daily_refresh(hass: HomeAssistant) -> None:
    """A water draw must not push the daily tariff refresh out of reach.

    Publishing the live figure through async_set_updated_data cancels and
    re-arms the update_interval timer. A meter reports far more often than
    once a day, so the tariff fetch would never come due again and the
    snapshot would stay frozen until HA restarts.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")
    fetches = 0

    async def _fetch(_session: Any) -> WaterTariff:
        nonlocal fetches
        fetches += 1
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        assert fetches == 1

        coordinator = hass.data[DOMAIN][entry.entry_id]
        # The pending refresh is stored as the TimerHandle's bound cancel().
        scheduled = coordinator._unsub_refresh
        assert scheduled is not None

        # Draws keep arriving between ticks. None of them may cancel or
        # replace the pending refresh, or it never comes due.
        for reading in ("105", "110", "115"):
            hass.states.async_set("sensor.water_meter", reading)
            await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 35.0
        assert fetches == 1
        assert coordinator._unsub_refresh is scheduled
        assert not scheduled.__self__.cancelled()


async def test_live_tracking_follows_a_changed_auto_discovered_meter(
    hass: HomeAssistant,
) -> None:
    """Live tracking must move when auto-discovery resolves another meter.

    The Energy dashboard's water source can change with no options change,
    so nothing reloads the entry. A subscription left behind on the old
    entity would feed that meter's unrelated cumulative reading into the
    running total.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.meter_a", "100")
    hass.states.async_set("sensor.meter_b", "500")
    discovered = "sensor.meter_a"

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _discover(_hass: HomeAssistant) -> str | None:
        return discovered

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
            "custom_components.be_water_prices.coordinator._discover_energy_water_meter",
            new=_discover,
        ),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # The dashboard is re-pointed at another meter; the next tick
        # re-anchors on it (baseline 500 - 20).
        discovered = "sensor.meter_b"
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator._meter_entity_id == "sensor.meter_b"
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # The abandoned meter must no longer reach the cycle.
        hass.states.async_set("sensor.meter_a", "100.3")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # The new meter must.
        hass.states.async_set("sensor.meter_b", "505")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0


@pytest.mark.asyncio
async def test_litre_meter_is_converted_to_m3(hass: HomeAssistant) -> None:
    """A meter reporting litres is converted to m³ before the YTD math.

    Without unit normalisation a litre reading would be billed as if it
    were already cubic metres (~1000× too high).
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    # 100 000 L == 100 m³, seeded before setup so _compute_ytd anchors it.
    hass.states.async_set("sensor.water_meter", "100000", {"unit_of_measurement": "L"})

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        # live 100 m³ - recorder_ytd 20 m³ == reading at Jan 1.
        assert coordinator._ytd.offset_m3 == 80.0
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # Draw 5 m³ == 5000 L: 100 000 -> 105 000 L. YTD jumps 20 -> 25 m³.
        hass.states.async_set("sensor.water_meter", "105000", {"unit_of_measurement": "L"})
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0


@pytest.mark.asyncio
async def test_live_ytd_reanchors_on_year_rollover(hass: HomeAssistant) -> None:
    """A meter event after Jan 1 must not report the stale prior-year baseline.

    Until the next daily tick re-anchors, a live event would otherwise
    compute ``live - last_year_baseline`` -- nearly a full extra year.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        # Pretend the baseline was anchored last year and never re-ticked.
        # The recorder figure has to age with it: a current-year recorder
        # year alongside a prior-year baseline means the tick did run this
        # year and published a current-year figure, which is the recovery
        # case below rather than a rollover.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)

        # A draw to 130 m³ would naively read 130 - 80 == 50 m³ YTD; the
        # rollover guard re-anchors to 130, so YTD resets to ~0 instead.
        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 0.0
        assert coordinator._ytd.year == dt_util.now().year


@pytest.mark.asyncio
async def test_live_ytd_recovers_after_meter_unavailable_at_tick(hass: HomeAssistant) -> None:
    """A meter dropout at the daily tick must not freeze live YTD all day.

    The recorder figure still surfaces, and once the meter returns the
    baseline is reconstructed so live tracking resumes immediately.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    # Meter is unavailable at the moment of the setup (daily) tick.
    hass.states.async_set("sensor.water_meter", "unavailable")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        # No baseline could be captured, but the recorder YTD still shows.
        assert coordinator._ytd.offset_m3 is None
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # Meter returns at 100 m³: baseline reconstructed as 100 - 20 = 80.
        hass.states.async_set("sensor.water_meter", "100")
        await hass.async_block_till_done()
        assert coordinator._ytd.offset_m3 == 80.0
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # A further draw now tracks live: 105 -> 25 m³.
        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0


@pytest.mark.asyncio
async def test_live_ytd_recovery_resets_when_meter_down_across_year_boundary(
    hass: HomeAssistant,
) -> None:
    """Meter down at the year-N tick, recovering in year N+1, must reset to ~0.

    The recovery branch must not reconstruct from the stale prior-year
    recorder figure, which would report nearly a full extra year.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "unavailable")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.offset_m3 is None
        # Pretend the recorder figure (20) was captured in the prior year.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)

        # Meter returns at 100 in the new year: a same-year recovery would
        # anchor 100-20=80 and report 20; the cross-year guard anchors to
        # 100 so YTD resets to ~0 instead.
        hass.states.async_set("sensor.water_meter", "100")
        await hass.async_block_till_done()
        assert coordinator._ytd.offset_m3 == 100.0
        assert coordinator.data.ytd_consumption_m3 == 0.0


@pytest.mark.asyncio
async def test_live_ytd_republishes_when_only_cost_changes(hass: HomeAssistant) -> None:
    """The dedup must not suppress a cost-only change (year rollover /
    midnight fee proration) when the volume figure is unchanged.
    """
    from dataclasses import replace

    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator = hass.data[DOMAIN][entry.entry_id]
        real_cost = coordinator.data.current_year_cost_eur
        assert real_cost is not None
        # Simulate a stale cost (e.g. left over from a prior-year tick)
        # while the volume figure stays the same.
        coordinator.async_set_updated_data(replace(coordinator.data, current_year_cost_eur=999.0))

        # A same-value re-report: YTD stays 20, but the cost must be
        # recomputed and republished, not left at the stale 999.
        hass.states.async_set("sensor.water_meter", "100", {"tick": 1})
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 20.0
        assert coordinator.data.current_year_cost_eur == real_cost


@pytest.mark.asyncio
async def test_live_ytd_baseline_survives_restart(hass: HomeAssistant) -> None:
    """A restart must not snap the running cost down to the recorder total.

    The Jan 1 baseline is persisted, so after a restart YTD stays
    ``live - baseline`` instead of re-deriving from the recorder's
    trailing daily figure (which lags the live meter and used to pull the
    published cost downward on every restart / reload).
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=20.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        # baseline = live(100) - recorder_ytd(20) == reading at Jan 1.
        assert coordinator._ytd.offset_m3 == 80.0

        # Draw 30 m³ live: 100 -> 130, YTD 20 -> 50.
        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 50.0

        # Restart: unload, the recorder now trails the live meter (45 < the
        # true 50 because its daily statistics lag), then set up again.
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        recorder.return_value = 45.0
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator2 = hass.data[DOMAIN][entry.entry_id]
        # Baseline restored from the Store (80), not re-derived to 130-45=85.
        assert coordinator2._ytd.offset_m3 == 80.0
        # YTD stays live-baseline (50), no downward snap to the recorder's 45.
        assert coordinator2.data.ytd_consumption_m3 == 50.0


@pytest.mark.asyncio
async def test_live_ytd_ignores_meter_glitch_down(hass: HomeAssistant) -> None:
    """A momentary lower meter reading must not drop the running YTD.

    A year-to-date figure is monotonic within the year, so a glitch or a
    down-rounded cumulative reading is clamped to the cycle high-water
    mark rather than published as a decrease.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # Draw to 105 m³: YTD 20 -> 25.
        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0
        cost_peak = coordinator.data.current_year_cost_eur

        # Meter glitches 1 m³ backward (104): YTD must hold at 25, not drop.
        hass.states.async_set("sensor.water_meter", "104")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0
        assert coordinator.data.current_year_cost_eur == cost_peak

        # A real draw past the mark (106) resumes climbing: 25 -> 26.
        hass.states.async_set("sensor.water_meter", "106")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 26.0


def _brussels_tariff(linear_incl_vat: float) -> WaterTariff:
    """A Brussels tariff whose linear rate is tunable to force a cost change."""
    return WaterTariff(
        utility="vivaqua",
        region="brussels",
        valid_from=date(2026, 1, 1),
        valid_until=date(2030, 12, 31),
        publication_label="VIVAQUA test 2026",
        source_url="https://example.invalid/",
        yearly_fixed_fee=40.23 / 1.06,
        linear_eur_per_m3=linear_incl_vat / 1.06,
        sanering_gemeentelijk_eur_per_m3=2.73 / 1.06,
    )


@pytest.mark.asyncio
async def test_live_ytd_cost_held_when_tariff_drops(hass: HomeAssistant) -> None:
    """A later fetch with a lower tariff must not drop the running cost.

    Consumption is monotonic (the m³ high-water mark), but the EUR cost is
    recomputed each tick from the fetched tariff. A transient lower-but-valid
    tariff (an extractor mis-parse, or a genuine downward correction) is
    clamped to the cycle cost high-water mark so the running bill never
    decreases while consumption stays flat.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    # First fetch (setup) is the normal rate; every later fetch returns a
    # strictly lower linear rate, so the un-clamped cost would drop.
    calls = {"n": 0}

    async def _fetch(_session: Any) -> WaterTariff:
        calls["n"] += 1
        return _brussels_tariff(2.62) if calls["n"] == 1 else _brussels_tariff(1.50)

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        cost_peak = coordinator.data.current_year_cost_eur
        assert cost_peak is not None

        # Daily tick fetches the lower tariff; the meter has not moved.
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        # m³ is flat and the new tariff is cheaper, so the un-clamped cost
        # would fall; the floor holds it at the peak.
        assert coordinator.data.ytd_consumption_m3 == 20.0
        assert coordinator.data.current_year_cost_eur == cost_peak


@pytest.mark.asyncio
async def test_ytd_cost_floor_survives_restart(hass: HomeAssistant) -> None:
    """The cost high-water mark is persisted, so a lower tariff after a
    restart is still clamped to the pre-restart peak."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    rate = {"linear": 2.62}

    async def _fetch(_session: Any) -> WaterTariff:
        return _brussels_tariff(rate["linear"])

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        cost_peak = coordinator.data.current_year_cost_eur
        assert cost_peak is not None

        # Restart with a cheaper tariff in place.
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        rate["linear"] = 1.50
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator2 = hass.data[DOMAIN][entry.entry_id]
        # cost_hwm restored from the Store clamps the cheaper recomputed cost.
        assert coordinator2._ytd.cost == cost_peak
        assert coordinator2.data.current_year_cost_eur == cost_peak


@pytest.mark.asyncio
async def test_ytd_cost_floor_drops_on_rollover_while_meter_offline(hass: HomeAssistant) -> None:
    """A meter offline across Jan 1 must still drop the cost to the new year.

    The cost floor is reset when the persisted cycle is from a prior year, so
    the recorder-fallback path (meter unavailable) serves the small new-year
    figure rather than clamping it up to last year's peak.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=20.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.data.ytd_consumption_m3 == 20.0
        cost_peak = coordinator.data.current_year_cost_eur
        assert cost_peak is not None

        # Meter goes offline and stays down across the Jan 1 rollover: the
        # persisted cycle is from last year, the meter reads unavailable, and
        # the recorder reports a small new-year figure. The cost mark is
        # stamped with the year it was raised in, so age it alongside the
        # baseline rather than leaving it looking like this year's.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        recorder.return_value = 2.0
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # Recorder fallback serves the new-year consumption, and the cost drops
        # with it instead of being pinned to last year's peak.
        assert coordinator.data.ytd_consumption_m3 == 2.0
        cost_now = coordinator.data.current_year_cost_eur
        assert cost_now is not None
        assert cost_now < cost_peak


@pytest.mark.asyncio
async def test_meter_recovery_keeps_the_new_year_recorder_figure(hass: HomeAssistant) -> None:
    """Recovering after a rollover dropout must not discard the year's usage.

    The tick keeps a prior-year baseline while the meter is down and serves
    the current-year recorder figure. On recovery the live path has to
    reconstruct the baseline from that figure; re-anchoring on the raw
    reading instead would publish a decrease inside the same year.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=20.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # Down across Jan 1: last year's cycle kept, recorder serves 5 m³.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        recorder.return_value = 5.0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 5.0

        # The meter comes back. Those 5 m³ are this year's and must survive.
        hass.states.async_set("sensor.water_meter", "1000.5")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 5.0
        assert coordinator._ytd.offset_m3 == 995.5

        # And it tracks from there.
        hass.states.async_set("sensor.water_meter", "1003.5")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 8.0


@pytest.mark.asyncio
async def test_live_ytd_hwm_survives_restart_against_glitch(hass: HomeAssistant) -> None:
    """The climbing high-water mark is persisted across a restart.

    A live draw advances the mark between daily ticks. Without persisting
    that climb, a restart reverted the mark to the bootstrap reading, and
    the first low-but-valid reading after the restart was published as a
    decrease. The mark must survive so the glitch is still clamped.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.offset_m3 == 80.0

        # Live draw to 130 between daily ticks: mark climbs, YTD 20 -> 50.
        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 50.0

        # Restart, then the meter momentarily reports 120 at setup time -- a
        # glitch below the climbed mark but above the Jan 1 baseline (80).
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        hass.states.async_set("sensor.water_meter", "120")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        coordinator2 = hass.data[DOMAIN][entry.entry_id]
        # The persisted mark (130) clamps the glitch: YTD holds at 50, not
        # the un-clamped 120 - 80 = 40.
        assert coordinator2._ytd.m3 == 50.0
        assert coordinator2.data.ytd_consumption_m3 == 50.0


async def _setup_metered_entry(hass: HomeAssistant) -> Any:
    """Set up a Brussels entry with a meter at 100 m³ and a Jan 1 baseline of 80."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.offset_m3 == 80.0
        # Draw to 105 m³ so the cycle has a high-water mark to hold: YTD 25.
        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0
        return coordinator


@pytest.mark.asyncio
async def test_live_ytd_single_subbaseline_reading_is_held(hass: HomeAssistant) -> None:
    """A single reading below the baseline is a glitch, not a meter swap.

    A rebooting meter that momentarily reports a value below the Jan 1 anchor
    must not floor the year-to-date figure to ~0; it is held until a swap is
    confirmed by repeated low readings.
    """
    coordinator = await _setup_metered_entry(hass)

    # One reading well below the baseline (80): held at 25, not floored to 0.
    hass.states.async_set("sensor.water_meter", "50")
    await hass.async_block_till_done()
    assert coordinator.data.ytd_consumption_m3 == 25.0

    # A reading back above the baseline clears the run and resumes climbing.
    hass.states.async_set("sensor.water_meter", "106")
    await hass.async_block_till_done()
    assert coordinator.data.ytd_consumption_m3 == 26.0


@pytest.mark.asyncio
async def test_live_ytd_sustained_subbaseline_reanchors(hass: HomeAssistant) -> None:
    """Several consecutive sub-baseline readings are a genuine meter swap.

    The new meter climbs from ~0, so once enough consecutive readings sit
    below the old anchor the cycle re-anchors and YTD restarts at ~0.
    """
    coordinator = await _setup_metered_entry(hass)

    # New meter climbing from ~10 -- three distinct sub-baseline readings.
    for reading in ("10", "11", "12"):
        hass.states.async_set("sensor.water_meter", reading)
        await hass.async_block_till_done()

    # The third consecutive sub-baseline reading confirms the swap.
    assert coordinator._ytd.offset_m3 == 12.0
    assert coordinator.data.ytd_consumption_m3 == 0.0


@pytest.mark.asyncio
async def test_repair_issue_cleared_on_entry_unload(hass: HomeAssistant) -> None:
    yesterday = date.today() - timedelta(days=1)

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff(valid_until=yesterday)

    entry = await _setup_entry(hass, _fetch)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    issue_reg = ir.async_get(hass)
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is not None

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is None


@pytest.mark.asyncio
async def test_recorder_ytd_query_shape_and_summing(hass: HomeAssistant) -> None:
    """Pin the recorder query itself, which every other test mocks away.

    The three decisions here all move money: reading "change" rather than
    the all-time "sum", asking for m3 so a litre meter is not summed a
    thousand times too high, and flooring a meter swap's negative delta.
    Patched with autospec so the call signature is part of the assertion:
    an HA signature change surfaces here instead of at a user's next tick.
    """
    from unittest.mock import MagicMock

    from homeassistant.util.unit_conversion import VolumeConverter

    from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

    instance = MagicMock()

    async def _run(func: Any, *args: Any) -> Any:
        return func(*args)

    instance.async_add_executor_job = _run
    with (
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            autospec=True,
        ) as stats,
        patch("homeassistant.components.recorder.get_instance", return_value=instance),
    ):
        stats.return_value = {"sensor.wm": [{"change": 10.0}, {"change": 5.5}, {"change": None}]}
        total = await _recorder_ytd_m3(hass, "sensor.wm", date(2026, 1, 1), date(2026, 6, 30))

    assert total == 15.5
    _hass, _start, _end, ids, period, units, types = stats.call_args.args
    assert ids == {"sensor.wm"}
    assert period == "day"
    assert types == {"change"}
    assert units == {VolumeConverter.UNIT_CLASS: UnitOfVolume.CUBIC_METERS}


@pytest.mark.asyncio
async def test_recorder_ytd_floors_a_meter_swap(hass: HomeAssistant) -> None:
    """Replacing a meter mid-year must not surface a negative year to date."""
    from unittest.mock import MagicMock

    from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

    def _stats(*_args: Any) -> dict[str, list[dict[str, Any]]]:
        return {"sensor.wm": [{"change": 10.0}, {"change": -30.0}]}

    instance = MagicMock()

    async def _run(func: Any, *args: Any) -> Any:
        return func(*args)

    instance.async_add_executor_job = _run
    with (
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            new=_stats,
        ),
        patch("homeassistant.components.recorder.get_instance", return_value=instance),
    ):
        assert await _recorder_ytd_m3(hass, "sensor.wm", date(2026, 1, 1), date(2026, 6, 30)) == 0.0


@pytest.mark.asyncio
async def test_recorder_ytd_reads_no_statistics_as_zero(hass: HomeAssistant) -> None:
    """A successful query over an empty year answers zero, not unknown.

    An empty year may be anchored at zero; an unreadable one may not. The
    caller can only tell them apart if the query says which happened.
    """
    from unittest.mock import MagicMock

    from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

    instance = MagicMock()

    async def _run(func: Any, *args: Any) -> Any:
        return func(*args)

    instance.async_add_executor_job = _run
    for rows in ({}, {"sensor.wm": []}, {"sensor.wm": [{"change": None}]}):

        def _stats(*_args: Any, _rows: Any = rows) -> Any:
            return _rows

        with (
            patch(
                "homeassistant.components.recorder.statistics.statistics_during_period",
                new=_stats,
            ),
            patch("homeassistant.components.recorder.get_instance", return_value=instance),
        ):
            got = await _recorder_ytd_m3(hass, "sensor.wm", date(2026, 1, 1), date(2026, 6, 30))
        assert got == 0.0


@pytest.mark.asyncio
async def test_recorder_ytd_reads_an_absent_recorder_as_zero(hass: HomeAssistant) -> None:
    """A recorder that is not running is an empty year, not a failed read.

    An install without default_config that never enabled the recorder has
    the component importable but no instance behind it. Reporting that as
    unreadable makes the caller wait for a recovery that cannot come, and
    both YTD sensors would sit unknown for the life of the install.
    """
    from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

    def _no_instance(*_args: Any) -> Any:
        raise KeyError("recorder")

    with patch("homeassistant.components.recorder.get_instance", new=_no_instance):
        got = await _recorder_ytd_m3(hass, "sensor.wm", date(2026, 1, 1), date(2026, 6, 30))

    assert got == 0.0


@pytest.mark.asyncio
async def test_recorder_ytd_raises_when_the_query_fails(hass: HomeAssistant) -> None:
    """A query that could not run is reported as such, not as an empty year."""
    from unittest.mock import MagicMock

    from custom_components.be_water_prices.coordinator import (
        RecorderUnavailable,
        _recorder_ytd_m3,
    )

    def _stats(*_args: Any) -> Any:
        raise RuntimeError("database is locked")

    instance = MagicMock()

    async def _run(func: Any, *args: Any) -> Any:
        return func(*args)

    instance.async_add_executor_job = _run
    with (
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            new=_stats,
        ),
        patch("homeassistant.components.recorder.get_instance", return_value=instance),
        pytest.raises(RecorderUnavailable),
    ):
        await _recorder_ytd_m3(hass, "sensor.wm", date(2026, 1, 1), date(2026, 6, 30))


@pytest.mark.asyncio
async def test_energy_dashboard_discovery_and_override(hass: HomeAssistant) -> None:
    """Auto-discovery reads the Energy dashboard, and the option wins.

    Nothing exercised the discovery walk itself: the malformed entries it
    steps over, the non-water sources it skips, or the fact that an
    explicit option short-circuits it before the energy component is even
    consulted.
    """
    from unittest.mock import MagicMock

    from custom_components.be_water_prices.coordinator import (
        _discover_energy_water_meter,
    )

    manager = MagicMock()
    manager.data = {
        "energy_sources": [
            {"type": "grid", "stat_energy_from": "sensor.grid"},
            "a malformed entry",
            {"type": "water", "stat_energy_from": "sensor.wm"},
            {"type": "water", "stat_energy_from": "sensor.second_wm"},
        ]
    }

    async def _get_manager(_hass: HomeAssistant) -> Any:
        return manager

    with patch("homeassistant.components.energy.async_get_manager", new=_get_manager):
        assert await _discover_energy_water_meter(hass) == "sensor.wm"

        # No water source configured, and no energy data at all.
        manager.data = {"energy_sources": [{"type": "grid", "stat_energy_from": "sensor.grid"}]}
        assert await _discover_energy_water_meter(hass) is None
        manager.data = None
        assert await _discover_energy_water_meter(hass) is None

    async def _raises(_hass: HomeAssistant) -> Any:
        raise RuntimeError("energy component not set up")

    with patch("homeassistant.components.energy.async_get_manager", new=_raises):
        assert await _discover_energy_water_meter(hass) is None


@pytest.mark.asyncio
async def test_explicit_meter_option_wins_over_discovery(hass: HomeAssistant) -> None:
    """The OptionsFlow override must not consult the Energy dashboard."""

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _must_not_run(_hass: HomeAssistant) -> str | None:
        raise AssertionError("discovery ran despite an explicit override")

    hass.states.async_set("sensor.chosen", "100")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_WATER_METER_SENSOR: "sensor.chosen",
        },
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._discover_energy_water_meter",
            new=_must_not_run,
        ),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        await hass.config.async_set_time_zone("Europe/Brussels")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._meter_entity_id == "sensor.chosen"


@pytest.mark.asyncio
async def test_unusable_meter_readings_leave_the_total_alone(hass: HomeAssistant) -> None:
    """Garbage states must be ignored, not folded into the running total.

    Only the "unavailable" branch was covered. A non-numeric state and a
    reading carrying a unit that is not a volume both reach the same
    sanitiser, and either one landing in the cycle would corrupt the year.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0

        # A meter integration emitting a non-numeric placeholder.
        hass.states.async_set("sensor.water_meter", "n/a")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0

        # A numeric reading whose unit is not a volume at all. Kept close
        # to the last good value so the implausible-jump hold is not what
        # rejects it and the unit check is genuinely under test.
        hass.states.async_set("sensor.water_meter", "106", {"unit_of_measurement": "kWh"})
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0

        # Recovering with a real reading resumes tracking.
        hass.states.async_set("sensor.water_meter", "110")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 30.0


@pytest.mark.asyncio
async def test_repointed_meter_does_not_inherit_the_old_meter_baseline(
    hass: HomeAssistant,
) -> None:
    """A re-pointed meter must not be anchored with the old meter's usage.

    The tick moves the subscription to the newly resolved meter and then
    awaits the recorder. If the new meter reports during that await, the
    live path finds no baseline and would reconstruct one from the figure
    still on screen, which belongs to the meter just left behind.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.meter_a", "100")
    hass.states.async_set("sensor.meter_b", "unavailable")
    discovered = "sensor.meter_a"
    ytd_for = {"sensor.meter_a": 20.0, "sensor.meter_b": 40.0}

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _discover(_hass: HomeAssistant) -> str | None:
        return discovered

    async def _recorder(_hass: HomeAssistant, meter: str, _s: date, _e: date) -> float:
        if meter == "sensor.meter_b":
            # The new meter reports while its recorder query is in flight,
            # and the loop gets a turn to deliver that event before the
            # tick resumes, which is what opens the window.
            hass.states.async_set("sensor.meter_b", "5000")
            await asyncio.sleep(0)
        return ytd_for[meter]

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
            "custom_components.be_water_prices.coordinator._discover_energy_water_meter",
            new=_discover,
        ),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=_recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.data.ytd_consumption_m3 == 20.0

        discovered = "sensor.meter_b"
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 40.0

        # The frame comes from meter_b's own figure: the 5000 it reported
        # during the query against the 40 that query answered. Built out of
        # meter_a's 20.0 instead it would be 4980 and publish 21.0.
        assert coordinator._ytd.offset_m3 == 4960.0

        # And it climbs from there.
        hass.states.async_set("sensor.meter_b", "5001")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 41.0

        hass.states.async_set("sensor.meter_b", "5002")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 42.0


@pytest.mark.asyncio
async def test_a_reading_that_lands_during_the_recorder_query_is_used(
    hass: HomeAssistant,
) -> None:
    """The meter must be read after the recorder await, not before it.

    The query is the one place the tick yields to the loop. A meter that was
    unavailable when the tick started and reports while the query runs would
    otherwise be missed, and the tick is what frames the year: waiting for
    the next one costs a day of live tracking, and the frame it eventually
    builds is placed against a figure a day out of date.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "unavailable")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _recorder(_hass: HomeAssistant, _meter: str, _s: date, _e: date) -> float:
        # The meter comes back while the query is in flight, and the loop
        # gets a turn to deliver that event before the tick resumes.
        hass.states.async_set("sensor.water_meter", "100")
        await asyncio.sleep(0)
        return 20.0

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=_recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # The tick framed the year on the reading that arrived mid-query.
        assert coordinator._ytd.offset_m3 == 80.0
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # So live tracking is running already, without waiting a day.
        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0


@pytest.mark.asyncio
async def test_rollover_with_no_recorder_figure_still_reanchors(hass: HomeAssistant) -> None:
    """A new year with nothing to reconstruct from must still start.

    Restarting in early January with the meter down and no current-year
    statistics leaves both YTD sensors unknown. The first usable reading
    has to re-anchor the year rather than wait for the next daily tick,
    which is a day of missing history.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=20.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # The anchor is last year's, the meter is down, and the recorder
        # has no statistics for the new year yet.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        recorder.return_value = 0.0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 0.0

        # The meter comes back: the new year starts now, not tomorrow.
        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 0.0
        assert coordinator._ytd.offset_m3 == 130.0
        assert coordinator._ytd.year == dt_util.now().year

        hass.states.async_set("sensor.water_meter", "133")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 3.0


@pytest.mark.asyncio
async def test_transient_recorder_gap_does_not_reset_the_year(hass: HomeAssistant) -> None:
    """A recorder hiccup must not throw away the year already published.

    The live path reads the last published figure, which is None for the
    whole interval after a tick whose recorder query failed. Treating that
    like a year with no statistics re-anchors the cycle at the current
    reading, and because the baseline year then matches, no later tick ever
    consults the recorder again: the year's usage is gone for good.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "unavailable")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=12.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # The meter has been down since December, so the cycle has this
        # year's 12 m3 from the recorder and no frame to read a meter with.
        assert coordinator._ytd.offset_m3 is None
        assert coordinator.data.ytd_consumption_m3 == 12.0

        # The next tick's recorder query fails transiently. The figure is
        # still this year's, so it keeps being served rather than blanked.
        recorder.side_effect = RecorderUnavailable("database is locked")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 12.0

        # The meter comes back mid-interval. Those 12 m3 are still this
        # year's, so the cycle anchors from them (4520 - 12) rather than
        # being reset at the raw reading, which would publish ~0.
        hass.states.async_set("sensor.water_meter", "4520")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 12.0
        assert coordinator._ytd.offset_m3 == 4508.0

        # And it climbs from there.
        hass.states.async_set("sensor.water_meter", "4523")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 15.0


@pytest.mark.asyncio
async def test_a_recorder_gap_across_a_restart_keeps_the_year(hass: HomeAssistant) -> None:
    """The transient-gap guard must still hold across a process boundary.

    The guard tells a momentarily unpublished figure from a year with no
    statistics. What answers that is the year's own figure and the year it
    is stamped with, and both are on disk, so this is the same physical
    event as test_transient_recorder_gap_does_not_reset_the_year with a
    restart in the middle of it, and it has to resolve the same way.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "unavailable")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=12.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        # The meter has been down since December, so this year's 12 m3 came
        # from the recorder and no frame was ever built.
        assert coordinator.data.ytd_consumption_m3 == 12.0
        assert coordinator._ytd.m3 == 12.0
        assert coordinator._ytd.offset_m3 is None

        # Restart, and the first tick after it hits a recorder failure.
        assert await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
        recorder.side_effect = RecorderUnavailable("database is locked")
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        # The 12 m3 came back off disk, so they are still served.
        assert coordinator._ytd.m3 == 12.0
        assert coordinator.data.ytd_consumption_m3 == 12.0

        # The meter returns: the year must not be reset at the reading.
        hass.states.async_set("sensor.water_meter", "4520")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 12.0
        assert coordinator._ytd.offset_m3 == 4508.0


@pytest.mark.asyncio
async def test_tick_defers_the_anchor_when_the_recorder_query_fails(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """The daily tick must not reset the year on a failed recorder read.

    Same trap as the live path: anchoring at the raw reading publishes ~0
    and makes the baseline year match, so no later tick consults the
    recorder again and the year's usage is lost for good.

    Reached after a restart, which is the one state where nothing has been
    published yet and so nothing can be served or reconstructed from: the
    stored anchor is last year's while the stored recorder year says this
    year does have statistics.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "4520")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    hass_storage[f"{DOMAIN}.{entry.entry_id}.ytd"] = {
        "version": 1,
        "data": {
            "meter": "sensor.water_meter",
            "year": dt_util.now().year - 1,
            "baseline_m3": 4000.0,
            "live_hwm_m3": 4000.0,
            "cost_hwm": None,
            "cost_year": None,
            "recorder_year": dt_util.now().year,
        },
    }
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(side_effect=RecorderUnavailable("database is locked"))
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        # First tick after the restart: the meter reads fine but the
        # recorder query fails, so there is nothing to anchor from.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.year == dt_util.now().year - 1
        assert coordinator.data.ytd_consumption_m3 is None

        # The next healthy tick anchors properly and keeps the 40.
        recorder.side_effect = None
        recorder.return_value = 40.0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator._ytd.offset_m3 == 4480.0
        assert coordinator.data.ytd_consumption_m3 == 40.0


@pytest.mark.asyncio
async def test_an_old_recorder_failure_does_not_block_the_new_year(
    hass: HomeAssistant,
) -> None:
    """The tick's verdict on the recorder expires when it stops asking.

    A failed query blocks a live reading from declaring the year starts at
    zero, which is right for the interval it happened in. But the tick only
    queries while the cycle cannot answer on its own, so once the year is
    healthy the verdict is never refreshed, and a hiccup back in March would
    still be blocking the first reading of January.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=20.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # Some tick in March: the meter blips out and the database is locked.
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        recorder.side_effect = RecorderUnavailable("database is locked")
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        # Both are healthy for the rest of the year, so no tick asks again.
        recorder.side_effect = None
        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 50.0

        # January 1, and a reading arrives before the day's tick. The year
        # must start here rather than republishing last year's 50 m³ under
        # the new year's last_reset.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)
        hass.states.async_set("sensor.water_meter", "132")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 0.0
        assert coordinator._ytd.offset_m3 == 132.0


@pytest.mark.asyncio
async def test_a_record_an_older_release_wrote_back_is_not_emptied(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A rollback and a re-upgrade must not cost the year.

    Home Assistant re-stamps a store with its own minor version after any
    migration, so a release rolled back over this one hands the record
    straight back under the old label without understanding a key of it.
    Reading that label rather than the keys would fold a current record as
    if it were the old shape and empty it.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "4100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    hass_storage[f"{DOMAIN}.{entry.entry_id}.ytd"] = {
        "version": 1,
        "minor_version": 1,
        "data": {
            "meter": "sensor.water_meter",
            "year": dt_util.now().year,
            "m3": 100.0,
            "cost": 999.0,
            "offset_m3": 4000.0,
        },
    }

    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(side_effect=RecorderUnavailable("database is locked")),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

    # The year, its frame and its cost floor all came back.
    assert coordinator._ytd.offset_m3 == 4000.0
    assert coordinator.data.ytd_consumption_m3 == 100.0
    assert coordinator.data.current_year_cost_eur == 999.0


@pytest.mark.asyncio
async def test_cost_floor_drops_at_rollover_for_a_never_anchored_cycle(
    hass: HomeAssistant,
) -> None:
    """A cycle that never anchors must still drop its floor on January 1.

    HA's Energy dashboard accepts an external statistic id as the water
    source, and several water integrations publish that way. There is no
    entity behind it, so the meter reading is always None, the cycle never
    anchors, and the baseline year stays None forever. Keying the rollover
    reset on that year meant the cost mark was never dropped and the new
    year opened pinned to the old year's final bill.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _discover(_hass: HomeAssistant) -> str | None:
        # An external statistic id, not an entity: hass.states.get() is None.
        return "watermeter:daily_consumption"

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=78.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._discover_energy_water_meter",
            new=_discover,
        ),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        # A full year of consumption served straight off the recorder; the
        # cycle never anchored, so there is no baseline year at all.
        assert coordinator._ytd.offset_m3 is None
        assert coordinator.data.ytd_consumption_m3 == 78.0
        december_cost = coordinator.data.current_year_cost_eur
        assert december_cost is not None

        # Roll over: the figure now belongs to last year.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)
        recorder.return_value = 0.4
        await coordinator.async_refresh()
        await hass.async_block_till_done()

        assert coordinator.data.ytd_consumption_m3 == 0.4
        january_cost = coordinator.data.current_year_cost_eur
        assert january_cost is not None
        assert january_cost < december_cost


@pytest.mark.asyncio
async def test_resuming_live_tracking_keeps_the_cost_floor(hass: HomeAssistant) -> None:
    """Picking live tracking back up is not a new cycle, so the bill holds.

    The resume path reconstructs the baseline so the published m3 is
    unchanged, but it went through the same helper a genuine reset uses,
    which clears the cost mark. A tariff cut during the dropout would then
    surface as a decrease in the running bill inside one year.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "unavailable")

    cheap = False

    async def _fetch(_session: Any) -> WaterTariff:
        t = _fresh_tariff()
        return replace(t, linear_eur_per_m3=1.20) if cheap else t

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.offset_m3 is None  # never anchored, meter down
        peak = coordinator.data.current_year_cost_eur
        assert peak is not None

        # The utility cuts its rate while the meter is still down; the
        # floor holds the bill where it was.
        cheap = True
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.current_year_cost_eur == peak

        # The meter returns and live tracking resumes on the same year.
        hass.states.async_set("sensor.water_meter", "500")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 20.0
        assert coordinator.data.current_year_cost_eur == peak


@pytest.mark.asyncio
async def test_recorder_hiccup_does_not_blank_a_known_figure(hass: HomeAssistant) -> None:
    """A recorder failure must not blank sensors whose figure is known.

    With the meter also down there is no live reading, but this year's
    high-water mark is still in memory, so both sensors can keep reporting
    it instead of going unknown for a whole day.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=20.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 50.0

        # Meter drops out and the recorder query fails on the same tick.
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        recorder.side_effect = RecorderUnavailable("database is locked")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 50.0
        assert coordinator.data.current_year_cost_eur is not None


@pytest.mark.asyncio
async def test_cost_floor_from_an_older_store_still_drops_at_rollover(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """An anchor written before the cost year existed must still roll over.

    The rollover reset is keyed on the year the cost mark belongs to. An
    entry persisted by an earlier version has no such key, so without a
    fallback the mark would look unstamped and the reset would never fire
    for exactly the installs upgrading into it.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "unavailable")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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

    # Exactly the shape the previous version wrote: no cost_year, no
    # recorder_year, and a cost mark left over from last year.
    hass_storage[f"{DOMAIN}.{entry.entry_id}.ytd"] = {
        "version": 1,
        "data": {
            "meter": "sensor.water_meter",
            "year": dt_util.now().year - 1,
            "baseline_m3": 4000.0,
            "live_hwm_m3": 4100.0,
            "cost_hwm": 999.0,
        },
    }

    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=2.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

    assert coordinator.data.ytd_consumption_m3 == 2.0
    # Last year's EUR 999 floor must not clamp the new year's small bill.
    assert coordinator.data.current_year_cost_eur is not None
    assert coordinator.data.current_year_cost_eur < 999.0


@pytest.mark.asyncio
async def test_served_recorder_figure_is_folded_into_the_mark(hass: HomeAssistant) -> None:
    """A recorder figure above the live mark must not be walked back down.

    The cycle anchors on the bare reading when the first tick's recorder
    query fails, so the mark starts at zero consumption while the recorder
    still knows about the year. Once that larger figure has been published,
    every later path that reports the mark has to be at or above it.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=0.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        # First tick: the recorder has nothing, so the cycle anchors on the
        # bare reading and the figure starts at zero consumption.
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.offset_m3 == 100.0

        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 5.0

        # Meter drops out and the recorder now answers, above the mark.
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        recorder.return_value = 30.0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 30.0

        # The recorder then fails: the figure must hold, not drop back.
        recorder.side_effect = RecorderUnavailable("database is locked")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 30.0

        # And the meter recovering must not walk it back either.
        hass.states.async_set("sensor.water_meter", "106")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 30.0

        # That reading rebuilds the frame around the figure the recorder
        # proved, so the next draw is counted from there. Left on the old
        # frame the meter would have to climb from 106 to 130 first, and
        # those 24 m³ would never be reported at all.
        assert coordinator._ytd.offset_m3 == 76.0
        hass.states.async_set("sensor.water_meter", "107")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 31.0


@pytest.mark.asyncio
async def test_undatable_cost_floor_from_an_older_store_is_dropped(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A pre-upgrade mark with no year at all must not be carried forward.

    A cycle that never anchors persists year=None, so falling back to the
    baseline year leaves the mark undatable. The rollover reset is keyed on
    that year and the clamp branch never stamps one, so such a mark would
    pin the bill at the old peak in this year and every year after it.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _discover(_hass: HomeAssistant) -> str | None:
        return "watermeter:daily_consumption"

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)

    # Exactly what the previous release wrote for a never-anchored cycle:
    # a cost mark, and nothing to date it by.
    hass_storage[f"{DOMAIN}.{entry.entry_id}.ytd"] = {
        "version": 1,
        "data": {
            "meter": "watermeter:daily_consumption",
            "year": None,
            "baseline_m3": None,
            "live_hwm_m3": None,
            "cost_hwm": 999.0,
        },
    }

    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._discover_energy_water_meter",
            new=_discover,
        ),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=2.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

    assert coordinator.data.ytd_consumption_m3 == 2.0
    cost = coordinator.data.current_year_cost_eur
    assert cost is not None
    assert cost < 999.0


@pytest.mark.asyncio
async def test_never_anchored_entry_keeps_reporting_through_a_recorder_gap(
    hass: HomeAssistant,
) -> None:
    """An external-statistic source must not blank on a recorder hiccup.

    Those entries never anchor, so there is no cycle mark to fall back on
    and the previous last-resort path could not help them. The figure
    already published is this year's, so keep serving it rather than
    reporting unknown for a whole day.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _discover(_hass: HomeAssistant) -> str | None:
        return "watermeter:daily_consumption"

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=70.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._discover_energy_water_meter",
            new=_discover,
        ),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.offset_m3 is None  # never anchors
        assert coordinator.data.ytd_consumption_m3 == 70.0
        cost = coordinator.data.current_year_cost_eur

        # The recorder hiccups: keep serving what is known.
        recorder.side_effect = RecorderUnavailable("database is locked")
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 70.0
        assert coordinator.data.current_year_cost_eur == cost

        # After a rollover there is nothing this year to serve yet.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 is None


@pytest.mark.asyncio
async def test_first_anchor_of_a_running_year_keeps_the_cost_floor(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """Anchoring a year the recorder was already billing is a continuation.

    Needing a bootstrap is not the same as starting a new cycle: it is also
    the first tick where a meter becomes usable inside a year whose bill has
    already been published from the recorder. Clearing the floor there lets
    a lower tariff fetch publish a decrease, and after a restart the tick is
    the path that anchors, so the floor persisted to survive restarts was
    the one being thrown away.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        # Cheaper rates than the ones that set the persisted floor.
        return replace(_fresh_tariff(), linear_eur_per_m3=1.20)

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
    # Persisted by a tick that served the recorder while the meter was down:
    # no anchor, but a cost floor stamped with this year.
    hass_storage[f"{DOMAIN}.{entry.entry_id}.ytd"] = {
        "version": 1,
        "data": {
            "meter": "sensor.water_meter",
            "year": None,
            "baseline_m3": None,
            "live_hwm_m3": None,
            "cost_hwm": 177.25,
            "cost_year": dt_util.now().year,
            "recorder_year": dt_util.now().year,
        },
    }

    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=30.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

    # The meter anchored this year for the first time, but the year's bill
    # had already been published, so it must not drop.
    assert coordinator._ytd.offset_m3 == 70.0
    assert coordinator.data.current_year_cost_eur == 177.25


@pytest.mark.asyncio
async def test_served_volume_does_not_walk_back_without_an_anchor(
    hass: HomeAssistant,
) -> None:
    """A falling recorder total must not lower the published volume.

    An external-statistic source never anchors, so the cycle mark cannot
    floor it and the recorder figure was published raw. HA sums a
    `total` sensor unconditionally, so a meter that steps backwards, or a
    user adjusting the statistic, really does lower the same-year total.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    async def _discover(_hass: HomeAssistant) -> str | None:
        return "myintegration:water_consumption"

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    recorder = AsyncMock(return_value=45.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._discover_energy_water_meter",
            new=_discover,
        ),
        patch("custom_components.be_water_prices.coordinator._recorder_ytd_m3", new=recorder),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator._ytd.offset_m3 is None  # never anchors
        assert coordinator.data.ytd_consumption_m3 == 45.0

        # The recorder total steps backwards inside the same year.
        recorder.return_value = 40.0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 45.0

        # A genuine climb still gets through.
        recorder.return_value = 51.0
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 51.0

        # And the new year is free to start near zero.
        coordinator._ytd = replace(coordinator._ytd, year=dt_util.now().year - 1)
        recorder.return_value = 0.4
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 0.4


@pytest.mark.asyncio
async def test_tick_does_not_publish_a_figure_the_cycle_moved_past(
    hass: HomeAssistant,
) -> None:
    """A draw during the tick's Store save must not be undone by the tick.

    The tick computes the figure, then awaits the save. A meter event
    handled while that runs advances the mark, so publishing the earlier
    snapshot walks both YTD sensors backwards inside the year.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # Make the next tick's Store save yield, and draw water while it does.
        real_save = coordinator._store.async_save

        async def _slow_save(data: Any) -> None:
            hass.states.async_set("sensor.water_meter", "140")
            await asyncio.sleep(0)
            await real_save(data)

        coordinator._cycle_dirty = True
        with patch.object(coordinator._store, "async_save", new=_slow_save):
            await coordinator.async_refresh()
            await hass.async_block_till_done()

        # 140 - 80 == 60; the tick must not republish the earlier 20.
        assert coordinator.data.ytd_consumption_m3 == 60.0


@pytest.mark.asyncio
async def test_meter_draw_does_not_churn_the_rate_sensors(hass: HomeAssistant) -> None:
    """A draw must only move the two sensors it actually affects.

    snapshot_age_hours says how old the tariff snapshot is, which a meter
    reading cannot change. Recomputing it on every draw changed an
    attribute on every entity, so all eight sensors wrote a recorder row
    per reading rather than the two that moved.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

        changed: list[str] = []

        @callback
        def _track(event: Any) -> None:
            eid = event.data["entity_id"]
            if eid.startswith("sensor.vivaqua_"):
                changed.append(eid)

        # Age the snapshot so a recompute would land on a different value:
        # the attribute is rounded to 0.01 h, so within one test run only
        # real elapsed time makes the difference visible, exactly as it does
        # for a meter reporting every few minutes.
        coordinator = hass.data[DOMAIN][entry.entry_id]
        coordinator.data = replace(
            coordinator.data, fetched_at=coordinator.data.fetched_at - timedelta(hours=2)
        )

        hass.bus.async_listen("state_changed", _track)
        hass.states.async_set("sensor.water_meter", "105")
        await hass.async_block_till_done()

    # Only the two YTD sensors move; the rate sensors stay put.
    assert set(changed) == {
        "sensor.vivaqua_year_to_date_consumption",
        "sensor.vivaqua_current_year_cost",
    }


@pytest.mark.asyncio
async def test_unreadable_cycle_store_does_not_block_setup(hass: HomeAssistant) -> None:
    """A cycle record that cannot be read must cost a bootstrap, not the entry.

    The load runs during entry setup with nothing catching it, so anything
    raising there would leave the integration unusable until the user found
    and deleted the file.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

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

    async def _boom(_self: Any) -> Any:
        # Stands in for a future migration that cannot handle a record.
        raise ValueError("unmigratable cycle record")

    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.coordinator._recorder_ytd_m3",
            new=AsyncMock(return_value=20.0),
        ),
        patch(
            "custom_components.be_water_prices.coordinator._YtdStore.async_load",
            new=_boom,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]

    # Bootstrapped fresh from the recorder rather than failing setup.
    assert coordinator.data.ytd_consumption_m3 == 20.0
