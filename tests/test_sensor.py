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

"""WaterSensor last_reset handling for TOTAL sensors that can decrease."""

from __future__ import annotations

from custom_components.be_water_prices.const import CONF_UTILITY
from custom_components.be_water_prices.sensor import (
    SENSORS,
    WaterSensor,
    _jan_1_local,
    _source_url_without_commune,
)


class _StubEntry:
    def __init__(self) -> None:
        self.entry_id = "e1"
        self.title = "VIVAQUA"
        self.data = {CONF_UTILITY: "vivaqua"}


class _StubCoordinator:
    def __init__(self) -> None:
        self.entry = _StubEntry()
        self.data = None


def _sensor(key: str) -> WaterSensor:
    desc = next(d for d in SENSORS if d.key == key)
    return WaterSensor(_StubCoordinator(), desc)  # type: ignore[arg-type]


def test_last_reset_advances_on_mid_cycle_drop() -> None:
    sensor = _sensor("ytd_consumption")
    jan1 = _jan_1_local()
    # A normal climb keeps last_reset at the calendar-year start.
    sensor._note_value(10.0)
    sensor._note_value(20.0)
    assert sensor.last_reset == jan1
    # A meter swap floors YTD to 0 mid-year: last_reset moves past Jan 1 so
    # HA opens a fresh statistics cycle instead of recording a negative delta.
    sensor._note_value(0.0)
    reset = sensor.last_reset
    assert reset is not None and reset > jan1
    # A subsequent climb keeps the new reset point (no further advance).
    sensor._note_value(3.0)
    assert sensor.last_reset == reset


def test_last_reset_none_for_non_total_sensor() -> None:
    sensor = _sensor("basis_rate")
    sensor._note_value(5.0)
    sensor._note_value(1.0)  # a drop, but no last_reset_fn -> untracked
    assert sensor.last_reset is None


def test_source_url_redacts_commune_slug() -> None:
    url = "https://www.pidpa.be/ons-aanbod/je-gemeente/geel"
    # Per-commune Pidpa URL embeds the town slug -> redacted.
    assert "geel" not in _source_url_without_commune(url, "geel")
    # No commune configured, or a commune id not present in the URL
    # (e.g. Farys numeric id), leaves the URL untouched.
    assert _source_url_without_commune(url, None) == url
    assert _source_url_without_commune(url, "25071") == url
