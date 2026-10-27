from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.climate import HVACMode
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady

from custom_components.hitachi_rs485 import (
    async_options_updated,
    async_setup_entry,
    async_unload_entry,
    prune_removed_devices,
)
from custom_components.hitachi_rs485.binary_sensor import HitachiProblem
from custom_components.hitachi_rs485.binary_sensor import (
    async_setup_entry as setup_problem,
)
from custom_components.hitachi_rs485.climate import HitachiClimate
from custom_components.hitachi_rs485.climate import async_setup_entry as setup_climate
from custom_components.hitachi_rs485.const import DOMAIN
from custom_components.hitachi_rs485.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.hitachi_rs485.hub import Hub
from custom_components.hitachi_rs485.models import Device
from custom_components.hitachi_rs485.sensor import SENSORS, HitachiSensor
from custom_components.hitachi_rs485.sensor import async_setup_entry as setup_sensors
from custom_components.hitachi_rs485.transport import GatewayError


@pytest.fixture
async def hub(hass, entry, device, gateway):
    h = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 0, 30)
    entry.runtime_data = h
    yield h
    await h.stop()


async def test_entities_confirmed_and_metadata(hass, entry, device, gateway, hub):
    c = hub.coordinator
    climate = HitachiClimate(entry, device)
    assert climate.available  # Unknown state remains explicitly controllable at Poll=0.
    assert climate.hvac_mode is None and climate.current_temperature is None
    assert climate.target_temperature is None and climate.fan_mode is None
    assert climate.name is None
    assert (
        climate.min_temp == 16
        and climate.max_temp == 32
        and climate.target_temperature_step == 1
    )
    assert HVACMode.HEAT in climate.hvac_modes
    assert HVACMode.HEAT not in HitachiClimate(entry, Device(1)).hvac_modes
    assert (
        HVACMode.AUTO
        not in HitachiClimate(entry, replace(device, unit_type=6)).hvac_modes
    )
    assert "quiet" not in HitachiClimate(entry, replace(device, unit_type=6)).fan_modes
    await c.async_poll_round()
    await c.async_poll_round(slow=True)
    assert climate.available and climate.hvac_mode == HVACMode.COOL
    assert (
        climate.current_temperature == 25
        and climate.target_temperature == 24
        and climate.fan_mode == "auto"
    )
    for mode in (
        HVACMode.COOL,
        HVACMode.DRY,
        HVACMode.FAN_ONLY,
        HVACMode.AUTO,
        HVACMode.HEAT,
        HVACMode.OFF,
    ):
        await climate.async_set_hvac_mode(mode)
        assert climate.hvac_mode == mode
    with pytest.raises(ValueError):
        await climate.async_set_hvac_mode("invalid")
    await climate.async_turn_on()
    assert climate.hvac_mode == HVACMode.HEAT
    await climate.async_set_temperature(temperature=26.0)
    assert climate.target_temperature == 26
    for value in (15, 33, 24.5, True, "24"):
        with pytest.raises(ValueError):
            await climate.async_set_temperature(temperature=value)
    for name in climate.fan_modes:
        await climate.async_set_fan_mode(name)
        assert climate.fan_mode == name
    c.states[1].raw[66] = 99
    assert climate.fan_mode is None and climate.available
    assert climate.extra_state_attributes["raw_fan"] == 99
    assert climate.extra_state_attributes["slave_id"] == 1
    assert climate.device_info["model"] == "RC-R02XT"
    problem = HitachiProblem(entry, device)
    assert not problem.is_on
    c.states[1].raw[68] = 42
    assert problem.is_on
    sensors = {d.key: HitachiSensor(entry, device, d) for d in SENSORS}
    assert sensors["energy"].device_class == SensorDeviceClass.ENERGY
    assert sensors["energy"].state_class == SensorStateClass.TOTAL_INCREASING
    assert sensors["energy"].native_unit_of_measurement == "kWh"
    for raw, expected in ((137, 13.7), (150, 15), (0, 0), (40000, 4000), (0, 0)):
        c.states[1].raw[56] = raw
        assert sensors["energy"].native_value == expected
    assert sensors["current"].native_value == pytest.approx(1.7)
    assert sensors["outdoor_temperature"].native_value == 31
    assert sensors["maintenance_runtime"].native_value == 20
    assert sensors["filter_runtime"].native_value == 15
    assert sensors["error_code"].native_value == 42
    assert not sensors["current"].entity_registry_enabled_default
    c.states[1].optional_unavailable = {35, 56}
    assert climate.target_temperature is None
    assert not sensors["energy"].available
    c.states[1].available = False
    assert not sensors["energy"].available
    c.states[1].available = True
    c.states[1].optional_unavailable.clear()
    assert sensors["energy"].available
    c.states[1].raw.clear()
    assert sensors["current"].native_value is None and not sensors["current"].available
    assert problem.is_on is None
    for setup, count in ((setup_climate, 1), (setup_sensors, 6), (setup_problem, 1)):
        added = []
        await setup(hass, entry, lambda es: added.extend(es))
        assert len(added) == count
    hub.coordinator.devices = (Device(1),)
    added = []
    await setup_sensors(hass, entry, lambda es: added.extend(es))
    assert [e.entity_description.key for e in added] == ["error_code"]


