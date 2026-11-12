"""Fan mode, manual request safety and cached temperature regressions."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.hitachi_rs485.climate import HitachiClimate
from custom_components.hitachi_rs485.coordinator import Coordinator
from custom_components.hitachi_rs485.hub import Hub
from custom_components.hitachi_rs485.manual import manual_request
from custom_components.hitachi_rs485.profiles import Register
from custom_components.hitachi_rs485.protocol import frame
from custom_components.hitachi_rs485.transport import OfflineError


@pytest.mark.parametrize("control", [False, True])
async def test_fan_skips_target_and_preserves_cache(
    hass, gateway, device, entry, control
):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,))
    entry.runtime_data = hub
    c = hub.coordinator
    try:
        await c.async_poll_round()
        assert c.states[1].raw[35] == 24
        gateway.registers[65] = 2
        gateway.modes[(1, 3, 35)] = 3
        gateway.requests.clear()
        if control:
            await c.async_command(1, "mode", "fan_only")
        else:
            await c.async_poll_round()
        assert not any(r[1] == 3 and r[2:4] == b"\x00\x23" for r in gateway.requests)
        assert c.states[1].raw[35] == 24
        assert HitachiClimate(entry, device).target_temperature is None
        assert hub.transport.statistics.modbus_exception_count == 0
        gateway.registers[65] = 0
        gateway.modes.clear()
        await c.async_poll_round()
        assert HitachiClimate(entry, device).target_temperature == 24
        gateway.modes[(1, 3, 35)] = 3
        await c.async_poll_round()
        assert hub.transport.statistics.modbus_exception_count == 1
        gateway.registers[65] = 2
        await c.async_poll_round()
        gateway.modes[(1, 3, 64)] = "timeout"
        hub.transport.timeout = 0.01
        gateway.requests.clear()
        await c.async_poll_round()
        assert any(r[2:4] == b"\x00\x23" for r in gateway.requests)
    finally:
        await hub.stop()


@pytest.mark.parametrize(
    "text,auto,confirmed",
    [
        ("", True, False),
        ("xx", True, False),
        ("00 03 00 00 00 01", True, False),
        ("F8 03 00 00 00 01", True, False),
        ("01 04 00 00 00 01", True, False),
        ("01 03 00 00 00 00", True, False),
        ("01 03 FF FF 00 02", True, False),
        ("01 03 00 00 00 01 00", True, False),
        ("01 03 00 00 00 01 00 00", False, False),
        ("01 06 00 20 00 01", True, False),
        ("01 06 00 20 00 02", True, True),
        ("01 06 00 50 00 00", True, True),
        ("02 06 00 20 00 01", True, True),
        ("01 06 00 21 00 09", True, True),
        ("01 06 00 22 00 09", True, True),
        ("01 06 00 23 00 0F", True, True),
        ("01 06 00 23 00 18 00", True, True),
        ("01 10 00 20 00 00 00", True, True),
        ("01 10 00 20 00 02 02 00 01", True, True),
        ("00", False, False),
        ("00" * 513, True, False),
    ],
)
async def test_manual_rejects_invalid_requests(hass, device, text, auto, confirmed):
    c = Coordinator(hass, AsyncMock(), (device,))
    with pytest.raises(ValueError):
        manual_request(text, auto, c, confirmed)


@pytest.mark.parametrize(
    "body",
    [
        "09 03 00 23 00 01",
        "01 06 00 20 00 01",
        "01 06 00 21 00 00",
        "01 06 00 22 00 04",
        "01 06 00 23 00 18",
        "01 10 00 20 00 04 08 00 01 00 00 00 04 00 18",
    ],
)
async def test_manual_valid_frames(hass, device, body):
    c = Coordinator(hass, AsyncMock(), (device,))
    packet = frame(bytes.fromhex(body))
    assert manual_request(body, True, c, True) == packet
    assert manual_request(packet.hex(), False, c, True) == packet


@pytest.mark.parametrize("mode", ["normal", "timeout", "crc", 3, "disconnect"])
async def test_manual_transport_trace_and_recovery(hass, gateway, device, mode):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,))
    t = hub.transport
    t.timeout = 0.02
    try:
        await t.connect()
        gateway.modes[(1, 3, 35)] = mode
        packet = manual_request("01 03 00 23 00 01", True, hub.coordinator)
        if mode == "normal":
            assert await t.submit_manual(packet)
        else:
            with pytest.raises((TimeoutError, ValueError, ConnectionError)):
                await t.submit_manual(packet)
        events = t.trace.snapshot()
        assert events and all(e["source"] == "MANUAL" for e in events)
        assert events[0]["direction"] == "TX"
        assert hub.coordinator.states[1].raw == {}
    finally:
        await hub.stop()


async def test_manual_cancel_retains_isolation_and_duplicate_rejected(
    hass, gateway, device
):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,))
    t = hub.transport
    try:
        await t.connect()
        gateway.block = True
        packet = manual_request("01 03 00 23 00 01", True, hub.coordinator)
        pending = asyncio.create_task(t.submit_manual(packet))
        await gateway.arrived.wait()
        with pytest.raises(OfflineError):
            await t.submit_manual(packet)
        pending.cancel()
        await asyncio.gather(pending, return_exceptions=True)
        poll = asyncio.create_task(t.submit(packet, poll=True))
        await asyncio.sleep(0.01)
        assert len(gateway.requests) == 1
        gateway.release.set()
        await poll
        assert gateway.connections == 1
        await asyncio.sleep(0)
        async with t.exclusive():
            with pytest.raises(OfflineError):
                await t.submit_manual(packet)
    finally:
        await hub.stop()


async def test_scan_progress_is_live_without_refresh(hass, entry):
    from custom_components.hitachi_rs485.config_flow import (
        HitachiConfigFlow,
        HitachiOptionsFlow,
    )

    for flow in (HitachiConfigFlow(), HitachiOptionsFlow()):
        flow.hass = hass
        flow.flow_id = "test"
        flow.handler = "hitachi_rs485"
        flow._draft.update(scan_start=1, scan_end=9)
        progress = flow._progress
        with (
            patch.object(flow, "async_update_progress") as update,
            patch.object(flow, "async_notify_flow_changed") as refresh,
        ):
            for i in range(10):
                flow._scan_progress(i, 9, i // 3)
            assert [call.args[0] for call in update.call_args_list] == [
                i / 9 for i in range(10)
            ]
            assert progress["found"] == "3" and progress["current"] == "9"
            assert refresh.call_count == 10
        flow._scan_task = asyncio.create_task(asyncio.sleep(10))
        await flow.async_step_cancel()
        assert flow._scan_task is None


async def test_indoor_sensor_uses_confirmed_cache(hass, entry, device):
    from custom_components.hitachi_rs485.sensor import SENSORS, HitachiSensor

    hub = Hub.create(hass, "unused", 502, (device,))
    entry.runtime_data = hub
    sensor = HitachiSensor(
        entry, device, next(s for s in SENSORS if s.key == "indoor_temperature")
    )
    assert sensor.native_value is None and not sensor.available
    hub.coordinator.states[1].raw[Register.INDOOR_TEMPERATURE] = 25
    hub.coordinator.states[1].available = True
    assert sensor.native_value == 25 and sensor.available
    assert sensor.device_class == "temperature"
    assert sensor.state_class == "measurement"
    assert sensor.native_unit_of_measurement == "°C"
    assert sensor.entity_registry_enabled_default
    assert HitachiClimate(entry, device).current_temperature == 25
    assert hub.transport.queue_depth == 0
    await hub.stop()


@pytest.mark.parametrize(
    "body", ["01 06 00 20 00 01", "01 10 00 20 00 02 04 00 01 00 00"]
)
async def test_manual_writes_are_one_shot_and_preserve_cache(
    hass, gateway, device, body
):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,))
    try:
        await hub.transport.connect()
        packet = manual_request(body, True, hub.coordinator, True)
        assert await hub.transport.submit_manual(packet)
        assert gateway.requests == [packet]
        assert not hub.coordinator.states[1].raw
        started = asyncio.get_running_loop().time()
        await hub.transport.submit_manual(packet)
        assert asyncio.get_running_loop().time() - started >= 0.074
        assert gateway.connections == 1
    finally:
        await hub.stop()


async def test_manual_offline_and_disconnect_during_drain(hass, gateway, device):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,))
    t = hub.transport
    packet = frame(bytes.fromhex("01 03 00 23 00 01"))
    try:
        with pytest.raises(OfflineError):
            await t.submit_manual(packet)
        await t.connect()
        original = t._idle.wait

        async def lose():
            await original()
            await t.disconnect()

        with patch.object(t._idle, "wait", side_effect=lose):
            with pytest.raises(OfflineError):
                await t.submit_manual(packet)
        assert not gateway.requests
    finally:
        await hub.stop()
