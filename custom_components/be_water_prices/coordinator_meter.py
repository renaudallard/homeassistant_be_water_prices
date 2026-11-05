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

"""The meter's year: its closed days read once a day, the rolling year and
the calendar year's projection, and the Repairs cards the meter raises.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, State, callback
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

# The daily rows are read through their module: the readers there ask for
# them too, and all of them have to get the same function, as they did
# when they shared a module.
from . import meter_reads
from .const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_PERSONS,
    CONF_SOCIAL_TARIFF,
    CONF_WATER_METER_SENSOR,
    DEFAULT_CONSUMPTION_M3,
    DEFAULT_PERSONS,
    DOMAIN,
    MAX_CONSUMPTION_M3,
    MIN_CONSUMPTION_M3,
    PROJECTION_DRIFT_RATIO,
)
from .coordinator_store import _metered_from_record, _MeteredDays
from .coordinator_ytd import _priced_on
from .meter_reads import (
    RecorderUnavailable,
    _admitted_changes,
    _discover_energy_water_meter,
    _recorder_full_year_m3,
    _rest_of_year_m3,
    _rolling_year_m3,
)
from .pricing import compute_annual_cost
from .providers import WaterTariff
from .providers.base import WaterExtractor
from .ytd_fold import _YtdCycle

if TYPE_CHECKING:
    from .coordinator import CoordinatorData, WaterConfigEntry

_LOGGER = logging.getLogger(__name__)


# How far back the year figures read the meter: the rolling year's 365
# closed days and the month before them, which proves the meter was already
# running when the year opened. Last year's remaining days fall inside it.
_METERED_DAYS = 365 + 31


# How long a read of them stands in for one the recorder could not answer.
# Long enough to ride out a locked or restarting database, short enough
# that a recorder which stays broken does not publish an old year as today's.
_METERED_HOLD = timedelta(days=7)


@dataclass(frozen=True)
class YearFigures:
    """The rolling year and the calendar year's projection, read off the meter.

    Each is ``None`` until the meter's statistics cover the days it needs.
    """

    rolling_m3: float | None = None
    rolling_cost_eur: float | None = None
    projected_m3: float | None = None
    projected_end_cost_eur: float | None = None


class _MeterMixin:
    """Mixed into WaterCoordinator."""

    # State WaterCoordinator.__init__ creates, as bare annotations: a value
    # here would change what hasattr() and the instance dict see.
    _extractor: WaterExtractor
    _meter_entity_id: str | None
    _metered: _MeteredDays | None
    _metered_store: Store[dict[str, Any]]
    _projection_checked: tuple[str, int, float, bool] | None
    _ytd: _YtdCycle
    data: CoordinatorData
    entry: WaterConfigEntry
    hass: HomeAssistant
    meter_history_pending: bool

    if TYPE_CHECKING:
        # Defined by WaterCoordinator, its other mixin or DataUpdateCoordinator.
        def _card_in_force(self, tariff: WaterTariff, year: int) -> WaterTariff: ...
        def _owns_the_entry(self) -> bool: ...
        def _sees_a_register(self, meter: str, state: State | None) -> bool: ...
        def _ytd_cost_from_m3(
            self, tariff: WaterTariff, ytd_m3: float, year: int, day: date | None = None
        ) -> float | None: ...
        def async_update_listeners(self) -> None: ...

    @property
    def projection_issue_id(self) -> str:
        """Stable Repairs issue id for this entry's projection-drift prompt."""
        return f"projection_outdated_{self.entry.entry_id}"

    async def _sync_projection_issue(self, meter: str) -> None:
        """Offer a whole metered year as the projected-cost sensor's input.

        The projection runs off a figure typed once during setup and never
        revisited, while the meter has since measured a full year of the
        real thing. Overwriting the option outright would move a sensor
        with no visible cause, so the measured figure is offered through a
        Repair the user accepts or ignores.

        Reads a closed year straight from the recorder and asks the running
        cycle nothing, so it cannot disturb the year in progress.
        """
        year = dt_util.now().year - 1
        configured = float(
            self.entry.options.get(CONF_CONSUMPTION_M3_PER_YEAR, DEFAULT_CONSUMPTION_M3)
        )
        register = self._sees_a_register(meter, self.hass.states.get(meter))
        if self._projection_checked == (meter, year, configured, register):
            return
        try:
            metered = await _recorder_full_year_m3(self.hass, meter, year, register=register)
        except RecorderUnavailable as err:
            # Leave the pair unrecorded so the next tick asks again rather
            # than treating an unreadable database as a settled answer.
            _LOGGER.debug("could not read %d for %s: %s", year, meter, err)
            return
        self._projection_checked = (meter, year, configured, register)
        if not self._owns_the_entry():
            # That read yielded to the loop; the entry may be gone by now.
            return
        offer = round(metered) if metered is not None else None
        if (
            offer is None
            or not MIN_CONSUMPTION_M3 <= offer <= MAX_CONSUMPTION_M3
            or configured <= 0
            or abs(offer - configured) / configured < PROJECTION_DRIFT_RATIO
        ):
            ir.async_delete_issue(self.hass, DOMAIN, self.projection_issue_id)
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            self.projection_issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="projection_outdated",
            translation_placeholders={
                "utility": self._extractor.label,
                "year": str(year),
                "metered": f"{offer:d}",
                "configured": f"{configured:.0f}",
            },
            # The fix flow writes the option, so it needs both the entry to
            # write it to and the figure to write; the issue's placeholders
            # are display strings and are not read back as numbers.
            data={"entry_id": self.entry.entry_id, "consumption_m3": offer},
        )

    def _project_cost(self, tariff: WaterTariff) -> float | None:
        opts = self.entry.options
        consumption = float(opts.get(CONF_CONSUMPTION_M3_PER_YEAR, DEFAULT_CONSUMPTION_M3))
        return self._annual_cost(tariff, consumption)

    def _annual_cost(self, tariff: WaterTariff, m3: float) -> float | None:
        """A year's bill for ``m3`` on ``tariff``, for this household."""
        opts = self.entry.options
        persons = int(opts.get(CONF_PERSONS, DEFAULT_PERSONS))
        social = bool(opts.get(CONF_SOCIAL_TARIFF, False))
        return compute_annual_cost(tariff, m3, persons, social_tariff=social)

    async def _read_metered_days(self, meter: str) -> None:
        """Read the meter's closed days for :meth:`_year_figures`.

        Once a day: the days are closed, so a second read the same day
        finds the same water. A read that fails keeps what an earlier one
        found for this meter for up to :data:`_METERED_HOLD`, so a database
        hiccup leaves the figures a day behind rather than unknown until
        the next tick. A read made as a register's for a meter that has
        since said it resets is made again, and is not kept when that
        fails: a daily meter read that way gave a fifth of its water.
        """
        today = dt_util.now().date()
        register = self._sees_a_register(meter, self.hass.states.get(meter))
        held = self._metered
        if (
            held is not None
            and held.meter == meter
            and held.read_on == today
            and held.register == register
        ):
            return
        start = today - timedelta(days=_METERED_DAYS)
        end = today - timedelta(days=1)
        try:
            rows = await meter_reads._recorder_daily_rows(
                self.hass, meter, start, end, register=register
            )
        except RecorderUnavailable as err:
            _LOGGER.debug("could not read the last year of %s: %s", meter, err)
            if held is not None and (
                held.meter != meter
                or held.register != register
                or today - held.read_on > _METERED_HOLD
            ):
                self._metered = None
            return
        # Quietly: this reads the same thirteen months every day, so a bucket
        # refused here would be warned about daily until it left the window.
        # The year-to-date and full-year readers say so when they meet one.
        admitted, _refused, _taken_back = _admitted_changes(
            rows or [], meter, start, end, level=logging.DEBUG
        )
        days = {
            dt_util.as_local(datetime.fromtimestamp(bucket, UTC)).date(): m3
            for bucket, m3 in admitted
            if bucket is not None
        }
        self._metered = _MeteredDays(meter=meter, read_on=today, days=days, register=register)
        if self._owns_the_entry():
            # Only for the entry this coordinator still speaks for: a read
            # that lands after a removal would bring the file back.
            await self._metered_store.async_save(
                {
                    "meter": meter,
                    "read_on": today.isoformat(),
                    "register": register,
                    "days": {day.isoformat(): m3 for day, m3 in days.items()},
                }
            )

    async def _read_meter_history(self, meter: str) -> None:
        """The two reads of a year of the meter: the projection check and
        the days behind the year figures.

        Advisory, so a failure logs and is dropped rather than taking the
        tick down and blanking every sensor on the entry.
        """
        try:
            await self._sync_projection_issue(meter)
        except Exception:
            _LOGGER.exception("could not check the projection against a metered year")
        try:
            await self._read_metered_days(meter)
        except Exception:
            _LOGGER.exception("could not read the last year of the water meter")

    async def async_read_meter_history(self) -> None:
        """Make the reads setup's own refresh left out, and publish the
        year figures they give.

        Started by setup as a background task once its refresh is in, so
        Home Assistant does not wait on them. Nothing is folded: the year
        figures are built from the year-to-date already published, read
        afresh after the reads, so a meter event handled meanwhile is not
        overwritten.
        """
        if not self.meter_history_pending:
            return
        self.meter_history_pending = False
        meter = self._meter_entity_id
        if meter is None:
            return
        await self._read_meter_history(meter)
        if self.data is None or not self._owns_the_entry():
            return
        self.data = replace(
            self.data,
            year_figures=self._year_figures(
                self.data.tariff, self.data.ytd_consumption_m3, self.data.current_year_cost_eur
            ),
        )
        self.async_update_listeners()

    def _year_figures(
        self, tariff: WaterTariff, ytd_m3: float | None, ytd_cost: float | None
    ) -> YearFigures:
        """The rolling year and the year-end projection on the card in
        force, which is ``tariff`` unless that is dated ahead of the year.

        The rolling year is the 365 closed days before the last read,
        priced as a year. The projection is the year so far plus last
        year's same remaining days, and its cost is the running bill plus
        what that rest adds to it: a running bill its floor holds above
        today's card carries into the year end rather than being priced
        away.
        """
        metered = self._metered
        if metered is None or metered.meter != self._meter_entity_id:
            return YearFigures()
        rolling = _rolling_year_m3(metered.days, metered.read_on)
        # The year the published figure was folded for, not the one the
        # clock reads now: a tick that folds 31 December and returns after
        # midnight would otherwise add last year's whole rest to a year
        # that has closed.
        year = self._ytd.year
        # The card the running bill is on, or the projection would add the
        # rest of the year at rates the year to date was never billed at.
        card = tariff if year is None else self._card_in_force(tariff, year)
        projected = end_cost = None
        if year is not None and ytd_m3 is not None:
            rest = _rest_of_year_m3(metered.days, _priced_on(year))
            if rest is not None:
                projected = ytd_m3 + rest
                if ytd_cost is not None:
                    so_far = self._ytd_cost_from_m3(card, ytd_m3, year)
                    whole = self._annual_cost(card, projected)
                    if so_far is not None and whole is not None:
                        end_cost = round(ytd_cost + whole - so_far, 2)
        return YearFigures(
            rolling_m3=rolling,
            rolling_cost_eur=None if rolling is None else self._annual_cost(card, rolling),
            projected_m3=projected,
            projected_end_cost_eur=end_cost,
        )

    async def async_resolve_meter_entity(self) -> str | None:
        """Return the water-meter entity_id to query for YTD computations.

        Priority order:

          1. ``CONF_WATER_METER_SENSOR`` from the OptionsFlow -- explicit
             override that wins over auto-discovery.
          2. The first ``water`` source configured in HA's Energy
             dashboard. Most users wire their water meter there once
             already; auto-discovering it from that config means the
             YTD sensors light up without having to re-pick the same
             entity in our OptionsFlow.
          3. ``None`` -- YTD entities stay unknown.
        """
        explicit = self.entry.options.get(CONF_WATER_METER_SENSOR)
        if explicit:
            self._sync_several_meters_issue(0)
            return str(explicit)
        meter, count = await _discover_energy_water_meter(self.hass)
        self._sync_several_meters_issue(count)
        return meter

    @property
    def several_meters_issue_id(self) -> str:
        """Stable Repairs issue id for this entry's ambiguous-meter notice."""
        return f"several_water_meters_{self.entry.entry_id}"

    @callback
    def _sync_several_meters_issue(self, count: int) -> None:
        """Say so when the dashboard leaves the choice of meter to list order.

        There is no right answer to fall back on: summing would double
        count a sub-meter and over-bill a rainwater or well meter. So the
        one meter is still billed and the household is asked which, rather
        than the choice being made silently on the order the sources
        happen to be stored in. A log line was the only signal, and a bill
        computed from a hot-water sub-meter is not a log-level problem.
        """
        if not self._owns_the_entry():
            # The card is keyed on the entry, so after a reload a retired
            # coordinator would take down the one its successor raised.
            return
        if count > 1:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.several_meters_issue_id,
                is_fixable=False,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="several_water_meters",
                translation_placeholders={
                    "utility": self._extractor.label,
                    "count": str(count),
                },
            )
            return
        ir.async_delete_issue(self.hass, DOMAIN, self.several_meters_issue_id)

    async def async_load_metered_days(self) -> None:
        """Restore the last read of the meter's days before the first refresh.

        That refresh leaves the read out, so without this the year figures
        read ``unknown`` after every restart until the read setup starts
        lands; with it they stand where they stood, and a restart on the
        day of the read makes none at all. A record older than
        :data:`_METERED_HOLD` is not adopted, for the reason a failed read
        stops standing in after it.
        """
        try:
            data = await self._metered_store.async_load()
        except Exception:
            _LOGGER.exception("could not load the meter's days; reading them afresh")
            return
        held = _metered_from_record(data)
        if held is None or dt_util.now().date() - held.read_on > _METERED_HOLD:
            return
        self._metered = held
