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

"""The commune stays out of the entry's not-ready reason and the log."""

from __future__ import annotations

import logging
from typing import Any
from unittest.mock import patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_COMMUNE,
    CONF_COMMUNE_LABEL,
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    DOMAIN,
)
from custom_components.be_water_prices.providers.base import (
    TransientFetchError,
    WaterExtractor,
    WaterTariff,
)
from tests.test_ha_coordinator import _fresh_tariff


async def test_a_failed_first_refresh_does_not_name_the_commune(
    hass: HomeAssistant, caplog: Any
) -> None:
    """The cached path scrubbed the message; the first-refresh path raised it raw."""
    caplog.set_level(logging.DEBUG)

    async def _fetch(_session: Any) -> WaterTariff:
        raise AssertionError("not used")

    async def _fetch_for_commune(_session: Any, slug: str) -> WaterTariff:
        # Same message shape as the fetch helpers: the URL carries the slug.
        raise TransientFetchError(
            f"network error fetching https://www.pidpa.be/ons-aanbod/je-gemeente/{slug}: timeout"
        )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_COMMUNE: "mol",
            CONF_COMMUNE_LABEL: "2400 - Mol",
        },
        unique_id=f"{DOMAIN}_pidpa",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(
        id="pidpa",
        label="Pidpa",
        region="flanders",
        fetch=_fetch,
        fetch_for_commune=_fetch_for_commune,
    )
    with patch("custom_components.be_water_prices.coordinator.get", return_value=fake):
        assert not await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert not [r for r in caplog.records if "je-gemeente/mol" in r.getMessage()]
    assert "mol" not in (entry.reason or "")
    assert "**redacted**" in (entry.reason or "")


async def test_a_fault_on_the_cached_path_does_not_print_the_raw_fetch_error(
    hass: HomeAssistant, caplog: Any
) -> None:
    """Raised inside the handler, the fault carried the fetch error as its context."""
    caplog.set_level(logging.DEBUG)
    outage = False

    async def _fetch(_session: Any) -> WaterTariff:
        raise AssertionError("not used")

    async def _fetch_for_commune(_session: Any, slug: str) -> WaterTariff:
        if outage:
            raise TransientFetchError(
                f"network error fetching https://www.pidpa.be/ons-aanbod/je-gemeente/{slug}: timeout"
            )
        return _fresh_tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Pidpa",
        data={CONF_UTILITY: "pidpa"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_COMMUNE: "mol",
            CONF_COMMUNE_LABEL: "2400 - Mol",
        },
        unique_id=f"{DOMAIN}_pidpa",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(
        id="pidpa",
        label="Pidpa",
        region="flanders",
        fetch=_fetch,
        fetch_for_commune=_fetch_for_commune,
    )
    with patch("custom_components.be_water_prices.coordinator.get", return_value=fake):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        coordinator = hass.data[DOMAIN][entry.entry_id]
        outage = True
        with patch.object(coordinator, "_compute_ytd", side_effect=RuntimeError("fault")):
            await coordinator.async_refresh()
            await hass.async_block_till_done()

    assert "fault" in caplog.text
    assert "je-gemeente/mol" not in caplog.text
