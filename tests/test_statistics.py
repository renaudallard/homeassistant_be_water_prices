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
from homeassistant.util import dt as dt_util
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


async def test_orphan_cleanup_runs_before_a_stale_snapshot_defers_the_backfill(
    hass: HomeAssistant,
) -> None:
    """The cleanup finds the rows through a registry entry setup removes next.

    Deferring the whole run on a stale snapshot skipped the cleanup too,
    and by the next setup the entry was gone, so the previous operator's
    comfort_rate line was stranded for good.
    """
    from datetime import date as _date
    from types import SimpleNamespace

    from custom_components.be_water_prices.statistics import (
        DATA_BACKFILL_YEAR,
        async_maybe_backfill_once,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua", DATA_BACKFILL_YEAR: "2026:farys:2026"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    # A real CoordinatorData always carries a tariff; the gate reads its
    # year, so the stub has to as well.
    stale = SimpleNamespace(
        data=SimpleNamespace(
            snapshot_stale=True,
            tariff=SimpleNamespace(valid_from=_date(2026, 1, 1)),
        )
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = stale

    with (
        patch(
            "custom_components.be_water_prices.statistics._async_clear_orphan_backfill_keys",
            new=AsyncMock(),
        ) as clear,
        patch(
            "custom_components.be_water_prices.statistics.async_backfill_prices",
            new=AsyncMock(return_value=0),
        ) as backfill,
    ):
        await async_maybe_backfill_once(hass, entry)

    clear.assert_awaited_once()
    backfill.assert_not_awaited()
    assert entry.data[DATA_BACKFILL_YEAR] == "2026:farys:2026"


async def test_backfill_writes_the_card_s_last_hour(hass: HomeAssistant) -> None:
    """The window closes at the midnight after valid_until, not at 23:00 of it."""
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
        tariff=expired, fetched_at=None, snapshot_age_hours=0.0, snapshot_stale=True
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_basis_rate", suggested_object_id="v_basis_rate"
    )
    hass.data[DATA_INSTANCE] = MagicMock()
    imported: list[Any] = []
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=MagicMock()),
        patch(
            "homeassistant.components.recorder.statistics.async_import_statistics",
            side_effect=lambda *a, **k: imported.append(a),
        ),
    ):
        rows = await async_backfill_prices(hass, entry, start=datetime(2020, 1, 1))

    assert rows == 366 * 24
    last = imported[-1][2][-1]["start"]
    assert last.astimezone(dt_util.DEFAULT_TIME_ZONE).hour == 23


async def test_a_start_past_the_window_says_so(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """clear=True on a start past the card's end wrote and cleared nothing, in silence."""
    import logging
    from datetime import UTC, date, datetime

    from homeassistant.helpers.recorder import DATA_INSTANCE

    from custom_components.be_water_prices.coordinator import CoordinatorData
    from custom_components.be_water_prices.statistics import async_backfill_prices
    from tests.test_ha_coordinator import _fresh_tariff

    entry = _entry(hass)
    coordinator = MagicMock()
    coordinator.data = CoordinatorData(
        tariff=_fresh_tariff(valid_until=date(2026, 3, 31)),
        fetched_at=datetime.now(UTC),
        snapshot_age_hours=0.0,
        snapshot_stale=False,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_basis_rate", suggested_object_id="v_basis_rate"
    )
    recorder = MagicMock()
    hass.data[DATA_INSTANCE] = recorder
    caplog.set_level(logging.INFO, logger="custom_components.be_water_prices.statistics")
    with patch("homeassistant.components.recorder.get_instance", return_value=recorder):
        rows = await async_backfill_prices(
            hass, entry, start=datetime(2026, 6, 1, tzinfo=UTC), clear=True
        )
    assert rows == 0
    recorder.async_clear_statistics.assert_not_called()
    assert "nothing written or cleared" in caplog.text


async def test_an_unreadable_run_marker_leaves_the_last_hour_out(
    hass: HomeAssistant, freezer: Any
) -> None:
    """Without the recorder's run marker the hour that just ended stays unwritten.

    The recorder compiles that hour ten seconds after it closes, and a row
    imported ahead of it makes the compile fail for every entity.
    """
    from datetime import UTC, datetime, timedelta

    from homeassistant.helpers.recorder import DATA_INSTANCE

    from custom_components.be_water_prices.coordinator import CoordinatorData
    from tests.test_ha_coordinator import _fresh_tariff

    freezer.move_to("2026-10-05 14:00:05+00:00")
    entry = _entry(hass)
    coordinator = MagicMock()
    coordinator.data = CoordinatorData(
        tariff=_fresh_tariff(),
        fetched_at=datetime.now(UTC),
        snapshot_age_hours=0.0,
        snapshot_stale=False,
    )
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    er.async_get(hass).async_get_or_create(
        "sensor", DOMAIN, f"{entry.entry_id}_basis_rate", suggested_object_id="v_basis_rate"
    )
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(side_effect=RuntimeError("db locked"))
    hass.data[DATA_INSTANCE] = recorder
    imported: list[Any] = []
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=recorder),
        patch(
            "homeassistant.components.recorder.statistics.async_import_statistics",
            side_effect=lambda *a, **k: imported.append(a),
        ),
    ):
        rows = await async_backfill_prices(hass, entry, start=datetime(2026, 10, 5, tzinfo=UTC))

    assert rows > 0
    last = imported[-1][2][-1]["start"]
    assert last + timedelta(hours=1) == datetime(2026, 10, 5, 13, tzinfo=UTC)


