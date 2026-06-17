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

"""Daily-refresh coordinator for be_water_prices."""

from __future__ import annotations

import asyncio
import calendar
import logging
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    ATTR_UNIT_OF_MEASUREMENT,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    UnitOfVolume,
)
from homeassistant.core import Event, EventStateChangedData, HomeAssistant, State, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_track_state_change_event
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import VolumeConverter

from .const import (
    CONF_COMMUNE,
    CONF_COMMUNE_LABEL,
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_PERSONS,
    CONF_SOCIAL_TARIFF,
    CONF_UTILITY,
    CONF_WATER_METER_SENSOR,
    DEFAULT_CONSUMPTION_M3,
    DEFAULT_PERSONS,
    DOMAIN,
    SNAPSHOT_STALE_AFTER_DAYS,
    UPDATE_INTERVAL_HOURS,
)
from .pricing import compute_annual_cost, compute_ytd_cost
from .providers import ExtractorError, WaterTariff, get
from .providers.base import WaterExtractor, relabel_with_human_commune

_LOGGER = logging.getLogger(__name__)

# Bumped only if the persisted YTD cycle dict changes shape incompatibly.
_YTD_STORE_VERSION = 1


def utility_device_info(coordinator: WaterCoordinator) -> DeviceInfo:
    """Build the HA DeviceInfo block shared by every entity on this entry.

    Anchors every sensor onto one per-entry device identified by
    ``(DOMAIN, entry.entry_id)`` so the integration's *Devices* tab
    shows a single card per configured utility instead of orphan
    entities. ``manufacturer`` carries the utility label, ``model``
    carries the region, and ``configuration_url`` deep-links to the
    utility's tariff publication for one-click verification.
    """
    utility_id = str(coordinator.entry.data.get(CONF_UTILITY, ""))
    try:
        extractor = get(utility_id)
        manufacturer = extractor.label
        model = extractor.region.title()
    except ExtractorError:
        manufacturer = utility_id or "Belgian Water"
        model = ""
    source_url: str | None = None
    if coordinator.data is not None:
        source_url = coordinator.data.tariff.source_url
    return DeviceInfo(
        identifiers={(DOMAIN, coordinator.entry.entry_id)},
        name=coordinator.entry.title,
        manufacturer=manufacturer,
        model=model or None,
        configuration_url=source_url,
    )


@dataclass
class CoordinatorData:
    tariff: WaterTariff
    fetched_at: datetime
    snapshot_age_hours: float
    snapshot_stale: bool
    last_error: str = ""
    projected_annual_cost_eur: float | None = None
    current_year_cost_eur: float | None = None
    ytd_consumption_m3: float | None = None


