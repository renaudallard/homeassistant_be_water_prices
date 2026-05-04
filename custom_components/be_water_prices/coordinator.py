"""Daily-refresh coordinator for be_water_prices."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import (
    CONF_CONSUMPTION_M3_PER_YEAR,
    CONF_PERSONS,
    CONF_UTILITY,
    DEFAULT_CONSUMPTION_M3,
    DEFAULT_PERSONS,
    DOMAIN,
    SNAPSHOT_STALE_AFTER_DAYS,
    UPDATE_INTERVAL_HOURS,
)
from .pricing import compute_annual_cost
from .providers import ExtractorError, WaterTariff, get
from .providers.base import WaterExtractor

_LOGGER = logging.getLogger(__name__)


@dataclass
class CoordinatorData:
    tariff: WaterTariff
    fetched_at: datetime
    snapshot_age_hours: float
    snapshot_stale: bool
    last_error: str = ""
    projected_annual_cost_eur: float | None = None


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
        try:
            tariff = await self._extractor.fetch(session)
        except ExtractorError as err:
            # On fetch failure keep serving the last good snapshot rather
            # than blanking every sensor; the recorded staleness gives the
            # user (and the repair issue) a clear signal.
            if self._last_good is not None:
                _LOGGER.warning("water tariff fetch failed, serving cached: %s", err)
                stale = self._is_stale(self._last_good.tariff, self._last_good.fetched_at)
                cached = CoordinatorData(
                    tariff=self._last_good.tariff,
                    fetched_at=self._last_good.fetched_at,
                    snapshot_age_hours=self._age_hours(self._last_good.fetched_at),
                    snapshot_stale=stale,
                    last_error=str(err),
                    projected_annual_cost_eur=self._project_cost(self._last_good.tariff),
                )
                return cached
            raise UpdateFailed(str(err)) from err

        now = datetime.now(UTC)
        data = CoordinatorData(
            tariff=tariff,
            fetched_at=now,
            snapshot_age_hours=0.0,
            snapshot_stale=self._is_stale(tariff, now),
            projected_annual_cost_eur=self._project_cost(tariff),
        )
        self._last_good = data
        return data

    @staticmethod
    def _age_hours(fetched_at: datetime) -> float:
        return (datetime.now(UTC) - fetched_at).total_seconds() / 3600.0

    @staticmethod
    def _is_stale(tariff: WaterTariff, fetched_at: datetime) -> bool:
        age_days = (datetime.now(UTC) - fetched_at).days
        if age_days > SNAPSHOT_STALE_AFTER_DAYS:
            return True
        return tariff.valid_until is not None and tariff.valid_until < datetime.now().date()

    def _project_cost(self, tariff: WaterTariff) -> float | None:
        opts = self.entry.options
        consumption = float(opts.get(CONF_CONSUMPTION_M3_PER_YEAR, DEFAULT_CONSUMPTION_M3))
        persons = int(opts.get(CONF_PERSONS, DEFAULT_PERSONS))
        return compute_annual_cost(tariff, consumption, persons)
