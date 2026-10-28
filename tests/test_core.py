import asyncio
from dataclasses import replace

import pytest

from custom_components.hitachi_rs485.coordinator import Coordinator, valid_poll_interval
from custom_components.hitachi_rs485.hub import Hub
from custom_components.hitachi_rs485.models import (
    Device,
    DeviceState,
    HeatSupport,
    devices_from_options,
)
from custom_components.hitachi_rs485.profiles import (
    FAN_CODES,
    FAST,
    MODE_CODES,
    classify,
    command,
    decode_identity,
    energy_kwh,
    fan_mode,
    hvac_mode,
    supports_heat,
)
from custom_components.hitachi_rs485.protocol import (
    DeviceException,
    ProtocolError,
    read_holding_registers,
    write_single_register,
)
from custom_components.hitachi_rs485.scanner import scan, validate_range
from custom_components.hitachi_rs485.trace import ProtocolTraceBuffer
from custom_components.hitachi_rs485.transport import (
    GatewayError,
    OfflineError,
    Transport,
)


def make_transport(gateway, **kwargs):
    return Transport("127.0.0.1", gateway.port, timeout=0.02, **kwargs)


def test_models_and_codecs(device):
    assert Device.from_dict(device.as_dict()) == device
    assert devices_from_options({"devices": [device.as_dict()]}) == (device,)
    for data in ({"slave": 0}, {"slave": True}, {"slave": 248}):
        with pytest.raises(ValueError):
            Device(**data)
    with pytest.raises(ValueError):
        devices_from_options({"devices": [device.as_dict()] * 2})
    with pytest.raises(ValueError):
        devices_from_options({"devices": [replace(device, family="unknown").as_dict()]})
    assert decode_identity({7: 0x4B57, 8: 0x4520, 9: 0x400}) == ("KWE", "4.0")
    assert decode_identity({}) == ("", "")
    assert supports_heat(device, DeviceState())
    assert supports_heat(replace(device, heat=HeatSupport.SUPPORTED), DeviceState())
    assert not supports_heat(
        replace(device, heat=HeatSupport.UNSUPPORTED), DeviceState(observed_heat=True)
    )
    assert supports_heat(Device(1), DeviceState(observed_heat=True))
    assert not supports_heat(Device(1), DeviceState())
    for mode, raw in MODE_CODES.items():
        assert hvac_mode(DeviceState(raw={64: 1, 65: raw})) == mode
        assert command(device, DeviceState(), "mode", mode)[4:6] == raw.to_bytes(
            2, "big"
        )
    assert hvac_mode(DeviceState(raw={64: 0, 65: 999})) == "off"
    assert hvac_mode(DeviceState()) is None
    assert hvac_mode(DeviceState(raw={64: 1, 65: 999})) is None
    for name, raw in FAN_CODES.items():
        assert fan_mode(raw) == name
        assert command(device, DeviceState(), "fan", name)[4:6] == raw.to_bytes(
            2, "big"
        )
    assert fan_mode(99) is None
    assert fan_mode(None) is None
    for field, value in [
        ("power", 0),
        ("power", 1),
        ("temperature", 16),
        ("temperature", 32),
    ]:
        assert command(device, DeviceState(), field, value)[1] == 6
    for field, value in [
        ("power", 2),
        ("power", True),
        ("mode", "invalid"),
        ("fan", "invalid"),
        ("temperature", 15),
        ("temperature", 33),
        ("temperature", 24.5),
        ("reset", 0),
    ]:
        with pytest.raises(ValueError):
            command(device, DeviceState(), field, value)
    for field, value in [("mode", "auto"), ("fan", "quiet")]:
        with pytest.raises(ValueError):
            command(replace(device, unit_type=6), DeviceState(), field, value)
    with pytest.raises(ValueError):
        command(Device(1), DeviceState(), "mode", "heat")
    assert classify(device.identity) == ("air_conditioner", "RC-R02XT", True)
    assert classify({5: 32})[0] == "heat_exchanger"
    assert classify({5: 8})[0] == "dehumidifier"
    assert classify({5: 7})[0] == "dehumidifier"
    assert not classify({})[2]
    for raw, expected in [
        (137, 13.7),
        (0, 0),
        (140, 14),
        (40000, 4000),
        (0, 0),
        (None, None),
        (40001, None),
        (-1, None),
    ]:
        assert energy_kwh(raw) == expected