class WaterCoordinator(DataUpdateCoordinator[CoordinatorData]):
    """Fetches the configured utility's tariff once a day."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        utility_id = entry.data[CONF_UTILITY]
        self._extractor: WaterExtractor = get(utility_id)
        self._last_good: CoordinatorData | None = None
        # Resolved meter entity and the meter's reading back at Jan 1,
        # captured on each daily tick so meter state-change events can
        # recompute YTD live without re-querying the recorder.
        self._meter_entity_id: str | None = None
        # The year-to-date cycle anchor, persisted across restarts via
        # _store. ``_ytd_baseline_m3`` is the meter's cumulative reading at
        # the cycle start (Jan 1, or the moment of a meter swap); YTD is
        # ``live - baseline`` from there on. Restoring it means an HA
        # restart / reload no longer re-derives the baseline from the
        # trailing recorder figure, which used to snap the published YTD
        # downward.
        self._ytd_baseline_m3: float | None = None
        # Calendar year the baseline belongs to, so a live meter event
        # that fires after the Jan 1 rollover (but before the next daily
        # tick re-anchors) does not report the stale prior-year baseline.
        self._ytd_baseline_year: int | None = None
        # Meter the persisted cycle belongs to: if the user repoints the
        # water-meter option at a different entity, the stored baseline is
        # meaningless and must be re-bootstrapped from the recorder.
        self._ytd_meter_id: str | None = None
        # Calendar year the last recorder YTD figure belongs to. Lets the
        # meter-recovery branch tell a current-year figure from a stale
        # prior-year one when the meter was down across the rollover.
        self._ytd_recorder_year: int | None = None
        # Set when the cycle anchor changes; _compute_ytd flushes it to the
        # Store. The live meter-event path only marks it -- the next daily
        # tick persists, and a swap / rollover self-heals on restart anyway.
        self._cycle_dirty = False
        self._store: Store[dict[str, Any]] = Store(
            hass, _YTD_STORE_VERSION, f"{DOMAIN}.{entry.entry_id}.ytd"
        )
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
            update_interval=timedelta(hours=UPDATE_INTERVAL_HOURS),
        )

    async def _async_update_data(self) -> CoordinatorData:
        session = async_get_clientsession(self.hass)
        commune = self.entry.options.get(CONF_COMMUNE)
        try:
            if commune and self._extractor.fetch_for_commune is not None:
                tariff = await self._extractor.fetch_for_commune(session, str(commune))
                tariff = relabel_with_human_commune(
                    tariff,
                    commune_id=str(commune),
                    commune_label=self.entry.options.get(CONF_COMMUNE_LABEL),
                )
            else:
                tariff = await self._extractor.fetch(session)
        except Exception as err:
            # Catch broader than ExtractorError so a future extractor
            # that forgets to wrap (asyncio.TimeoutError, ssl.SSLError,
            # KeyError on a malformed response) still falls through to
            # the cached-snapshot path. Two exception families must
            # still propagate:
            #   - asyncio.CancelledError: HA cancels coordinator tasks
            #     on shutdown / reload, that signal has to reach the
            #     event loop unchanged.
            #   - ConfigEntryAuthFailed / ConfigEntryError: HA's
            #     DataUpdateCoordinator runs the reauth flow on these,
            #     swallowing them here would silently break future
            #     credentialed-tariff extractors.
            from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError

            if isinstance(err, asyncio.CancelledError | ConfigEntryAuthFailed | ConfigEntryError):
                raise
            # On fetch failure keep serving the last good snapshot
            # rather than blanking every sensor; snapshot_age_hours and
            # last_error are surfaced as attributes so dashboards can
            # flag the issue.
            if self._last_good is not None:
                _LOGGER.warning(
                    "water tariff fetch failed (%s), serving cached: %s",
                    type(err).__name__,
                    err,
                )
                stale = self._is_stale(self._last_good.tariff, self._last_good.fetched_at)
                ytd_m3, ytd_cost = await self._compute_ytd(self._last_good.tariff)
                cached = CoordinatorData(
                    tariff=self._last_good.tariff,
                    fetched_at=self._last_good.fetched_at,
                    snapshot_age_hours=self._age_hours(self._last_good.fetched_at),
                    snapshot_stale=stale,
                    last_error=str(err),
                    projected_annual_cost_eur=self._project_cost(self._last_good.tariff),
                    current_year_cost_eur=ytd_cost,
                    ytd_consumption_m3=ytd_m3,
                )
                self._sync_repair_issue(cached)
                return cached
            raise UpdateFailed(str(err)) from err

        now = datetime.now(UTC)
        ytd_m3, ytd_cost = await self._compute_ytd(tariff)
        data = CoordinatorData(
            tariff=tariff,
            fetched_at=now,
            snapshot_age_hours=0.0,
            snapshot_stale=self._is_stale(tariff, now),
            projected_annual_cost_eur=self._project_cost(tariff),
            current_year_cost_eur=ytd_cost,
            ytd_consumption_m3=ytd_m3,
        )
        self._last_good = data
        self._sync_repair_issue(data)
        return data

    @staticmethod
    def _age_hours(fetched_at: datetime) -> float:
        # Use abs() so a future-dated fetched_at (clock skew, container
        # restored from a future-dated snapshot) surfaces a positive
        # age on the sensor attribute -- masking it with a 0 clamp
        # would hide the symptom while _is_stale fired the Repair.
        return abs((datetime.now(UTC) - fetched_at).total_seconds()) / 3600.0

    @staticmethod
    def _is_stale(tariff: WaterTariff, fetched_at: datetime) -> bool:
        delta = datetime.now(UTC) - fetched_at
        # Future-dated cache is always stale: a snapshot 'from the
        # future' is suspect by definition (clock skew, NTP jump,
        # container restored). Surfacing it as stale lets the
        # snapshot_stale Repair fire on day one instead of waiting
        # for real time to catch up over weeks / months.
        if delta.total_seconds() < 0:
            return True
        age_days = delta.days
        if age_days > SNAPSHOT_STALE_AFTER_DAYS:
            return True
        return tariff.valid_until is not None and tariff.valid_until < dt_util.now().date()

    @property
    def stale_issue_id(self) -> str:
        """Stable Repairs issue id for this entry's stale-snapshot warning."""
        return f"snapshot_stale_{self.entry.entry_id}"

    def _sync_repair_issue(self, data: CoordinatorData) -> None:
        """Create or clear the stale-snapshot Repair issue for this entry.

        Surfaces in Settings -> Repairs as a warning card when the
        snapshot has not refreshed for SNAPSHOT_STALE_AFTER_DAYS days
        or the parsed valid_until is in the past. Auto-clears on the
        next successful, fresh fetch.
        """
        if data.snapshot_stale:
            ir.async_create_issue(
                self.hass,
                DOMAIN,
                self.stale_issue_id,
                is_fixable=True,
                is_persistent=False,
                severity=ir.IssueSeverity.WARNING,
                translation_key="snapshot_stale",
                translation_placeholders={
                    "utility": self._extractor.label,
                    "age_days": f"{int(data.snapshot_age_hours // 24)}",
                    "valid_until": (
                        data.tariff.valid_until.isoformat()
                        if data.tariff.valid_until is not None
                        else "unknown"
                    ),
                    "last_error": data.last_error or "(none)",
                },
                # Carry the entry id so the Repairs UI flow handler in
                # repairs.py knows which coordinator to refresh when the
                # user clicks the fix button.
                data={"entry_id": self.entry.entry_id},
            )
        else:
            ir.async_delete_issue(self.hass, DOMAIN, self.stale_issue_id)

    def _project_cost(self, tariff: WaterTariff) -> float | None:
        opts = self.entry.options
        consumption = float(opts.get(CONF_CONSUMPTION_M3_PER_YEAR, DEFAULT_CONSUMPTION_M3))
        persons = int(opts.get(CONF_PERSONS, DEFAULT_PERSONS))
        social = bool(opts.get(CONF_SOCIAL_TARIFF, False))
        return compute_annual_cost(tariff, consumption, persons, social_tariff=social)

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
          3. ``None`` -- YTD entities stay unavailable.
        """
        explicit = self.entry.options.get(CONF_WATER_METER_SENSOR)
        if explicit:
            return str(explicit)
        return await _discover_energy_water_meter(self.hass)

    async def async_load_ytd_state(self) -> None:
        """Restore the persisted YTD cycle anchor before the first refresh.

        Restoring the Jan 1 baseline across restarts is what keeps the
        running cost monotonic: without it every restart re-derived the
        baseline from the recorder's trailing daily total and snapped the
        published YTD downward.
        """
        data = await self._store.async_load()
        if not data:
            return
        self._ytd_meter_id = data.get("meter")
        self._ytd_baseline_year = data.get("year")
        self._ytd_baseline_m3 = data.get("baseline_m3")

    def _cycle_state(self) -> dict[str, Any]:
        return {
            "meter": self._ytd_meter_id,
            "year": self._ytd_baseline_year,
            "baseline_m3": self._ytd_baseline_m3,
        }

    def _reset_cycle(self) -> None:
        self._ytd_baseline_m3 = None
        self._ytd_baseline_year = None

    def _set_cycle(self, year: int, baseline: float) -> None:
        self._ytd_baseline_year = year
        self._ytd_baseline_m3 = baseline
        self._cycle_dirty = True

    def _apply_cycle(self, live: float) -> float:
        """Return YTD m³ for ``live`` and re-anchor the cycle on a reset.

        Monotonic within a cycle: YTD is ``max(0, live - baseline)`` and the
        baseline only re-anchors (YTD -> ~0) on a genuine reset -- the Jan 1
        rollover, or a meter swap where the cumulative reading drops below
        its own cycle anchor (a working meter never reads below its past).
        """
        now_year = dt_util.now().year
        if self._ytd_baseline_m3 is None or self._ytd_baseline_year != now_year:
            self._set_cycle(now_year, live)
            return 0.0
        if live < self._ytd_baseline_m3:
            self._set_cycle(now_year, live)
            return 0.0
        return max(0.0, live - self._ytd_baseline_m3)

    async def _compute_ytd(self, tariff: WaterTariff) -> tuple[float | None, float | None]:
        """Compute YTD m³ and cost, anchoring the cycle baseline.

        The baseline is restored from the Store across restarts, so this no
        longer re-derives it from the recorder on every tick. The recorder
        is consulted only to *bootstrap* the baseline the first time (or
        after a year rollover / meter change), placing the Jan 1 reading as
        ``live - recorder_ytd``. From there YTD tracks the live meter via
        :meth:`_apply_cycle`, which keeps it monotonic.

        Returns ``(ytd_m3, ytd_cost_eur)``; both ``None`` when no meter is
        configured or there is no usable reading to anchor or serve.
        """
        meter = await self.async_resolve_meter_entity()
        self._meter_entity_id = meter
        if not meter:
            return None, None
        if self._ytd_meter_id != meter:
            # Option repointed at a different meter -> the stored baseline
            # belongs to the old one; drop it and re-bootstrap.
            self._reset_cycle()
            self._ytd_meter_id = meter
            self._cycle_dirty = True
        now_year = dt_util.now().year
        live = _state_volume_m3(self.hass.states.get(meter))
        need_bootstrap = self._ytd_baseline_m3 is None or self._ytd_baseline_year != now_year
        recorder_ytd: float | None = None
        if need_bootstrap or live is None:
            today = dt_util.now().date()
            jan1 = date(now_year, 1, 1)
            recorder_ytd = await _recorder_ytd_m3(self.hass, meter, jan1, today)
            if recorder_ytd is not None:
                self._ytd_recorder_year = now_year
        if live is not None and need_bootstrap:
            # baseline == reading at Jan 1, reconstructed from the recorder's
            # "consumption since Jan 1"; fall back to the current reading
            # (YTD ~0) when the recorder has nothing to place it.
            baseline = (live - recorder_ytd) if recorder_ytd is not None else live
            self._set_cycle(now_year, baseline)
        if self._ytd_baseline_m3 is not None and live is not None:
            ytd_m3 = self._apply_cycle(live)
            if self._cycle_dirty:
                self._cycle_dirty = False
                await self._store.async_save(self._cycle_state())
            return ytd_m3, self._ytd_cost_from_m3(tariff, ytd_m3)
        # Meter unavailable right now: serve the recorder's daily figure
        # read-only (no anchoring) so the sensor is not blanked for a day.
        if recorder_ytd is not None:
            return recorder_ytd, self._ytd_cost_from_m3(tariff, recorder_ytd)
        return None, None

    def _ytd_cost_from_m3(self, tariff: WaterTariff, ytd_m3: float) -> float | None:
        """Apply the pro-rated YTD bill math to a year-to-date m³ figure.

        Shared by the daily recorder path (:meth:`_compute_ytd`) and the
        live meter-event path (:meth:`_recompute_live_ytd`) so both use
        identical fee pro-rating and regional math.
        """
        today = dt_util.now().date()
        jan1 = date(today.year, 1, 1)
        elapsed = (today - jan1).days + 1  # include today
        days_in_year = 366 if calendar.isleap(today.year) else 365
        fraction = elapsed / days_in_year
        persons = int(self.entry.options.get(CONF_PERSONS, DEFAULT_PERSONS))
        social = bool(self.entry.options.get(CONF_SOCIAL_TARIFF, False))
        return compute_ytd_cost(tariff, ytd_m3, persons, fraction, social_tariff=social)

    @callback
    def async_setup_live_tracking(self) -> None:
        """Subscribe to the configured meter so YTD sensors update on each draw.

        Called once after the first refresh has resolved the meter. The
        unsub is registered on the config entry, so an options-change
        reload (which may point at a different meter) re-subscribes
        cleanly and an unload tears it down -- no leaked listener.
        """
        if self._meter_entity_id is None:
            return
        self.entry.async_on_unload(
            async_track_state_change_event(
                self.hass, [self._meter_entity_id], self._async_meter_state_event
            )
        )

    @callback
    def _async_meter_state_event(self, event: Event[EventStateChangedData]) -> None:
        self._recompute_live_ytd(event.data["new_state"])

    @callback
    def _recompute_live_ytd(self, state: State | None) -> None:
        """Push a fresh YTD figure from the meter's live state.

        Pure in-memory arithmetic (no recorder / network call), so it is
        safe to run on every meter update. No-ops until the daily tick
        has anchored a baseline, or when the meter reads unavailable /
        unknown / non-numeric / a non-convertible unit -- the last good
        value stays.
        """
        if self.data is None:
            return
        live = _state_volume_m3(state)
        if live is None:
            return
        if self._ytd_baseline_m3 is None:
            # The daily tick could not anchor a baseline because the meter
            # was unavailable at tick time. Reconstruct it from the last
            # recorder YTD figure on the first usable reading so live
            # tracking resumes now instead of staying frozen until the
            # next daily tick (~24h).
            recorder_ytd = self.data.ytd_consumption_m3
            if recorder_ytd is None:
                return
            now_year = dt_util.now().year
            if self._ytd_recorder_year != now_year:
                # The meter was down across the Jan 1 rollover, so the
                # recorder figure is last year's. Start the new year at ~0
                # rather than reconstructing a stale prior-year baseline.
                self._set_cycle(now_year, live)
            else:
                self._set_cycle(now_year, live - recorder_ytd)
        # _apply_cycle handles the Jan 1 rollover and meter-swap re-anchors
        # and keeps the figure monotonic; the daily tick later persists it.
        ytd_m3 = self._apply_cycle(live)
        ytd_cost = self._ytd_cost_from_m3(self.data.tariff, ytd_m3)
        if ytd_m3 == self.data.ytd_consumption_m3 and ytd_cost == self.data.current_year_cost_eur:
            # Nothing changed -- a same-value meter re-report or an
            # attribute-only state event. Skip so a frequently-reporting
            # meter does not write a redundant recorder row for every
            # sensor. The cost is part of the check so a year rollover (or
            # the midnight fee-proration step) still republishes even when
            # the volume figure is unchanged at ~0.
            return
        self.async_set_updated_data(
            replace(
                self.data,
                snapshot_age_hours=self._age_hours(self.data.fetched_at),
                ytd_consumption_m3=ytd_m3,
                current_year_cost_eur=ytd_cost,
            )
        )


def _numeric_state(state: State | None) -> float | None:
    """Return ``state``'s numeric value, or ``None`` if not usable.

    Filters the unavailable / unknown sentinels and any non-numeric
    payload so a flapping meter never pushes a garbage YTD figure.
    """
    if state is None or state.state in (STATE_UNKNOWN, STATE_UNAVAILABLE):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _state_volume_m3(state: State | None) -> float | None:
    """Return ``state``'s reading converted to cubic metres, or ``None``.

    The YTD / running-cost math works in m³, but HA permits a
    ``water`` sensor (the explicit override and the Energy-dashboard
    auto-pick alike) to report litres, gallons, ft³, etc. Read the
    meter's own ``unit_of_measurement`` and convert so a non-m³ meter
    is not silently billed ~1000× too high. A reading with no unit is
    assumed to already be m³ (the common case); a unit we cannot
    convert to a volume is rejected so the YTD sensors stay unknown
    rather than publish a garbage figure.
    """
    value = _numeric_state(state)
    if value is None or state is None:
        return None
    unit = state.attributes.get(ATTR_UNIT_OF_MEASUREMENT)
    if unit in (None, UnitOfVolume.CUBIC_METERS):
        return value
    try:
        return VolumeConverter.convert(value, unit, UnitOfVolume.CUBIC_METERS)
    except HomeAssistantError:
        _LOGGER.debug("meter unit %r is not a convertible volume; ignoring reading", unit)
        return None


async def _discover_energy_water_meter(hass: HomeAssistant) -> str | None:
    """Return the first ``water`` source's ``stat_energy_from`` from
    HA's Energy dashboard, or ``None`` when no water source is
    configured (or the energy component is unavailable).

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
        return None
    try:
        manager = await async_get_manager(hass)
    except Exception as err:  # energy component may surface anything; degrade gracefully
        _LOGGER.debug("energy manager unavailable: %s", err)
        return None
    data = getattr(manager, "data", None)
    if not data:
        return None
    for source in data.get("energy_sources", []):
        if not isinstance(source, dict):
            continue
        if source.get("type") != "water":
            continue
        stat = source.get("stat_energy_from")
        if stat:
            return str(stat)
    return None


