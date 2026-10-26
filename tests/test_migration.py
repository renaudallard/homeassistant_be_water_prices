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

"""Entry migrations: a downgrade says what happened, and an old move out
of Flanders leaves no household behind."""

from __future__ import annotations

import logging

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices import async_migrate_entry
from custom_components.be_water_prices.config_flow import BeWaterPricesConfigFlow
from custom_components.be_water_prices.const import (
    CONF_CARD_ARCHIVE,
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_PERSONS,
    CONF_SOCIAL_TARIFF,
    CONF_UTILITY,
    DOMAIN,
)


async def test_an_entry_from_a_newer_release_says_why_it_cannot_load(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.ERROR)
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
        version=3,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id) is False
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.MIGRATION_ERROR
    # Releases after 2026.2.3 refuse a newer entry themselves, before the
    # integration is asked; the older ones leave it to async_migrate_entry.
    # Either way one error says which version the entry carries.
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("custom_components")]
    theirs = [
        r.getMessage()
        for r in caplog.records
        if r.name == "homeassistant.config_entries" and "version 3" in r.getMessage()
    ]
    assert len(ours) + len(theirs) == 1
    if ours:
        assert "newer release (version 3" in ours[0]


def _old_entry(
    hass: HomeAssistant, utility: str, options: dict[str, object], **kwargs: object
) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=utility,
        data={CONF_UTILITY: utility},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80, **options},
        unique_id=f"{DOMAIN}_{utility}",
        version=2,
        **kwargs,
    )
    entry.add_to_hass(hass)
    return entry


async def test_a_flemish_social_tariff_left_on_a_brussels_entry_is_cleared(
    hass: HomeAssistant,
) -> None:
    """0.5.2 to 0.5.6 kept it through a move, and 0.11.2 read it as BIM status."""
    entry = _old_entry(hass, "vivaqua", {CONF_PERSONS: 3, CONF_SOCIAL_TARIFF: True})
    assert entry.minor_version == 1
    assert await async_migrate_entry(hass, entry)
    assert entry.options == {CONF_CONSUMPTION_M3_PER_YEAR: 80}
    assert entry.minor_version == 2


async def test_bim_status_set_through_the_brussels_form_is_kept(hass: HomeAssistant) -> None:
    options = {CONF_PERSONS: 3, CONF_SOCIAL_TARIFF: True, CONF_CARD_ARCHIVE: True}
    entry = _old_entry(hass, "vivaqua", options)
    assert await async_migrate_entry(hass, entry)
    assert entry.options[CONF_SOCIAL_TARIFF] is True
    assert entry.options[CONF_PERSONS] == 3
    assert entry.minor_version == 2


async def test_a_walloon_entry_loses_the_household_it_never_used(hass: HomeAssistant) -> None:
    options = {CONF_PERSONS: 2, CONF_SOCIAL_TARIFF: True, CONF_CARD_ARCHIVE: True}
    entry = _old_entry(hass, "swde", options)
    assert await async_migrate_entry(hass, entry)
    assert CONF_PERSONS not in entry.options
    assert CONF_SOCIAL_TARIFF not in entry.options
    assert entry.options[CONF_CARD_ARCHIVE] is True


async def test_a_flemish_household_is_left_alone(hass: HomeAssistant) -> None:
    entry = _old_entry(hass, "pidpa", {CONF_PERSONS: 2, CONF_SOCIAL_TARIFF: True})
    assert await async_migrate_entry(hass, entry)
    assert entry.options[CONF_SOCIAL_TARIFF] is True
    assert entry.options[CONF_PERSONS] == 2


async def test_an_entry_already_migrated_is_not_looked_at_again(hass: HomeAssistant) -> None:
    """Once stamped, a later BIM tick without the archive switch stands."""
    options = {CONF_PERSONS: 3, CONF_SOCIAL_TARIFF: True}
    entry = _old_entry(hass, "vivaqua", options, minor_version=2)
    assert await async_migrate_entry(hass, entry)
    assert entry.options[CONF_SOCIAL_TARIFF] is True


async def test_a_v1_entry_is_brought_to_the_current_version(hass: HomeAssistant) -> None:
    entry = _old_entry(hass, "vivaqua", {CONF_SOCIAL_TARIFF: True})
    hass.config_entries.async_update_entry(entry, version=1)
    assert await async_migrate_entry(hass, entry)
    assert (entry.version, entry.minor_version) == (2, 2)
    assert CONF_SOCIAL_TARIFF not in entry.options


async def test_setup_runs_the_migration(hass: HomeAssistant) -> None:
    """Home Assistant asks for it because the flow's minor version moved."""
    assert BeWaterPricesConfigFlow.MINOR_VERSION == 2
    entry = _old_entry(hass, "vivaqua", {CONF_SOCIAL_TARIFF: True})
    assert await entry.async_migrate(hass)
    assert entry.minor_version == 2
    assert CONF_SOCIAL_TARIFF not in entry.options
