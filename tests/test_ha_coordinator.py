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

from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
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
        assert coordinator._ytd_baseline_m3 == 80.0
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
        assert coordinator._ytd_baseline_m3 == 80.0
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
        coordinator._ytd_baseline_year = dt_util.now().year - 1
        coordinator._ytd_recorder_year = dt_util.now().year - 1

        # A draw to 130 m³ would naively read 130 - 80 == 50 m³ YTD; the
        # rollover guard re-anchors to 130, so YTD resets to ~0 instead.
        hass.states.async_set("sensor.water_meter", "130")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 0.0
        assert coordinator._ytd_baseline_year == dt_util.now().year


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
        assert coordinator._ytd_baseline_m3 is None
        assert coordinator.data.ytd_consumption_m3 == 20.0

        # Meter returns at 100 m³: baseline reconstructed as 100 - 20 = 80.
        hass.states.async_set("sensor.water_meter", "100")
        await hass.async_block_till_done()
        assert coordinator._ytd_baseline_m3 == 80.0
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
        assert coordinator._ytd_baseline_m3 is None
        # Pretend the recorder figure (20) was captured in the prior year.
        coordinator._ytd_recorder_year = dt_util.now().year - 1

        # Meter returns at 100 in the new year: a same-year recovery would
        # anchor 100-20=80 and report 20; the cross-year guard anchors to
        # 100 so YTD resets to ~0 instead.
        hass.states.async_set("sensor.water_meter", "100")
        await hass.async_block_till_done()
        assert coordinator._ytd_baseline_m3 == 100.0
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
        assert coordinator._ytd_baseline_m3 == 80.0

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
        assert coordinator2._ytd_baseline_m3 == 80.0
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
        assert coordinator2._ytd_cost_hwm == cost_peak
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
        # the recorder reports a small new-year figure.
        coordinator._ytd_baseline_year = dt_util.now().year - 1
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

        # Down across Jan 1: prior-year baseline kept, recorder serves 5 m³.
        coordinator._ytd_baseline_year = dt_util.now().year - 1
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
        assert coordinator._ytd_baseline_m3 == 995.5

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
        assert coordinator._ytd_baseline_m3 == 80.0

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
        assert coordinator2._ytd_live_hwm_m3 == 130.0
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
        assert coordinator._ytd_baseline_m3 == 80.0
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
    assert coordinator._ytd_baseline_m3 == 12.0
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
    Autospec makes the call signature part of the assertion, so an HA
    signature change surfaces here instead of at a user's next tick.
    """
    from unittest.mock import MagicMock

    from homeassistant.util.unit_conversion import VolumeConverter

    from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

    captured: dict[str, Any] = {}

    def _stats(*args: Any) -> dict[str, list[dict[str, Any]]]:
        captured["args"] = args
        return {"sensor.wm": [{"change": 10.0}, {"change": 5.5}, {"change": None}]}

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
        total = await _recorder_ytd_m3(hass, "sensor.wm", date(2026, 1, 1), date(2026, 6, 30))

    assert total == 15.5
    _hass, _start, _end, ids, period, units, types = captured["args"]
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
async def test_recorder_ytd_returns_none_without_usable_rows(hass: HomeAssistant) -> None:
    """No statistics, or only empty deltas, must read as unknown not zero."""
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
        assert got is None


@pytest.mark.asyncio
async def test_recorder_ytd_returns_none_when_the_query_raises(hass: HomeAssistant) -> None:
    """A transient query failure degrades to unknown rather than crashing."""
    from unittest.mock import MagicMock

    from custom_components.be_water_prices.coordinator import _recorder_ytd_m3

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
    ):
        assert (
            await _recorder_ytd_m3(hass, "sensor.wm", date(2026, 1, 1), date(2026, 6, 30)) is None
        )


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
