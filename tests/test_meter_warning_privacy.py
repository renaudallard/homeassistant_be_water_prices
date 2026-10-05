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

"""The default-level log says "the water meter", never which one."""

from __future__ import annotations

import logging
from datetime import date, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from custom_components.be_water_prices import coordinator as module
from custom_components.be_water_prices.coordinator import (
    WaterCoordinator,
    _admitted_changes,
    _discover_energy_water_meter,
    _fold,
    _recorder_full_year_m3,
    _recorder_ytd_m3,
    _YtdCycle,
)

_METER = "sensor.water_meter_serial_12345678"


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
        assert await _discover_energy_water_meter(hass) == ("sensor.kitchen_water", 2)
    assert "2 water meters" in caplog.text
    assert "sensor.kitchen_water" not in caplog.text
    assert "sensor.garden_water" not in caplog.text


def _warned(caplog: pytest.LogCaptureFixture, words: str) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.levelno >= logging.WARNING and words in r.getMessage()
    ]


def _fold_round(cycle: _YtdCycle, reading: float | None) -> None:
    _fold(
        cycle,
        now_year=2026,
        meter=_METER,
        reading=reading,
        recorder_m3=10.0,
        recorder_has_statistic=True,
        recorder_ok=True,
        hold_m3=None,
        hold_run=0,
        hold_span_s=0.0,
        run_m3=None,
        elapsed_s=0.0,
        now_ts=0.0,
        high_m3=None,
        after_swap=False,
        basis="b",
        cost_of=lambda m3: m3,
    )


def test_the_fold_warnings_name_no_entity(caplog: pytest.LogCaptureFixture) -> None:
    """Both of the fold's "taking the recorder" warnings reach the default log."""
    with caplog.at_level(logging.WARNING):
        _fold_round(
            _YtdCycle(
                meter=_METER, year=2026, m3=10.0, offset_m3=0.0, recorder_hwm=10.0, basis="b"
            ),
            20.0,
        )
        _fold_round(
            _YtdCycle(
                meter=_METER, year=2026, m3=20.0, offset_m3=0.0, recorder_hwm=10.0, basis="b"
            ),
            20.0,
        )
    assert _warned(caplog, "a reading of the water meter implying 20.0 m3")
    assert _warned(caplog, "the water meter's year stood at 20.0 m3")
    assert _METER not in caplog.text


def _rows(days: dict[date, float]) -> list[dict[str, Any]]:
    total = 4000.0
    rows = []
    for day, change in sorted(days.items()):
        total += change
        rows.append(
            {
                "start": dt_util.start_of_local_day(day).timestamp(),
                "change": change,
                "state": total,
            }
        )
    return rows


async def test_the_year_to_date_reader_warns_without_the_entity(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _rows({date(2026, 3, 2): 1189.6, date(2026, 3, 3): 0.3})
    with (
        patch.object(module, "_recorder_daily_rows", new=AsyncMock(return_value=rows)),
        caplog.at_level(logging.WARNING),
    ):
        await _recorder_ytd_m3(hass, _METER, date(2026, 3, 1), date(2026, 3, 5))
    assert _warned(caplog, "ignoring a single change of 1189.6 m3")
    assert _METER not in caplog.text


async def test_the_full_year_reader_warns_without_the_entity(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    start = date(2024, 12, 20)
    days = {start + timedelta(days=n): 0.2 for n in range(380)}
    days[date(2025, 6, 2)] = 1180.0
    rows = _rows(days)
    with (
        patch.object(module, "_recorder_daily_rows", new=AsyncMock(return_value=rows)),
        caplog.at_level(logging.WARNING),
    ):
        assert await _recorder_full_year_m3(hass, _METER, 2025) is None
    assert _warned(caplog, "ignoring a full-year change of 1180.0 m3")
    assert _METER not in caplog.text


async def test_a_quiet_refusal_still_names_the_meter(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """At DEBUG the id stays, so a household with two entries can tell them apart."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _rows({date(2026, 3, 2): 1189.6, date(2026, 3, 3): 0.3})
    with caplog.at_level(logging.DEBUG):
        _admitted_changes(rows, _METER, date(2026, 3, 1), date(2026, 3, 5), level=logging.DEBUG)
    assert [
        r.levelno
        for r in caplog.records
        if f"1189.6 m3 over 2 day(s) on {_METER}" in r.getMessage()
    ] == [logging.DEBUG]


async def test_a_failed_meter_history_read_names_no_entity(
    caplog: pytest.LogCaptureFixture,
) -> None:
    owner = SimpleNamespace(
        _sync_projection_issue=AsyncMock(),
        _read_metered_days=AsyncMock(side_effect=RuntimeError("boom")),
    )
    with caplog.at_level(logging.WARNING):
        await WaterCoordinator._read_meter_history(owner, _METER)  # type: ignore[arg-type]
    assert _warned(caplog, "could not read the last year of the water meter")
    assert _METER not in caplog.text
