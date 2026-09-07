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

"""The prompt that offers a metered year as the projection's input.

Two halves, tested apart: whether a closed year in the recorder counts
as a *whole* year (:func:`_recorder_full_year_m3`), and what the
coordinator does with the answer (:meth:`_sync_projection_issue` plus
the Repair flow that writes the option).
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from custom_components.be_water_prices.coordinator import (
    RecorderUnavailable,
    _recorder_full_year_m3,
)
from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff
from custom_components.be_water_prices.repairs import (
    ProjectionOutdatedRepairFlow,
    SnapshotStaleRepairFlow,
    async_create_fix_flow,
)

_ROWS = "custom_components.be_water_prices.coordinator._recorder_daily_rows"
_FULL_YEAR = "custom_components.be_water_prices.coordinator._recorder_full_year_m3"
_YTD = "custom_components.be_water_prices.coordinator._recorder_ytd_m3"


def _tariff() -> WaterTariff:
    return WaterTariff(
        utility="vivaqua",
        region="brussels",
        valid_from=date(2026, 1, 1),
        valid_until=date(2030, 12, 31),
        publication_label="VIVAQUA test",
        source_url="https://example.invalid/",
        yearly_fixed_fee=40.23 / 1.06,
        linear_eur_per_m3=2.0,
        vat_rate=0.06,
    )


def _buckets(spec: list[tuple[date, float | None]]) -> list[dict[str, Any]]:
    """Daily statistics rows as the recorder hands them over."""
    return [
        {"start": dt_util.start_of_local_day(day).timestamp(), "change": change}
        for day, change in spec
    ]


async def _fetch_tariff(_session: Any) -> WaterTariff:
    return _tariff()


async def _setup_entry(
    hass: HomeAssistant, *, configured: int = 80, full_year: Any = None
) -> MockConfigEntry:
    """Set an entry up with the projection check patched across setup.

    The check runs on the first refresh, which setup performs, and its
    answer is memoised per (meter, year, configured figure). So the query a
    test wants to observe happens HERE, not on a later refresh: patching it
    only afterwards leaves the memo already written and the mock untouched.
    """
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: configured,
            CONF_WATER_METER_SENSOR: "sensor.water_meter",
        },
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch_tariff)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
        patch(_FULL_YEAR, new=full_year if full_year is not None else AsyncMock(return_value=None)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


# --- is the closed year a whole year? ---------------------------------------


@pytest.mark.asyncio
async def test_full_year_sums_only_the_year_itself(hass: HomeAssistant) -> None:
    """History either side proves the year; December of the year before is not in it."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets(
        [
            (date(2024, 12, 15), 3.0),  # proves the meter predates the year
            *_quiet_year(2025, date(2025, 1, 5), date(2025, 6, 1), date(2025, 12, 20)),
            (date(2025, 1, 5), 10.0),
            (date(2025, 6, 1), 20.0),
            (date(2025, 12, 20), 5.0),  # proves it ran to the end
            # Asking for a day period makes HA re-align the end of the
            # window to the next local midnight, so the real query hands
            # back the first day of the following year as well.
            (date(2026, 1, 1), 7.0),
        ]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) == 35.0


@pytest.mark.asyncio
async def test_a_swap_after_the_year_does_not_refuse_it(hass: HomeAssistant) -> None:
    """The negative delta is in the next year's bucket, so it is not this year's."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets(
        [
            (date(2024, 12, 15), 3.0),
            *_quiet_year(2025, date(2025, 6, 1), date(2025, 12, 20)),
            (date(2025, 6, 1), 30.0),
            (date(2025, 12, 20), 5.0),
            (date(2026, 1, 1), -900.0),  # meter replaced on New Year's day
        ]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) == 35.0


def _quiet_year(year: int, *busy: date) -> list[tuple[date, float]]:
    """The change-0 bucket Home Assistant compiles for every idle day, ``busy`` days left out."""
    return _daily(date(year, 1, 1), date(year, 12, 31), 0.0, *((day, day) for day in busy))


def _daily(
    first: date, last: date, m3: float, *skip: tuple[date, date]
) -> list[tuple[date, float]]:
    """One bucket a day from ``first`` to ``last``, none inside the ``skip`` spans."""
    days = []
    day = first
    while day <= last:
        if not any(start <= day <= end for start, end in skip):
            days.append((day, m3))
        day += timedelta(days=1)
    return days


@pytest.mark.asyncio
async def test_history_on_both_sides_of_a_hole_is_not_a_full_year(hass: HomeAssistant) -> None:
    """Unavailable from January to November, back for December: two sides, no year."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets(
        [(date(2024, 12, 15), 3.0), *_daily(date(2025, 12, 1), date(2025, 12, 31), 0.1)]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) is None


