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

"""Sensors exposed by be_water_prices."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CURRENCY_EURO, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.util import dt as dt_util

from .const import (
    CONF_UTILITY,
    DOMAIN,
    REGION_FLANDERS,
)
from .coordinator import CoordinatorData, WaterCoordinator, utility_device_info
from .providers import get

EUR_PER_M3 = f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}"
EUR_PER_YEAR = f"{CURRENCY_EURO}/year"

# Strips the trailing "(Commune)" suffix from publication labels so
# the sensor's extra_state_attributes don't expose the user's commune
# in screenshots, the recorder, or HA exports. Mirrors the redaction
# applied by diagnostics.py to CONF_COMMUNE_LABEL.
_PUBLICATION_LABEL_COMMUNE_SUFFIX = re.compile(r"\s*\([^()]+\)\s*$")


def _publication_label_without_commune(label: str) -> str:
    """Return ``label`` with the trailing ``(commune)`` suffix removed."""
    return _PUBLICATION_LABEL_COMMUNE_SUFFIX.sub("", label).strip()


@dataclass(frozen=True, kw_only=True)
class WaterSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CoordinatorData], float | None]
    # Optional: returns the timestamp of the last reset for ``TOTAL``
    # state-class sensors (the YTD ones). HA's long-term-statistics
    # engine uses this to bucket each reset cycle as its own period;
    # without it ``TOTAL`` falls back to detecting drops as resets,
    # which is fragile around year boundaries.
    last_reset_fn: Callable[[], datetime] | None = None


def _jan_1_local() -> datetime:
    """Return the local-timezone start of the current calendar year.

    Used as ``last_reset`` for the YTD sensors so the statistics engine
    treats each calendar year as its own bucket.
    """
    return dt_util.now().replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)


def _basis_or_linear(data: CoordinatorData) -> float | None:
    """Headline distributor rate per m³.

    Prefers the explicit basis (Flanders block 1) or linear (Brussels);
    falls back to the Walloon CVD when neither is set so the SWDE-style
    breakdown still surfaces something on this sensor.
    """
    t = data.tariff
    if t.basis_eur_per_m3 is not None:
        return t.basis_eur_per_m3
    if t.linear_eur_per_m3 is not None:
        return t.linear_eur_per_m3
    if t.cvd_eur_per_m3:
        return t.cvd_eur_per_m3
    return None


def _comfort(data: CoordinatorData) -> float | None:
    return data.tariff.comfort_eur_per_m3


def _sanering_total(data: CoordinatorData) -> float:
    t = data.tariff
    return (
        t.sanering_bovengemeentelijk_eur_per_m3
        + t.sanering_gemeentelijk_eur_per_m3
        + t.cva_eur_per_m3
        + t.fse_eur_per_m3
    )


def _all_in_basis(data: CoordinatorData) -> float | None:
    base = _basis_or_linear(data)
    if base is None:
        return None
    san = _sanering_total(data)
    return round((base + san) * (1.0 + data.tariff.vat_rate), 4)


def _yearly_fee(data: CoordinatorData) -> float:
    return round(data.tariff.yearly_fixed_fee, 2)


def _projected_cost(data: CoordinatorData) -> float | None:
    return data.projected_annual_cost_eur


def _current_year_cost(data: CoordinatorData) -> float | None:
    return data.current_year_cost_eur


def _ytd_consumption(data: CoordinatorData) -> float | None:
    return data.ytd_consumption_m3


SENSORS: tuple[WaterSensorDescription, ...] = (
    WaterSensorDescription(
        key="yearly_fee",
        translation_key="yearly_fee",
        native_unit_of_measurement=EUR_PER_YEAR,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        value_fn=_yearly_fee,
    ),
    WaterSensorDescription(
        key="basis_rate",
        translation_key="basis_rate",
        native_unit_of_measurement=EUR_PER_M3,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_basis_or_linear,
    ),
    WaterSensorDescription(
        key="comfort_rate",
        translation_key="comfort_rate",
        native_unit_of_measurement=EUR_PER_M3,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_comfort,
    ),
    WaterSensorDescription(
        key="sanering_rate",
        translation_key="sanering_rate",
        native_unit_of_measurement=EUR_PER_M3,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_sanering_total,
    ),
    WaterSensorDescription(
        key="all_in_basis",
        translation_key="all_in_basis",
        native_unit_of_measurement=EUR_PER_M3,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=4,
        value_fn=_all_in_basis,
    ),
    WaterSensorDescription(
        key="projected_annual_cost",
        translation_key="projected_annual_cost",
        native_unit_of_measurement=EUR_PER_YEAR,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_projected_cost,
    ),
    WaterSensorDescription(
        key="current_year_cost",
        translation_key="current_year_cost",
        # Running bill since Jan 1: pro-rated annual fees + YTD volumetric.
        # MONETARY rules out TOTAL_INCREASING (HA only allows None or TOTAL
        # for monetary). TOTAL with a Jan 1 last_reset lets the long-term
        # stats engine treat each year as its own bucket and tolerates the
        # natural drop on Jan 1 without flagging it as a meter reset.
        native_unit_of_measurement=CURRENCY_EURO,
        device_class=SensorDeviceClass.MONETARY,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=2,
        value_fn=_current_year_cost,
        last_reset_fn=_jan_1_local,
    ),
    WaterSensorDescription(
        key="ytd_consumption",
        translation_key="ytd_consumption",
        # YTD m³ resets to ~0 on Jan 1, so TOTAL + last_reset is the right
        # state-class even though WATER would also accept TOTAL_INCREASING:
        # the latter would treat the Jan 1 drop as a meter reset and emit
        # a spike in the long-term-stats deltas.
        native_unit_of_measurement=UnitOfVolume.CUBIC_METERS,
        device_class=SensorDeviceClass.WATER,
        state_class=SensorStateClass.TOTAL,
        suggested_display_precision=3,
        value_fn=_ytd_consumption,
        last_reset_fn=_jan_1_local,
    ),
)


def _is_applicable(desc: WaterSensorDescription, *, region: str) -> bool:
    """Filter sensors that don't apply to this entry's utility/options.

    Comfort tarief is a Flemish-only construct -- creating the entity
    for Brussels or Wallonia entries leaves it permanently ``unknown``,
    which is just noise on the device card.

    The YTD pair (current_year_cost + ytd_consumption) is always
    created: even without a configured water meter the entities show
    as ``unavailable`` until the user wires one up via the OptionsFlow
    OR via the Energy dashboard. Adding the meter via the OptionsFlow
    triggers a reload; adding it via the Energy dashboard now also
    surfaces values on the next coordinator tick without requiring an
    HA restart, because the entity is already there waiting.
    """
    return not (desc.key == "comfort_rate" and region != REGION_FLANDERS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WaterCoordinator = hass.data[DOMAIN][entry.entry_id]
    region = get(entry.data[CONF_UTILITY]).region
    async_add_entities(
        WaterSensor(coordinator, desc) for desc in SENSORS if _is_applicable(desc, region=region)
    )


class WaterSensor(CoordinatorEntity[WaterCoordinator], SensorEntity):
    _attr_has_entity_name = True
    entity_description: WaterSensorDescription

    def __init__(
        self,
        coordinator: WaterCoordinator,
        description: WaterSensorDescription,
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.entry.entry_id}_{description.key}"
        self._attr_device_info = utility_device_info(coordinator)

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def last_reset(self) -> datetime | None:
        fn = self.entity_description.last_reset_fn
        return fn() if fn is not None else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        if self.coordinator.data is None:
            return {}
        t = self.coordinator.data.tariff
        return {
            "utility": t.utility,
            "region": t.region,
            "valid_from": t.valid_from.isoformat(),
            "valid_until": t.valid_until.isoformat() if t.valid_until else None,
            "publication_label": _publication_label_without_commune(t.publication_label),
            "source_url": t.source_url,
            "snapshot_age_hours": round(self.coordinator.data.snapshot_age_hours, 2),
            "snapshot_stale": self.coordinator.data.snapshot_stale,
            "last_error": self.coordinator.data.last_error,
        }
