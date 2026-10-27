"""Home Assistant climate adapter; protocol semantics remain in profiles."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import HitachiEntity
from .models import Device
from .profiles import FAN_CODES, Register, fan_mode, hvac_mode, supports_heat

if TYPE_CHECKING:
    from . import HitachiConfigEntry


async def async_setup_entry(
    hass: HomeAssistant,
    entry: HitachiConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    async_add_entities(
        HitachiClimate(entry, d) for d in entry.runtime_data.coordinator.devices
    )


class HitachiClimate(HitachiEntity, ClimateEntity):
    _attr_name = None
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_min_temp = 16
    _attr_max_temp = 32
    _attr_target_temperature_step = 1
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.FAN_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, entry: HitachiConfigEntry, device: Device) -> None:
        super().__init__(entry, device, "climate")

    @property
    def available(self) -> bool:
        state = self.confirmed_state
        initial_unknown = (
            not state.raw
            and not state.consecutive_failures
            and state.communication_issue is None
        )
        return self.coordinator.gateway_available and (
            state.available or initial_unknown
        )

    @property
    def hvac_modes(self) -> list[HVACMode]:
        modes = [HVACMode.OFF, HVACMode.COOL, HVACMode.DRY, HVACMode.FAN_ONLY]
        if self.device.unit_type != 6:
            modes.append(HVACMode.AUTO)
        if supports_heat(self.device, self.confirmed_state):
            modes.append(HVACMode.HEAT)
        return modes

    @property
    def hvac_mode(self) -> HVACMode | None:
        value = hvac_mode(self.confirmed_state)
        return HVACMode(value) if value is not None else None

    @property
    def current_temperature(self) -> float | None:
        return self.confirmed_state.raw.get(Register.INDOOR_TEMPERATURE)

    @property
    def target_temperature(self) -> float | None:
        if Register.TARGET in self.confirmed_state.optional_unavailable:
            return None
        return self.confirmed_state.raw.get(Register.TARGET)

    @property
    def fan_modes(self) -> list[str]:
        return [f for f in FAN_CODES if self.device.unit_type != 6 or f != "quiet"]

    @property
    def fan_mode(self) -> str | None:
        return fan_mode(self.confirmed_state.raw.get(Register.FAN_STATE))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {
            **super().extra_state_attributes,
            "raw_mode": self.confirmed_state.raw.get(Register.MODE_STATE),
            "raw_fan": self.confirmed_state.raw.get(Register.FAN_STATE),
        }

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        if hvac_mode not in self.hvac_modes:
            raise ValueError("Unsupported HVAC mode")
        if hvac_mode == HVACMode.OFF:
            await self.async_turn_off()
        else:
            await self.coordinator.async_command(self.device.slave, "mode", hvac_mode)
            await self.async_turn_on()

    async def async_turn_on(self) -> None:
        await self.coordinator.async_command(self.device.slave, "power", 1)

    async def async_turn_off(self) -> None:
        await self.coordinator.async_command(self.device.slave, "power", 0)

    async def async_set_temperature(self, **kwargs: Any) -> None:
        value = kwargs[ATTR_TEMPERATURE]
        if (
            not isinstance(value, (float, int))
            or isinstance(value, bool)
            or not float(value).is_integer()
        ):
            raise ValueError("Temperature must be integral")
        await self.coordinator.async_command(
            self.device.slave, "temperature", int(value)
        )

    async def async_set_fan_mode(self, fan_mode: str) -> None:
        await self.coordinator.async_command(self.device.slave, "fan", fan_mode)