async def test_the_auto_once_gate_holds_for_the_same_year_and_utility(hass: HomeAssistant) -> None:
    """Deleting the gate re-ran the backfill on every setup and nothing noticed.

    No coordinator is registered here, so the card year is None and the
    stored gate has to say so too.
    """
    from custom_components.be_water_prices.statistics import (
        DATA_BACKFILL_YEAR,
        async_maybe_backfill_once,
    )

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={
            CONF_UTILITY: "vivaqua",
            DATA_BACKFILL_YEAR: f"{dt_util.now().year}:vivaqua:None:None",
        },
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    with patch(
        "custom_components.be_water_prices.statistics.async_backfill_prices",
        new=AsyncMock(return_value=0),
    ) as backfill:
        await async_maybe_backfill_once(hass, entry)
    backfill.assert_not_awaited()


async def test_the_price_line_starts_on_1_january(hass: HomeAssistant, freezer: Any) -> None:
    """The auto-once run and the service without a start date both write
    the year from 1 January 00:00 local.

    The date is written out rather than asked of the code under test: a
    start that moved to the 1st of the month would agree with itself.
    """
    from datetime import datetime
    from types import SimpleNamespace

    from custom_components.be_water_prices.statistics import async_maybe_backfill_once

    await hass.config.async_set_time_zone("Europe/Brussels")
    freezer.move_to("2026-07-15 10:00:00+00:00")
    jan_1 = datetime(2026, 1, 1, tzinfo=dt_util.get_time_zone("Europe/Brussels"))
    entry = _entry(hass)
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = SimpleNamespace(data=None)
    async_register_services(hass)
    with patch(
        "custom_components.be_water_prices.statistics.async_backfill_prices",
        new=AsyncMock(return_value=0),
    ) as backfill:
        await async_maybe_backfill_once(hass, entry)
        await hass.services.async_call(DOMAIN, SERVICE_BACKFILL_PRICES, {}, blocking=True)
    assert [call.kwargs["start"] for call in backfill.await_args_list] == [jan_1, jan_1]


