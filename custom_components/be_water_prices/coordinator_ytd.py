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

"""The year to date: restoring and saving its cycle, folding each reading
into it, pricing it on the card in force, and following the meter live.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import math
import time
from dataclasses import replace
from datetime import UTC, date, datetime
from typing import TYPE_CHECKING, Any

from homeassistant.core import (
    CALLBACK_TYPE,
    Event,
    EventStateChangedData,
    HomeAssistant,
    State,
    callback,
)
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_change
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

# The daily rows are read through their module: the readers there ask for
# them too, and all of them have to get the same function, as they did
# when they shared a module.
from . import meter_reads
from .const import (
    BACKFILL_YIELD_EVERY,
    CONF_COMMUNE,
    CONF_PERSONS,
    CONF_SOCIAL_TARIFF,
    DEFAULT_PERSONS,
    INTEGRATION_VERSION,
)
from .coordinator_store import _card_from_record, _cycle_from_record, _MeteredDays
from .meter_reads import (
    RecorderUnavailable,
    _admitted_changes,
    _is_external_statistic,
    _numeric_state,
    _recorder_ytd_m3,
    _resets_on_a_cycle,
    _state_volume_m3,
)
from .pricing import compute_ytd_cost
from .providers import WaterTariff
from .providers.base import tariff_to_dict
from .ytd_fold import _fold, _YtdCycle

if TYPE_CHECKING:
    from .coordinator import CoordinatorData, WaterConfigEntry
    from .coordinator_meter import YearFigures

_LOGGER = logging.getLogger(__name__)


# Debounce window for the live path's best-effort Store flush. The daily
# tick and a clean unload save authoritatively; this only bounds how much of
# the climbing high-water mark a hard crash between ticks can lose.
_YTD_SAVE_DELAY_S = 30


def _priced_on(year: int) -> date:
    """The day a round folded for ``year`` is priced on.

    Today, unless the clock moved on mid-round. Then the year the round is
    about, which is the one its consumption belongs to: its last day when
    the clock is already past it, its first when the clock stepped back.
    """
    today = dt_util.now().date()
    if today.year == year:
        return today
    return date(year, 12, 31) if today.year > year else date(year, 1, 1)


def _cost_basis(
    *, utility: str, commune: str | None, persons: int, social: bool, card_year: int
) -> str:
    """What the household is billed as, rather than what it is billed for.

    The cost floor exists to stop a momentary dip publishing a decrease: a
    backward clock step, or a tariff fetch that came back cheaper than the
    one before it. It is not meant to outlive a change in who the bill is
    for. Enabling the social tariff, registering a resident, or resolving
    the commune the household actually lives in each lower the bill for
    the rest of the year, and clamping those to a floor measured under the
    old answer published 437.56 EUR where 87.51 was owed, until January.

    Only those inputs are here. The tariff's own rates are deliberately
    absent: a cheaper card is exactly the transient this floor is for, and
    a real mid-year price cut still waits for the year to turn, which is
    the behaviour the README documents and four tests pin.

    The release is here for the same reason the others are. A release that
    corrects a rate downwards is not a dip either, and keying the floor on
    the rates would defeat it, so the version stands in: an upgrade
    rebuilds the floor once, and a correction reaches the running bill the
    day it ships rather than in January. Rebuilding is safe because the m3
    mark comes down only where the recorder has corrected it, and that path
    drops the floor as it goes, so the recomputed cost can only come out
    lower when the rates really are.

    The card's own year is here for the same reason again, and the price
    backfill has kept it in its gate all along for exactly this: when a
    publisher runs late every extractor serves last year's card until
    31 March, so January to March accrues at a stand-in's rates. Those are
    not a transient fetch, and if the real card comes in cheaper a floor
    that cannot tell the two apart holds the household on the stand-in
    until the year turns. A card year moves once, when the publisher
    catches up. A card put up ahead of its year does not move it: the year
    is the card in force's, so next year's card arriving in December leaves
    the closing year's floor where it is.
    """
    return "|".join(
        str(part)
        for part in (INTEGRATION_VERSION, utility, commune or "", persons, social, card_year)
    )


class _YtdMixin:
    """Mixed into WaterCoordinator."""

    # State WaterCoordinator.__init__ creates, as bare annotations: a value
    # here would change what hasattr() and the instance dict see.
    _cycle_dirty: bool
    _hourly_unsub: CALLBACK_TYPE | None
    _meter_entity_id: str | None
    _meter_history_deferred: bool
    _meter_unsub: CALLBACK_TYPE | None
    _metered: _MeteredDays | None
    _recorder_ok: bool | None
    _resetting_meter: str | None
    _store: Store[dict[str, Any]]
    _ytd: _YtdCycle
    _ytd_arbitrate: bool
    _ytd_card: tuple[WaterTariff, str | None] | None
    _ytd_high_m3: float | None
    _ytd_hold_m3: float | None
    _ytd_hold_run: int
    _ytd_hold_span_s: float
    _ytd_last_fold_at: float | None
    _ytd_lock: asyncio.Lock
    _ytd_run_m3: float | None
    data: CoordinatorData
    entry: WaterConfigEntry
    hass: HomeAssistant
    meter_history_pending: bool

    if TYPE_CHECKING:
        # Defined by WaterCoordinator, its other mixin or DataUpdateCoordinator.
        def _owns_the_entry(self) -> bool: ...
        async def _read_meter_history(self, meter: str) -> None: ...
        def _year_figures(
            self, tariff: WaterTariff, ytd_m3: float | None, ytd_cost: float | None
        ) -> YearFigures: ...
        async def async_resolve_meter_entity(self) -> str | None: ...
        def async_update_listeners(self) -> None: ...

    async def async_load_ytd_state(self) -> None:
        """Restore the persisted YTD cycle before the first refresh.

        Restoring it across restarts is what stops the running cost
        falling back: without it every restart re-derived the year from the
        recorder's trailing daily total and snapped the published figure
        downward.

        A cycle that cannot be read or migrated is dropped rather than
        raised: this runs during entry setup with nothing catching it, so a
        bad record would otherwise block the entry entirely. Starting with
        no cycle costs one re-bootstrap from the recorder.
        """
        try:
            data = await self._store.async_load()
        except Exception:
            _LOGGER.exception("could not load the YTD cycle; starting a fresh one")
            return
        if not data:
            return
        cycle = _cycle_from_record(data)
        if cycle is None:
            _LOGGER.warning("the persisted YTD cycle is not readable; starting a fresh one")
            return
        self._ytd = cycle
        self._ytd_card = _card_from_record(data.get("card"))
        # A swap stamps the start of the year and clears what the recorder
        # had reported for it, and only a recorder answer reports again, so
        # a record in that shape is one whose swap no tick has dated yet.
        # The flag asking for that tick is gone with the process, and with
        # the meter in sight nothing else would ask: the year would stay on
        # the new register instead of coming back to the recorder's figure.
        # A meter the recorder holds no statistics for keeps the same shape
        # all year and is asked once per restart, and its zero answer never
        # lowers the year.
        self._ytd_arbitrate = cycle.started_at is not None and cycle.recorder_hwm is None

    async def async_save_ytd_state(self) -> None:
        """Flush a pending cycle change to the Store on a clean unload / reload.

        The daily tick persists on each change and the live path schedules a
        debounced save, but a reload can land between those, so flush here
        too. Otherwise the climbing figure would revert to its last persisted
        value and a glitch right after the restart would no longer be
        clamped.
        """
        if self._cycle_dirty:
            self._cycle_dirty = False
            await self._store.async_save(self._cycle_state())

    def _cycle_state(self) -> dict[str, Any]:
        return {
            "meter": self._ytd.meter,
            "year": self._ytd.year,
            "m3": self._ytd.m3,
            "cost": self._ytd.cost,
            "offset_m3": self._ytd.offset_m3,
            "basis": self._ytd.basis,
            "recorder_hwm": self._ytd.recorder_hwm,
            "seen_at": self._ytd.seen_at,
            "started_at": self._ytd.started_at,
            "card": (
                None
                if self._ytd_card is None
                else {**tariff_to_dict(self._ytd_card[0]), "commune": self._ytd_card[1]}
            ),
        }

    def _card_in_force(self, tariff: WaterTariff, year: int) -> WaterTariff:
        """The card ``year``'s running bill is priced on: ``tariff``, unless
        it is dated after the day the round is priced on.

        Farys and the Walloon pages can put next year's card up in December,
        and Belgian tariffs apply from 1 January. Priced on it, the whole
        closing year was billed at next year's rates, and every flip of the
        page between the two cards dropped the cost floor, so the running
        bill could walk down. The card in force is held instead, as long as
        it was fetched for this operator and commune. A fresh install in
        December holds none and prices on the card it has.
        """
        if tariff.valid_from <= _priced_on(year):
            return tariff
        if self._ytd_card is not None:
            card, commune = self._ytd_card
            if (
                card.utility == tariff.utility
                and commune == self.entry.options.get(CONF_COMMUNE)
                and card.valid_from.year <= year
            ):
                return card
        return tariff

    def _fold_cycle(
        self,
        tariff: WaterTariff,
        *,
        meter: str,
        now_year: int,
        reading: float | None,
        recorder_m3: float | None,
        recorder_taken_back: float,
        recorder_has_statistic: bool,
    ) -> tuple[float | None, float | None]:
        """Fold a round of evidence into the cycle and return what to publish.

        The rule itself is :func:`_fold`; this is the only place that keeps
        its answer. The record and the transient hold state are written back
        before returning, so a caller that decides not to publish still
        cannot lose a swap run, a held reading, or a rebuilt frame.
        """
        # How long since the last round. The rule itself keeps no clock,
        # so the caller measures the gap and hands it over as evidence --
        # that is what tells a run of readings spread over hours from a
        # burst of them inside a second.
        now = time.monotonic()
        elapsed_s = 0.0 if self._ytd_last_fold_at is None else now - self._ytd_last_fold_at
        self._ytd_last_fold_at = now
        card = self._card_in_force(tariff, now_year)
        held = (card, self.entry.options.get(CONF_COMMUNE))
        if card.valid_from <= _priced_on(now_year) and held != self._ytd_card:
            self._ytd_card = held
            self._cycle_dirty = True
        out = _fold(
            self._ytd,
            now_year=now_year,
            meter=meter,
            reading=reading,
            recorder_m3=recorder_m3,
            recorder_taken_back=recorder_taken_back,
            recorder_has_statistic=recorder_has_statistic,
            recorder_ok=self._recorder_ok,
            hold_m3=self._ytd_hold_m3,
            hold_run=self._ytd_hold_run,
            hold_span_s=self._ytd_hold_span_s,
            run_m3=self._ytd_run_m3,
            elapsed_s=elapsed_s,
            # Wall clock, not the monotonic one above: this is the stamp
            # the record carries, and the gap it has to measure is the one
            # across a restart, which no monotonic clock survives.
            now_ts=dt_util.utcnow().timestamp(),
            high_m3=self._ytd_high_m3,
            basis=_cost_basis(
                utility=tariff.utility,
                commune=self.entry.options.get(CONF_COMMUNE),
                persons=int(self.entry.options.get(CONF_PERSONS, DEFAULT_PERSONS)),
                social=bool(self.entry.options.get(CONF_SOCIAL_TARIFF, False)),
                card_year=card.valid_from.year,
            ),
            cost_of=lambda m3: self._ytd_cost_from_m3(card, m3, now_year),
        )
        if out.cycle != self._ytd:
            self._cycle_dirty = True
        self._ytd = out.cycle
        self._ytd_hold_m3 = out.hold_m3
        self._ytd_hold_run = out.hold_run
        self._ytd_hold_span_s = out.hold_span_s
        self._ytd_run_m3 = out.run_m3
        self._ytd_high_m3 = out.high_m3
        if out.swapped or out.arbitrate:
            # Nothing has dated the new register yet, or the round met a
            # step it could not weigh. Ask the recorder on the next tick:
            # without it the frame is self-consistent from the moment it
            # is built, so the gate in _compute_ytd never queries again
            # and a meter that was merely offline bills its whole
            # lifetime into this year.
            self._ytd_arbitrate = True
        return out.m3, out.cost

    async def _compute_ytd(self, tariff: WaterTariff) -> tuple[float | None, float | None]:
        """Compute YTD m³ and cost, one caller at a time: see :meth:`_read_ytd`."""
        async with self._ytd_lock:
            return await self._read_ytd(tariff)

    async def _read_ytd(self, tariff: WaterTariff) -> tuple[float | None, float | None]:
        """Compute YTD m³ and cost for the daily tick and the hourly read.

        The recorder is consulted only when the cycle cannot answer on its
        own: no frame for this year, or no reading to put through it. From
        there the live meter drives, and :func:`_fold` decides what the year
        publishes.

        Returns ``(ytd_m3, ytd_cost_eur)``; both ``None`` when no meter is
        configured or nothing is known about this year yet.
        """
        deferred = self._meter_history_deferred
        self._meter_history_deferred = False
        meter = await self.async_resolve_meter_entity()
        if meter != self._meter_entity_id:
            # Auto-discovery can start resolving a different Energy-dashboard
            # source with no options change, so nothing reloads the entry.
            # Move the live subscription across before anchoring on it.
            self._meter_entity_id = meter
            self.async_setup_live_tracking()
        if not meter:
            self._metered = None
            return None, None
        # Before anything reads the meter or folds the cycle, not after:
        # these await recorder queries, and an await between the fold and
        # the return is exactly the window a live meter event uses to
        # publish a higher figure that the stale locals here would then
        # overwrite with a lower one. Everything below re-reads its state.
        if deferred:
            self.meter_history_pending = True
        else:
            await self._read_meter_history(meter)
        now_year = dt_util.now().year
        live = self._live_reading(meter, self.hass.states.get(meter))
        recorder_m3: float | None = None
        recorder_taken_back = 0.0
        recorder_has_statistic = False
        # The frame produces less than the year has already published, so it
        # has fallen behind what is known. Only a reading and a recorder
        # figure read together can put it back, so ask for one. The mismatch
        # closes as soon as the frame is rebuilt, which makes this
        # self-terminating: a year whose meter drives it never queries.
        # A frame is built as reading - published, and subtracting it back
        # leaves binary residue on about half of all pairs, which read as
        # the frame trailing the figure by a femtolitre and asked the
        # recorder on every idle tick.
        stale_frame = (
            self._ytd.m3 is not None
            and self._ytd.offset_m3 is not None
            and live is not None
            and self._ytd.m3 > live - self._ytd.offset_m3
            and not math.isclose(self._ytd.m3, live - self._ytd.offset_m3, abs_tol=1e-6)
        )
        # Decide before folding, not after: the fold clears the record itself
        # when the meter has been repointed, and a gate reading the cleared
        # record would miss the very case that needs the query most.
        if (
            self._ytd.meter != meter
            or self._ytd.year != now_year
            or self._ytd.offset_m3 is None
            or live is None
            or stale_frame
            or self._ytd_arbitrate
        ):
            self._ytd_arbitrate = False
            today = dt_util.now().date()
            jan1 = date(now_year, 1, 1)
            try:
                answer = await _recorder_ytd_m3(
                    self.hass,
                    meter,
                    jan1,
                    today,
                    register=self._sees_a_register(meter, self.hass.states.get(meter)),
                )
                self._recorder_ok = True
                # A meter with no statistic still starts an empty year at
                # zero, or one Home Assistant keeps no statistics for could
                # never anchor; the fold is told the zero came from nowhere.
                recorder_has_statistic = answer is not None
                recorder_m3, recorder_taken_back = (0.0, 0.0) if answer is None else answer
            except RecorderUnavailable as err:
                _LOGGER.debug("recorder unreadable for %s: %s", meter, err)
                self._recorder_ok = False
            # That query yielded to the loop, and the reading captured before
            # it is the one thing here that can have gone stale meanwhile. Read
            # the meter again: this is the tick that decides how the year is
            # framed, and framing it on a reading the meter has already moved
            # past is wrong for the rest of the year, not just for this tick.
            live = self._live_reading(meter, self.hass.states.get(meter))
        else:
            # The cycle answered on its own, so nothing asked the recorder and
            # there is no pending doubt about it. Forget the last answer rather
            # than letting it stand: this branch is the healthy year, so a
            # failure from months ago would otherwise still be believed at the
            # January rollover and block the first live reading from starting
            # the new year.
            self._recorder_ok = None
        ytd_m3, ytd_cost = self._fold_cycle(
            tariff,
            meter=meter,
            now_year=now_year,
            reading=live,
            recorder_m3=recorder_m3,
            recorder_taken_back=recorder_taken_back,
            recorder_has_statistic=recorder_has_statistic,
        )
        if self._cycle_dirty and self._owns_the_entry():
            self._cycle_dirty = False
            folded = self._ytd
            await self._store.async_save(self._cycle_state())
            if self._ytd != folded:
                # That save yielded to the loop and a meter event handled
                # while it ran moved the cycle on. Publish the cycle as it
                # stands rather than the figure computed before the await,
                # which the year has already climbed past.
                ytd_m3, ytd_cost = self._fold_cycle(
                    tariff,
                    meter=meter,
                    now_year=now_year,
                    reading=None,
                    recorder_m3=None,
                    recorder_taken_back=0.0,
                    recorder_has_statistic=False,
                )
        return ytd_m3, ytd_cost

    def _ytd_cost_from_m3(
        self, tariff: WaterTariff, ytd_m3: float, year: int, day: date | None = None
    ) -> float | None:
        """Apply the pro-rated YTD bill math to a year-to-date m³ figure,
        the fees accrued to ``day`` when given.

        Shared by the daily recorder path (:meth:`_compute_ytd`) and the
        live meter-event path (:meth:`_recompute_live_ytd`) so both use
        identical fee pro-rating and regional math.

        ``year`` is the one the round is being folded for, not the one the
        clock reads now. A tick that starts on 31 December and crosses
        local midnight while its recorder query runs would otherwise price
        the closing year at one day elapsed and hand back a fee of nothing.
        """
        today = _priced_on(year) if day is None else day
        jan1 = date(year, 1, 1)
        elapsed = (today - jan1).days + 1  # include today
        days_in_year = 366 if calendar.isleap(year) else 365
        fraction = elapsed / days_in_year
        persons = int(self.entry.options.get(CONF_PERSONS, DEFAULT_PERSONS))
        social = bool(self.entry.options.get(CONF_SOCIAL_TARIFF, False))
        return compute_ytd_cost(tariff, ytd_m3, persons, fraction, social_tariff=social)

    async def async_year_by_hour(
        self, hours: list[datetime]
    ) -> list[tuple[float, float | None]] | None:
        """The year's m³ and cost at the end of each of ``hours``, UTC hours
        from 1 January, for the statistics backfill.

        Each day holds the water its daily change admits, as the year's own
        reading does, shared over its hours as the meter's hourly changes
        rose, and the whole is scaled to the m³ the year publishes, so the
        last hour is the sensor's figure. Each hour is priced as the year
        is, on the card the running bill is on and the fees accrued to its
        own day. None while the year publishes no figure, or one for another
        year, without a meter or statistics to read, or when the recorder
        did not answer.
        """
        data = self.data
        meter = self._meter_entity_id
        if data is None or meter is None or data.ytd_consumption_m3 is None or not hours:
            return None
        year = dt_util.as_local(hours[0]).year
        if self._ytd.year != year:
            # The figure is the new year's when the hours were counted out
            # before midnight, and scaled to it the year would end at the
            # new one's first minutes.
            return None
        # Not the card fetched, which can be next year's from December: priced
        # on that, the closing year was written at next year's rates.
        card = self._card_in_force(data.tariff, year)
        jan1 = date(year, 1, 1)
        last_day = dt_util.as_local(hours[-1]).date()
        register = self._sees_a_register(meter, self.hass.states.get(meter))
        try:
            daily = await meter_reads._recorder_daily_rows(
                self.hass, meter, jan1, last_day, register=register
            )
            hourly = await meter_reads._recorder_daily_rows(
                self.hass, meter, jan1, last_day, register=register, period="hour"
            )
        except RecorderUnavailable:
            return None
        if daily is None or hourly is None:
            return None
        admitted, _refused, _taken_back = _admitted_changes(
            daily, meter, jan1, last_day, level=logging.DEBUG
        )
        used: dict[date, float] = {}
        for bucket, m3 in admitted:
            if bucket is not None:
                day = dt_util.as_local(datetime.fromtimestamp(bucket, UTC)).date()
                used[day] = used.get(day, 0.0) + m3
        rises = {
            float(row["start"]): max(0.0, float(row["change"]))
            for row in hourly
            if row.get("change") is not None
        }
        days: dict[date, list[tuple[datetime, float]]] = {}
        on: list[date] = []
        for n, hour in enumerate(hours, 1):
            day = dt_util.as_local(hour).date()
            on.append(day)
            days.setdefault(day, []).append((hour, rises.get(hour.timestamp(), 0.0)))
            if n % BACKFILL_YIELD_EVERY == 0:
                await asyncio.sleep(0)
        water = dict.fromkeys(hours, 0.0)
        for day, m3 in used.items():
            day_hours = days.get(day)
            if not day_hours:
                continue
            rose = sum(rise for _hour, rise in day_hours)
            if rose == 0.0:
                # Water the day admits in an hour after the last one there is.
                water[day_hours[-1][0]] += m3
                continue
            for hour, rise in day_hours:
                water[hour] += m3 * rise / rose
        recorded = sum(water.values())
        if recorded == 0.0:
            # A year the recorder holds no water for: what it publishes is
            # put on the last hour.
            water[hours[-1]] = recorded = data.ytd_consumption_m3
        scale = 0.0 if recorded == 0.0 else data.ytd_consumption_m3 / recorded
        out: list[tuple[float, float | None]] = []
        total = 0.0
        for n, (hour, day) in enumerate(zip(hours, on, strict=True), 1):
            total += water[hour]
            m3 = total * scale
            out.append((m3, self._ytd_cost_from_m3(card, m3, year, day)))
            if n % BACKFILL_YIELD_EVERY == 0:
                await asyncio.sleep(0)
        return out

    def _sees_a_register(self, meter: str, state: State | None) -> bool:
        """Whether ``meter`` reads as a register that only ever climbs.

        One that goes back to zero on a schedule, a daily utility meter for
        one, cannot be framed as a register: each reset looked like a dip
        and the year only moved on a day that used more than every day
        before it. Its water is in the recorder's running sum, so such a
        meter is read from there alone.

        The verdict needs the attributes of a reading, which Home Assistant
        drops while an entity is unavailable, so the last one is kept until
        the meter reports again. An external statistic has no entity, so
        no register either.
        """
        if _is_external_statistic(meter):
            return False
        if _numeric_state(state) is not None:
            self._resetting_meter = meter if _resets_on_a_cycle(state) else None
        return self._resetting_meter != meter

    def _live_reading(self, meter: str, state: State | None) -> float | None:
        """The meter's reading in m³, or ``None`` when it is not a register."""
        if not self._sees_a_register(meter, state):
            return None
        return _state_volume_m3(state)

    async def _async_hourly_read(self, _now: datetime) -> None:
        """Read the year again for a meter with no register to follow.

        Such a meter's water reaches the year only through the recorder,
        and the daily tick left it up to a day behind. Home Assistant
        compiles an hour's statistics just after it ends, and an
        integration importing its own does so soon after (Niko Home Control
        at two past), which is why this runs at five past. Nothing changes
        in between: the daily buckets are built from the hourly rows alone.
        A refresh already reading the year is left to it.
        """
        meter = self._meter_entity_id
        if self.data is None or meter is None or self._ytd_lock.locked():
            return
        if self._sees_a_register(meter, self.hass.states.get(meter)):
            return
        ytd_m3, ytd_cost = await self._compute_ytd(self.data.tariff)
        if self._owns_the_entry():
            self._publish_ytd(ytd_m3, ytd_cost)

    @callback
    def async_setup_live_tracking(self) -> None:
        """Subscribe to the resolved meter so YTD sensors update on each draw.

        Called after the first refresh has resolved the meter, and again by
        any later tick that resolves a different one. An options change
        reloads the entry, but the Energy-dashboard source behind
        auto-discovery can change with no reload at all, so the
        subscription has to follow the meter rather than stay pinned to
        whichever entity was resolved at setup. The hourly read for a
        meter with no register is set up and torn down with it.
        """
        self.async_unsub_live_tracking()
        if self._meter_entity_id is None or not self._owns_the_entry():
            # A late refresh on a retired coordinator resolves the meter
            # too; subscribed, it would fold readings into a dead cycle
            # and write the Store of an entry that is gone.
            return
        self._meter_unsub = async_track_state_change_event(
            self.hass, [self._meter_entity_id], self._async_meter_state_event
        )
        self._hourly_unsub = async_track_time_change(
            self.hass, self._async_hourly_read, minute=5, second=0
        )

    @callback
    def async_unsub_live_tracking(self) -> None:
        """Tear down the live meter subscription and the hourly read, if any."""
        if self._meter_unsub is not None:
            self._meter_unsub()
            self._meter_unsub = None
        if self._hourly_unsub is not None:
            self._hourly_unsub()
            self._hourly_unsub = None

    @callback
    def _async_meter_state_event(self, event: Event[EventStateChangedData]) -> None:
        # Ignore anything that is not the meter we are currently anchored
        # on, so a subscription that outlives a meter change cannot feed a
        # foreign reading into the cycle.
        if event.data["entity_id"] != self._meter_entity_id:
            return
        self._recompute_live_ytd(event.data["new_state"])

    @callback
    def _recompute_live_ytd(self, state: State | None) -> None:
        """Push a fresh YTD figure from the meter's live state.

        Pure in-memory arithmetic (no recorder / network call), so it is
        safe to run on every meter update. The same rule the daily tick
        uses, minus the recorder answer the tick can bring: a reading that
        the cycle cannot place yet contributes nothing and the last good
        value stays, as it does when the meter reads unavailable, unknown,
        non-numeric or in a unit that is not a volume.
        """
        if self.data is None:
            return
        meter = self._meter_entity_id
        if meter is None:
            return
        live = self._live_reading(meter, state)
        if live is None:
            return
        ytd_m3, ytd_cost = self._fold_cycle(
            self.data.tariff,
            meter=meter,
            now_year=dt_util.now().year,
            reading=live,
            recorder_m3=None,
            recorder_taken_back=0.0,
            recorder_has_statistic=False,
        )
        self._publish_ytd(ytd_m3, ytd_cost)

    @callback
    def _publish_ytd(self, ytd_m3: float | None, ytd_cost: float | None) -> None:
        """Publish a year-to-date figure folded outside the daily tick."""
        if self.data is None:
            return
        if ytd_m3 is None:
            # Nothing is known about this year yet. The fold has still kept
            # whatever the reading proved, so the sensors hold rather than
            # blanking on a year that has not started.
            return
        figures = self._year_figures(self.data.tariff, ytd_m3, ytd_cost)
        if (
            ytd_m3 == self.data.ytd_consumption_m3
            and ytd_cost == self.data.current_year_cost_eur
            and figures == self.data.year_figures
        ):
            # Nothing changed -- a same-value meter re-report or an
            # attribute-only state event. Skip so a frequently-reporting
            # meter does not write a redundant recorder row for every
            # sensor. The cost is part of the check so a year rollover (or
            # the midnight fee-proration step) still republishes even when
            # the volume figure is unchanged at ~0, and so are the year
            # figures, which the hourly read can read again with the year
            # where it stood.
            return
        if self._cycle_dirty and self._owns_the_entry():
            # The cycle moved. Schedule a debounced flush (the daily tick and
            # the unload save authoritatively; this just bounds how much of
            # the climbing figure a hard crash between ticks can lose).
            # _cycle_dirty stays set so the next authoritative save still
            # writes and clears it.
            self._store.async_delay_save(self._cycle_state, _YTD_SAVE_DELAY_S)
        # Publish without async_set_updated_data: that helper cancels and
        # re-arms the update_interval timer, and a meter reports far more
        # often than once a day, so the daily tariff refresh would be pushed
        # forward on every draw and never come due.
        # snapshot_age_hours is deliberately left alone: a meter draw says
        # nothing about how old the tariff snapshot is, and refreshing it here
        # changed an attribute on every entity on every reading, so every
        # sensor wrote a recorder row per draw instead of the ones that
        # actually moved. It advances on the daily tick, as it already does on
        # an install with no meter at all.
        # The year-end projection is the year so far plus a rest that only
        # the daily tick reads, so it moves with the year.
        self.data = replace(
            self.data,
            ytd_consumption_m3=ytd_m3,
            current_year_cost_eur=ytd_cost,
            ytd_started_at=self._ytd.started,
            year_figures=figures,
        )
        self.async_update_listeners()
