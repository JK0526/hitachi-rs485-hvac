"""Stable slave identity shared by every entity."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .models import Device, DeviceState

if TYPE_CHECKING:
    from . import HitachiConfigEntry


class HitachiEntity(CoordinatorEntity):
    _attr_has_entity_name = True

    def __init__(self, entry: HitachiConfigEntry, device: Device, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self.device = device
        self._attr_unique_id = f"{entry.entry_id}_slave_{device.slave}_{key}"
        self._attr_translation_key = key
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{entry.entry_id}_slave_{device.slave}")},
            manufacturer="Hitachi",
            model="RC-R02XT",
            name=f"Hitachi AC #{device.slave:02d}",
            sw_version=device.saanet_version or None,
        )

    @property
    def confirmed_state(self) -> DeviceState:
        return self.coordinator.states[self.device.slave]

    @property
    def available(self) -> bool:
        return self.coordinator.gateway_available and self.confirmed_state.available

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"slave_id": self.device.slave, "interface": self.device.interface}