@pytest.mark.asyncio
async def test_a_summer_away_still_makes_a_full_year(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    away = (date(2025, 7, 1), date(2025, 8, 31))
    rows = _buckets(
        [(date(2024, 12, 15), 3.0), *_daily(date(2025, 1, 1), date(2025, 12, 31), 0.1, away)]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        metered = await _recorder_full_year_m3(hass, "sensor.water_meter", 2025)
    assert metered is not None and round(metered, 1) == 30.3


@pytest.mark.asyncio
async def test_a_meter_off_for_half_the_year_is_not_a_full_year(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    off = (date(2025, 3, 1), date(2025, 9, 30))
    rows = _buckets(
        [(date(2024, 12, 15), 3.0), *_daily(date(2025, 1, 1), date(2025, 12, 31), 0.1, off)]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) is None


@pytest.mark.asyncio
async def test_meter_installed_mid_year_is_not_a_full_year(hass: HomeAssistant) -> None:
    """A June-to-December figure is indistinguishable from a frugal year."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets([(date(2025, 6, 1), 20.0), (date(2025, 12, 20), 5.0)])
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) is None


@pytest.mark.asyncio
async def test_meter_that_stopped_before_december_is_not_a_full_year(
    hass: HomeAssistant,
) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets([(date(2024, 12, 15), 3.0), (date(2025, 6, 1), 20.0)])
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) is None


@pytest.mark.asyncio
async def test_meter_swapped_mid_year_is_refused(hass: HomeAssistant) -> None:
    """A register that went backwards makes the deltas around it meaningless."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets(
        [
            (date(2024, 12, 15), 3.0),
            (date(2025, 3, 1), -40.0),
            (date(2025, 12, 20), 5.0),
        ]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) is None


@pytest.mark.asyncio
async def test_no_statistics_at_all_is_not_a_full_year(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    with patch(_ROWS, new=AsyncMock(return_value=[])):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) is None


@pytest.mark.asyncio
async def test_quiet_days_without_a_bucket_still_count(hass: HomeAssistant) -> None:
    """A household away over New Year has no January 1 bucket and a full year."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets(
        [
            (date(2024, 12, 3), 1.0),
            *_daily(date(2025, 2, 15), date(2025, 12, 30), 0.0),
            (date(2025, 2, 14), 40.0),
            (date(2025, 12, 31), 2.0),
        ]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) == 42.0


@pytest.mark.asyncio
async def test_rows_without_a_change_value_are_skipped(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")
    rows = _buckets(
        [
            (date(2024, 12, 15), 3.0),
            *_quiet_year(2025, date(2025, 4, 1), date(2025, 4, 2), date(2025, 12, 9)),
            (date(2025, 4, 1), None),
            (date(2025, 4, 2), 12.0),
            (date(2025, 12, 9), 1.0),
        ]
    )
    with patch(_ROWS, new=AsyncMock(return_value=rows)):
        assert await _recorder_full_year_m3(hass, "sensor.water_meter", 2025) == 13.0


# --- what the coordinator does with the answer -------------------------------


@pytest.mark.asyncio
async def test_measured_year_far_from_the_configured_one_raises_the_prompt(
    hass: HomeAssistant,
) -> None:
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=131.4))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id)
    assert issue is not None
    assert issue.is_fixable
    assert issue.translation_placeholders["metered"] == "131"
    assert issue.translation_placeholders["configured"] == "80"
    assert issue.translation_placeholders["utility"] == "VIVAQUA"
    assert issue.data["consumption_m3"] == 131


@pytest.mark.asyncio
async def test_the_year_asked_about_is_the_one_that_just_closed(hass: HomeAssistant) -> None:
    """Nothing else in the file pins the year, so an off-by-one would slip through."""
    full_year = AsyncMock(return_value=131.0)
    await _setup_entry(hass, configured=80, full_year=full_year)
    assert full_year.await_args is not None
    assert full_year.await_args.args[1] == "sensor.water_meter"
    assert full_year.await_args.args[2] == dt_util.now().year - 1


@pytest.mark.asyncio
async def test_a_different_meter_is_re_checked_without_an_options_change(
    hass: HomeAssistant,
) -> None:
    """Energy-dashboard auto-discovery repoints the meter with no reload."""
    full_year = AsyncMock(return_value=131.0)
    entry = await _setup_entry(hass, configured=80, full_year=full_year)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert full_year.await_count == 1
    with patch(_YTD, new=AsyncMock(return_value=20.0)), patch(_FULL_YEAR, new=full_year):
        # Same year, same configured figure, different meter: that is a
        # different year's worth of water, not a settled answer.
        hass.states.async_set("sensor.other_meter", "500")
        with patch.object(
            type(coordinator),
            "async_resolve_meter_entity",
            new=AsyncMock(return_value="sensor.other_meter"),
        ):
            await coordinator.async_refresh()
            await hass.async_block_till_done()
    assert full_year.await_count == 2
    assert full_year.await_args is not None
    assert full_year.await_args.args[1] == "sensor.other_meter"


@pytest.mark.asyncio
async def test_an_absurd_year_is_not_offered(hass: HomeAssistant) -> None:
    """The figure has to be writable through the options form's own bounds."""
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=9000.0))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id) is None