@pytest.mark.parametrize("start,end", [(0, 1), (1, 248), (5, 4), (True, 2), (1, 2.0)])
def test_scan_invalid(start, end):
    with pytest.raises(ValueError):
        validate_range(start, end)


async def test_scan_and_capabilities(gateway):
    transport = make_transport(gateway)
    gateway.absent = {2}
    gateway.modes[(1, 3, 56)] = 2
    progress = []
    try:
        result = await scan(transport, 1, 2, lambda *v: progress.append(v))
        assert len(result) == 1 and result[0].supported
        assert result[0].slave == 1
        assert "energy" not in result[0].device.capabilities
        assert "current" in result[0].device.capabilities
        assert progress[-1] == (2, 2, 1)
        assert all(p[1] == 3 for p in gateway.requests)
        assert transport.statistics.modbus_exception_count == 1
        assert transport.statistics.timeout_count == 1
    finally:
        await transport.close()


@pytest.mark.parametrize(
    "kind", ["legacy", "dehumidifier", "exchanger", "crc", "exception", "probe_timeout"]
)
async def test_scan_other_devices(gateway, kind):
    t = make_transport(gateway)
    if kind == "legacy":
        for r in range(5, 10):
            gateway.modes[(1, 3, r)] = 2
    elif kind == "dehumidifier":
        gateway.registers[5] = 8
    elif kind == "exchanger":
        gateway.registers[5] = 32
    elif kind == "crc":
        gateway.modes[1] = "crc"
    elif kind == "exception":
        gateway.modes[(1, 3, 0)] = 2
    else:
        gateway.modes[(1, 3, 7)] = "timeout"
    try:
        found = await scan(t, 1, 1)
        if kind == "crc":
            assert found == []
        elif kind == "exception":
            assert found[0].supported
        elif kind == "probe_timeout":
            assert found[0].supported
            assert found[0].identity_errors[7] == "timeout"
        else:
            assert not found[0].supported
    finally:
        await t.close()


async def test_transport_fragmented_exceptions_and_reconnect(gateway):
    trace = ProtocolTraceBuffer()
    t = make_transport(gateway, trace=trace)
    try:
        r = await t.submit(read_holding_registers(1, 64, 5), poll=True)
        assert FAST.decode(r)[67] == 25
        for fn, req in [
            (3, read_holding_registers(1, 56, 1)),
            (6, write_single_register(1, 32, 1)),
        ]:
            gateway.modes[(1, fn, 56 if fn == 3 else 32)] = 2
            with pytest.raises(DeviceException):
                await t.submit(req)
            assert t.connected
        gateway.modes.clear()
        await t.disconnect()
        await t.connect()
        assert t.statistics.reconnect_count == 1
        assert [e["direction"] for e in trace.snapshot()][:3] == ["TX", "RX", "OK"]
        trace.clear()
        assert trace.snapshot() == []
    finally:
        await t.close()
    with pytest.raises(OfflineError):
        await t.connect()
    with pytest.raises(OfflineError):
        await t.submit(read_holding_registers(1, 0, 1))


@pytest.mark.parametrize(
    "kwargs", [{"host": ""}, {"port": 0}, {"timeout": 0}, {"stateful_capacity": 0}]
)
def test_transport_invalid(kwargs):
    with pytest.raises(ValueError):
        Transport(**({"host": "localhost", "port": 502} | kwargs))


@pytest.mark.parametrize(
    "mode,error,counter",
    [
        ("timeout", TimeoutError, "timeout_count"),
        ("crc", ProtocolError, "crc_error_count"),
        ("slave", ProtocolError, "failed_requests"),
        ("disconnect", GatewayError, "failed_requests"),
    ],
)
async def test_transport_failures(gateway, mode, error, counter):
    t = make_transport(gateway)
    gateway.modes[1] = mode
    try:
        with pytest.raises(error):
            await t.submit(read_holding_registers(1, 0, 1))
        assert getattr(t.statistics, counter) == 1
    finally:
        await t.close()


