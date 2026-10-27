"""Adversarial lifecycle, scheduling and concurrency regressions."""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.hitachi_rs485.hub import Hub
from custom_components.hitachi_rs485.profiles import TARGET
from custom_components.hitachi_rs485.protocol import (
    ProtocolError,
    frame,
    read_holding_registers,
    write_single_register,
)
from custom_components.hitachi_rs485.trace import ProtocolTraceBuffer
from custom_components.hitachi_rs485.transport import GatewayError, Transport


async def test_connect_race_and_connection_refusal(gateway):
    t = Transport("127.0.0.1", gateway.port)
    await asyncio.gather(t.connect(), t.connect(), t.connect())
    assert gateway.connections == 1
    await t.close()
    with patch("asyncio.open_connection", side_effect=OSError):
        with pytest.raises(GatewayError):
            await Transport("test", 502).connect()


async def test_gateway_failures_propagate_without_optimism(hass, gateway, device):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 2, 30)
    c = hub.coordinator
    try:
        await c.async_poll_round()
        prior = dict(c.states[1].raw)
        gateway.modes[(1, 6, 32)] = "disconnect"
        with pytest.raises(GatewayError):
            await c.async_command(1, "power", 0)
        assert not c.gateway_available and not c.states[1].available
        assert c.states[1].raw == prior
        gateway.modes.clear()
        c.gateway_available = True
        gateway.modes[(1, 3, 64)] = "disconnect"
        await c.async_poll_round()
        assert not c.gateway_available
        gateway.modes.clear()
        c.gateway_available = True
        gateway.modes[(1, 3, 35)] = "disconnect"
        await c.async_poll_round(slow=True)
        assert not c.gateway_available
        gateway.modes.clear()
        c.gateway_available = True
        gateway.modes[(1, 3, 0)] = "disconnect"
        with pytest.raises(GatewayError):
            await hub.scan(1, 1)
        assert not c.scanning and not c.gateway_available
        with patch.object(hub.transport, "submit", new=AsyncMock(return_value=None)):
            c.gateway_available = True
            assert await c.async_command(1, "temperature", 26) is None
    finally:
        await hub.stop()


async def test_no_overlap_target_scheduling_and_slow_zero(hass, gateway, device):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 0.5, 0)
    c = hub.coordinator
    try:
        gateway.block = True
        c.start()
        await gateway.arrived.wait()
        other = asyncio.create_task(c.async_poll_round())
        for _ in range(5):
            await asyncio.sleep(0)
        assert len(gateway.requests) == 1
        gateway.release.set()
        await other
        # Let one real target period elapse; no slow requests or task backlog.
        await asyncio.sleep(0.52)
        assert 2 <= len(gateway.requests) <= 4
        assert all(r[2:6] == bytes.fromhex("00400005") for r in gateway.requests)
        assert c.transport.poll_depth <= 1
    finally:
        await hub.stop()


async def test_failure_threshold_and_paused_round_no_reads(hass, gateway, device):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 2, 30)
    c = hub.coordinator
    hub.transport.timeout = 0.01
    try:
        await c.async_poll_round()
        gateway.modes[1] = "timeout"
        for failures in range(1, 4):
            await c.async_poll_round()
            assert c.states[1].consecutive_failures == failures
            assert c.states[1].available == (failures < 3)
            assert c.gateway_available
        c.set_runtime_poll_paused(True)
        count = len(gateway.requests)
        await c.async_poll_round(background=True)
        assert len(gateway.requests) == count
        c.set_runtime_poll_paused(False)
        gateway.modes.clear()
        await c.async_poll_round()
        assert c.states[1].available
        with patch.object(c, "_read", new=AsyncMock(side_effect=GatewayError)):
            with pytest.raises(GatewayError):
                await c._optional_read(device, TARGET)
    finally:
        await hub.stop()


async def test_reconnect_backoff_failure_then_recovery_at_poll_zero(
    hass, gateway, device
):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 0, 30)
    c = hub.coordinator
    c.gateway_lost()
    c.next_backoff = lambda: 0.001
    original = hub.transport.connect
    count = 0

    async def connect():
        nonlocal count
        count += 1
        if count == 1:
            raise GatewayError()
        await original()

    try:
        with patch.object(hub.transport, "connect", side_effect=connect):
            c.start()
            for _ in range(100):
                if c.gateway_available:
                    break
                await asyncio.sleep(0.002)
        assert c.gateway_available and count == 2
        assert not c.states[1].available and gateway.requests == []
    finally:
        await hub.stop()


async def test_idle_rx_unsolicited_and_bad_framing(gateway):
    t = Transport("127.0.0.1", gateway.port, timeout=0.02)
    try:
        await t.connect()
        while not gateway.writers:
            await asyncio.sleep(0)
        writer = next(iter(gateway.writers))
        writer.write(frame(bytes.fromhex("0103020019")))
        await writer.drain()
        await asyncio.sleep(0.005)
        assert t.connected
        # Malformed framing while idle forces gateway recovery, never a dead reader.
        writer.write(bytes.fromhex("010303"))
        await writer.drain()
        await asyncio.sleep(0.005)
        assert not t.connected
        t._mark_connection_lost(OSError())  # duplicate loss notification is idempotent
    finally:
        await t.close()


async def test_malformed_pending_and_cancelled_submission(gateway):
    t = Transport("127.0.0.1", gateway.port, timeout=0.02)
    gateway.block = True
    pending = asyncio.create_task(t.submit(read_holding_registers(1, 0, 1), poll=True))
    await gateway.arrived.wait()
    writer = next(iter(gateway.writers))
    writer.write(bytes.fromhex("010303"))
    await writer.drain()
    with pytest.raises(ProtocolError):
        await pending
    await t.close()
    t = Transport("127.0.0.1", gateway.port, timeout=0.02)
    gateway.arrived.clear()
    blocked = asyncio.create_task(t.submit(read_holding_registers(1, 0, 1), poll=True))
    await gateway.arrived.wait()
    queued = asyncio.create_task(t.submit(write_single_register(1, 32, 1)))
    await asyncio.sleep(0)
    queued.cancel()
    await asyncio.gather(queued, return_exceptions=True)
    gateway.release.set()
    await blocked
    await asyncio.sleep(0.005)
    assert all(r[1] == 3 for r in gateway.requests)
    await t.close()


def test_trace_clock_backwards_and_observer_failure():
    trace = ProtocolTraceBuffer()
    now = datetime.now(UTC)
    clock = Mock(now=Mock(return_value=now))
    with patch("custom_components.hitachi_rs485.trace.datetime", clock):
        trace.record("TX", b"a", source="control")
        trace.record("RX", b"b", source="control")
    assert trace.snapshot()[1]["timestamp"] > trace.snapshot()[0]["timestamp"]
    t = Transport("test", 502, trace=trace)
    with patch.object(trace, "record", side_effect=RuntimeError):
        t._trace("TX", source="control")
    assert len(trace.snapshot()) == 2


async def test_gateway_loss_wakeup_does_not_skip_backoff(hass, gateway, device):
    hub = Hub.create(hass, "127.0.0.1", gateway.port, (device,), 0, 30)
    c = hub.coordinator
    c.gateway_lost()  # The schedule wake is already set.
    c.next_backoff = lambda: 0.05
    try:
        c.start()
        await asyncio.sleep(0.01)
        assert gateway.connections == 0
        for _ in range(100):
            if c.gateway_available:
                break
            await asyncio.sleep(0.002)
        assert c.gateway_available and gateway.connections == 1
        assert gateway.requests == []
    finally:
        await hub.stop()
