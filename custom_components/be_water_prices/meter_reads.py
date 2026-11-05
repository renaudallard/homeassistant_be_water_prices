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

"""Reading the household's water meter: its state, the Energy dashboard's
water meter, and the recorder's daily statistics with the buckets no
household could have drawn left out.
"""

from __future__ import annotations

import asyncio
import calendar
import logging
import math
from collections.abc import Mapping
from datetime import UTC, date, timedelta
from functools import partial
from typing import Any, Literal

from homeassistant.components.sensor import ATTR_LAST_RESET, ATTR_STATE_CLASS, SensorStateClass
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfVolume,
)
from homeassistant.core import HomeAssistant, State
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .ytd_fold import _AWAY_M3_PER_DAY, _IMPLAUSIBLE_JUMP_M3, _SECONDS_PER_DAY

_LOGGER = logging.getLogger(__name__)


# How long to wait for the Energy dashboard's manager singleton before
# giving up on it for this tick.
_ENERGY_MANAGER_TIMEOUT_S = 10.0


# How many refused daily buckets it takes before a year counts as
# unreadable rather than empty. A year that has barely begun has one or
# two, and if a reset is what they hold it has still used nothing; a
# meter that poisons every bucket accumulates them by the month.
_REFUSALS_BEFORE_UNREADABLE = 2


# Units that need no conversion because the figure behind them already is
# cubic metres. A reading with no unit at all is the common case, and
# ASCII "m3" is the superscript one typed on a keyboard: Home Assistant's
# volume converter knows neither, and both halves of the meter path have
# to accept exactly the same set or a meter is read on one and refused on
# the other. Spelled out separately they drifted apart twice.
_ALREADY_CUBIC_METRES: tuple[str | None, ...] = (None, "m3", UnitOfVolume.CUBIC_METERS)


class RecorderUnavailable(Exception):
    """The recorder could not be queried, as distinct from answering empty.

    An empty answer means the year holds no consumption yet and may be
    anchored at zero. This means the year's consumption is simply unknown
    right now, and anchoring on it would discard whatever has already been
    reported.
    """


def _numeric_state(state: State | None) -> float | None:
    """Return ``state``'s numeric value, or ``None`` if not usable.

    Filters the unavailable / unknown sentinels and any non-numeric
    payload so a flapping meter never pushes a garbage YTD figure.
    """
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        value = float(state.state)
    except (TypeError, ValueError):
        return None
    # "inf", "nan" and an overflowing exponent all parse. Two infinite
    # readings pinned the year at infinity, which the sensors refused to
    # publish until a restart.
    return value if math.isfinite(value) else None


def _is_external_statistic(statistic_id: str) -> bool:
    """Whether ``statistic_id`` names a statistic another integration writes.

    Home Assistant files those under "domain:object", and an entity id
    never holds a colon.
    """
    return ":" in statistic_id


def _resets_on_a_cycle(state: State | None) -> bool:
    """Whether ``state`` is a meter that goes back to zero on a schedule.

    A utility meter with a cycle says when its next reset is due, and a
    ``total`` sensor names its last one. Either way its state is what was
    used since then, not a register.
    """
    if state is None:
        return False
    # utility_meter's ATTR_NEXT_RESET, spelt out rather than imported so
    # reading it does not load that integration.
    if state.attributes.get("next_reset") is not None:
        return True
    return (
        state.attributes.get(ATTR_STATE_CLASS) == SensorStateClass.TOTAL
        and state.attributes.get(ATTR_LAST_RESET) is not None
    )


def _state_volume_m3(state: State | None) -> float | None:
    """Return ``state``'s reading converted to cubic metres, or ``None``.

    The YTD / running-cost math works in m³, but HA permits a
    ``water`` sensor (the explicit override and the Energy-dashboard
    auto-pick alike) to report litres, gallons, ft³, etc. Read the
    meter's own ``unit_of_measurement`` and convert so a non-m³ meter
    is not silently billed ~1000× too high. A reading with no unit, or
    in ASCII ``m3``, is assumed to already be m³; a unit we cannot
    convert to a volume is rejected so the YTD sensors stay unknown
    rather than publish a garbage figure. The set that needs no
    conversion is shared with the recorder-side guard, which has to
    accept exactly the same units.
    """
    value = _numeric_state(state)
    if value is None or state is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit in _ALREADY_CUBIC_METRES:
        return value
    try:
        return VolumeConverter.convert(value, unit, UnitOfVolume.CUBIC_METERS)
    except HomeAssistantError:
        _LOGGER.debug("meter unit %r is not a convertible volume; ignoring reading", unit)
        return None