@pytest.mark.asyncio
async def test_a_year_with_no_consumption_is_not_offered(hass: HomeAssistant) -> None:
    """An empty year is below the form's minimum and says nothing about usage."""
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=0.0))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id) is None


@pytest.mark.asyncio
async def test_unloading_the_entry_clears_the_card(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=131.0))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    issue_id = coordinator.projection_issue_id
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is None


@pytest.mark.asyncio
async def test_measured_year_close_to_the_configured_one_stays_quiet(
    hass: HomeAssistant,
) -> None:
    """A household varies year to year without the projection being wrong."""
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=85.0))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id) is None


@pytest.mark.asyncio
async def test_partial_year_never_prompts(hass: HomeAssistant) -> None:
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=None))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id) is None


@pytest.mark.asyncio
async def test_answer_is_not_re_queried_while_nothing_changed(hass: HomeAssistant) -> None:
    """One thirteen-month query per (year, configured figure), not one per tick."""
    full_year = AsyncMock(return_value=131.0)
    entry = await _setup_entry(hass, configured=80, full_year=full_year)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    with patch(_YTD, new=AsyncMock(return_value=20.0)), patch(_FULL_YEAR, new=full_year):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert full_year.await_count == 1


@pytest.mark.asyncio
async def test_unreadable_recorder_is_retried_rather_than_believed(
    hass: HomeAssistant,
) -> None:
    """A database hiccup must not settle the question until the next restart."""
    full_year = AsyncMock(side_effect=[RecorderUnavailable("locked"), 131.0])
    entry = await _setup_entry(hass, configured=80, full_year=full_year)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id) is None
    with patch(_YTD, new=AsyncMock(return_value=20.0)), patch(_FULL_YEAR, new=full_year):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert full_year.await_count == 2
    assert ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id) is not None


@pytest.mark.asyncio
async def test_a_draw_during_the_query_is_not_overwritten(hass: HomeAssistant) -> None:
    """The query must not sit between the fold and what the tick publishes.

    It awaits, and an await there is the window a live meter event uses to
    publish a figure the stale locals would then overwrite with a lower one.
    """
    entry = await _setup_entry(hass, configured=80)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # Setup anchored the year on reading 100 against 20 m³ from the
    # recorder, so the meter contributes reading - 80 from here.
    assert coordinator.data.ytd_consumption_m3 == 20.0

    async def _draw_while_querying(*_args: Any) -> float | None:
        hass.states.async_set("sensor.water_meter", "130")
        await asyncio.sleep(0)
        return None

    # As if this were the first tick of a new day: the answer is memoised
    # per (meter, year, configured figure) and setup already spent it.
    coordinator._projection_checked = None
    with (
        patch(_YTD, new=AsyncMock(return_value=20.0)),
        patch(_FULL_YEAR, new=_draw_while_querying),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.data.ytd_consumption_m3 == 50.0


@pytest.mark.asyncio
async def test_no_meter_asks_nothing(hass: HomeAssistant) -> None:
    await hass.config.async_set_time_zone("Europe/Brussels")

    async def _fetch(_session: Any) -> WaterTariff:
        return _tariff()

    entry = MockConfigEntry(
        domain=DOMAIN,
        title="VIVAQUA",
        data={CONF_UTILITY: "vivaqua"},
        options={CONF_CONSUMPTION_M3_PER_YEAR: 80},
        unique_id=f"{DOMAIN}_vivaqua",
    )
    entry.add_to_hass(hass)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch)
    full_year = AsyncMock(return_value=131.0)
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(_FULL_YEAR, new=full_year),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    assert full_year.await_count == 0


