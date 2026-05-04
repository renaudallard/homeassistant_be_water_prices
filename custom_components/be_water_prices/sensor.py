"""Sensors exposed by be_water_prices."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
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

from .const import DOMAIN
from .coordinator import CoordinatorData, WaterCoordinator

EUR_PER_M3 = f"{CURRENCY_EURO}/{UnitOfVolume.CUBIC_METERS}"
EUR_PER_YEAR = f"{CURRENCY_EURO}/year"


@dataclass(frozen=True, kw_only=True)
class WaterSensorDescription(SensorEntityDescription):
    value_fn: Callable[[CoordinatorData], float | None]


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
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    coordinator: WaterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(WaterSensor(coordinator, desc) for desc in SENSORS)


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

    @property
    def native_value(self) -> float | None:
        if self.coordinator.data is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

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
            "publication_label": t.publication_label,
            "source_url": t.source_url,
            "snapshot_age_hours": round(self.coordinator.data.snapshot_age_hours, 2),
            "snapshot_stale": self.coordinator.data.snapshot_stale,
            "last_error": self.coordinator.data.last_error,
        }