async def _discover_energy_water_meter(hass: HomeAssistant) -> tuple[str | None, int]:
    """Return the first ``water`` source's ``stat_energy_from`` from HA's
    Energy dashboard, and how many water sources it holds.

    The count comes back with the meter because the caller has to say so:
    the dashboard's own order decides which of several is billed, and that
    reflects the order they were added, not which one the utility
    invoices. ``(None, 0)`` when no water source is configured, or the
    energy component is unavailable.

    Wraps every failure mode -- ImportError on old HA without the
    energy component, manager raising on a fresh install, malformed
    source dicts -- so a coordinator tick never crashes on this path.
    """
    try:
        # async_get_manager is the documented public entry point but is not
        # listed in homeassistant.components.energy.__all__, so mypy --strict
        # flags it; the ignore matches the recorder pattern in this file.
        from homeassistant.components.energy import (  # type: ignore[attr-defined]
            async_get_manager,
        )
    except ImportError:
        return None, 0
    try:
        # The manager is a singleton behind an asyncio.Event that is only
        # set once its first load succeeds. A failed read of
        # .storage/energy -- an EIO on the SD card is the classic one --
        # leaves the event unset forever, and every later caller waits on
        # it. Bound the wait so a poisoned singleton costs one tick
        # instead of wedging the coordinator for the life of the process.
        async with asyncio.timeout(_ENERGY_MANAGER_TIMEOUT_S):
            manager = await async_get_manager(hass)
    except Exception as err:  # a timeout, or whatever the energy component surfaced
        _LOGGER.debug("energy manager unavailable: %s", err)
        return None, 0
    data = getattr(manager, "data", None)
    if not data:
        return None, 0
    stats = [
        str(source["stat_energy_from"])
        for source in data.get("energy_sources") or []
        if isinstance(source, dict)
        and source.get("type") == "water"
        and source.get("stat_energy_from")
    ]
    if not stats:
        return None, 0
    if len(stats) > 1:
        # One meter is what the YTD helpers are built around: they take a
        # single statistic id, and the live path tracks one entity. A
        # household with two water meters gets the first and no hint that
        # the rest are missing from the bill, so say so once per tick at
        # a level that reaches the log by default.
        # Without the entity ids: diagnostics redact the meter, and this
        # line reaches the default log that users attach to issues.
        _LOGGER.warning(
            "Energy dashboard lists %d water meters; billing the first one listed. "
            "Set the water meter explicitly in the integration options to choose.",
            len(stats),
        )
    return stats[0], len(stats)


# Units the recorder hands back untouched and which need no conversion,
# because the figures behind them already are cubic metres. ``None`` is a
# sensor with no unit, which _state_volume_m3 reads as m3 on the live
# side; ``m3`` is the ASCII spelling of the same thing.
async def _refuse_an_unconvertible_unit(hass: HomeAssistant, instance: Any, entity_id: str) -> bool:
    """Stop before reading a statistic Home Assistant cannot put into m³.

    Returns whether the recorder holds a statistic for ``entity_id`` at
    all, which the year-to-date fold has to know: see :func:`_fold`.

    The query below asks the recorder for cubic metres, and the recorder
    obliges only for a unit its volume converter knows. For anything else
    it returns the figures untouched and says nothing, so a meter labelled
    ``l`` rather than ``L`` -- which Home Assistant only warns about, and
    still records statistics for -- was summed as though litres were
    cubic metres: a 100 m3 year published 100000 m3 and a bill of about
    1.04 million EUR.

    The live path already rejects such a meter, which is what made this
    reachable: with no usable reading, every tick fell through to the
    recorder. So the two halves now agree, and the year reports nothing
    rather than something absurd.

    Agreeing means agreeing on what passes, too, which is why both
    halves read the same ``_ALREADY_CUBIC_METRES``: a reading with no
    unit at all, and one in ASCII ``m3``, are cubic metres already, so
    there is nothing to convert and nothing to get wrong. Spelled out
    separately the two sets drifted apart, and a meter labelled ``m3``
    was refused by the live path while this one took it, which left the
    year anchored at zero on an install with no statistics yet.
    """
    try:
        from homeassistant.components.recorder.statistics import get_metadata
    except ImportError:
        return False
    try:
        metadata = await instance.async_add_executor_job(
            partial(get_metadata, hass, statistic_ids={entity_id})
        )
    except Exception as err:
        # Unreadable metadata is unreadable, and guessing that the unit is
        # fine is exactly the guess this exists to stop.
        raise RecorderUnavailable(f"could not read the unit of {entity_id}: {err}") from err
    entry = metadata.get(entity_id)
    if entry is None:
        # No statistic under this id: none yet, or the meter was renamed
        # or deleted. There is nothing to convert and nothing to misread.
        return False
    unit = entry[1].get("unit_of_measurement")
    if unit in VolumeConverter.VALID_UNITS or unit in _ALREADY_CUBIC_METRES:
        return True
    raise RecorderUnavailable(
        f"{entity_id} records statistics in {unit!r}, which Home Assistant cannot "
        f"convert to {UnitOfVolume.CUBIC_METERS} and which is not cubic metres "
        f"already; set the meter's unit to one of "
        f"{sorted(str(u) for u in VolumeConverter.VALID_UNITS)}"
    )


