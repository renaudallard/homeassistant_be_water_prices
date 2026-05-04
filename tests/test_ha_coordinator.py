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
from unittest.mock import patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    DOMAIN,
)
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    WaterExtractor,
    WaterTariff,
)


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
