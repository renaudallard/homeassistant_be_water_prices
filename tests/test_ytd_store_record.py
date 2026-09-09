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

"""A YTD Store file nobody can read costs one fresh cycle, not the entry."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from custom_components.be_water_prices.coordinator import _YTD_STORE_MINOR_VERSION
from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
from tests.test_ha_coordinator import _fresh_tariff


async def _fetch(_session: Any) -> WaterTariff:
    return _fresh_tariff()


@pytest.mark.parametrize(
    "record",
    [
        {
            "meter": "sensor.water_meter",
            "year": 2026,
            "m3": "twenty",
            "cost": 1.0,
            "offset_m3": 80.0,
        },
        {"meter": "sensor.water_meter", "year": 2026, "m3": [20.0], "cost": 1.0, "offset_m3": 80.0},
        {"meter": ["sensor.water_meter"], "year": 2026, "m3": 20.0, "cost": 1.0, "offset_m3": 80.0},
        {"meter": "sensor.water_meter", "year": "2026", "m3": 20.0, "cost": 1.0, "offset_m3": 80.0},
        ["not", "a", "record"],
    ],
)
async def test_a_malformed_record_starts_a_fresh_cycle(
    hass: HomeAssistant, hass_storage: dict[str, Any], caplog: Any, record: Any
) -> None:
    hass.states.async_set("sensor.water_meter", "100")
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
        "minor_version": _YTD_STORE_MINOR_VERSION,
        "data": record,
    }
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
    assert entry.state is ConfigEntryState.LOADED
    assert "starting a fresh one" in caplog.text
    assert hass.data[DOMAIN][entry.entry_id].data.ytd_consumption_m3 == 20.0


def test_a_record_written_before_the_new_keys_can_still_be_corrected() -> None:
    """An upgrading install has to get the fix, or nobody does.

    Nothing in an older record may stand in the way of the correction: a
    year sitting on a spike today is exactly what it was written for. What
    such a record does lack is a recorder high-water mark, so the first
    answer of the year still proves nothing about its own history and
    cannot lower anything on its own, which is the one guard that should
    hold an upgrading install back and only for a round.
    """
    from custom_components.be_water_prices.coordinator import _cycle_from_record

    old = _cycle_from_record(
        {"meter": "sensor.water_meter", "year": 2026, "m3": 40.0, "offset_m3": 1000.0}
    )
    assert old is not None
    assert old.recorder_hwm is None
    assert old.seen_at is None


def test_the_new_keys_round_trip_through_the_record() -> None:
    from custom_components.be_water_prices.coordinator import _cycle_from_record

    cycle = _cycle_from_record(
        {
            "meter": "sensor.water_meter",
            "year": 2026,
            "m3": 40.0,
            "offset_m3": 1000.0,
            "recorder_hwm": 39.5,
            "seen_at": 1_760_000_000.0,
        }
    )
    assert cycle is not None
    assert cycle.recorder_hwm == 39.5
    assert cycle.seen_at == 1_760_000_000.0


def test_when_the_meter_was_last_seen_survives_a_restart() -> None:
    """The gap that matters most is the one no in-process clock survives.

    The step bound is scaled by how long the meter has been out of sight,
    and Home Assistant being down is the longest such gap there is. Only a
    stamp on the record can measure it, so it is written and read back.
    """
    from custom_components.be_water_prices.coordinator import _cycle_from_record

    base = {"meter": "sensor.water_meter", "year": 2026, "m3": 40.0, "offset_m3": 1000.0}

    kept = _cycle_from_record({**base, "seen_at": 1_760_000_000.0})
    assert kept is not None
    assert kept.seen_at == 1_760_000_000.0

    # A record from before the stamp says nothing about when the meter was
    # last seen, and the honest reading of that is no gap rather than one
    # of unknown length handed over for free.
    silent = _cycle_from_record(base)
    assert silent is not None
    assert silent.seen_at is None

    assert _cycle_from_record({**base, "seen_at": "recently"}) is None


def test_a_recorder_high_water_mark_that_is_not_a_number_is_refused() -> None:
    """The same rule the other figures follow: unreadable means re-bootstrap."""
    from custom_components.be_water_prices.coordinator import _cycle_from_record

    assert (
        _cycle_from_record(
            {"meter": "sensor.water_meter", "year": 2026, "m3": 40.0, "recorder_hwm": "lots"}
        )
        is None
    )