async def _recorder_daily_rows(
    hass: HomeAssistant,
    entity_id: str,
    start: date,
    end: date,
    *,
    register: bool = True,
    period: Literal["day", "hour"] = "day",
) -> list[Any] | None:
    """Return the daily statistics buckets for ``entity_id`` over ``[start, end]``,
    or the hourly ones with ``period``.

    Wraps :func:`statistics_during_period` via the recorder's executor so
    the SQLite query never runs on the event loop.

    Returns an empty list for an empty period, and ``None`` when there is
    no statistic to read at all: no recorder, or none kept for this meter.
    Raises :class:`RecorderUnavailable` only when a recorder that is
    running could not answer this query.

    The absent-recorder cases belong with the missing statistic rather than
    with the failure, because they never resolve: reporting them as
    unreadable leaves the caller waiting for a recovery that cannot come.

    Asks for the ``change`` field, which the recorder defines as the
    delta of the cumulative ``sum`` between the bucket's first and
    last sample. Reading ``sum`` directly would yield the all-time
    running total -- summing those would multiply the figure by however
    many years of meter history exist. ``sum`` comes along anyway
    because the first bucket's ``change`` is only meaningful next to
    it: see :func:`_recorder_ytd_m3`.

    ``register`` is ``False`` for a meter whose state is not a register,
    one that goes back to zero on a schedule.
    """
    try:
        # mypy --strict flags both names because the recorder module
        # does not re-export them via __all__; they're public per HA's
        # docs and import-time errors degrade gracefully via the
        # ImportError handler below.
        from homeassistant.components.recorder import (  # type: ignore[attr-defined]
            get_instance,
        )
        from homeassistant.components.recorder.statistics import (
            statistics_during_period,
        )
    except ImportError:
        # No recorder component at all: there are no statistics to read and
        # there never will be, so the year starts here rather than being
        # treated as unreadable forever.
        return None

    try:
        instance = get_instance(hass)
    except Exception as err:
        # Importable but not running: an install without default_config that
        # never enabled the recorder, or one whose recorder failed to start.
        # manifest.json lists recorder under after_dependencies, so a
        # configured recorder is always set up before this integration and
        # this cannot be a startup race. It is the same answer as no recorder
        # component at all, and it has to be, or such an install could never
        # anchor a year and both YTD sensors would sit unknown forever.
        _LOGGER.debug("no recorder instance for %s: %s", entity_id, err)
        return None

    if not await _refuse_an_unconvertible_unit(hass, instance, entity_id):
        return None

    # ``state`` is the register at the end of the bucket, which is what a
    # day's change is measured against: see :func:`_change_exceeds_the_register`.
    # That holds for a statistic Home Assistant compiles from a sensor, and
    # only for that one. An external statistic, written by an integration
    # under a "domain:object" id, carries whatever state its writer chose,
    # and Niko Home Control and suez_water put each hour's own volume there.
    # Measured against that, nearly every day claimed more than its register
    # and was refused, and a year of 22 m3 read 0.085. Its sum is written
    # directly rather than by the compiler whose reset arithmetic the
    # register guards undo, so it is read from sum and change alone. So is a
    # meter that resets on a schedule: each day's state is that day's water,
    # and the compiler's sum already runs through every reset.
    if register and not _is_external_statistic(entity_id):
        types = {"change", "sum", "state"}
    else:
        types = {"change", "sum"}
    start_dt = dt_util.start_of_local_day(start).astimezone(UTC)
    end_dt = dt_util.start_of_local_day(end).astimezone(UTC) + timedelta(days=1)
    try:
        stats = await instance.async_add_executor_job(
            statistics_during_period,
            hass,
            start_dt,
            end_dt,
            {entity_id},
            period,
            # Normalise the change deltas to m³ regardless of the meter's
            # own unit (HA permits water sensors in L / gal / ft³ / CCF as
            # well as m³); the recorder converts via the statistic's unit
            # class. Without this a litre-reporting meter would be summed
            # as if it were already cubic metres -- ~1000× too high.
            # Asking is not enough on its own: the recorder hands back
            # whatever it cannot convert, unconverted and unremarked, which
            # is why the unit is checked above before the query runs.
            {VolumeConverter.UNIT_CLASS: UnitOfVolume.CUBIC_METERS},
            types,
        )
    except Exception as err:
        _LOGGER.debug("recorder query for %s failed: %s", entity_id, err)
        raise RecorderUnavailable(str(err)) from err

    rows: list[Any] = list(stats.get(entity_id, []))
    return rows