async def test_latest_wins_global_order_and_capacity(gateway):
    t = make_transport(gateway, stateful_capacity=2)
    gateway.block = True
    poll = asyncio.create_task(t.submit(read_holding_registers(1, 0, 1), poll=True))
    await gateway.arrived.wait()
    tasks = []
    try:
        for address, value, key in [
            (35, 24, "temperature"),
            (34, 4, "fan"),
            (35, 27, "temperature"),
        ]:
            tasks.append(
                asyncio.create_task(
                    t.submit(
                        write_single_register(1, address, value),
                        key=(1, "climate", key),
                    )
                )
            )
            await asyncio.sleep(0)
        assert t.control_depth == t.stateful_depth == 2
        assert t.queue_depth == 2
        assert await tasks[0] is None
        with pytest.raises(asyncio.QueueFull):
            await t.submit(write_single_register(1, 32, 1), key=(1, "climate", "power"))
        gateway.release.set()
        await asyncio.gather(poll, *tasks)
        assert [
            (int.from_bytes(r[2:4], "big"), int.from_bytes(r[4:6], "big"))
            for r in gateway.requests
            if r[1] == 6
        ] == [(34, 4), (35, 27)]
        assert gateway.connections == 1
    finally:
        await t.close()


async def test_exclusive_drain_and_shutdown(gateway):
    t = make_transport(gateway)
    async with t.exclusive():
        blocked = asyncio.create_task(t.submit(write_single_register(1, 32, 0)))
        await asyncio.sleep(0)
        assert not gateway.requests
        await t.submit(read_holding_registers(1, 0, 1), poll=True, exclusive=True)
        assert len(gateway.requests) == 1
    await blocked
    gateway.block = True
    gateway.arrived.clear()
    pending = asyncio.create_task(t.submit(read_holding_registers(1, 0, 1), poll=True))
    await gateway.arrived.wait()
    queued = asyncio.create_task(t.submit(write_single_register(1, 32, 1)))
    await asyncio.sleep(0)
    await t.close()
    results = await asyncio.gather(pending, queued, return_exceptions=True)
    assert all(isinstance(v, asyncio.CancelledError) for v in results)
    assert t._reader_task is None and t._worker is None and t.queue_depth == 0


async def test_disconnect_discards_controls(gateway):
    t = make_transport(gateway)
    gateway.block = True
    gateway.modes[1] = "disconnect"
    task = asyncio.create_task(t.submit(write_single_register(1, 32, 1)))
    await gateway.arrived.wait()
    queued = asyncio.create_task(t.submit(write_single_register(1, 35, 27)))
    await asyncio.sleep(0)
    gateway.release.set()
    results = await asyncio.gather(task, queued, return_exceptions=True)
    assert isinstance(results[0], GatewayError) and isinstance(results[1], OfflineError)
    gateway.modes.clear()
    gateway.block = False
    await t.submit(read_holding_registers(1, 0, 1), poll=True)
    assert len([r for r in gateway.requests if r[1] == 6]) == 1
    await t.close()