async def test_picking_a_commune_rewrites_the_price_line(hass: HomeAssistant) -> None:
    """Several of the sensors this writes are made of the commune.

    The gemeentelijke saneringsbijdrage is a commune's own number, so
    sanering_rate and all_in_basis move with it. A household that installs,
    gets the operator default flat-lined and then picks its own commune
    kept the default's rates in History until January: 0.59 EUR/m3 out on
    De Watergroep at Overijse, 0.66 on Water-link at Edegem.
    """
    from custom_components.be_water_prices.const import CONF_COMMUNE
    from custom_components.be_water_prices.statistics import (
        DATA_BACKFILL_YEAR,
        async_maybe_backfill_once,
    )

    year = dt_util.now().year
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="De Watergroep",
        data={
            CONF_UTILITY: "de_watergroep",
            DATA_BACKFILL_YEAR: f"{year}:de_watergroep:None:None",
        },
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_de_watergroep",
    )
    entry.add_to_hass(hass)

    # Nothing has changed yet, so nothing is rewritten.
    with patch(
        "custom_components.be_water_prices.statistics.async_backfill_prices",
        new=AsyncMock(return_value=0),
    ) as untouched:
        await async_maybe_backfill_once(hass, entry)
    untouched.assert_not_awaited()

    # The household picks its commune.
    hass.config_entries.async_update_entry(
        entry, options={**entry.options, CONF_COMMUNE: "{A-GUID}"}
    )
    with (
        patch(
            "custom_components.be_water_prices.statistics._async_clear_orphan_backfill_keys",
            new=AsyncMock(),
        ) as clear,
        patch(
            "custom_components.be_water_prices.statistics.async_backfill_prices",
            new=AsyncMock(return_value=99),
        ) as rewritten,
    ):
        await async_maybe_backfill_once(hass, entry)
    rewritten.assert_awaited_once()
    assert entry.data[DATA_BACKFILL_YEAR] == f"{year}:de_watergroep:None:{{A-GUID}}"

    # The operator has not changed, so there are no orphaned rows to sweep.
    # This is also what pins the utility to the second field: every term
    # since has been appended, and reading the gate from the other end
    # would take the commune for the operator on every entry that has one.
    clear.assert_not_awaited()


async def test_an_unreadable_history_counts_as_history(hass: HomeAssistant) -> None:
    """The orphan cleanup deletes whole statistics; a failed read must keep them."""
    from datetime import datetime

    from custom_components.be_water_prices.statistics import _async_has_statistics_before

    instance = MagicMock()
    instance.async_add_executor_job = AsyncMock(side_effect=RuntimeError("db locked"))
    with patch("homeassistant.components.recorder.get_instance", return_value=instance):
        assert await _async_has_statistics_before(
            hass, "sensor.x", datetime(2026, 1, 1, tzinfo=dt_util.UTC)
        )


async def test_a_backfill_that_raises_does_not_take_setup_down(
    hass: HomeAssistant, caplog: pytest.LogCaptureFixture
) -> None:
    """Narrowing the except around the backfill left the suite green."""
    import logging

    from homeassistant.config_entries import ConfigEntryState

    from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
    from tests.test_ha_coordinator import _fresh_tariff

    async def _fetch(_session: Any) -> WaterTariff:
        return _fresh_tariff()

    entry = _entry(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    caplog.set_level(logging.ERROR)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.statistics.async_maybe_backfill_once",
            new=AsyncMock(side_effect=RuntimeError("recorder exploded")),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert "backfill failed" in caplog.text


async def test_a_prior_year_card_does_not_stamp_the_gate_for_the_whole_year(
    hass: HomeAssistant,
) -> None:
    """January's flat line was written at last year's rate and never corrected."""
    from datetime import date as _date
    from types import SimpleNamespace

    from custom_components.be_water_prices.statistics import (
        DATA_BACKFILL_YEAR,
        async_maybe_backfill_once,
    )

    now_year = dt_util.now().year
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={
            CONF_UTILITY: "vivaqua",
            DATA_BACKFILL_YEAR: f"{now_year - 1}:vivaqua:{now_year - 1}",
        },
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)

    def _coordinator(card_year: int) -> SimpleNamespace:
        return SimpleNamespace(
            data=SimpleNamespace(
                snapshot_stale=False,
                tariff=SimpleNamespace(valid_from=_date(card_year, 1, 1)),
            )
        )

    # January: carry_prior_year_card is serving last year's card.
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = _coordinator(now_year - 1)
    with patch(
        "custom_components.be_water_prices.statistics.async_backfill_prices",
        new=AsyncMock(return_value=99),
    ) as first:
        await async_maybe_backfill_once(hass, entry)
    assert first.await_count == 1
    assert entry.data[DATA_BACKFILL_YEAR] == f"{now_year}:vivaqua:{now_year - 1}:None"

    # March: the operator publishes this year's card. The line has to be
    # rewritten at the rate that actually applied from 1 January.
    hass.data[DOMAIN][entry.entry_id] = _coordinator(now_year)
    with patch(
        "custom_components.be_water_prices.statistics.async_backfill_prices",
        new=AsyncMock(return_value=99),
    ) as second:
        await async_maybe_backfill_once(hass, entry)
    assert second.await_count == 1
    assert entry.data[DATA_BACKFILL_YEAR] == f"{now_year}:vivaqua:{now_year}:None"


