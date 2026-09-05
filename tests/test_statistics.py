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

"""Price-history backfill service and its destructive paths.

The backfill wipes long-term statistics, so its guards are the part that
matters most and none of them had a test.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import voluptuous as vol
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    DOMAIN,
)
from custom_components.be_water_prices.statistics import (
    SERVICE_BACKFILL_PRICES,
    _async_clear_orphan_backfill_keys,
    async_backfill_prices,
    async_register_services,
)


def _entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    return entry


async def test_clear_without_an_entry_id_is_refused(hass: HomeAssistant) -> None:
    """clear=true cascades over every loaded entry, so it must be targeted.

    Wiping the long-term statistics of the whole install cannot be undone,
    and the service is reachable from any automation.
    """
    async_register_services(hass)
    with pytest.raises(vol.Invalid, match="requires an explicit entry_id"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_BACKFILL_PRICES,
            {"clear": True},
            blocking=True,
        )


async def test_clear_with_an_entry_id_is_allowed(hass: HomeAssistant) -> None:
    """The targeted form must still get through the guard."""
    entry = _entry(hass)
    async_register_services(hass)
    # No coordinator in hass.data, so the call is a no-op past the guard.
    await hass.services.async_call(
        DOMAIN,
        SERVICE_BACKFILL_PRICES,
        {"clear": True, "entry_id": entry.entry_id},
        blocking=True,
    )


async def test_backfill_skips_cleanly_without_a_recorder(hass: HomeAssistant) -> None:
    """No recorder must mean zero rows, not an exception through setup."""
    entry = _entry(hass)
    assert await async_backfill_prices(hass, entry, start=None, clear=False) == 0


async def test_orphan_clear_only_touches_keys_the_tariff_lost(hass: HomeAssistant) -> None:
    """Switching operators must wipe only the metrics that went away.

    The Walloon tariffs carry no comfort rate, so a Flanders entry that
    moves to Wallonia leaves orphan comfort rows behind. Every other key
    still has a value and its history has to survive.
    """
    from datetime import date

    from homeassistant.helpers.recorder import DATA_INSTANCE

    from custom_components.be_water_prices.coordinator import CoordinatorData
    from custom_components.be_water_prices.providers.base import WaterTariff

    entry = _entry(hass)
    walloon = WaterTariff(
        utility="swde",
        region="wallonia",
        valid_from=date(2026, 1, 1),
        valid_until=date(2026, 12, 31),
        publication_label="SWDE 2026",
        source_url="https://example.invalid/",
        yearly_fixed_fee=147.24,
        cvd_eur_per_m3=3.24,
    )
    coordinator = MagicMock()
    coordinator.data = CoordinatorData(
        tariff=walloon,
        fetched_at=None,
        snapshot_age_hours=0.0,
        snapshot_stale=False,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    ent_reg = er.async_get(hass)
    for key in ("basis_rate", "comfort_rate", "yearly_fee"):
        ent_reg.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{key}", suggested_object_id=f"vivaqua_{key}"
        )

    recorder = MagicMock()
    cleared: list[Any] = []
    recorder.async_clear_statistics = cleared.append
    # Nothing recorded before this year, so the orphan line is safe to drop.
    recorder.async_add_executor_job = AsyncMock(return_value={})
    hass.data[DATA_INSTANCE] = recorder
    with patch("homeassistant.components.recorder.get_instance", return_value=recorder):
        await _async_clear_orphan_backfill_keys(hass, entry)

    flat = [e for call in cleared for e in call]
    assert any("comfort_rate" in e for e in flat)
    assert not any("basis_rate" in e for e in flat)
    assert not any("yearly_fee" in e for e in flat)


async def test_orphan_clear_keeps_a_key_that_has_older_history(hass: HomeAssistant) -> None:
    """A key with rows from an earlier year must not be wiped.

    The recorder only deletes a statistic whole, so clearing a comfort
    rate that ran for three Flemish years would destroy the record of
    what the household actually paid. A stale line is the lesser harm.
    """
    from datetime import date

    from homeassistant.helpers.recorder import DATA_INSTANCE

    from custom_components.be_water_prices.coordinator import CoordinatorData
    from custom_components.be_water_prices.providers.base import WaterTariff

    entry = _entry(hass)
    coordinator = MagicMock()
    coordinator.data = CoordinatorData(
        tariff=WaterTariff(
            utility="swde",
            region="wallonia",
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
            publication_label="SWDE 2026",
            source_url="https://example.invalid/",
            yearly_fixed_fee=147.24,
            cvd_eur_per_m3=3.24,
        ),
        fetched_at=None,
        snapshot_age_hours=0.0,
        snapshot_stale=False,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{entry.entry_id}_comfort_rate",
        suggested_object_id="vivaqua_comfort_rate",
    )

    recorder = MagicMock()
    cleared: list[Any] = []
    recorder.async_clear_statistics = cleared.append
    recorder.async_add_executor_job = AsyncMock(
        return_value={"sensor.vivaqua_comfort_rate": [{"mean": 5.85}]}
    )
    hass.data[DATA_INSTANCE] = recorder
    with patch("homeassistant.components.recorder.get_instance", return_value=recorder):
        await _async_clear_orphan_backfill_keys(hass, entry)

    assert cleared == []


async def test_backfill_stops_at_the_snapshot_s_validity(hass: HomeAssistant) -> None:
    """Last year's rates must not be flat-lined across the new year.

    Only the near end of the window was clamped to the tariff, so a
    rollover against a page that has not published the new year yet
    extended the old rate forward as though it still applied -- and the
    auto-once gate then stopped any later run correcting it.
    """
    from datetime import date, datetime

    from homeassistant.helpers.recorder import DATA_INSTANCE

    from custom_components.be_water_prices.coordinator import CoordinatorData
    from custom_components.be_water_prices.providers.base import WaterTariff

    entry = _entry(hass)
    expired = WaterTariff(
        utility="vivaqua",
        region="brussels",
        valid_from=date(2020, 1, 1),
        valid_until=date(2020, 12, 31),
        publication_label="VIVAQUA 2020",
        source_url="https://example.invalid/",
        yearly_fixed_fee=40.0,
        linear_eur_per_m3=2.0,
    )
    coordinator = MagicMock()
    coordinator.data = CoordinatorData(
        tariff=expired,
        fetched_at=None,
        snapshot_age_hours=0.0,
        snapshot_stale=True,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    ent_reg = er.async_get(hass)
    ent_reg.async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_basis_rate", suggested_object_id="v_basis_rate"
    )

    recorder = MagicMock()
    hass.data[DATA_INSTANCE] = recorder
    imported: list[Any] = []
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=recorder),
        patch(
            "homeassistant.components.recorder.statistics.async_import_statistics",
            side_effect=lambda *a, **k: imported.append(a),
        ),
    ):
        rows = await async_backfill_prices(hass, entry, start=datetime(2020, 1, 1))

    # The window closes at the tariff's own valid_until: one leap year of
    # hourly rows, not every hour from 2020 until now.
    hours_in_2020 = 366 * 24
    assert rows <= hours_in_2020 + 1
    unclamped = (datetime.now() - datetime(2020, 1, 1)).days * 24
    assert rows < unclamped / 2


async def test_orphan_entities_are_removed_after_the_statistics_cleanup(
    hass: HomeAssistant,
) -> None:
    """Order matters: the rows are found through the registry entry.

    The statistics cleanup looks each orphan key up by its entity id, so
    removing the registry entry during platform setup -- before the
    backfill runs -- left the rows behind with nothing left to find them
    by.
    """
    from datetime import date

    from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff

    calls: list[str] = []

    async def _fetch(_session: Any) -> WaterTariff:
        return WaterTariff(
            utility="vivaqua",
            region="brussels",
            valid_from=date(2026, 1, 1),
            valid_until=date(2026, 12, 31),
            publication_label="VIVAQUA 2026",
            source_url="https://example.invalid/",
            yearly_fixed_fee=40.0,
            linear_eur_per_m3=2.0,
        )

    entry = _entry(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)

    async def _backfill(*_a: Any, **_k: Any) -> None:
        calls.append("backfill")

    def _remove(*_a: Any, **_k: Any) -> None:
        calls.append("remove_entities")

    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.statistics.async_maybe_backfill_once",
            new=_backfill,
        ),
        patch(
            "custom_components.be_water_prices.sensor.async_remove_inapplicable_entities",
            new=_remove,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    assert calls == ["backfill", "remove_entities"], calls