async def test_coordinator_reads_controls_health(hass, gateway, device):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 0, 30)
    hub.transport.timeout = 0.02
    c = hub.coordinator
    try:
        await hub.start()
        await asyncio.sleep(0)
        assert gateway.requests == []
        assert not c.states[1].available
        await c.async_poll_round()
        assert c.states[1].available
        assert len(gateway.requests) == 1
        await c.async_poll_round(slow=True)
        assert c.states[1].raw[56] == 137
        assert c.states[1].raw[35] == 24
        await c.async_command(1, "temperature", 27)
        assert c.states[1].raw[35] == 27
        assert [r[1] for r in gateway.requests][-3:] == [6, 3, 3]
        last = dict(c.states[1].raw)
        gateway.modes[(1, 3, 64)] = "timeout"
        await c.async_command(1, "power", 0)
        assert c.states[1].raw[64] == last[64]
        assert c.states[1].communication_issue == "TimeoutError"
        await c.async_poll_round()
        assert c.states[1].available
        await c.async_poll_round()
        assert not c.states[1].available
        gateway.modes.clear()
        await c.async_poll_round()
        assert c.states[1].available
        gateway.modes[(1, 3, 56)] = 2
        await c.async_poll_round(slow=True)
        assert c.states[1].available and 56 in c.states[1].optional_unavailable
        gateway.modes.clear()
        await c.async_poll_round(slow=True)
        assert not c.states[1].optional_unavailable
        gateway.modes[(1, 6, 32)] = 3
        with pytest.raises(DeviceException):
            await c.async_command(1, "power", 1)
        c.gateway_lost()
        assert not c.states[1].available
        with pytest.raises(OfflineError):
            await c.async_command(1, "power", 1)
        c.gateway_available = True
        async with c.pause():
            with pytest.raises(OfflineError):
                await c.async_command(1, "power", 1)
        assert await c._async_update_data() == c.states
        assert c.last_fast_poll_round_ms >= 0 and c.average_fast_poll_round_ms >= 0
        assert [c.next_backoff() for _ in range(8)] == [1, 2, 4, 8, 15, 30, 30, 30]
    finally:
        await hub.stop()


async def test_scheduler_pause_resume_reconnect(hass, gateway, device):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 0.5, 10)
    c = hub.coordinator
    try:
        c.set_runtime_poll_paused(True)
        await hub.start()
        await asyncio.sleep(0)
        assert not gateway.requests
        c.set_runtime_poll_paused(False)
        for _ in range(100):
            if c.states[1].raw.get(56) == 137:
                break
            await asyncio.sleep(0.002)
        assert c.states[1].raw[56] == 137
        c.set_runtime_poll_paused(True)
        async with c._round_lock:
            count = len(gateway.requests)
        await asyncio.sleep(0.01)
        assert len(gateway.requests) == count
        await c.async_command(1, "power", 0)
        assert len(gateway.requests) > count
        c.next_backoff = lambda: 0.001
        c.gateway_lost()
        assert not c.states[1].available
        c._wake.set()
        await asyncio.sleep(0.01)
        assert c.gateway_available and not c.states[1].available
        await hub.scan(1, 1)
        assert c.runtime_poll_paused
    finally:
        await hub.stop()


async def test_zero_poll_and_invalid(hass, gateway, device):
    for fast, slow in [(-1, 30), (0.2, 30), (2, 1), (float("nan"), 30)]:
        with pytest.raises(ValueError):
            Coordinator(hass, make_transport(gateway), (device,), fast, slow)
    assert not valid_poll_interval(float("inf"))
    c = Coordinator(hass, make_transport(gateway), (device,), 0, 0)
    assert not c.set_runtime_poll_paused(True)
    await c.stop()
    await c.transport.close()
    for value in (0, 200, 501):
        if value != 200:
            with pytest.raises(ValueError):
                ProtocolTraceBuffer(value)
    trace = ProtocolTraceBuffer(1)
    trace.record("TX", b"1", source="scan")
    trace.record("RX", b"2", source="scan", latency_ms=1.23456)
    assert len(trace.snapshot()) == 1 and trace.snapshot()[0]["latency_ms"] == 1.235

async def test_real_nk_identity_and_individual_reads(gateway):
    gateway.registers.update({4: 1, 5: 1, 6: 71, 7: 0x4E4B, 8: 0x2020, 9: 0x0400})
    transport = make_transport(gateway)
    try:
        result = (await scan(transport, 1, 1))[0]
        assert result.supported
        assert (result.device.unit_type, result.device.capacity, result.device.series, result.device.saanet_version) == (1, 71, "NK", "4.0")
        assert [p[2:6] for p in gateway.requests if p[2:4] in [i.to_bytes(2, "big") for i in range(4, 10)]] == [i.to_bytes(2, "big") + b"\x00\x01" for i in range(4, 10)]
        assert decode_identity({7: 0x4142, 8: 0x4300})[0] == "ABC"
    finally:
        await transport.close()