async def test_diagnostics(hass, entry, hub, device):
    hub.coordinator.states[1].available = True
    result = await async_get_config_entry_diagnostics(hass, entry)
    assert result["integration"]["version"] == "0.1.0"
    assert result["gateway"]["host"] == "**REDACTED**"
    assert "127.0.0.1" not in str(result)
    assert result["devices"][0]["capabilities"] == list(device.capabilities)
    assert result["devices"][0]["saanet_version"] == "4.0"
    assert result["coordinator"]["available_slaves"] == 1


async def test_setup_reload_unload(hass, entry, gateway):
    hass.config_entries.async_update_entry(
        entry, data={"host": "127.0.0.1", "port": gateway.port}
    )
    with patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ) as forward:
        assert await async_setup_entry(hass, entry)
        forward.assert_awaited_once()
    assert gateway.requests == []
    with patch.object(hass.config_entries, "async_reload", new=AsyncMock()) as reload:
        await async_options_updated(hass, entry)
        reload.assert_awaited_once_with(entry.entry_id)
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=False)
    ):
        assert not await async_unload_entry(hass, entry)
    with patch.object(
        hass.config_entries, "async_unload_platforms", new=AsyncMock(return_value=True)
    ):
        assert await async_unload_entry(hass, entry)
    assert entry.runtime_data.transport._worker is None
    before = HitachiClimate(entry, entry.runtime_data.coordinator.devices[0]).unique_id
    with patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ):
        assert await async_setup_entry(hass, entry)
    assert (
        HitachiClimate(entry, entry.runtime_data.coordinator.devices[0]).unique_id
        == before
    )
    await entry.runtime_data.stop()


async def test_setup_failures(hass, entry):
    with patch("custom_components.hitachi_rs485.Hub.create", side_effect=ValueError):
        with pytest.raises(ConfigEntryError):
            await async_setup_entry(hass, entry)
    for error, expected in (
        (GatewayError(), ConfigEntryNotReady),
        (RuntimeError(), RuntimeError),
    ):
        with patch("custom_components.hitachi_rs485.Hub.start", side_effect=error):
            with pytest.raises(expected):
                await async_setup_entry(hass, entry)


async def test_platform_failure_and_shutdown(hass, entry, gateway):
    hass.config_entries.async_update_entry(
        entry, data={"host": "127.0.0.1", "port": gateway.port}
    )
    with patch.object(
        hass.config_entries, "async_forward_entry_setups", side_effect=RuntimeError
    ):
        with pytest.raises(RuntimeError):
            await async_setup_entry(hass, entry)
    with patch.object(
        hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
    ):
        await async_setup_entry(hass, entry)
    hass.bus.async_fire("homeassistant_stop")
    await hass.async_block_till_done()
    assert not entry.runtime_data.transport.connected


async def test_registry_prunes_only_removed(hass, entry, device):
    from homeassistant.helpers import device_registry as dr
    from homeassistant.helpers import entity_registry as er

    registry = dr.async_get(hass)
    entities = er.async_get(hass)
    keep = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_slave_1")},
    )
    removed = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, f"{entry.entry_id}_slave_5")},
    )
    entity = entities.async_get_or_create(
        "sensor", DOMAIN, "removed", config_entry=entry, device_id=removed.id
    )
    prune_removed_devices(hass, entry, (device,))
    assert registry.async_get(keep.id) is not None
    assert entities.async_get(entity.entity_id) is None