async def test_a_daily_tick_rewrites_the_price_line_when_the_card_lands(
    hass: HomeAssistant,
) -> None:
    """The gate carries the card's year, and only setup ever consulted it.

    Publishers run late and last year's card stands until 31 March, so
    January's flat line goes in at last year's rate. An install that does
    not restart between January and the new card landing kept it. Once the
    tick did check the gate, it ran before the refresh had published its
    result and compared the previous card's year, so the new card waited
    another day.
    """
    from datetime import date

    from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
    from custom_components.be_water_prices.statistics import DATA_BACKFILL_YEAR

    written: list[tuple[int, float | None]] = []
    # Tied to the clock so the card never runs past its validity and
    # marks the snapshot stale, which would hold the gate shut.
    year = dt_util.now().year
    card_year = year - 1

    async def _fetch(_session: Any) -> WaterTariff:
        return WaterTariff(
            utility="vivaqua",
            region="brussels",
            valid_from=date(card_year, 1, 1),
            valid_until=date(year, 12, 31),
            publication_label=f"VIVAQUA {card_year}",
            source_url="https://example.invalid/",
            yearly_fixed_fee=40.0,
            linear_eur_per_m3=2.0 if card_year == year - 1 else 3.0,
        )

    entry = _entry(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)

    async def _backfill(hass_: HomeAssistant, entry_: Any, **_k: Any) -> int:
        tariff = hass_.data[DOMAIN][entry_.entry_id].data.tariff
        written.append((tariff.valid_from.year, tariff.linear_eur_per_m3))
        return 99

    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(
            "custom_components.be_water_prices.statistics.async_backfill_prices",
            new=_backfill,
        ),
        # Stamping the gate reloads the entry, and the reload's own setup
        # would catch up on the card. Only the tick is under test here.
        patch(
            "custom_components.be_water_prices._async_update_listener",
            new=AsyncMock(),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
        # Setup ran it once; the first refresh inside setup must not.
        assert written == [(year - 1, 2.0)], written
        assert entry.data[DATA_BACKFILL_YEAR] == f"{year}:vivaqua:{year - 1}:None"

        # The operator finally publishes, and the daily tick picks it up.
        card_year = year
        coordinator = hass.data[DOMAIN][entry.entry_id]
        await coordinator.async_refresh()
        await hass.async_block_till_done()

    assert written == [(year - 1, 2.0), (year, 3.0)], written
    assert entry.data[DATA_BACKFILL_YEAR] == f"{year}:vivaqua:{year}:None"


async def _backfill_a_metered_entry(
    hass: HomeAssistant, freezer: Any, *, clear: bool
) -> tuple[int, list[tuple[str, Any]]]:
    """Backfill a Brussels entry in October that has every sensor and a meter.

    The card runs to 31 December, the recorder's newest run marker lags an
    hour behind the clock, and every meter-driven figure carries a value,
    so only the key filter keeps those sensors out. Returns the row count
    and the recorder calls in the order they were made.
    """
    from datetime import UTC, date, datetime

    from homeassistant.helpers.recorder import DATA_INSTANCE

    from custom_components.be_water_prices.coordinator import CoordinatorData, YearFigures
    from custom_components.be_water_prices.sensor import SENSORS
    from tests.test_ha_coordinator import _fresh_tariff

    await hass.config.async_set_time_zone("Europe/Brussels")
    freezer.move_to("2026-10-04 20:30:00+00:00")
    entry = _entry(hass)
    data = CoordinatorData(
        tariff=_fresh_tariff(valid_until=date(2026, 12, 31)),
        fetched_at=datetime.now(UTC),
        snapshot_age_hours=0.0,
        snapshot_stale=False,
        projected_annual_cost_eur=300.0,
        current_year_cost_eur=50.0,
        ytd_consumption_m3=20.0,
        year_figures=YearFigures(
            rolling_m3=80.0,
            rolling_cost_eur=310.0,
            projected_m3=78.0,
            projected_end_cost_eur=305.0,
        ),
    )
    # A meter-driven sensor with no value is skipped before the key filter
    # is reached, and the test would then pass with the filter gone. Only
    # the comfort rate, which Brussels does not have, may be empty.
    for desc in SENSORS:
        if desc.key != "comfort_rate":
            assert desc.value_fn(data) is not None, desc.key
    coordinator = MagicMock()
    coordinator.data = data
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    ent_reg = er.async_get(hass)
    for desc in SENSORS:
        ent_reg.async_get_or_create(
            "sensor", DOMAIN, f"{entry.entry_id}_{desc.key}", suggested_object_id=f"v_{desc.key}"
        )

    calls: list[tuple[str, Any]] = []
    recorder = MagicMock()
    recorder.async_add_executor_job = AsyncMock(
        return_value=datetime(2026, 10, 4, 18, 55, tzinfo=UTC)
    )
    recorder.async_clear_statistics = lambda ids: calls.append(("clear", ids))
    hass.data[DATA_INSTANCE] = recorder
    with (
        patch("homeassistant.components.recorder.get_instance", return_value=recorder),
        patch(
            "homeassistant.components.recorder.statistics.async_import_statistics",
            side_effect=lambda _h, meta, rows: calls.append(("import", (meta, rows))),
        ),
    ):
        rows = await async_backfill_prices(hass, entry, start=datetime(2026, 3, 15), clear=clear)
    return rows, calls


async def test_the_backfill_writes_only_the_card_s_rates_up_to_the_compiled_hour(
    hass: HomeAssistant, freezer: Any
) -> None:
    """A card valid to 31 December must stop at the recorder, not at the card.

    Clamping to the card alone would write three months of rows into the
    future. The year-to-date cost and consumption are TOTAL statistics made
    of the meter's history, and flat rows written over them would invent
    consumption, as would rows for the projections.
    """
    from datetime import UTC, datetime, timedelta

    from custom_components.be_water_prices.sensor import EUR_PER_M3, EUR_PER_YEAR

    rows, calls = await _backfill_a_metered_entry(hass, freezer, clear=False)

    assert all(kind == "import" for kind, _ in calls), calls
    imports = {meta["statistic_id"]: (meta, written) for _, (meta, written) in calls}
    # Vivaqua has no comfort rate, so four sensors and no more.
    assert {sid: meta["unit_of_measurement"] for sid, (meta, _) in imports.items()} == {
        "sensor.v_yearly_fee": EUR_PER_YEAR,
        "sensor.v_basis_rate": EUR_PER_M3,
        "sensor.v_sanering_rate": EUR_PER_M3,
        "sensor.v_all_in_basis": EUR_PER_M3,
    }
    # 15 March 00:00 in Brussels to the end of the last compiled hour,
    # which is behind both the clock and the card's end.
    first = datetime(2026, 3, 14, 23, tzinfo=UTC)
    end = datetime(2026, 10, 4, 19, tzinfo=UTC)
    hours = int((end - first) / timedelta(hours=1))
    for _meta, written in imports.values():
        assert written[0]["start"] == first
        assert written[-1]["start"] + timedelta(hours=1) == end
        assert len(written) == hours
    assert rows == 4 * hours


async def test_clear_wipes_each_written_sensor_before_its_rows_go_in(
    hass: HomeAssistant, freezer: Any
) -> None:
    """clear=True must clear what it rewrites, and nothing it does not.

    The recorder only deletes a statistic whole, so a clear that reached
    the year-to-date sensors would erase the meter's entire history.
    """
    _rows, calls = await _backfill_a_metered_entry(hass, freezer, clear=True)

    order = [(kind, arg if kind == "clear" else [arg[0]["statistic_id"]]) for kind, arg in calls]
    assert order == [
        step
        for sid in (
            "sensor.v_yearly_fee",
            "sensor.v_basis_rate",
            "sensor.v_sanering_rate",
            "sensor.v_all_in_basis",
        )
        for step in (("clear", [sid]), ("import", [sid]))
    ]
