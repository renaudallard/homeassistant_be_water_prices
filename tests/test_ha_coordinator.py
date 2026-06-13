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
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
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
    result = await flow.async_step_init()
    assert result["type"] == "form"
    result = await flow.async_step_init({})
    assert result["type"] == "create_entry"
    await hass.async_block_till_done()

    # The fix flow consumed the second (fresh) fetch result, so the
    # snapshot is no longer stale and the issue cleared itself.
    assert coordinator.data is not None
    assert coordinator.data.snapshot_stale is False
    assert issue_reg.async_get_issue(DOMAIN, coordinator.stale_issue_id) is None


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

        # A flapping meter (unavailable) must not blank the running total.
        hass.states.async_set("sensor.water_meter", "unavailable")
        await hass.async_block_till_done()
        assert coordinator.data.ytd_consumption_m3 == 25.0


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
