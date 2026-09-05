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

from datetime import date, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.util import dt as dt_util

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
        self.data: dict[str, str] = {CONF_UTILITY: "vivaqua"}
        self.options: dict[str, str] = {}


class _StubCoordinator:
    def __init__(self) -> None:
        self.entry = _StubEntry()
        self.data = None


def _sensor(key: str) -> WaterSensor:
    return _sensor_with(key, _StubCoordinator())


def _sensor_with(key: str, coordinator: _StubCoordinator) -> WaterSensor:
    desc = next(d for d in SENSORS if d.key == key)
    return WaterSensor(coordinator, desc)  # type: ignore[arg-type]


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


async def test_the_drop_guard_survives_a_restart() -> None:
    """A restart must not forget a drop, nor the value it was measured against.

    Held only in memory, the guard came back empty: the recorded reset
    point was lost, so the recorder saw the next figure as a negative
    delta on a cycle that had already been closed, and the last value was
    lost too, so a drop happening across the restart went unnoticed.
    """
    sensor = _sensor("ytd_consumption")
    earlier = dt_util.now() - timedelta(days=3)

    class _Stored:
        def as_dict(self) -> dict[str, Any]:
            return {"reset_at": earlier.isoformat(), "last_native": 42.0}

    with (
        patch.object(WaterSensor, "async_get_last_extra_data", AsyncMock(return_value=_Stored())),
        patch(
            "homeassistant.helpers.update_coordinator.CoordinatorEntity.async_added_to_hass",
            AsyncMock(),
        ),
    ):
        await sensor.async_added_to_hass()

    assert sensor._reset_at == earlier
    assert sensor._last_native == 42.0
    # The restored value is a real comparison point straight away, so a
    # drop over the restart is caught on the first update.
    sensor._note_value(1.0)
    assert sensor._reset_at is not None and sensor._reset_at > earlier


def test_the_guard_is_handed_out_for_storage() -> None:
    sensor = _sensor("ytd_consumption")
    sensor._note_value(10.0)
    stored = sensor.extra_restore_state_data
    assert stored is not None
    assert stored.as_dict()["last_native"] == 10.0
    # A sensor with no drop guard has nothing to store.
    assert _sensor("basis_rate").extra_restore_state_data is None


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


def test_label_suffix_survives_when_it_is_not_the_commune() -> None:
    """Only a suffix naming the commune is redaction; the rest is content.

    VIVAQUA puts the VAT basis in that parenthetical and De Watergroep's
    fallback puts its drinkwater-only marker there. Dropping them told
    the user less about their tariff and hid nothing.
    """
    from custom_components.be_water_prices.sensor import (
        _publication_label_without_commune as strip,
    )

    assert strip("Price from January 1st 2026 (VAT included 6 %)", ["Geel"]) == (
        "Price from January 1st 2026 (VAT included 6 %)"
    )
    # An entry with no commune configured has nothing to redact.
    assert strip("Pidpa tarieven 2026 (Geel)", []) == "Pidpa tarieven 2026 (Geel)"
    # The commune itself still goes, nested marker and all.
    assert strip("Pidpa tarieven 2026 (Geel)", ["Geel"]) == "Pidpa tarieven 2026"
    assert (
        strip("De Watergroep tarieven 2026 (Halle (DWG-served default))", ["Halle"])
        == "De Watergroep tarieven 2026"
    )


def test_last_error_is_scrubbed_of_the_commune() -> None:
    """A fetch error quotes the URL it failed on, commune slug and all.

    The neighbouring source_url and publication_label attributes are both
    redacted, so publishing last_error raw put the household's location
    back into the recorder through the side door.
    """
    from datetime import UTC

    from custom_components.be_water_prices.const import CONF_COMMUNE, CONF_COMMUNE_LABEL
    from custom_components.be_water_prices.coordinator import CoordinatorData
    from custom_components.be_water_prices.providers.base import WaterTariff

    coordinator = _StubCoordinator()
    coordinator.entry.data = {CONF_UTILITY: "pidpa", CONF_COMMUNE: "geel"}
    coordinator.entry.options = {CONF_COMMUNE_LABEL: "Geel"}
    coordinator.data = CoordinatorData(  # type: ignore[assignment]
        tariff=WaterTariff(
            utility="pidpa",
            region="flanders",
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
            publication_label="Pidpa tarieven 2026 (Geel)",
            source_url="https://www.pidpa.be/tarieven/geel",
            yearly_fixed_fee=50.0,
            basis_eur_per_m3=2.0,
            comfort_eur_per_m3=4.0,
        ),
        fetched_at=datetime(2026, 1, 2, tzinfo=UTC),
        snapshot_age_hours=1.0,
        snapshot_stale=False,
        last_error="HTTP 404 fetching https://www.pidpa.be/tarieven/geel",
    )

    attrs = _sensor_with("basis_rate", coordinator).extra_state_attributes
    assert "geel" not in attrs["last_error"].lower()
    assert "404" in attrs["last_error"]
