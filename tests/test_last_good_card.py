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

"""The last good card is stored, so a restart while the utility is down
serves it instead of retrying setup until the utility is back."""

from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)

from custom_components.be_water_prices.const import (
    CONF_CARD_ARCHIVE,
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    DOMAIN,
)
from custom_components.be_water_prices.providers.base import (
    ExtractorError,
    WaterExtractor,
    WaterTariff,
    tariff_to_dict,
)
from tests.test_ha_coordinator import _this_years_card

_GET = "custom_components.be_water_prices.coordinator.get"


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80, CONF_CARD_ARCHIVE: False},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    return entry


async def _down(_session: Any) -> WaterTariff:
    raise ExtractorError("HTTP 503 fetching the tariff page")


async def _setup(hass: HomeAssistant, entry: MockConfigEntry, fetch: Any) -> None:
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=fetch)
    with patch(_GET, return_value=fake):
        await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()


async def test_a_restart_while_the_utility_is_down_serves_the_stored_card(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    card = replace(_this_years_card(), publication_label="VIVAQUA stored")

    async def _up(_session: Any) -> WaterTariff:
        return card

    entry = _entry(hass)
    await _setup(hass, entry, _up)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    stored = hass_storage[f"{DOMAIN}.{entry.entry_id}.card"]["data"]
    assert stored["card"]["publication_label"] == "VIVAQUA stored"
    fetched_at = entry.runtime_data.data.fetched_at
    assert await hass.config_entries.async_unload(entry.entry_id)

    # The restart: a fresh coordinator, the utility down, no archive.
    await _setup(hass, entry, _down)
    assert entry.state is ConfigEntryState.LOADED
    data = entry.runtime_data.data
    assert data.tariff.publication_label == "VIVAQUA stored"
    assert data.fetched_at == fetched_at
    assert "503" in data.last_error
    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_without_a_stored_card_the_setup_retries(hass: HomeAssistant) -> None:
    """A store written before the card was kept has no card file at all."""
    entry = _entry(hass)
    await _setup(hass, entry, _down)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_a_card_of_another_operator_is_not_served(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    """A reconfigured entry keeps its id; the old operator's card is not its own."""
    entry = _entry(hass)
    other = replace(_this_years_card(), utility="swde", region="wallonia")
    hass_storage[f"{DOMAIN}.{entry.entry_id}.card"] = {
        "version": 1,
        "data": {
            "card": tariff_to_dict(other),
            "fetched_at": dt_util.utcnow().isoformat(),
            "commune": None,
        },
    }
    await _setup(hass, entry, _down)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_an_unreadable_card_record_is_dropped(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    entry = _entry(hass)
    hass_storage[f"{DOMAIN}.{entry.entry_id}.card"] = {
        "version": 1,
        "data": {"card": {"utility": "vivaqua"}, "fetched_at": "yesterday"},
    }
    await _setup(hass, entry, _down)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_removing_the_entry_deletes_the_stored_card(
    hass: HomeAssistant, hass_storage: dict[str, Any]
) -> None:
    async def _up(_session: Any) -> WaterTariff:
        return _this_years_card()

    entry = _entry(hass)
    await _setup(hass, entry, _up)
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=1))
    await hass.async_block_till_done()
    key = f"{DOMAIN}.{entry.entry_id}.card"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage
