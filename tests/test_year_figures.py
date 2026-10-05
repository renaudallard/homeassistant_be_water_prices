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

"""The rolling year and the calendar year's projection.

The rolling year is the meter's 365 closed days before today, priced as a
year on today's card. The projection is the year so far plus last year's
same remaining days, and its cost the running bill plus what that rest adds
to it.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date, timedelta
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.be_water_prices.const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_PERSONS,
    CONF_SOCIAL_TARIFF,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DOMAIN,
)
from custom_components.be_water_prices.coordinator import (
    RecorderUnavailable,
    YearFigures,
    _metered_m3,
    _recorder_ytd_m3,
    _rest_of_year_m3,
    _rolling_year_m3,
)
from custom_components.be_water_prices.pricing import compute_annual_cost, compute_ytd_cost
from custom_components.be_water_prices.providers._flanders import build_flanders_tariff
from custom_components.be_water_prices.providers.base import WaterExtractor, WaterTariff

_ROWS = "custom_components.be_water_prices.coordinator._recorder_daily_rows"
_FULL_YEAR = "custom_components.be_water_prices.coordinator._recorder_full_year_m3"
_YTD = "custom_components.be_water_prices.coordinator._recorder_ytd_m3"

# Noon in Brussels. 258 days of 2026 have elapsed, today included, and 107
# are left after it.
_NOW = "2026-09-15 10:00:00+00:00"
_TODAY = date(2026, 9, 15)


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


def _span(first: date, last: date, m3: float = 0.25) -> dict[date, float]:
    days = {}
    day = first
    while day <= last:
        days[day] = m3
        day += timedelta(days=1)
    return days


# --- the windows ------------------------------------------------------------


def test_a_window_the_meter_covered_is_summed() -> None:
    days = _span(date(2025, 1, 1), date(2025, 12, 31))
    assert _metered_m3(days, date(2025, 3, 1), date(2025, 3, 31)) == pytest.approx(7.75)


def test_a_window_the_meter_was_not_running_before_is_not_read() -> None:
    """A meter installed on the window's first day may have missed its start."""
    days = _span(date(2025, 3, 1), date(2025, 12, 31))
    assert _metered_m3(days, date(2025, 3, 1), date(2025, 3, 31)) is None


def test_a_window_the_meter_stopped_in_is_not_read() -> None:
    """Stopped on 15 November: more than two days in three, but not to the end."""
    days = _span(date(2024, 12, 1), date(2025, 11, 15))
    assert _metered_m3(days, date(2025, 1, 1), date(2025, 12, 31)) is None


def test_a_window_with_a_bucket_on_two_days_in_three_is_read() -> None:
    days = _span(date(2024, 12, 1), date(2025, 12, 31))
    for day in _span(date(2025, 7, 1), date(2025, 9, 30)):
        del days[day]
    assert _metered_m3(days, date(2025, 1, 1), date(2025, 12, 31)) == pytest.approx(
        (365 - 92) * 0.25
    )


def test_a_window_the_meter_was_away_for_most_of_is_not_read() -> None:
    days = _span(date(2024, 12, 1), date(2025, 12, 31))
    for day in _span(date(2025, 2, 1), date(2025, 11, 30)):
        del days[day]
    assert _metered_m3(days, date(2025, 1, 1), date(2025, 12, 31)) is None


def test_an_empty_window_holds_no_water() -> None:
    assert _metered_m3({}, date(2026, 1, 1), date(2025, 12, 31)) == 0.0


def test_the_rolling_year_is_the_365_days_before_today() -> None:
    days = _span(_TODAY - timedelta(days=400), _TODAY - timedelta(days=1))
    # Neither today nor the 366th day back belongs to it.
    days[_TODAY] = 50.0
    days[_TODAY - timedelta(days=366)] = 50.0
    assert _rolling_year_m3(days, _TODAY) == pytest.approx(365 * 0.25)


def test_the_rest_of_the_year_is_last_years_same_days() -> None:
    days = _span(date(2025, 1, 1), date(2026, 9, 14))
    days[date(2025, 9, 15)] = 50.0  # today's twin is not part of the rest
    assert _rest_of_year_m3(days, _TODAY) == pytest.approx(107 * 0.25)


def test_on_31_december_nothing_of_the_year_is_left() -> None:
    assert _rest_of_year_m3({}, date(2026, 12, 31)) == 0.0


def test_29_february_borrows_last_years_28th() -> None:
    """2028 is leap: 29 February to 31 December is 307 days, as is 28
    February to 31 December 2027."""
    days = _span(date(2027, 1, 1), date(2028, 2, 27))
    assert _rest_of_year_m3(days, date(2028, 2, 28)) == pytest.approx(307 * 0.25)


def test_last_years_29_february_has_no_twin() -> None:
    """2025 is not leap: 28 February to 31 December is 307 days, and 2024's
    same dates are 308 with its 29 February."""
    days = _span(date(2024, 1, 1), date(2025, 2, 26))
    assert _rest_of_year_m3(days, date(2025, 2, 27)) == pytest.approx(307 * 0.25)


def test_a_rest_last_year_does_not_cover_is_not_projected() -> None:
    days = _span(date(2025, 11, 1), date(2026, 9, 14))
    assert _rest_of_year_m3(days, _TODAY) is None


# --- the sensors ------------------------------------------------------------


def _buckets(days: dict[date, float]) -> list[dict[str, Any]]:
    return [
        {"start": dt_util.start_of_local_day(day).timestamp(), "change": m3}
        for day, m3 in sorted(days.items())
    ]


def _a_year_of_water() -> list[dict[str, Any]]:
    return _buckets(_span(_TODAY - timedelta(days=396), _TODAY - timedelta(days=1)))