async def _recorder_ytd_m3(
    hass: HomeAssistant, entity_id: str, start: date, end: date, *, register: bool = True
) -> tuple[float, float] | None:
    """Sum daily ``change`` deltas for ``entity_id`` over ``[start, end]``.

    Returns the period's consumption, ``0.0`` when the meter's statistic
    holds none, and ``None`` when there is no statistic to read at all.
    Raises :class:`RecorderUnavailable` only when a recorder that is
    running could not answer the query.

    The consumption comes paired with how much of it was taken back out of
    days a later register showed to be misreads. Such a take-back lowers an
    answer below one read before the correction's day was compiled, so the
    caller adds it back to tell a corrected answer from a database that
    lost some of the year.

    Those are different answers and the caller has to tell them apart: a
    year with no statistics may be anchored at zero, a query that failed may
    not, or a database hiccup would discard consumption already reported.
    Collapsing both into ``None`` is what every year-stamp and deferral
    guard in this module was re-deriving one call later. A missing
    statistic may anchor an empty year too, but a zero from it is no
    evidence against a year that already has a figure.
    """
    rows = await _recorder_daily_rows(hass, entity_id, start, end, register=register)
    if rows is None:
        return None
    admitted, refused, taken_back = _admitted_changes(
        rows, entity_id, start, end, level=logging.WARNING
    )
    if not admitted and refused > _REFUSALS_BEFORE_UNREADABLE:
        # Every bucket the year had was refused, and there were enough of
        # them to mean something. A handful is not enough: on 1 and 2
        # January a year has one or two buckets, and if a reset or a
        # backwards day is what they hold, the year really has used
        # nothing yet and anchoring it at zero is right.
        raise RecorderUnavailable(
            f"every one of {refused} daily buckets for {entity_id} was refused; "
            "the year cannot be read rather than being empty"
        )
    # No admitted change is negative any more, but the floor stays: it
    # costs nothing and the sensor must never read negative.
    return max(0.0, sum(m3 for _bucket, m3 in admitted)), taken_back


