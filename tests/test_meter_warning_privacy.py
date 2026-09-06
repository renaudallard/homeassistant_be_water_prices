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

"""The two-meter warning says how many, not which."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.be_water_prices.coordinator import _discover_energy_water_meter


async def test_the_second_meter_warning_names_no_entity(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Diagnostics redact the meter id; the default-level log must not print it."""
    manager = SimpleNamespace(
        data={
            "energy_sources": [
                {"type": "water", "stat_energy_from": "sensor.kitchen_water"},
                {"type": "water", "stat_energy_from": "sensor.garden_water"},
            ]
        }
    )
    with (
        patch(
            "homeassistant.components.energy.async_get_manager",
            new=AsyncMock(return_value=manager),
        ),
        caplog.at_level(logging.WARNING),
    ):
        assert await _discover_energy_water_meter(hass) == "sensor.kitchen_water"
    assert "2 water meters" in caplog.text
    assert "sensor.kitchen_water" not in caplog.text
    assert "sensor.garden_water" not in caplog.text