# --- the Repair flow that writes the option ---------------------------------


@pytest.mark.asyncio
async def test_fix_flow_writes_the_measured_year_into_the_options(
    hass: HomeAssistant,
) -> None:
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=131.0))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    issue = ir.async_get(hass).async_get_issue(DOMAIN, coordinator.projection_issue_id)
    assert issue is not None
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch_tariff)
    with (
        # Submitting writes the options, which reloads the entry: `get` has
        # to stay patched across that or the reload fetches vivaqua.be for
        # real and the assertion below passes or fails on the network.
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
        patch(_FULL_YEAR, new=AsyncMock(return_value=131.0)),
    ):
        flow = await async_create_fix_flow(hass, coordinator.projection_issue_id, issue.data)
        flow.hass = hass
        # The RepairsFlowManager sets these before the first step in production.
        flow.handler = DOMAIN
        flow.issue_id = coordinator.projection_issue_id
        result = await flow.async_step_init()
        assert result["type"] == "form"
        assert result["description_placeholders"]["metered"] == "131"
        result = await flow.async_step_confirm({})
        assert result["type"] == "create_entry"
        await hass.async_block_till_done()

    assert entry.options[CONF_CONSUMPTION_M3_PER_YEAR] == 131


@pytest.mark.asyncio
async def test_opening_the_card_shows_a_form_before_doing_anything(
    hass: HomeAssistant,
) -> None:
    """Going through the real manager must not apply the fix on the way in.

    The Repairs manager starts a flow with the issue id as its init data,
    so the first step is called with a dict rather than None. A flow that
    reads that as a submission rewrites the entry the moment somebody
    opens the card.
    """
    from homeassistant.setup import async_setup_component

    assert await async_setup_component(hass, "repairs", {})
    entry = await _setup_entry(hass, configured=80, full_year=AsyncMock(return_value=131.0))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    before = dict(entry.options)
    fake = WaterExtractor(id="vivaqua", label="VIVAQUA", region="brussels", fetch=_fetch_tariff)

    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(_YTD, new=AsyncMock(return_value=20.0)),
        patch(_FULL_YEAR, new=AsyncMock(return_value=131.0)),
    ):
        result = await hass.data["repairs"]["flow_manager"].async_init(
            DOMAIN, data={"issue_id": coordinator.projection_issue_id}
        )

    assert result["type"] == "form"
    assert result["step_id"] == "confirm"
    assert dict(entry.options) == before


@pytest.mark.asyncio
async def test_fix_flow_aborts_when_the_entry_cannot_be_updated(hass: HomeAssistant) -> None:
    """Completing would delete the issue; aborting leaves the card in place."""
    await hass.config.async_set_time_zone("Europe/Brussels")
    flow = ProjectionOutdatedRepairFlow(entry_id="does-not-exist", consumption_m3=131)
    flow.hass = hass
    flow.handler = DOMAIN
    flow.issue_id = "projection_outdated_does-not-exist"
    result = await flow.async_step_confirm({})
    assert result["type"] == "abort"
    assert result["reason"] == "cannot_apply"


@pytest.mark.asyncio
async def test_each_issue_id_gets_its_own_flow(hass: HomeAssistant) -> None:
    projection = await async_create_fix_flow(
        hass, "projection_outdated_abc", {"entry_id": "abc", "consumption_m3": 131}
    )
    assert isinstance(projection, ProjectionOutdatedRepairFlow)
    stale = await async_create_fix_flow(hass, "snapshot_stale_abc", {"entry_id": "abc"})
    assert isinstance(stale, SnapshotStaleRepairFlow)