def _admitted_changes(
    rows: list[Any], entity_id: str, start: date, end: date, *, level: int
) -> tuple[list[tuple[float | None, float]], int, float]:
    """The daily changes in ``rows`` over ``[start, end]`` that read as water.

    Returns each admitted change with the start of the bucket it was
    booked in, how many buckets a guard refused, and how much was taken
    back out of buckets a later register showed to be misreads. A bucket
    refused for claiming more than its days could hold is logged at
    ``level``.
    """
    # The first-bucket trim below is not a refusal: it is a boundary
    # artefact, not evidence about the year. A year every guard rejects
    # sums to the same 0.0 as a year that used no water, and the
    # caller may anchor an empty year at zero but not an unreadable one: a
    # cumulative meter that republishes 0 on a nightly reconnect and is
    # still at 0 when each day closes leaves every bucket carrying the
    # whole register with no climb to measure it by, all of them dropped,
    # and the year restarted at zero with the water already used lost
    # until January.
    admitted: list[tuple[float | None, float]] = []
    refused = 0
    taken_back = 0.0
    # The register at the end of the bucket before, which the bucket
    # after a dip climbs from. Unknown before the first row: the window
    # holds nothing earlier.
    register: float | None = None
    # A register drop waiting for the bucket after it, see below.
    pending_drop = 0.0
    # The registers either side of the last admitted bucket, so a fall
    # that comes after it can show it was a misread. Kept across refused
    # buckets and gaps, which do not move the bucket it belongs to.
    around: tuple[float, float] | None = None
    # Asking for a day period makes Home Assistant re-align the end of the
    # window to the following local midnight, and the end handed over is
    # already midnight, so the query comes back one day longer than it was
    # asked for. _recorder_full_year_m3 drops the overshoot by bucket and
    # this did not: a clock that ran ahead before NTP corrected it leaves a
    # bucket dated tomorrow, and on 31 December tomorrow is next year.
    # Measured as the next local midnight rather than a fixed 86400, or the
    # 23-hour day the clocks go forward on would fall short of the cut.
    after_end = dt_util.start_of_local_day(end + timedelta(days=1)).timestamp()
    # The day before the window, so the first bucket in it counts as
    # following on rather than as arriving out of nowhere. Days with no
    # bucket of their own sit between this and the next one, and their
    # water is in whichever bucket comes next.
    previous = dt_util.start_of_local_day(start).timestamp() - _SECONDS_PER_DAY
    for index, row in enumerate(rows):
        before = register
        state = row.get("state")
        register = None if state is None else float(state)
        delta = row.get("change")
        if delta is None:
            continue
        bucket = row.get("start")
        if bucket is not None and bucket >= after_end:
            continue
        gap_days = 0.0
        if bucket is not None:
            gap_days = max(0.0, (bucket - previous) / _SECONDS_PER_DAY - 1.0)
            previous = bucket
        if index == 0 and _change_is_the_whole_register(row):
            # The recorder builds ``change`` by subtracting the sum it
            # finds immediately before the window, and falls back to zero
            # when it finds none. The first bucket's change is then the
            # meter's entire running total rather than that day's
            # consumption, and billing it into the window inflates the
            # year by every cubic metre the meter has ever measured.
            #
            # It reads the same whether the earlier rows were purged or
            # the meter is genuinely new, so the bucket is dropped either
            # way: one missing day beats an unbounded over-count that
            # persists until the year turns.
            _LOGGER.debug(
                "%s has no bucket before %s; dropping the first day rather than"
                " billing the meter's running total into the year",
                entity_id,
                start,
            )
            # Deliberately not counted as a refusal. It is a boundary trim
            # every healthy year takes at most once, and counting it made a
            # meter whose statistics start today, whose only bucket is this
            # one, look like a year nothing could read: the year then
            # refused to anchor at all where it used to anchor at zero.
            continue
        if delta < 0:
            # A register that went backwards is not consumption, and
            # subtracting it from the rest of the year would erase months
            # of water that was really used. It is held against the bucket
            # that follows it instead: a dip that recovers the next day (a
            # `total` meter rebooting across midnight) nets to the water
            # actually used, while a genuine swap or a lost run-up leaves
            # the pair negative and is dropped whole. A fall that takes
            # back the last admitted bucket has already been settled
            # against it, and holding it as well would cost the next day.
            refused += 1
            taken = _took_back_a_spike(admitted, around, register)
            if taken is None:
                pending_drop += float(delta)
            else:
                taken_back += taken
            continue
        if pending_drop < 0.0:
            netted = float(delta) + pending_drop
            pending_drop = 0.0
            if netted <= 0.0:
                _LOGGER.debug("%s: dropping a register drop and its follow-up bucket", entity_id)
                refused += 1
                continue
            if _exceeds_a_day(netted, entity_id, "netted", gap_days, level=level):
                refused += 1
                continue
            admitted.append((bucket, netted))
            # Its water is measured across the drop, not from the
            # register before it, so there is no spike to read off it.
            around = None
            continue
        if _change_exceeds_the_register(row):
            # A register cannot consume more than it reads. Home Assistant
            # treats a numeric dip on a total_increasing meter as a reset
            # and then adds the whole recovered reading to the sum, so a
            # meter that briefly reported 0 leaves a day whose change is
            # its entire register. Billed into the year that became the
            # high-water mark and pinned both sensors until January.
            #
            # The day still drew water, and the register's climb from the
            # end of the bucket before is that water. Dropping the bucket
            # whole lost it for good, and every later answer stayed short
            # by it while still climbing, which is exactly what a whole
            # history looks like to the fold. A previous register of 0 is
            # the dip itself rather than a reading to climb from, and one
            # above this bucket's is a different register, so either way
            # the day is refused. So is one whose register shows the last
            # admitted bucket was a spike, which is taken back out of that
            # bucket instead.
            taken = _took_back_a_spike(admitted, around, register)
            if taken is not None:
                taken_back += taken
                refused += 1
                continue
            if register is not None and before is not None and 0.0 < before <= register:
                climb = register - before
                if _exceeds_a_day(climb, entity_id, "recovered", gap_days, level=level):
                    refused += 1
                    continue
                admitted.append((bucket, climb))
                around = (before, register)
                continue
            _LOGGER.debug(
                "%s: dropping a bucket whose change %s exceeds the register %s",
                entity_id,
                delta,
                row.get("state"),
            )
            refused += 1
            continue
        if _exceeds_a_day(float(delta), entity_id, "single", gap_days, level=level):
            refused += 1
            continue
        admitted.append((bucket, float(delta)))
        around = None if before is None or register is None else (before, register)
    return admitted, refused, taken_back