async def _setup_entry(
    hass: HomeAssistant,
    rows: Any,
    ytd: AsyncMock | None = None,
    full_year: AsyncMock | None = None,
    card: WaterTariff | None = None,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Set an entry up on a meter at 100 m³ whose year so far is 20 m³,
    or whatever ``ytd`` has the recorder say, on ``card`` or the Brussels
    one, with ``options`` laid over the defaults."""
    card = card or _tariff()
    await hass.config.async_set_time_zone("Europe/Brussels")
    hass.states.async_set("sensor.water_meter", "100")
    entry = MockConfigEntry(
        domain=DOMAIN,
        title=card.utility.upper(),
        data={CONF_UTILITY: card.utility},
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 80,
            CONF_WATER_METER_SENSOR: "sensor.water_meter",
            **(options or {}),
        },
        unique_id=f"{DOMAIN}_{card.utility}",
    )
    entry.add_to_hass(hass)
    with _card_and_recorder(rows, ytd, full_year, card):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()
    return entry


@contextmanager
def _card_and_recorder(
    rows: Any,
    ytd: AsyncMock | None = None,
    full_year: AsyncMock | None = None,
    card: WaterTariff | None = None,
) -> Iterator[None]:
    """The card a setup fetches and the recorder it reads."""
    card = card or _tariff()

    async def _fetch(_session: Any) -> WaterTariff:
        return card

    fake = WaterExtractor(
        id=card.utility, label=card.utility.upper(), region=card.region, fetch=_fetch
    )
    with (
        patch("custom_components.be_water_prices.coordinator.get", return_value=fake),
        patch(_YTD, new=ytd or AsyncMock(return_value=(20.0, 0.0))),
        patch(_FULL_YEAR, new=full_year or AsyncMock(return_value=None)),
        patch(_ROWS, new=rows),
    ):
        yield


def _state(hass: HomeAssistant, key: str, utility: str = "vivaqua") -> float:
    state = hass.states.get(f"sensor.{utility}_{key}")
    assert state is not None
    return float(state.state)


@pytest.mark.asyncio
async def test_the_four_sensors_publish_the_meters_year(hass: HomeAssistant, freezer: Any) -> None:
    freezer.move_to(_NOW)
    await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    tariff = _tariff()
    assert _state(hass, "rolling_year_consumption") == pytest.approx(91.25)
    assert _state(hass, "rolling_year_cost") == compute_annual_cost(tariff, 91.25, 1)
    assert _state(hass, "projected_year_consumption") == pytest.approx(20.0 + 26.75)
    assert _state(hass, "projected_year_end_cost") == compute_annual_cost(tariff, 46.75, 1)
    assert _state(hass, "projected_year_end_cost") == 139.34


@pytest.mark.asyncio
async def test_a_flemish_household_is_billed_on_its_own_options(
    hass: HomeAssistant, freezer: Any
) -> None:
    """Brussels prices neither persons nor the social tariff, and 80 m³ is
    the default consumption, so only a Flemish card with all three moved
    shows that the household's options reach the bill.

    The figures are worked out by hand from the VMM rules. Three residents
    give a basis volume of 30 + 3 x 30 = 120 m³, billed at 2.00 + 0.60 +
    1.00 = 3.60 EUR/m³, and the comfort volume above it pays double, 7.20
    EUR/m³. The vastrecht of 100 EUR less 20 EUR per resident is 40 EUR,
    of which the year so far carries 258/365. VAT is 6 % and the social
    tariff takes 80 % off the total.
    """
    freezer.move_to(_NOW)
    card = build_flanders_tariff(
        utility_id="pidpa",
        year=2026,
        publication_label="Pidpa test",
        source_url="https://example.invalid/",
        basis=2.0,
        comfort=4.0,
        sanering_gemeentelijk=0.6,
        sanering_bovengemeentelijk=1.0,
    )
    await _setup_entry(
        hass,
        AsyncMock(return_value=_a_year_of_water()),
        card=card,
        options={
            CONF_CONSUMPTION_M3_PER_YEAR: 150,
            CONF_PERSONS: 3,
            CONF_SOCIAL_TARIFF: True,
        },
    )
    # (120 x 3.60 + 30 x 7.20 + 40) x 1.06 x 0.20 = 145.856
    assert _state(hass, "projected_annual_cost", "pidpa") == 145.86
    # (20 x 3.60 + 40 x 258 / 365) x 1.06 x 0.20 = 21.258
    assert _state(hass, "current_year_cost", "pidpa") == 21.26
    # (91.25 x 3.60 + 40) x 1.06 x 0.20 = 78.122
    assert _state(hass, "rolling_year_cost", "pidpa") == 78.12
    # (46.75 x 3.60 + 40) x 1.06 x 0.20 = 44.1596
    assert _state(hass, "projected_year_end_cost", "pidpa") == 44.16


@pytest.mark.asyncio
async def test_a_meter_without_a_years_history_projects_nothing(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    since_june = _buckets(_span(date(2026, 6, 1), _TODAY - timedelta(days=1)))
    await _setup_entry(hass, AsyncMock(return_value=since_june))
    for key in (
        "rolling_year_consumption",
        "rolling_year_cost",
        "projected_year_consumption",
        "projected_year_end_cost",
    ):
        state = hass.states.get(f"sensor.vivaqua_{key}")
        assert state is not None
        assert state.state == "unknown"


@pytest.mark.asyncio
async def test_a_draw_moves_the_projection_with_the_year(hass: HomeAssistant, freezer: Any) -> None:
    freezer.move_to(_NOW)
    await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    hass.states.async_set("sensor.water_meter", "101")
    await hass.async_block_till_done()
    assert _state(hass, "year_to_date_consumption") == pytest.approx(21.0)
    assert _state(hass, "projected_year_consumption") == pytest.approx(21.0 + 26.75)
    assert _state(hass, "rolling_year_consumption") == pytest.approx(91.25)


@pytest.mark.asyncio
async def test_the_meter_is_read_once_a_day(hass: HomeAssistant, freezer: Any) -> None:
    freezer.move_to(_NOW)
    rows = AsyncMock(return_value=_a_year_of_water())
    entry = await _setup_entry(hass, rows)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    with patch(_YTD, new=AsyncMock(return_value=(20.0, 0.0))), patch(_ROWS, new=rows):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert rows.await_count == 1
        freezer.tick(timedelta(days=1))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert rows.await_count == 2


@pytest.mark.asyncio
async def test_an_unreadable_recorder_keeps_the_last_read(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    freezer.tick(timedelta(days=1))
    with (
        patch(_YTD, new=AsyncMock(return_value=(20.0, 0.0))),
        patch(_ROWS, new=AsyncMock(side_effect=RecorderUnavailable("locked"))),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.data.year_figures.rolling_m3 == pytest.approx(91.25)
    # The rest is last year's and is still there, one day shorter.
    assert coordinator.data.year_figures.projected_m3 == pytest.approx(20.0 + 26.5)


@pytest.mark.asyncio
async def test_a_read_kept_too_long_is_dropped(hass: HomeAssistant, freezer: Any) -> None:
    """A recorder that stays broken must not publish an old year as today's."""
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    broken = AsyncMock(side_effect=RecorderUnavailable("locked"))
    with patch(_YTD, new=AsyncMock(return_value=(20.0, 0.0))), patch(_ROWS, new=broken):
        freezer.tick(timedelta(days=7))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
        assert coordinator.data.year_figures.rolling_m3 == pytest.approx(91.25)
        freezer.tick(timedelta(days=1))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.data.year_figures.rolling_m3 is None


@pytest.mark.asyncio
async def test_the_volume_is_projected_without_a_running_bill(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    figures = coordinator._year_figures(_tariff(), 20.0, None)
    assert figures.projected_m3 == pytest.approx(46.75)
    assert figures.projected_end_cost_eur is None


@pytest.mark.asyncio
async def test_a_draw_during_the_read_is_not_overwritten(hass: HomeAssistant, freezer: Any) -> None:
    """The read awaits, so it has to come before the fold: after it, a
    meter event handled meanwhile is overwritten by the tick's stale locals."""
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    # Setup framed the meter on the recorder's answer and asks it once more
    # on the next tick; that tick comes first, so the one below is the
    # meter's alone.
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    async def _draw_while_reading(*_args: Any) -> list[dict[str, Any]]:
        hass.states.async_set("sensor.water_meter", "130")
        await asyncio.sleep(0)
        return _a_year_of_water()

    freezer.tick(timedelta(days=1))
    with (
        patch(_YTD, new=AsyncMock(return_value=(20.0, 0.0))),
        patch(_ROWS, new=_draw_while_reading),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.data.ytd_consumption_m3 == 50.0


@pytest.mark.asyncio
async def test_a_running_bill_held_by_its_floor_carries_into_the_year_end(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    tariff = _tariff()
    on_today = compute_ytd_cost(tariff, 20.0, 1, 258 / 365)
    assert on_today is not None
    figures = coordinator._year_figures(tariff, 20.0, on_today + 5.0)
    assert figures.projected_end_cost_eur == pytest.approx(
        compute_annual_cost(tariff, 46.75, 1) + 5.0
    )


@pytest.mark.asyncio
async def test_next_years_card_in_december_does_not_price_the_year_figures(
    hass: HomeAssistant, freezer: Any
) -> None:
    """The year end is this year's bill, and the card in force prices it."""
    freezer.move_to("2026-12-15 10:00:00+00:00")
    rows = AsyncMock(return_value=_buckets(_span(date(2025, 11, 1), date(2026, 12, 14))))
    entry = await _setup_entry(hass, rows)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    held = coordinator.data.year_figures
    assert held.rolling_cost_eur is not None
    assert held.projected_end_cost_eur is not None
    early = replace(
        _tariff(),
        valid_from=date(2027, 1, 1),
        valid_until=date(2027, 12, 31),
        yearly_fixed_fee=50.0,
        linear_eur_per_m3=3.0,
    )
    ytd_cost = coordinator.data.current_year_cost_eur
    assert coordinator._year_figures(early, 20.0, ytd_cost) == held


@pytest.mark.asyncio
async def test_a_tick_that_returns_after_midnight_projects_the_year_it_folded(
    hass: HomeAssistant, freezer: Any
) -> None:
    """A tick folding 31 December whose recorder query ends in January.

    Read off the clock, the projection added last year's whole rest to the
    year just closed and published 180.75 m3 for a 90 m3 year.
    """
    freezer.move_to("2026-12-31 22:59:50+00:00")  # 23:59:50 in Brussels
    rows = AsyncMock(return_value=_buckets(_span(date(2025, 11, 1), date(2026, 12, 30))))

    async def _past_midnight(*_args: Any) -> tuple[float, float]:
        freezer.tick(timedelta(seconds=20))
        return 90.0, 0.0

    entry = await _setup_entry(hass, rows, AsyncMock(side_effect=_past_midnight))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert dt_util.now().year == 2027
    assert coordinator.data.ytd_consumption_m3 == 90.0
    assert coordinator.data.year_figures.projected_m3 == 90.0
    assert coordinator.data.year_figures.projected_end_cost_eur == compute_annual_cost(
        _tariff(), 90.0, 1
    )


@pytest.mark.asyncio
async def test_a_refused_day_is_not_warned_about_on_every_read(
    hass: HomeAssistant, freezer: Any, caplog: Any
) -> None:
    """The year figures read the same thirteen months daily, so one bad
    bucket in them warned once a day until it left the window. The
    year-to-date reader still warns about it."""
    freezer.move_to(_NOW)
    days = _span(_TODAY - timedelta(days=396), _TODAY - timedelta(days=1))
    days[date(2026, 2, 10)] = 500.0
    rows = AsyncMock(return_value=_buckets(days))
    entry = await _setup_entry(hass, rows)
    coordinator = hass.data[DOMAIN][entry.entry_id]
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    with patch(_YTD, new=AsyncMock(return_value=(20.0, 0.0))), patch(_ROWS, new=rows):
        freezer.tick(timedelta(days=1))
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    refused = [r for r in caplog.records if "ignoring a single change of 500.0" in r.getMessage()]
    assert rows.await_count == 2
    assert refused
    assert all(r.levelno == logging.DEBUG for r in refused)
    caplog.clear()
    with patch(_ROWS, new=rows):
        await _recorder_ytd_m3(hass, "sensor.water_meter", date(2026, 1, 1), _TODAY)
    refused = [r for r in caplog.records if "ignoring a single change of 500.0" in r.getMessage()]
    assert [r.levelno for r in refused] == [logging.WARNING]


@pytest.mark.asyncio
async def test_a_read_of_another_meter_is_not_kept(hass: HomeAssistant, freezer: Any) -> None:
    """The Energy dashboard can point at another meter with no reload. The
    old meter's year is not that meter's, even when its own read fails."""
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data.year_figures.rolling_m3 == pytest.approx(91.25)
    hass.states.async_set("sensor.other_meter", "40")
    with (
        patch.object(
            coordinator, "async_resolve_meter_entity", AsyncMock(return_value="sensor.other_meter")
        ),
        patch(_YTD, new=AsyncMock(return_value=(20.0, 0.0))),
        patch(_ROWS, new=AsyncMock(side_effect=RecorderUnavailable("locked"))),
    ):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.data.year_figures == YearFigures()


@pytest.mark.asyncio
async def test_a_meter_that_goes_away_takes_its_year_along(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert coordinator.data.year_figures.rolling_m3 == pytest.approx(91.25)
    with patch.object(coordinator, "async_resolve_meter_entity", AsyncMock(return_value=None)):
        await coordinator.async_refresh()
        await hass.async_block_till_done()
    assert coordinator.data.year_figures == YearFigures()


@pytest.mark.asyncio
async def test_setup_does_not_wait_on_the_meters_year(hass: HomeAssistant, freezer: Any) -> None:
    """Home Assistant waits on setup inside a startup stage every
    integration shares, and each of the two reads covers a year of the
    meter's hourly statistics. They run once setup has returned."""
    freezer.move_to(_NOW)
    release = asyncio.Event()

    async def _full_year(*_args: Any) -> None:
        await release.wait()

    full_year = AsyncMock(side_effect=_full_year)
    rows = AsyncMock(return_value=_a_year_of_water())
    entry = await _setup_entry(hass, rows, full_year=full_year)
    assert entry.state is ConfigEntryState.LOADED
    assert full_year.await_count == 1
    assert rows.await_count == 0
    state = hass.states.get("sensor.vivaqua_rolling_year_consumption")
    assert state is not None
    assert state.state == "unknown"
    with patch(_ROWS, new=rows):
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)
    assert rows.await_count == 1
    assert _state(hass, "rolling_year_consumption") == pytest.approx(91.25)
    assert _state(hass, "projected_year_consumption") == pytest.approx(20.0 + 26.75)


# --- the read kept across a restart -------------------------------------------


async def _restart(hass: HomeAssistant, entry: MockConfigEntry, rows: Any) -> None:
    """Reload the entry, as a restart sets it up, on a recorder answering ``rows``."""
    with _card_and_recorder(rows):
        assert await hass.config_entries.async_reload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_a_restart_on_the_day_of_the_read_reads_nothing(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    rows = AsyncMock(return_value=[])
    await _restart(hass, entry, rows)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert rows.await_count == 0
    assert _state(hass, "rolling_year_consumption") == pytest.approx(91.25)


@pytest.mark.asyncio
async def test_a_restart_publishes_the_last_read_until_the_next_one_lands(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    freezer.tick(timedelta(days=1))
    release = asyncio.Event()
    wetter = _buckets(_span(_TODAY - timedelta(days=395), _TODAY, 0.5))

    async def _rows(*_args: Any) -> list[dict[str, Any]]:
        await release.wait()
        return wetter

    rows = AsyncMock(side_effect=_rows)
    await _restart(hass, entry, rows)
    assert rows.await_count == 1
    assert _state(hass, "rolling_year_consumption") == pytest.approx(91.25)
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)
    assert _state(hass, "rolling_year_consumption") == pytest.approx(182.5)


@pytest.mark.asyncio
async def test_a_read_older_than_the_hold_is_not_restored(
    hass: HomeAssistant, freezer: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    freezer.tick(timedelta(days=8))
    release = asyncio.Event()

    async def _rows(*_args: Any) -> list[dict[str, Any]]:
        await release.wait()
        return []

    await _restart(hass, entry, AsyncMock(side_effect=_rows))
    state = hass.states.get("sensor.vivaqua_rolling_year_consumption")
    assert state is not None
    assert state.state == "unknown"
    release.set()
    await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.parametrize("day, m3", [("yesterday", 1.0), ("2026-09-14", "a lot")])
@pytest.mark.asyncio
async def test_a_record_that_is_not_a_read_is_read_again(
    hass: HomeAssistant, freezer: Any, hass_storage: dict[str, Any], day: str, m3: Any
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    key = f"{DOMAIN}.{entry.entry_id}.metered"
    assert hass_storage[key]["data"]["read_on"] == _TODAY.isoformat()
    hass_storage[key]["data"]["days"][day] = m3
    rows = AsyncMock(return_value=_a_year_of_water())
    await _restart(hass, entry, rows)
    await hass.async_block_till_done(wait_background_tasks=True)
    assert rows.await_count == 1
    assert _state(hass, "rolling_year_consumption") == pytest.approx(91.25)


@pytest.mark.asyncio
async def test_removing_the_entry_removes_its_read(
    hass: HomeAssistant, freezer: Any, hass_storage: dict[str, Any]
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    key = f"{DOMAIN}.{entry.entry_id}.metered"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


@pytest.mark.asyncio
async def test_a_read_landing_after_removal_does_not_bring_the_file_back(
    hass: HomeAssistant, freezer: Any, hass_storage: dict[str, Any]
) -> None:
    freezer.move_to(_NOW)
    entry = await _setup_entry(hass, AsyncMock(return_value=_a_year_of_water()))
    coordinator = hass.data[DOMAIN][entry.entry_id]
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    freezer.tick(timedelta(days=1))
    with patch(_ROWS, new=AsyncMock(return_value=_a_year_of_water())):
        await coordinator._read_metered_days("sensor.water_meter")
    await hass.async_block_till_done()
    assert f"{DOMAIN}.{entry.entry_id}.metered" not in hass_storage
