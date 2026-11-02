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

"""Long-term-statistics backfill for be_water_prices price sensors.

The History dashboard and the Energy dashboard's tariff overlays draw
their multi-day chart from the recorder's hourly long-term-statistics
table. A fresh config entry starts with that table empty, so the price
line only appears from the install moment forward.

This module backfills hourly rows from the configured start
(default Jan 1 of the current year) up to the last hour the recorder
has compiled, flat-lining at the currently-known tariff value for every
``MEASUREMENT``-class price sensor on the entry. The ``TOTAL`` YTD
sensors are written apart, from the meter's own history, by
:func:`async_backfill_year`.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime, timedelta
from functools import partial
from typing import Any, Literal

import voluptuous as vol
from homeassistant.const import CURRENCY_EURO, UnitOfVolume
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.service import async_register_admin_service
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .const import BACKFILL_YIELD_EVERY, DOMAIN
from .coordinator import WaterConfigEntry, entry_coordinator, jan_1_local, loaded_entries

_LOGGER = logging.getLogger(__name__)

SERVICE_BACKFILL_PRICES = "backfill_prices"
ATTR_ENTRY_ID = "entry_id"
ATTR_START_DATE = "start_date"
ATTR_CLEAR = "clear"

# Persisted on entry.data once a year has been backfilled, so the
# auto-once path skips on subsequent HA restarts. When the calendar year
# rolls over, the value will no longer match and the next setup extends
# the flat line into the new year automatically.
DATA_BACKFILL_YEAR = "backfill_year"

# Sensor keys eligible for the flat-line backfill: the card's rates and fee,
# read straight off the latest tariff. The year-to-date sensors come from
# the meter's history instead (_YEAR_KEYS), and the projected annual cost
# is computed from the configured consumption rather than read off the card.
_BACKFILL_KEYS: tuple[str, ...] = (
    "yearly_fee",
    "basis_rate",
    "comfort_rate",
    "sanering_rate",
    "all_in_basis",
)


SERVICE_BACKFILL_SCHEMA = vol.Schema(
    {
        vol.Optional(ATTR_ENTRY_ID): cv.string,
        vol.Optional(ATTR_START_DATE): cv.date,
        vol.Optional(ATTR_CLEAR, default=False): cv.boolean,
    }
)


async def async_backfill_prices(
    hass: HomeAssistant,
    entry: WaterConfigEntry,
    *,
    start: datetime,
    clear: bool = False,
) -> int:
    """Import flat-line hourly stats for this entry's MEASUREMENT price sensors.

    ``start`` is clamped to ``max(start, tariff.valid_from)`` so the
    backfill never invents prices for a period where no tariff snapshot
    existed. With ``clear=True`` the targeted ``statistic_id`` rows are
    wiped first; otherwise the import upserts on (statistic_id, start)
    and is safely idempotent.

    Returns the total number of rows handed to the recorder.
    """
    try:
        # mypy --strict flags these because the recorder module does not
        # re-export them via __all__; the same pattern as in coordinator.py.
        from homeassistant.components.recorder import (  # type: ignore[attr-defined]
            get_instance,
        )
        from homeassistant.components.recorder.models import (  # type: ignore[attr-defined,unused-ignore]
            StatisticData,
            StatisticMeanType,
            StatisticMetaData,
        )
        from homeassistant.components.recorder.statistics import (
            async_import_statistics,
        )
        from homeassistant.helpers.recorder import DATA_INSTANCE
    except ImportError:
        _LOGGER.warning("recorder unavailable; skipping price backfill")
        return 0

    # Skip cleanly when the recorder hasn't been initialised yet (e.g.
    # the pytest_homeassistant_custom_component harness without the
    # recorder fixture). Without this gate get_instance() throws
    # KeyError mid-import and propagates out of async_setup_entry.
    if DATA_INSTANCE not in hass.data:
        _LOGGER.debug("recorder not initialised; skipping price backfill")
        return 0

    from .sensor import SENSORS  # local import: sensor.py imports from coordinator

    coordinator = entry_coordinator(entry)
    if coordinator is None or coordinator.data is None:
        _LOGGER.debug("coordinator not ready for %s; skipping backfill", entry.entry_id)
        return 0

    data = coordinator.data
    tariff = data.tariff

    tz = dt_util.DEFAULT_TIME_ZONE
    valid_from_dt = datetime(
        tariff.valid_from.year,
        tariff.valid_from.month,
        tariff.valid_from.day,
        tzinfo=tz,
    )
    if start.tzinfo is None:
        start = start.replace(tzinfo=tz)
    if start < valid_from_dt:
        start = valid_from_dt

    start_utc = start.astimezone(dt_util.UTC).replace(minute=0, second=0, microsecond=0)
    end_utc = await _async_compiled_until(hass)
    # Clamp the far end to the snapshot too. Only the near end was
    # clamped, so on a year rollover against a page that has not
    # published the new year yet, last year's rates were flat-lined
    # across January as though they still applied.
    if tariff.valid_until is not None:
        # The card runs to the end of its last day, so the exclusive end
        # is the midnight after it. Clamping at 23:00 left the last hour
        # of every card unwritten.
        valid_until_dt = (
            datetime(
                tariff.valid_until.year,
                tariff.valid_until.month,
                tariff.valid_until.day,
                tzinfo=tz,
            )
            + timedelta(days=1)
        ).astimezone(dt_util.UTC)
        end_utc = min(end_utc, valid_until_dt.replace(minute=0, second=0, microsecond=0))
    if end_utc <= start_utc:
        # Nothing to write, and with clear=True nothing was cleared
        # either: say why, or the service call ends in silence.
        _LOGGER.info(
            "be_water_prices backfill for %s: start %s is not before the window's end %s; "
            "nothing written or cleared",
            entry.entry_id,
            start_utc.isoformat(),
            end_utc.isoformat(),
        )
        return 0

    ent_reg = er.async_get(hass)
    rows_total = 0

    cleared_done = False
    for desc in SENSORS:
        if desc.key not in _BACKFILL_KEYS:
            continue
        unique_id = f"{entry.entry_id}_{desc.key}"
        entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id is None:
            # Sensor not created for this entry (e.g. comfort_rate outside Flanders).
            continue
        value = desc.value_fn(data)
        if value is None:
            continue

        if clear:
            # ``async_clear_statistics`` is an instance method on the
            # Recorder (not exported from ``recorder.statistics``); call
            # it through get_instance(hass). Schedules the delete on the
            # recorder executor so we do not block the event loop.
            get_instance(hass).async_clear_statistics([entity_id])
            cleared_done = True

        bucket = start_utc
        rows: list[StatisticData] = []
        while bucket < end_utc:
            rows.append(
                StatisticData(
                    start=bucket,
                    mean=float(value),
                    min=float(value),
                    max=float(value),
                )
            )
            bucket += timedelta(hours=1)
            # A full year is ~8760 rows per sensor and five sensors run
            # through this loop, so building them in one go held the
            # event loop for a third of a second on this hardware. Give
            # it air every month's worth.
            if len(rows) % BACKFILL_YIELD_EVERY == 0:
                await asyncio.sleep(0)

        metadata = StatisticMetaData(
            statistic_id=entity_id,
            source="recorder",
            name=None,
            unit_of_measurement=desc.native_unit_of_measurement,
            has_mean=True,
            mean_type=StatisticMeanType.ARITHMETIC,
            has_sum=False,
            unit_class=None,
        )
        async_import_statistics(hass, metadata, rows)
        rows_total += len(rows)

    _LOGGER.info(
        "be_water_prices backfill for %s: %d rows%s",
        entry.entry_id,
        rows_total,
        " (cleared first)" if cleared_done else "",
    )
    return rows_total


def _newest_statistics_run(hass: HomeAssistant) -> datetime | None:
    """Start of the newest period the recorder has compiled, recorder thread only."""
    from homeassistant.components.recorder.db_schema import StatisticsRuns
    from homeassistant.components.recorder.models import process_timestamp
    from homeassistant.helpers.recorder import session_scope
    from sqlalchemy import func

    with session_scope(hass=hass, read_only=True) as session:
        start: datetime | None = session.query(func.max(StatisticsRuns.start)).scalar()
    return process_timestamp(start)


async def _async_compiled_until(hass: HomeAssistant) -> datetime:
    """End of the newest hour the recorder has compiled long-term rows for.

    The recorder writes an hour's row when it compiles that hour's last
    five-minute period, ten seconds after the hour, and longer after a
    restart, since the catch-up waits for Home Assistant to finish
    starting. It inserts those rows without an upsert, so a row this
    import got in first makes the insert fail, and the recorder then
    rolls back that whole period for every entity along with its run
    marker, and never compiles it again. Each compiled period leaves a
    StatisticsRuns marker, and the catch-up only resumes after the
    newest one, so every hour that ends by the end of that period is
    settled and safe to write.
    """
    from homeassistant.components.recorder import (  # type: ignore[attr-defined]
        get_instance,
    )
    from homeassistant.components.recorder.db_schema import StatisticsShortTerm

    hour = dt_util.utcnow().replace(minute=0, second=0, microsecond=0)
    try:
        newest = await get_instance(hass).async_add_executor_job(_newest_statistics_run, hass)
    except Exception as err:
        _LOGGER.debug("could not read the recorder's last statistics run: %s", err)
        newest = None
    if newest is None:
        # Without the marker the best guess is that the recorder is on
        # time, which leaves only the hour that just ended uncompiled.
        return hour - timedelta(hours=1)
    end: datetime = newest + StatisticsShortTerm.duration
    return min(hour, end.replace(minute=0, second=0, microsecond=0))


async def _async_has_statistics_before(
    hass: HomeAssistant, entity_id: str, cutoff: datetime
) -> bool:
    """Whether ``entity_id`` carries long-term rows older than ``cutoff``."""
    from homeassistant.components.recorder import (  # type: ignore[attr-defined]
        get_instance,
    )
    from homeassistant.components.recorder.statistics import (
        statistics_during_period,
    )

    instance = get_instance(hass)
    try:
        stats = await instance.async_add_executor_job(
            statistics_during_period,
            hass,
            datetime(1970, 1, 1, tzinfo=dt_util.UTC),
            cutoff,
            {entity_id},
            "month",
            None,
            {"mean"},
        )
    except Exception as err:
        # Any failure here means we cannot prove the history is empty, so
        # keep the rows rather than risk deleting years of real data.
        _LOGGER.debug("could not read history for %s: %s", entity_id, err)
        return True
    return bool(stats.get(entity_id))


async def _async_clear_orphan_backfill_keys(hass: HomeAssistant, entry: WaterConfigEntry) -> None:
    """Clear LTS rows for backfill keys the *current* operator's tariff
    does not produce (orphan rows left over by the previous operator).

    The recorder only offers a whole-statistic delete, so this refuses to
    run for any key that carries rows from an earlier year: those are a
    genuine record of what the household paid under the old operator and
    losing them is worse than leaving a stale line behind. Only a key
    whose entire history sits inside the current year -- the flat line the
    previous operator's backfill just wrote -- is cleared.
    """
    # mypy --strict flags get_instance because the recorder module does
    # not re-export it via __all__; same pattern as async_backfill_prices.
    from homeassistant.components.recorder import (  # type: ignore[attr-defined]
        get_instance,
    )
    from homeassistant.helpers.recorder import DATA_INSTANCE

    from .sensor import SENSORS  # local import: sensor.py imports from coordinator

    # Same guard pattern async_backfill_prices uses: skip cleanly when
    # the recorder is not yet up (cold-boot, test harness without the
    # recorder fixture). Without this the get_instance() call below
    # raises KeyError mid-setup, escaping async_setup_entry and
    # leaving the OptionsFlow reload listener unregistered.
    if DATA_INSTANCE not in hass.data:
        return

    coordinator = entry_coordinator(entry)
    if coordinator is None or coordinator.data is None:
        return
    ent_reg = er.async_get(hass)
    recorder = get_instance(hass)
    year_start = dt_util.start_of_local_day(
        datetime(dt_util.now().year, 1, 1, tzinfo=dt_util.DEFAULT_TIME_ZONE)
    ).astimezone(dt_util.UTC)
    for desc in SENSORS:
        if desc.key not in _BACKFILL_KEYS:
            continue
        # Current operator's tariff doesn't produce this metric ->
        # any LTS rows are orphans from the previous operator.
        if desc.value_fn(coordinator.data) is not None:
            continue
        unique_id = f"{entry.entry_id}_{desc.key}"
        entity_id = ent_reg.async_get_entity_id("sensor", DOMAIN, unique_id)
        if entity_id is None:
            continue
        if await _async_has_statistics_before(hass, entity_id, year_start):
            _LOGGER.debug(
                "keeping %s: it holds history from before %s", entity_id, year_start.date()
            )
            continue
        recorder.async_clear_statistics([entity_id])


async def async_maybe_backfill_once(hass: HomeAssistant, entry: WaterConfigEntry) -> None:
    """Run the auto-once backfill, gated by ``(year, utility)``.

    The flag records the calendar year the entry was last backfilled, the
    utility it was backfilled for, and the year of the card the rates came
    off. When the year rolls over, OR the user reconfigures to a different
    operator (postcode-resolver move, manual override, split-postcode
    choice), OR the operator finally publishes the new year's card, the
    gate trips again and the fresh flat line replaces the old one rather
    than mixing rates inside the same calendar year.
    """
    from .const import CONF_COMMUNE, CONF_UTILITY

    coordinator_now = entry_coordinator(entry)
    current_year = dt_util.now().year
    current_utility = entry.data.get(CONF_UTILITY)
    # The card's own year is part of the gate. Publishers run late, and
    # carry_prior_year_card lets last year's card stand until 31 March, so
    # in January the flat line is written at last year's rate and the gate
    # was stamped for the calendar year: when the real card arrived in
    # March the gate already matched and nothing ever corrected the line.
    card_year = (
        coordinator_now.data.tariff.valid_from.year
        if coordinator_now is not None and coordinator_now.data is not None
        else None
    )
    # The commune belongs here too. Several of the sensors this writes are
    # made of it: the gemeentelijke saneringsbijdrage is a commune's own
    # number, so sanering_rate and all_in_basis move with it. A household
    # that installs, gets the operator default flat-lined, and then picks
    # its own commune kept the default's rates in History for the rest of
    # the year, 0.59 EUR/m3 out on De Watergroep at Overijse and 0.66 on
    # Water-link at Edegem. Appended rather than inserted: the orphan
    # cleanup below reads the utility out of field one.
    current_commune = entry.options.get(CONF_COMMUNE)
    current_gate = f"{current_year}:{current_utility}:{card_year}:{current_commune}"
    previous_gate = entry.data.get(DATA_BACKFILL_YEAR)
    if previous_gate == current_gate:
        return

    # On operator change, any LTS rows the previous operator wrote for
    # sensor keys that the new operator does not produce (e.g.
    # comfort_rate after Flanders -> Wallonia) become orphans: the
    # entity may be removed from the registry but its historical rows
    # persist forever because the new backfill loop skips them. Detect
    # the orphans by checking each backfill key's value_fn against the
    # current tariff and clear the stale rows. This runs before the
    # stale-snapshot deferral below: the cleanup finds the rows through
    # the registry entry that setup removes right after this, so a
    # deferred cleanup never got a second chance.
    if isinstance(previous_gate, str) and ":" in previous_gate:
        # "<year>:<utility>" first, then the card year joined it, then the
        # commune. A utility id never contains a colon and every term since
        # has been appended, so the second field is the utility whichever
        # release wrote the string.
        previous_utility = previous_gate.split(":")[1]
        if previous_utility != str(current_utility):
            await _async_clear_orphan_backfill_keys(hass, entry)

    coordinator = entry_coordinator(entry)
    if coordinator is not None and coordinator.data is not None and coordinator.data.snapshot_stale:
        # The snapshot cannot cover the window we are about to fill, and
        # stamping the gate anyway would lock in whatever we wrote: the
        # gate is keyed on (year, utility), so a later run with a fresh
        # tariff would find it already satisfied and never correct the
        # line. Leave it unstamped and retry on the next setup.
        _LOGGER.debug("snapshot is stale; deferring the auto-once backfill for %s", entry.entry_id)
        return

    # ``clear=False`` is sufficient: async_import_statistics overwrites
    # rows at matching (statistic_id, bucket_start) timestamps, so
    # re-running for the same year just replaces the old operator's
    # flat-line for the year so far without wiping older historical rows
    # (prior years, manual annotations). The flatline replacement is
    # what we actually want on an operator switch.
    rows = await async_backfill_prices(hass, entry, start=jan_1_local(), clear=False)
    if rows <= 0:
        return

    hass.config_entries.async_update_entry(
        entry,
        data={**entry.data, DATA_BACKFILL_YEAR: current_gate},
    )


@callback
def async_register_services(hass: HomeAssistant) -> None:
    """Register the ``backfill_prices`` service, once for the integration.

    For administrators only, as Home Assistant registers its own statistics
    services: ``clear`` deletes a statistic's whole history, which nothing
    gives back, and any other user could call it. A call with no user, as
    an automation's is, still goes through.
    """

    async def _handle_backfill(call: ServiceCall) -> None:
        target_id = call.data.get(ATTR_ENTRY_ID)
        clear = bool(call.data.get(ATTR_CLEAR, False))
        # clear=True wipes the long-term-statistics rows for every
        # backfill key on every targeted entry. Without an explicit
        # entry_id the operation cascades across the user's entire
        # be_water_prices install -- a footgun that's irreversible.
        # Refuse the blanket combination loudly so the user has to
        # opt in per-entry.
        if clear and not target_id:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="clear_needs_entry"
            )
        targets = loaded_entries(hass, target_id or None)

        start_date = call.data.get(ATTR_START_DATE)
        if start_date is not None:
            start_dt = datetime(
                start_date.year,
                start_date.month,
                start_date.day,
                tzinfo=dt_util.DEFAULT_TIME_ZONE,
            )
        else:
            start_dt = jan_1_local()

        for entry in targets:
            await async_backfill_prices(hass, entry, start=start_dt, clear=clear)

    async_register_admin_service(
        hass,
        DOMAIN,
        SERVICE_BACKFILL_PRICES,
        _handle_backfill,
        schema=SERVICE_BACKFILL_SCHEMA,
    )


# The sensors whose year the meter's history fills: each with its place in
# the (m3, cost) pairs async_year_by_hour gives, and the unit those are in.
_YEAR_KEYS: tuple[tuple[str, int, str], ...] = (
    ("current_year_cost", 1, CURRENCY_EURO),
    ("ytd_consumption", 0, UnitOfVolume.CUBIC_METERS),
)


async def async_backfill_year(hass: HomeAssistant, entry: WaterConfigEntry) -> int | None:
    """Write the year-to-date sensors' hourly rows from 1 January.

    Home Assistant counts a new total sensor's statistics from its first
    value, so a monthly graph of the year's cost on a fresh entry starts at
    the install. The rows are the year's m³ and cost at the end of each
    hour, as the meter's history gives them (``async_year_by_hour``), the
    last of them the figures the sensors showed then.

    Only a sensor whose cycle opens on 1 January and whose statistics hold
    no row for that hour is written: one that has run since then, or was
    restarted on a meter swap since, already holds its year. Returns the
    rows written, or None when it could not tell or write, or a sensor has
    no live statistics yet to join its year to, to be asked again.
    """
    from homeassistant.helpers.recorder import DATA_INSTANCE

    if DATA_INSTANCE not in hass.data:
        return None
    coordinator = entry_coordinator(entry)
    if coordinator is None or coordinator.data is None:
        return None
    reset = dt_util.as_utc(jan_1_local())
    end = await _async_compiled_until(hass)
    hours: list[datetime] = []
    hour = reset
    while hour < end:
        hours.append(hour)
        hour += timedelta(hours=1)
    if not hours:
        return 0
    ent_reg = er.async_get(hass)
    wanted: list[tuple[int, str, str, str | None, Callable[[float], float]]] = []
    for key, column, ours in _YEAR_KEYS:
        sid = ent_reg.async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{key}")
        state = None if sid is None else hass.states.get(sid)
        if sid is None or state is None:
            continue
        opened = dt_util.parse_datetime(str(state.attributes.get("last_reset")))
        if opened is None or opened != reset:
            continue
        first = await _read(hass, sid, reset, reset + timedelta(hours=1), "hour", None)
        if first is None:
            return None
        if first:
            continue
        kept = await _kept_unit(hass, sid, ours)
        if kept is None:
            return None
        unit, unit_class = kept
        convert = None if unit is None else _converter(ours, unit)
        if unit is None or convert is None:
            _LOGGER.debug("%s keeps its statistics in %r; its year is not written", sid, unit)
            continue
        wanted.append((column, sid, unit, unit_class, convert))
    if not wanted:
        return 0
    year = await coordinator.async_year_by_hour(hours)
    if year is None:
        return None
    written = 0
    for column, sid, unit, unit_class, convert in wanted:
        totals: list[tuple[datetime, float]] = []
        for hour, figures in zip(hours, year, strict=True):
            value = figures[column]
            if value is None:
                return None
            totals.append((hour, convert(value)))
        if not await _import_year_rows(hass, sid, unit, unit_class, totals, reset):
            return None
        written += len(totals)
    _LOGGER.debug("be_water_prices year backfill for %s: %d rows", entry.entry_id, written)
    return written


async def _kept_unit(
    hass: HomeAssistant, sid: str, ours: str
) -> tuple[str | None, str | None] | None:
    """The unit and unit class ``sid``'s statistics are kept in, ``ours`` when
    it has none yet; None when the recorder did not answer.

    Home Assistant keeps a statistic in the unit the sensor showed when it
    was first compiled, and answers a read in the unit the sensor shows now,
    the reset times converted with the figures. Read and written in the kept
    unit, nothing is converted: read in litres, a reset came back as a date
    some fifty thousand years ahead, and a row written in cubic metres would
    have turned every litre kept before it into a cubic metre.
    """
    from homeassistant.components.recorder import (  # type: ignore[attr-defined]
        get_instance,
    )
    from homeassistant.components.recorder.statistics import get_metadata

    try:
        found = await get_instance(hass).async_add_executor_job(
            partial(get_metadata, hass, statistic_ids={sid})
        )
    except Exception as err:
        _LOGGER.debug("could not read the unit of %s: %s", sid, err)
        return None
    if sid not in found:
        volume = ours == UnitOfVolume.CUBIC_METERS
        return ours, VolumeConverter.UNIT_CLASS if volume else None
    metadata = found[sid][1]
    return metadata["unit_of_measurement"], metadata["unit_class"]


def _converter(ours: str, unit: str) -> Callable[[float], float] | None:
    """How a figure in ``ours`` is written in ``unit``, None when it cannot be."""
    if unit == ours:
        return float
    if ours == UnitOfVolume.CUBIC_METERS and unit in VolumeConverter.VALID_UNITS:
        return VolumeConverter.converter_factory(ours, unit)
    return None


async def _import_year_rows(
    hass: HomeAssistant,
    sid: str,
    unit: str,
    unit_class: str | None,
    totals: list[tuple[datetime, float]],
    reset: datetime,
) -> bool:
    """Import ``totals`` as the year of the recorded total, all of it in
    ``unit``, the one the statistic is kept in.

    The rows continue the total recorded before the year: the live compile
    carries its total on across a new cycle rather than restart it, so on
    an entry that ran through last year the row before the window holds
    last year's whole figure. They are scaled to end on the figure the
    sensor itself showed at the end of the last of them, so the hours after
    them count only what the sensor added since.

    The rows the live compile wrote are moved onto the imported total by
    the recorder: an adjustment queued ahead of the import shifts the total
    of every row from the reset by what lies between the two, five-minute
    and hourly alike, and the import then writes its hours over the live
    ones. A row compiled before the adjustment reaches the queue is shifted
    with the rest, and one compiled after it carries on from a shifted one.
    Read and written back from here, the rows the recorder compiled between
    the read and the import stayed on the old total, and the next hour fell
    by the whole year.

    False, with nothing imported, when the recorder did not answer one of
    the reads, or holds no live row of the sensor yet to join the year to:
    a new sensor's first is compiled within five minutes.
    """
    from homeassistant.components.recorder import (  # type: ignore[attr-defined]
        get_instance,
    )
    from homeassistant.components.recorder.db_schema import Statistics
    from homeassistant.components.recorder.models import (  # type: ignore[attr-defined,unused-ignore]
        StatisticData,
        StatisticMeanType,
        StatisticMetaData,
    )

    units = None if unit_class is None else {unit_class: unit}
    base = await _total_before(hass, sid, reset, units)
    if base is None:
        return False
    anchor = await _live_anchor(hass, sid, totals[-1][0] + timedelta(hours=1), units)
    if anchor is None:
        return False
    state, total = anchor
    last = totals[-1][1]
    scale = state / last if last else 1.0
    rows: list[StatisticData] = []
    for hour, value in totals:
        rows.append(
            StatisticData(
                start=hour,
                state=round(value * scale, 4),
                sum=round(base + value * scale, 4),
                last_reset=reset,
            )
        )
        if len(rows) % BACKFILL_YIELD_EVERY == 0:
            await asyncio.sleep(0)
    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=None,
        source="recorder",
        statistic_id=sid,
        unit_class=unit_class,
        unit_of_measurement=unit,
    )
    instance = get_instance(hass)
    # Within a cycle the live compile adds to the total exactly what the
    # state moves by, so every live row stands the same distance above or
    # below its state, and one shift puts them all on the imported total.
    # None is needed once they are there, as after a run cut off between
    # the two tasks.
    shift = round(base - (total - state), 4)
    if shift:
        instance.async_adjust_statistics(sid, reset, shift, unit)
    instance.async_import_statistics(metadata, rows, Statistics)
    return True


async def _live_anchor(
    hass: HomeAssistant, sid: str, end: datetime, units: dict[str, str] | None
) -> tuple[float, float] | None:
    """The state and the total of the live row the year joins at ``end``:
    the sensor's hour before it, or, for a sensor that started after it,
    its first five minutes. None when the recorder did not answer, or holds
    neither."""
    rows = await _read(hass, sid, end - timedelta(hours=1), end, "hour", units)
    if rows is not None and not rows:
        rows = await _read(hass, sid, end, None, "5minute", units)
    for row in rows or []:
        if row.get("state") is not None and row.get("sum") is not None:
            return float(row["state"]), float(row["sum"])
    return None


async def _read(
    hass: HomeAssistant,
    sid: str,
    start: datetime,
    end: datetime | None,
    period: Literal["5minute", "hour", "month"],
    units: dict[str, str] | None,
) -> list[Any] | None:
    """The rows of ``period`` from ``start`` to ``end``, or to the newest
    when None, in ``units`` or the unit the sensor shows; None when the
    recorder did not answer."""
    from homeassistant.components.recorder import (  # type: ignore[attr-defined]
        get_instance,
    )
    from homeassistant.components.recorder.statistics import (
        statistics_during_period,
    )

    try:
        got = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start,
            end,
            {sid},
            period,
            units,
            {"sum", "state"},
        )
    except Exception as err:
        _LOGGER.debug("could not read the statistics of %s: %s", sid, err)
        return None
    return list(got.get(sid, []))


async def _total_before(
    hass: HomeAssistant, sid: str, reset: datetime, units: dict[str, str] | None
) -> float | None:
    """What the recorded total holds before ``reset``, the whole of an
    earlier cycle: 0.0 with nothing recorded, None when the recorder did
    not answer.

    The last row is found by month, then by hour within that month, so a
    long gap costs two short reads. A read by month runs on to the end of
    the month ``reset`` opens, since it falls on the 1st, so a month from
    ``reset`` on is not one before it.
    """
    months = await _read(hass, sid, reset - timedelta(days=400), reset, "month", units)
    if months is None:
        return None
    months = [row for row in months if row["start"] < reset.timestamp()]
    if not months:
        return 0.0
    hours = await _read(
        hass, sid, datetime.fromtimestamp(months[-1]["start"], dt_util.UTC), reset, "hour", units
    )
    if hours is None:
        return None
    if not hours or hours[-1].get("sum") is None:
        return 0.0
    return float(hours[-1]["sum"])


async def async_maybe_backfill_year(hass: HomeAssistant, entry: WaterConfigEntry) -> None:
    """Run :func:`async_backfill_year` until it has settled the year.

    Asked on every update until it has, but at most once an hour while the
    recorder cannot tell or a sensor has compiled no statistics of its own
    yet, and not at all once it has for the calendar year.
    """
    coordinator = entry_coordinator(entry)
    data = None if coordinator is None else coordinator.data
    if (
        coordinator is None
        or data is None
        or data.ytd_consumption_m3 is None
        or data.current_year_cost_eur is None
    ):
        return
    now = dt_util.utcnow()
    year = dt_util.now().year
    if coordinator.year_backfilled == year:
        return
    tried = coordinator.year_backfill_tried
    if tried is not None and now - tried < timedelta(hours=1):
        return
    coordinator.year_backfill_tried = now
    try:
        written = await async_backfill_year(hass, entry)
    except Exception:
        _LOGGER.exception("year-to-date history backfill failed")
        return
    if written is not None:
        coordinator.year_backfilled = year
