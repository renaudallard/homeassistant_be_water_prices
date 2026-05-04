"""Diagnostics dump for be_water_prices."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .coordinator import WaterCoordinator


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: WaterCoordinator = hass.data[DOMAIN][entry.entry_id]
    data = coordinator.data
    return {
        "entry": {
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "snapshot": (
            {
                "tariff": _serialise(asdict(data.tariff)),
                "fetched_at": data.fetched_at.isoformat(),
                "snapshot_age_hours": data.snapshot_age_hours,
                "snapshot_stale": data.snapshot_stale,
                "projected_annual_cost_eur": data.projected_annual_cost_eur,
                "last_error": data.last_error,
            }
            if data is not None
            else None
        ),
    }


def _serialise(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _serialise(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_serialise(v) for v in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value
