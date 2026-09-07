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

"""A downgrade ends in a migration error that says what happened."""

from __future__ import annotations

import logging

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
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
    ours = [r.getMessage() for r in caplog.records if r.name.startswith("custom_components")]
    assert len(ours) == 1
    assert "newer release (version 3" in ours[0]
