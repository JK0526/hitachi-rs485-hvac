"""Problem state from the confirmed raw fault register."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import HitachiEntity
from .models import Device
from .profiles import Register

if TYPE_CHECKING:
    from . import HitachiConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HitachiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        HitachiProblem(entry, d) for d in entry.runtime_data.coordinator.devices
    )


class HitachiProblem(HitachiEntity, BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, entry: HitachiConfigEntry, device: Device) -> None:
        super().__init__(entry, device, "problem")

    @property
    def is_on(self) -> bool | None:
        value = self.confirmed_state.raw.get(Register.ERROR)
        return value != 0 if value is not None else None
