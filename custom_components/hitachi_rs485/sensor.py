"""Read-only sensors with optional capabilities and Energy Dashboard metadata."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    EntityCategory,
    UnitOfElectricCurrent,
    UnitOfEnergy,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import HitachiEntity
from .models import Device
from .profiles import Register, energy_kwh

if TYPE_CHECKING:
    from . import HitachiConfigEntry


@dataclass(frozen=True, kw_only=True)
class HitachiSensorDescription(SensorEntityDescription):
    register: int
    scale: float = 1


SENSORS = (
    HitachiSensorDescription(
        key="energy",
        translation_key="energy",
        register=Register.ENERGY,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        scale=0.1,
    ),
    HitachiSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        register=Register.OUTDOOR_TEMPERATURE,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
    ),
    HitachiSensorDescription(
        key="current",
        translation_key="current",
        register=Register.CURRENT,
        device_class=SensorDeviceClass.CURRENT,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfElectricCurrent.AMPERE,
        scale=0.1,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    HitachiSensorDescription(
        key="error_code",
        translation_key="error_code",
        register=Register.ERROR,
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    HitachiSensorDescription(
        key="maintenance_runtime",
        translation_key="maintenance_runtime",
        register=Register.MAINTENANCE_RUNTIME,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
    HitachiSensorDescription(
        key="filter_runtime",
        translation_key="filter_runtime",
        register=Register.FILTER_RUNTIME,
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.HOURS,
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HitachiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        HitachiSensor(entry, d, desc)
        for d in entry.runtime_data.coordinator.devices
        for desc in SENSORS
        if desc.key == "error_code" or desc.key in d.capabilities
    )


class HitachiSensor(HitachiEntity, SensorEntity):
    def __init__(
        self,
        entry: HitachiConfigEntry,
        device: Device,
        description: HitachiSensorDescription,
    ) -> None:
        super().__init__(entry, device, description.key)
        self.entity_description = description

    @property
    def available(self) -> bool:
        return (
            super().available
            and self.entity_description.register
            not in self.confirmed_state.optional_unavailable
            and self.native_value is not None
        )

    @property
    def native_value(self) -> float | None:
        raw = self.confirmed_state.raw.get(self.entity_description.register)
        if self.entity_description.key == "energy":
            return energy_kwh(raw)
        return raw * self.entity_description.scale if raw is not None else None
