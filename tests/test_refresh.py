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

"""The refresh service and the refresh button fetch the tariff again now."""

from __future__ import annotations

from typing import Any

import pytest
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from custom_components.be_water_prices import SERVICE_REFRESH
from custom_components.be_water_prices.const import CONF_CARD_ARCHIVE, DOMAIN
from custom_components.be_water_prices.providers.base import ExtractorError, WaterTariff
from tests.test_ha_coordinator import _fresh_tariff, _setup_entry


def _counting_fetch() -> tuple[Any, list[int]]:
    calls = [0]

    async def _fetch(_session: Any) -> WaterTariff:
        calls[0] += 1
        return _fresh_tariff()

    return _fetch, calls


async def test_the_service_refreshes_the_entry_it_names(hass: HomeAssistant) -> None:
    fetch, calls = _counting_fetch()
    entry = await _setup_entry(hass, fetch)
    assert calls[0] == 1

    await hass.services.async_call(
        DOMAIN, SERVICE_REFRESH, {"entry_id": entry.entry_id}, blocking=True
    )
    assert calls[0] == 2
    # Without an entry, every loaded one.
    await hass.services.async_call(DOMAIN, SERVICE_REFRESH, {}, blocking=True)
    assert calls[0] == 3


async def test_the_service_refuses_an_entry_that_is_not_loaded(hass: HomeAssistant) -> None:
    fetch, _calls = _counting_fetch()
    await _setup_entry(hass, fetch)
    with pytest.raises(ServiceValidationError) as err:
        await hass.services.async_call(
            DOMAIN, SERVICE_REFRESH, {"entry_id": "no-such-entry"}, blocking=True
        )
    assert err.value.translation_key == "unknown_entry"


async def test_the_button_refreshes_and_stays_pressable(hass: HomeAssistant) -> None:
    """The button is how an entry whose tariff cannot be read is retried,
    so it stays available while the sensors are not."""
    fail = False
    calls = 0

    async def _fetch(_session: Any) -> WaterTariff:
        nonlocal calls
        calls += 1
        if fail:
            raise ExtractorError("page moved")
        return _fresh_tariff()

    entry = await _setup_entry(hass, _fetch, options={CONF_CARD_ARCHIVE: False})
    button = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_refresh")
    assert button is not None
    assert er.async_get(hass).async_get(button).entity_category == "diagnostic"

    # Nothing held and no archive to fall back on: the refresh fails.
    fail = True
    entry.runtime_data._last_good = None
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert calls == 2
    assert entry.runtime_data.last_update_success is False
    assert hass.states.get(button).state != STATE_UNAVAILABLE

    fail = False
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert calls == 3
    assert entry.runtime_data.last_update_success is True