async def _recorder_ytd_m3(
    hass: HomeAssistant, entity_id: str, start: date, end: date
) -> float | None:
    """Sum daily ``change`` deltas for ``entity_id`` over ``[start, end]``.

    Wraps :func:`statistics_during_period` via the recorder's executor
    so the SQLite query never runs on the event loop. Returns ``None``
    when the recorder is unavailable, the meter has no statistics, or
    a transient query failure -- callers fall back to surfacing the
    YTD sensor as ``unknown`` rather than zero.

    Reads the ``change`` field, which the recorder defines as the
    delta of the cumulative ``sum`` between the bucket's first and
    last sample. Reading ``sum`` directly would yield the all-time
    running total -- summing those would multiply the figure by however
    many years of meter history exist.
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
        return None

    start_dt = dt_util.start_of_local_day(start).astimezone(UTC)
    end_dt = dt_util.start_of_local_day(end).astimezone(UTC) + timedelta(days=1)
    try:
        stats = await get_instance(hass).async_add_executor_job(
            statistics_during_period,
            hass,
            start_dt,
            end_dt,
            {entity_id},
            "day",
            # Normalise the change deltas to m³ regardless of the meter's
            # own unit (HA permits water sensors in L / gal / ft³ / CCF as
            # well as m³); the recorder converts via the statistic's unit
            # class. Without this a litre-reporting meter would be summed
            # as if it were already cubic metres -- ~1000× too high.
            {VolumeConverter.UNIT_CLASS: UnitOfVolume.CUBIC_METERS},
            {"change"},
        )
    except Exception as err:
        _LOGGER.debug("recorder query for %s failed: %s", entity_id, err)
        return None

    rows: list[Any] = list(stats.get(entity_id, []))
    if not rows:
        return None
    total = 0.0
    seen = False
    for row in rows:
        delta = row.get("change")
        if delta is None:
            continue
        total += float(delta)
        seen = True
    if not seen:
        return None
    # Replacing a water meter mid-year (cumulative sensor state drops
    # back to 0) produces a single large negative delta in the swap
    # bucket. Without a floor we'd surface a nonsensical -50 m³ as the
    # year-to-date consumption; floor at 0 so the sensor degrades to
    # "no consumption since meter swap" rather than negative numbers.
    return max(0.0, total)
