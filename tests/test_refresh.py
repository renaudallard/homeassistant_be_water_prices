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

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import patch

import pytest
from homeassistant.auth.const import GROUP_ID_USER
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ServiceValidationError, Unauthorized
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from pytest_homeassistant_custom_component.common import async_fire_time_changed

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

    # Moments after the setup's fetch: the action fetches however recent
    # the last fetch was.
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


async def test_the_button_refreshes_and_stays_pressable(hass: HomeAssistant, freezer: Any) -> None:
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
    freezer.tick(timedelta(minutes=15))
    fail = True
    entry.runtime_data._last_good = None
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert calls == 2
    assert entry.runtime_data.last_update_success is False
    assert hass.states.get(button).state != STATE_UNAVAILABLE

    # Past the quarter of an hour between two presses that fetch.
    freezer.tick(timedelta(minutes=15))
    fail = False
    await hass.services.async_call("button", "press", {"entity_id": button}, blocking=True)
    await hass.async_block_till_done()
    assert calls == 3
    assert entry.runtime_data.last_update_success is True


async def test_only_an_administrator_can_call_the_service(
    hass: HomeAssistant, hass_read_only_user: Any, hass_admin_user: Any
) -> None:
    """Every call fetches and parses the tariff again, so a user who is not
    an administrator is refused, read-only or not. An administrator is not,
    and neither is an automation: its actions run with no user, even when an
    event a user fired set it off."""
    fetch, calls = _counting_fetch()
    entry = await _setup_entry(hass, fetch)
    household = await hass.auth.async_create_user("Household", group_ids=[GROUP_ID_USER])
    assert not household.is_admin
    data = {"entry_id": entry.entry_id}
    for user in (hass_read_only_user, household):
        with pytest.raises(Unauthorized):
            await hass.services.async_call(
                DOMAIN, SERVICE_REFRESH, data, blocking=True, context=Context(user_id=user.id)
            )
    assert calls[0] == 1
    await hass.services.async_call(
        DOMAIN, SERVICE_REFRESH, data, blocking=True, context=Context(user_id=hass_admin_user.id)
    )
    assert calls[0] == 2
    assert await async_setup_component(
        hass,
        "automation",
        {
            "automation": {
                "triggers": {"trigger": "event", "event_type": "refresh_now"},
                "actions": {"action": f"{DOMAIN}.{SERVICE_REFRESH}", "data": data},
            }
        },
    )
    hass.bus.async_fire("refresh_now", context=Context(user_id=household.id))
    await hass.async_block_till_done()
    assert calls[0] == 3


async def test_the_button_fetches_once_a_quarter_hour(hass: HomeAssistant, freezer: Any) -> None:
    """Any user allowed to press a button can press this one, and it called
    the coordinator's refresh past its debouncer: twenty-five presses ran
    twenty-five fetches. A press within a quarter of an hour of the last
    fetch, the setup's included, is refused, together or one after the
    other, and says when the button can fetch again."""
    freezer.move_to("2026-11-06 10:00:00+01:00")
    fetch, calls = _counting_fetch()
    entry = await _setup_entry(hass, fetch)
    household = await hass.auth.async_create_user("Household", group_ids=[GROUP_ID_USER])
    button = er.async_get(hass).async_get_entity_id("button", DOMAIN, f"{entry.entry_id}_refresh")
    assert button is not None

    async def press() -> None:
        await hass.services.async_call(
            "button",
            "press",
            {"entity_id": button},
            blocking=True,
            context=Context(user_id=household.id),
        )

    with pytest.raises(ServiceValidationError) as err:
        await press()
    assert err.value.translation_placeholders == {"last": "10:00", "next": "10:15"}
    assert calls[0] == 1

    # A fetch begun at 10:15:20 holds the next to 10:30:20, so the time
    # given is 10:31.
    freezer.tick(timedelta(minutes=15, seconds=20))
    presses = await asyncio.gather(*(press() for _ in range(25)), return_exceptions=True)
    refused = [p for p in presses if isinstance(p, ServiceValidationError)]
    assert len(refused) == 24
    assert {p.translation_key for p in refused} == {"refresh_pressed_recently"}
    assert refused[0].translation_placeholders == {"last": "10:15", "next": "10:31"}
    assert calls[0] == 2
    freezer.tick(timedelta(minutes=14))
    with pytest.raises(ServiceValidationError):
        await press()
    freezer.tick(timedelta(minutes=1))
    await press()
    assert calls[0] == 3


@pytest.mark.fetch_pacing
async def test_update_entity_fetches_once_a_quarter_hour(hass: HomeAssistant, freezer: Any) -> None:
    """Any user who may control an entity can have Home Assistant update it,
    which refreshed the coordinator as soon as its debouncer's ten seconds
    had passed, and every refresh fetched. Within a quarter of an hour of a
    fetch a refresh fetches nothing; the daily tick, a day after the last
    refresh, fetches as it did."""
    fetch, calls = _counting_fetch()
    entry = await _setup_entry(hass, fetch)
    assert await async_setup_component(hass, "homeassistant", {})
    household = await hass.auth.async_create_user("Household", group_ids=[GROUP_ID_USER])
    sensor = next(
        e.entity_id
        for e in er.async_entries_for_config_entry(er.async_get(hass), entry.entry_id)
        if e.domain == "sensor"
    )

    async def update() -> None:
        await hass.services.async_call(
            "homeassistant",
            "update_entity",
            {"entity_id": sensor},
            blocking=True,
            context=Context(user_id=household.id),
        )
        await hass.async_block_till_done()

    for _ in range(8):
        freezer.tick(timedelta(seconds=11))
        await update()
    assert calls[0] == 1
    freezer.tick(timedelta(minutes=14))
    await update()
    assert calls[0] == 2

    freezer.tick(timedelta(days=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert calls[0] == 3


@pytest.mark.fetch_pacing
async def test_a_held_refresh_leaves_a_failed_one_failed(hass: HomeAssistant, freezer: Any) -> None:
    """A refresh that failed is not marked recovered by one the quarter of an
    hour holds: that one tried nothing, and it read as a recovery, the
    entities available again on what the failed refresh had not replaced."""
    fetch, calls = _counting_fetch()
    entry = await _setup_entry(hass, fetch)
    coordinator = entry.runtime_data
    freezer.tick(timedelta(minutes=15))
    with patch.object(coordinator, "_compute_ytd", side_effect=RuntimeError("fault")):
        await coordinator.async_refresh()
    assert calls[0] == 2
    assert coordinator.last_update_success is False

    freezer.tick(timedelta(minutes=1))
    await coordinator.async_refresh()
    assert calls[0] == 2
    assert coordinator.last_update_success is False

    freezer.tick(timedelta(minutes=14))
    await coordinator.async_refresh()
    assert calls[0] == 3
    assert coordinator.last_update_success is True
