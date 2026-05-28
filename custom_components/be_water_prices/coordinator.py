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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

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
            # the cached-snapshot path. asyncio.CancelledError is the
            # one exception we must not swallow -- HA cancels tasks on
            # shutdown / reload and that signal has to propagate.
            if isinstance(err, asyncio.CancelledError):
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
        return (datetime.now(UTC) - fetched_at).total_seconds() / 3600.0

    @staticmethod
    def _is_stale(tariff: WaterTariff, fetched_at: datetime) -> bool:
        age_days = (datetime.now(UTC) - fetched_at).days
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

    async def _compute_ytd(self, tariff: WaterTariff) -> tuple[float | None, float | None]:
        """Read YTD m³ from the recorder and apply the bill math.

        Returns ``(ytd_m3, ytd_cost_eur)``. Both are ``None`` when no
        water meter is configured (neither explicitly nor via the
        Energy dashboard) or the recorder has no usable data.
        """
        meter = await self.async_resolve_meter_entity()
        if not meter:
            return None, None
        today = dt_util.now().date()
        jan1 = date(today.year, 1, 1)
        ytd_m3 = await _recorder_ytd_m3(self.hass, meter, jan1, today)
        if ytd_m3 is None:
            return None, None

        elapsed = (today - jan1).days + 1  # include today
        days_in_year = 366 if calendar.isleap(today.year) else 365
        fraction = elapsed / days_in_year

        persons = int(self.entry.options.get(CONF_PERSONS, DEFAULT_PERSONS))
        social = bool(self.entry.options.get(CONF_SOCIAL_TARIFF, False))
        cost = compute_ytd_cost(tariff, ytd_m3, persons, fraction, social_tariff=social)
        return ytd_m3, cost


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
            None,
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
    return total if seen else None