def _took_back_a_spike(
    admitted: list[tuple[float | None, float]],
    around: tuple[float, float] | None,
    register: float | None,
) -> float | None:
    """Cap the last admitted bucket when ``register`` shows it was a misread.

    Returns how much the cap took out of it, or ``None`` when the register
    says nothing about that bucket.

    ``around`` holds the registers at the end of the bucket before that
    one and at its own end. A meter that misreads high before midnight and
    is corrected after it leaves the spike in an admitted bucket and the
    correction in the next one, either as a negative change or, past a
    tenth of the register, as a reset that is refused. Netting only went
    forward, so the spike stayed in the year and in the rolling year.

    A register that comes back between the two shows the high one was the
    outlier, and the bucket keeps only the climb to where it came back.
    One that comes back below where it stood before is a dip or a swap,
    and says nothing about the day before it.
    """
    if around is None or register is None:
        return None
    before, high = around
    if not before <= register < high:
        return None
    bucket, m3 = admitted[-1]
    capped = min(m3, register - before)
    admitted[-1] = (bucket, capped)
    return m3 - capped


def _exceeds_a_day(
    change: float, entity_id: str, kind: str, gap_days: float = 0.0, *, level: int
) -> bool:
    """Whether a bucket claims more water than the days behind it can hold.

    The live path holds a single report that climbs more than
    ``_MAX_STEP_M3``, plus whatever the meter's absence could have added,
    until the next reading agrees with it. The recorder path had no bound of
    any kind, so the same physical event was arbitrated when it arrived as a
    reading and billed on sight when it arrived as a statistics row.

    Two shapes reach here that :func:`_change_exceeds_the_register` cannot
    see, because it compares a day against its own register rather than
    against a day. A counter re-based onto the real meter reading leaves a
    bucket whose change is the whole re-base but whose register is larger
    still, so the shape test passes it. And a reset bucket that follows a
    negative day is netted rather than checked, so one glitch day carried
    4050 m3 into a year that had used half of one.

    A day is dropped rather than held: there is no next reading to confirm
    it against, and the year's figure is a high-water mark, so admitting
    one bad day pins the bill until January.

    ``gap_days`` is how many days with no bucket of their own sit behind
    this one. A bucket is not always one day of water: Home Assistant
    carries a meter's running total across a gap and attributes the whole
    of it to the bucket the meter comes back in, so the day a meter returns
    from an outage holds the outage. Read as one day it looked like a
    re-based register and was dropped, and losing it cost the whole absence
    rather than the one day this used to say. The gap buys room at the rate
    a household plausibly draws, which is generous enough to keep a real
    catch-up and far too mean to let a re-based register through: a
    register carrying 4050 m3 is still refused after a year out of sight.
    """
    allowance = _IMPLAUSIBLE_JUMP_M3 + max(0.0, gap_days) * _AWAY_M3_PER_DAY
    if change <= allowance:
        return False
    # The id only at DEBUG: diagnostics redact the meter, and the recorder
    # readers warn here at a level that reaches the default log users
    # attach to issues.
    _LOGGER.log(
        level,
        "ignoring a %s change of %.1f m3 over %.0f day(s) on %s; no household uses "
        "that much, so it reads as a re-based or reset register rather than water",
        kind,
        change,
        gap_days + 1.0,
        entity_id if level <= logging.DEBUG else "the water meter",
    )
    return True


