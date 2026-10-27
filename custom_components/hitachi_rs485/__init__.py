"""Independent Hitachi RC-R02XT integration for RTU frames over TCP."""

from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN
from .hub import Hub
from .models import Device, devices_from_options

HitachiConfigEntry = ConfigEntry[Hub]
PLATFORMS = [Platform.CLIMATE, Platform.SENSOR, Platform.BINARY_SENSOR]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:
    from .monitor import async_register_monitor

    await async_register_monitor(hass)
    return True


def prune_removed_devices(
    hass: HomeAssistant, entry: HitachiConfigEntry, devices: tuple[Device, ...]
) -> None:
    """Only explicitly removed slaves are pruned; scans never remove devices."""
    identifiers = {(DOMAIN, f"{entry.entry_id}_slave_{d.slave}") for d in devices}
    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        if device.identifiers & identifiers:
            continue
        for entity in er.async_entries_for_device(entities, device.id):
            if entity.config_entry_id == entry.entry_id:
                entities.async_remove(entity.entity_id)
        registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_setup_entry(hass: HomeAssistant, entry: HitachiConfigEntry) -> bool:
    try:
        devices = devices_from_options(entry.options)
        hub = Hub.create(
            hass,
            entry.data["host"],
            entry.data["port"],
            devices,
            entry.options.get("fast_poll_interval", 2),
            entry.options.get("slow_poll_interval", 30),
        )
    except (ValueError, KeyError, TypeError) as error:
        raise ConfigEntryError("Invalid stored configuration") from error
    try:
        await hub.start()
    except (ConnectionError, TimeoutError) as error:
        await hub.stop()
        raise ConfigEntryNotReady("Cannot connect to gateway") from error
    except BaseException:
        await hub.stop()
        raise
    entry.runtime_data = hub
    entry.async_on_unload(entry.add_update_listener(async_options_updated))

    async def shutdown(event: Any) -> None:
        await hub.stop()

    entry.async_on_unload(
        hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, shutdown)
    )
    try:
        prune_removed_devices(hass, entry, devices)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.stop()
        raise
    return True


async def async_options_updated(hass: HomeAssistant, entry: HitachiConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: HitachiConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        return True
    return False
