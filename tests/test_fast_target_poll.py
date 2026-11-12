"""Fast/target ordering, independent health, and Monitor load regressions."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.hitachi_rs485.coordinator import Coordinator
from custom_components.hitachi_rs485.monitor import _poll_load
from custom_components.hitachi_rs485.profiles import FAST, TARGET
from custom_components.hitachi_rs485.protocol import write_single_register
from custom_components.hitachi_rs485.transport import Transport


@pytest.mark.parametrize("slaves", [(1,), (1, 2, 3)])
async def test_fast_target_per_slave_and_slow_optional_only(
    hass, gateway, device, slaves
):
    transport = Transport("127.0.0.1", gateway.port)
    c = Coordinator(hass, transport, tuple(replace(device, slave=s) for s in slaves))
    try:
        await c.async_poll_round()
        assert [(r[0], r[1], r[2:6].hex()) for r in gateway.requests] == [
            (s, 3, block) for s in slaves for block in ("00400005", "00230001")
        ]
        assert len(c._round_times) == 1
        assert c.last_fast_poll_round_ms >= (2 * len(slaves) - 1) * 75
        gateway.requests.clear()
        await c.async_poll_round(slow=True)
        assert gateway.requests
        assert all(r[2:4] != b"\x00\x23" for r in gateway.requests)
        assert len(c._round_times) == 1
        assert gateway.connections == 1
    finally:
        await transport.close()


@pytest.mark.parametrize("error", ["timeout", "crc"])
async def test_target_failure_preserves_confirmed_state(hass, gateway, device, error):
    transport = Transport("127.0.0.1", gateway.port, timeout=0.02)
    c = Coordinator(hass, transport, (device,))
    try:
        await c.async_poll_round()
        prior = dict(c.states[1].raw)
        gateway.modes[(1, 3, 35)] = error
        for _ in range(3):
            await c.async_poll_round()
        state = c.states[1]
        assert state.raw == prior
        assert state.available and state.consecutive_failures == 0
        assert state.communication_issue is None
        assert TARGET.start in state.optional_unavailable
        gateway.modes.clear()
        await c.async_poll_round()
        assert TARGET.start not in state.optional_unavailable
    finally:
        await transport.close()


async def test_partial_paused_round_does_not_count_as_load_sample(hass, device):
    c = Coordinator(hass, Transport("memory", 502), (device,))

    async def readback(d):
        c.set_runtime_poll_paused(True)
        return True

    with (
        patch.object(c, "async_readback", side_effect=readback),
        patch.object(c, "_optional_read", new=AsyncMock()) as target,
    ):
        await c.async_poll_round(background=True)
        target.assert_not_awaited()
    assert not c._round_times
    await c.transport.close()


async def test_overrun_target_period_no_overlap_or_backlog(hass, device):
    c = Coordinator(hass, Transport("memory", 502), (device,), fast=0.5, slow=0)
    calls = []
    started = asyncio.Event()
    release = asyncio.Event()

    async def read(d, spec):
        calls.append((d.slave, spec))
        if len(calls) == 1:
            started.set()
            await release.wait()
        if len(calls) == 4:
            c.set_runtime_poll_paused(True)
        return {64: 1, 65: 0, 66: 5, 67: 25, 68: 0} if spec == FAST else {35: 24}

    try:
        with patch.object(c, "_read", side_effect=read):
            c.start()
            await asyncio.wait_for(started.wait(), 1)
            await asyncio.sleep(0.55)
            assert calls == [(1, FAST)]
            release.set()
            for _ in range(100):
                if len(calls) == 4:
                    break
                await asyncio.sleep(0.001)
            # Next due round starts immediately, without an extra 0.5s delay.
            assert calls == [(1, FAST), (1, TARGET), (1, FAST), (1, TARGET)]
            assert c._round_times[0] >= 550
            assert c.transport.poll_depth == 0
    finally:
        await c.stop()
        await c.transport.close()


@pytest.mark.parametrize(
    ("average", "samples", "fast", "devices", "paused", "expected"),
    [
        (1599, 20, 2, True, False, "normal"),
        (1600, 20, 2, True, False, "high"),
        (1999, 20, 2, True, False, "high"),
        (2000, 20, 2, True, False, "over_target"),
        (4000, 19, 2, True, False, "insufficient_samples"),
        (4000, 20, 0, True, False, "disabled"),
        (4000, 20, 2, False, False, "no_data"),
        (0, 0, 2, True, False, "no_data"),
        (4000, 20, 2, True, True, "paused"),
    ],
)
async def test_monitor_load(
    hass, device, average, samples, fast, devices, paused, expected
):
    c = Coordinator(
        hass, Transport("memory", 502), (device,) if devices else (), fast=fast
    )
    c._round_times.extend([average] * samples)
    c.runtime_poll_paused = paused
    result = _poll_load(c)
    assert result["state"] == expected
    assert result["samples"] == samples
    if expected in ("disabled", "no_data", "paused"):
        assert result["percent"] is None
    else:
        assert result["percent"] == pytest.approx(average / 20)
    await c.transport.close()


async def test_control_preempts_target_without_extra_connection(hass, gateway, device):
    transport = Transport("127.0.0.1", gateway.port)
    c = Coordinator(hass, transport, (device,))
    gateway.block = True
    polling = asyncio.create_task(c.async_poll_round(background=True))
    try:
        await asyncio.wait_for(gateway.arrived.wait(), 1)
        control = asyncio.create_task(transport.submit(write_single_register(1, 32, 0)))
        await asyncio.sleep(0)
        gateway.release.set()
        await asyncio.gather(polling, control)
        assert [(r[1], r[2:4].hex()) for r in gateway.requests] == [
            (3, "0040"),
            (6, "0020"),
            (3, "0023"),
        ]
        assert gateway.connections == 1
    finally:
        await transport.close()


async def test_scanner_pause_excludes_background_pair(hass, gateway, device):
    transport = Transport("127.0.0.1", gateway.port)
    c = Coordinator(hass, transport, (device,))
    try:
        async with c.pause():
            polling = asyncio.create_task(c.async_poll_round(background=True))
            await asyncio.sleep(0.01)
            assert c.scanning and not gateway.requests
            assert not polling.done()
        await polling
        assert [r[2:4].hex() for r in gateway.requests] == ["0040", "0023"]
    finally:
        await transport.close()


async def test_fast_zero_disables_fast_and_slow_background(hass, device):
    c = Coordinator(hass, Transport("memory", 502), (device,), fast=0, slow=30)
    try:
        with patch.object(c, "_read", new=AsyncMock()) as read:
            c.start()
            await asyncio.sleep(0.01)
            await c.async_poll_round(background=True)
            await c.async_poll_round(slow=True, background=True)
            read.assert_not_awaited()
        assert not c._round_times
    finally:
        await c.stop()
        await c.transport.close()


async def test_monitor_rolling_window_uses_latest_twenty(hass, device):
    c = Coordinator(hass, Transport("memory", 502), (device,))
    c._round_times.extend([10000] * 20)
    c._round_times.extend([100] * 20)
    assert c.fast_poll_round_samples == 20
    assert c.average_fast_poll_round_ms == 100
    assert _poll_load(c) == {"state": "normal", "percent": 5, "samples": 20}
    await c.transport.close()