def _change_exceeds_the_register(row: Any) -> bool:
    """Whether ``row`` claims more consumption than its register shows.

    True when the bucket's change is at least the register reading at its
    end, which no monotonic meter can produce in a day: it is the shape
    the recorder's reset arithmetic leaves behind after a dip.
    """
    change = row.get("change")
    state = row.get("state")
    if change is None or state is None:
        return False
    return float(change) > 0.0 and float(change) >= float(state)


def _change_is_the_whole_register(row: Any) -> bool:
    """Whether ``row``'s change is really the meter's cumulative total.

    True when the recorder had no earlier sum to subtract, which it
    reports by leaving ``change`` equal to ``sum``.
    """
    change = row.get("change")
    total = row.get("sum")
    if change is None or total is None:
        return False
    return bool(total) and abs(float(change) - float(total)) < 1e-9


async def _recorder_full_year_m3(
    hass: HomeAssistant, entity_id: str, year: int, *, register: bool = True
) -> float | None:
    """Return ``year``'s metered consumption, or ``None`` if it is not a whole year.

    Only a year the meter has statistics on both sides of can be read as a
    full one. A meter that first reported in June produces a June-to-December
    figure indistinguishable from a frugal year, and offering that as the
    yearly consumption would understate the projection for as long as the
    user kept it. So the window opens in the previous December: a bucket
    before 1 January proves the meter was already running when the year
    started, and a bucket in the year's own December proves it was still
    running when the year ended.

    Asking for history on either side rather than for a bucket dated
    1 January is deliberate. A meter reports when water moves, so a quiet
    day has no bucket at all, and a household away over New Year would fail
    the stricter test while having a perfectly complete year. The days in
    between have to be there too: a meter that was unavailable from
    January to November and came back in December had history on both
    sides and offered December's water as the year. A bucket on two days
    in three is the bar, which a household away for the summer clears and
    a meter installed in June does not.

    A negative daily delta means the recorded register went backwards. On a
    ``total`` meter that is what replacing the meter looks like, and the
    deltas around it no longer describe one meter's consumption, so the year
    is not offered rather than offered wrong. A ``total_increasing`` meter
    never produces one: Home Assistant reads the drop as the start of a new
    cycle and its running sum climbs straight through, which already gives
    this the figure it wants.
    """
    rows = await _recorder_daily_rows(
        hass, entity_id, date(year - 1, 12, 1), date(year, 12, 31), register=register
    )
    if rows is None:
        return None
    # Daily buckets start at local midnight, so each of these lands exactly
    # on a bucket boundary. The upper one is not defensive: asking for a
    # day period makes Home Assistant re-align the end of the window to the
    # following local midnight, and the end handed over is already midnight,
    # so it gains a whole day and the query returns 1 January of the year
    # after. Summing that would count a day that belongs to the next year,
    # and a meter replaced on New Year's day would put its negative delta in
    # that bucket and have the whole year refused for it.
    jan1 = dt_util.start_of_local_day(date(year, 1, 1)).timestamp()
    dec1 = dt_util.start_of_local_day(date(year, 12, 1)).timestamp()
    next_year = dt_util.start_of_local_day(date(year + 1, 1, 1)).timestamp()
    before_year = False
    into_december = False
    days = 0
    total = 0.0
    # The day before the window, so its first bucket counts as following on.
    # Kept across the December rows as well: a bucket on 20 December says the
    # meter was reporting then, which is what makes the gap to the next one
    # measurable at all.
    previous = dt_util.start_of_local_day(date(year - 1, 12, 1)).timestamp() - _SECONDS_PER_DAY
    for row in rows:
        bucket = row.get("start")
        if bucket is None:
            continue
        gap_days = max(0.0, (bucket - previous) / _SECONDS_PER_DAY - 1.0)
        previous = bucket
        if bucket < jan1:
            before_year = True
            continue
        if bucket >= next_year:
            continue
        if bucket >= dec1:
            into_december = True
        delta = row.get("change")
        if delta is None:
            continue
        if delta < 0:
            return None
        if _change_exceeds_the_register(row):
            # The same reset arithmetic as above; a year that carries the
            # whole register as one day's water is not a year to offer.
            return None
        if _exceeds_a_day(float(delta), entity_id, "full-year", gap_days, level=logging.WARNING):
            # _change_exceeds_the_register is a shape test and by
            # construction only catches a same-day reset: a counter
            # re-based onto the real meter reading leaves a bucket whose
            # register is larger still and sails through it. The daily
            # bound is in the year-to-date reader and was never reached
            # from here, so this offered 1262 m3 and a 6791.93 EUR
            # projection where the household had used 82 m3 and owed
            # 479.60, and accepting the Repair wrote it into the options.
            return None
        days += 1
        total += float(delta)
    days_in_year = 366 if calendar.isleap(year) else 365
    if not (before_year and into_december) or not _enough_days(days, days_in_year):
        return None
    return total


def _enough_days(days: int, length: int) -> bool:
    """Whether buckets on ``days`` of a period's ``length`` days cover it.

    Two days in three, which a household away for the summer clears and a
    meter that was down for most of the period does not.
    """
    return 3 * days >= 2 * length


def _metered_m3(days: Mapping[date, float], start: date, end: date) -> float | None:
    """What the meter recorded over ``[start, end]``, or ``None`` unless it
    recorded all of it.

    ``days`` holds each day's admitted water. The window is judged the way
    :func:`_recorder_full_year_m3` judges a calendar year: a bucket in the
    month before it proves the meter was already running when it opened,
    one in its last month that it still was when it closed, and buckets on
    two days in three in between that it was not away for most of it. An
    empty window holds no water.
    """
    if end < start:
        return 0.0
    month = timedelta(days=31)
    if not any(start - month <= day < start for day in days):
        return None
    if not any(end - month < day <= end for day in days):
        return None
    inside = [m3 for day, m3 in days.items() if start <= day <= end]
    if not _enough_days(len(inside), (end - start).days + 1):
        return None
    return sum(inside)


def _rolling_year_m3(days: Mapping[date, float], today: date) -> float | None:
    """What the meter recorded over the 365 days before ``today``.

    Closed days only: the figure is read once a tick, and a part of today
    frozen in it until the next one would be neither a day nor nothing.
    """
    return _metered_m3(days, today - timedelta(days=365), today - timedelta(days=1))


def _rest_of_year_m3(days: Mapping[date, float], today: date) -> float | None:
    """What the meter recorded last year from tomorrow's date to 31 December.

    The stand-in for what the rest of this year will use. Water follows the
    household's own season, a garden in summer or a pool filled in May, so
    last year's same days say more about it than a share of a yearly total.
    """
    tomorrow = today + timedelta(days=1)
    try:
        start = tomorrow.replace(year=tomorrow.year - 1)
    except ValueError:  # 29 February has no twin
        start = date(tomorrow.year - 1, 2, 28)
    rest = _metered_m3(days, start, date(today.year - 1, 12, 31))
    if rest is not None and calendar.isleap(today.year - 1):
        leap_day = date(today.year - 1, 2, 29)
        if start <= leap_day:
            # Nor has last year's in a year without one.
            rest -= days.get(leap_day, 0.0)
    if rest is not None and calendar.isleap(today.year) and tomorrow < date(today.year, 2, 29):
        # A 29 February still to come takes its water from last year's 28th,
        # which last year's window holds once, for this year's 28th.
        rest += days.get(date(today.year - 1, 2, 28), 0.0)
    return rest
