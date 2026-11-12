"""Focused transport turnaround tests using in-memory RTU streams."""

from __future__ import annotations

import asyncio
from collections import deque
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.hitachi_rs485.coordinator import Coordinator
from custom_components.hitachi_rs485.protocol import (
    CRCError,
    DeviceException,
    ProtocolError,
    frame,
    read_holding_registers,
)
from custom_components.hitachi_rs485.scanner import scan
from custom_components.hitachi_rs485.transport import (
    _MIN_TRANSACTION_TURNAROUND_SECONDS,
    GatewayError,
    Transport,
    _remaining_transaction_turnaround,
)


class MemoryGateway:
    """Provide fragmented RTU responses without opening a network socket."""

    def __init__(self) -> None:
        self.connections = 0
        self.writes: list[tuple[float, bytes]] = []
        self.modes: deque[str] = deque()
        self.partial_sent = asyncio.Event()
        self.partial_response: tuple[asyncio.StreamReader, bytes] | None = None

    async def open_connection(self, host: str, port: int):
        self.connections += 1
        reader = asyncio.StreamReader()
        return reader, MemoryWriter(self, reader)

    def release_partial(self) -> None:
        assert self.partial_response is not None
        reader, remainder = self.partial_response
        reader.feed_data(remainder)
        self.partial_response = None


class MemoryWriter:
    def __init__(self, gateway: MemoryGateway, reader: asyncio.StreamReader) -> None:
        self.gateway = gateway
        self.reader = reader
        self.closing = False

    def write(self, request: bytes) -> None:
        loop = asyncio.get_running_loop()
        self.gateway.writes.append((loop.time(), request))
        mode = self.gateway.modes.popleft() if self.gateway.modes else "normal"
        if mode == "timeout":
            return
        if mode == "exception":
            response = frame(bytes((request[0], request[1] | 0x80, 2)))
        elif request[1] == 3:
            count = int.from_bytes(request[4:6], "big")
            if mode == "invalid_length":
                count = 1
            response = frame(bytes((request[0], 3, count * 2)) + bytes(count * 2))
        else:
            response = request
        if mode == "crc":
            response = response[:-1] + bytes((response[-1] ^ 1,))
        if mode == "partial":
            self.reader.feed_data(response[:3])
            self.gateway.partial_response = (self.reader, response[3:])
            self.gateway.partial_sent.set()
            return
        loop.call_soon(self.reader.feed_data, response[:1])
        loop.call_soon(self.reader.feed_data, response[1:])

    async def drain(self) -> None:
        pass

    def is_closing(self) -> bool:
        return self.closing

    def close(self) -> None:
        if not self.closing:
            self.closing = True
            self.reader.feed_eof()

    async def wait_closed(self) -> None:
        pass


class TraceProbe:
    def __init__(self) -> None:
        self.transport: Transport | None = None
        self.events: list[dict] = []

    def record(self, direction: str, raw: bytes = b"", **kwargs) -> None:
        observed_at = asyncio.get_running_loop().time()
        assert self.transport is not None
        self.events.append(
            {
                "direction": direction,
                "at": observed_at,
                "rx_complete": self.transport._last_rx_complete,
                **kwargs,
            }
        )


@pytest.fixture
def memory_gateway(monkeypatch):
    gateway = MemoryGateway()
    monkeypatch.setattr(asyncio, "open_connection", gateway.open_connection)
    return gateway


def make_transport(gateway: MemoryGateway, *, timeout: float = 0.2):
    trace = TraceProbe()
    transport = Transport("memory", 502, timeout=timeout, trace=trace)
    trace.transport = transport
    return transport, trace


def assert_paced(gateway: MemoryGateway, trace: TraceProbe) -> None:
    rx = [
        event["rx_complete"]
        for event in trace.events
        if event["direction"] in ("OK", "ERROR")
    ]
    assert len(rx) == len(gateway.writes)
    for index in range(1, len(gateway.writes)):
        assert gateway.writes[index][0] - rx[index - 1] >= 0.075 - 0.000001


@pytest.mark.parametrize(
    ("last_rx", "now", "expected"),
    [
        (None, 100.0, 0.0),
        (10.0, 10.0, 0.075),
        (10.0, 10.05, 0.025),
        (10.0, 10.075, 0.0),
        (10.0, 10.2, 0.0),
    ],
)
def test_remaining_turnaround_calculation(last_rx, now, expected):
    assert _MIN_TRANSACTION_TURNAROUND_SECONDS == 0.075
    assert _remaining_transaction_turnaround(last_rx, now) == pytest.approx(expected)


async def test_first_transaction_returns_without_pacing_sleep(memory_gateway):
    transport, _ = make_transport(memory_gateway)
    try:
        with patch(
            "custom_components.hitachi_rs485.transport.asyncio.sleep",
            new=AsyncMock(side_effect=AssertionError("unexpected pacing sleep")),
        ):
            await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        assert len(memory_gateway.writes) == 1
        assert transport._last_rx_complete is not None
    finally:
        await transport.close()


async def test_rx_timestamp_is_recorded_only_after_complete_frame(memory_gateway):
    memory_gateway.modes.append("partial")
    transport, _ = make_transport(memory_gateway)
    task = asyncio.create_task(
        transport.submit(read_holding_registers(1, 64, 5), poll=True)
    )
    try:
        await asyncio.wait_for(memory_gateway.partial_sent.wait(), 1)
        await asyncio.sleep(0)
        assert transport._last_rx_complete is None
        assert not task.done()
        memory_gateway.release_partial()
        await task
        assert transport._last_rx_complete is not None
    finally:
        await transport.close()


async def test_valid_rx_timestamp_is_recorded_before_response_dispatch(memory_gateway):
    transport, trace = make_transport(memory_gateway)
    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        rx_event = next(event for event in trace.events if event["direction"] == "RX")
        assert rx_event["rx_complete"] is not None
        assert rx_event["rx_complete"] <= rx_event["at"]
        assert transport._last_rx_complete == rx_event["rx_complete"]
    finally:
        await transport.close()


async def test_valid_late_frame_updates_timestamp_after_pending_future_is_cancelled():
    transport = Transport("memory", 502)
    reader = asyncio.StreamReader()
    request = read_holding_registers(1, 64, 1)
    pending = asyncio.get_running_loop().create_future()
    pending.cancel()
    transport._reader = reader
    transport._pending_request = request
    transport._pending_response = pending
    reader.feed_data(frame(bytes.fromhex("01 03 02 00 00")))
    reader.feed_eof()
    try:
        await transport._read_loop(reader)
        assert transport._last_rx_complete is not None
    finally:
        await transport.close()


async def test_immediate_second_transaction_is_paced_and_latency_excludes_wait(
    memory_gateway,
):
    transport, trace = make_transport(memory_gateway)
    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        await transport.submit(read_holding_registers(1, 35, 1), poll=True)
        assert_paced(memory_gateway, trace)
        tx_events = [event for event in trace.events if event["direction"] == "TX"]
        ok_events = [event for event in trace.events if event["direction"] == "OK"]
        measured = (ok_events[1]["at"] - tx_events[1]["at"]) * 1000
        assert ok_events[1]["latency_ms"] == pytest.approx(measured, abs=5)
    finally:
        await transport.close()


async def test_elapsed_gap_has_no_additional_fixed_sleep(memory_gateway):
    transport, _ = make_transport(memory_gateway)
    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        await asyncio.sleep(0.09)
        with patch(
            "custom_components.hitachi_rs485.transport.asyncio.sleep",
            new=AsyncMock(side_effect=AssertionError("elapsed gap must not sleep")),
        ):
            await transport.submit(read_holding_registers(1, 35, 1), poll=True)
        assert len(memory_gateway.writes) == 2
    finally:
        await transport.close()


async def test_concurrent_callers_remain_serialized_and_paced(memory_gateway):
    transport, trace = make_transport(memory_gateway)
    requests = [read_holding_registers(1, address, 1) for address in (10, 11, 12)]
    try:
        await asyncio.gather(
            *(transport.submit(request, poll=True) for request in requests)
        )
        assert [request for _, request in memory_gateway.writes] == requests
        assert memory_gateway.connections == 1
        assert_paced(memory_gateway, trace)
    finally:
        await transport.close()


async def test_scanner_exclusive_path_inherits_transport_pacing(
    memory_gateway, monkeypatch
):
    monkeypatch.setattr(
        "custom_components.hitachi_rs485.scanner.IDENTITY_REGISTERS", ()
    )
    monkeypatch.setattr(
        "custom_components.hitachi_rs485.scanner.classify",
        lambda raw: ("unknown", "unknown", False),
    )
    transport, trace = make_transport(memory_gateway)
    try:
        results = await scan(transport, 1, 2)
        assert [result.slave for result in results] == [1, 2]
        assert [
            event["source"] for event in trace.events if event["direction"] == "TX"
        ] == [
            "scan",
            "scan",
        ]
        assert_paced(memory_gateway, trace)
    finally:
        await transport.close()


async def test_command_write_and_readbacks_inherit_pacing(hass, device, memory_gateway):
    transport, trace = make_transport(memory_gateway)
    coordinator = Coordinator(hass, transport, (device,), 0, 30)
    try:
        await coordinator.async_command(device.slave, "power", 1)
        assert [request[1] for _, request in memory_gateway.writes] == [6, 3, 3]
        assert [
            event["source"] for event in trace.events if event["direction"] == "TX"
        ] == [
            "control",
            "poll",
            "poll",
        ]
        assert_paced(memory_gateway, trace)
    finally:
        await transport.close()


async def test_timeout_disconnects_without_retry_or_fake_rx_timestamp(memory_gateway):
    memory_gateway.modes.append("timeout")
    transport, _ = make_transport(memory_gateway, timeout=0.02)
    try:
        with pytest.raises(TimeoutError):
            await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        assert transport._last_rx_complete is None
        assert not transport.connected
        assert transport.statistics.timeout_count == 1
        assert len(memory_gateway.writes) == memory_gateway.connections == 1
    finally:
        await transport.close()


async def test_crc_failure_does_not_create_rx_timestamp(memory_gateway):
    memory_gateway.modes.append("crc")
    transport, _ = make_transport(memory_gateway, timeout=0.02)
    try:
        with pytest.raises(CRCError):
            await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        assert transport._last_rx_complete is None
        assert transport.statistics.crc_error_count == 1
        assert not transport.connected
        assert len(memory_gateway.writes) == 1
    finally:
        await transport.close()


async def test_invalid_complete_response_does_not_create_rx_timestamp(memory_gateway):
    memory_gateway.modes.append("invalid_length")
    transport, _ = make_transport(memory_gateway)
    try:
        with pytest.raises(ProtocolError):
            await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        assert transport._last_rx_complete is None
        assert not transport.connected
        assert len(memory_gateway.writes) == 1
    finally:
        await transport.close()


async def test_timeout_preserves_last_complete_response_timestamp(memory_gateway):
    memory_gateway.modes.extend(("normal", "timeout"))
    transport, _ = make_transport(memory_gateway, timeout=0.02)
    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        last_rx = transport._last_rx_complete
        with pytest.raises(TimeoutError):
            await transport.submit(read_holding_registers(1, 35, 1), poll=True)
        assert transport._last_rx_complete == last_rx
        assert len(memory_gateway.writes) == 2
        assert memory_gateway.connections == 1
    finally:
        await transport.close()


async def test_reconnect_before_gap_expires_keeps_bus_protected(memory_gateway):
    transport, trace = make_transport(memory_gateway)
    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        last_rx = transport._last_rx_complete
        await transport.disconnect()
        assert transport._last_rx_complete == last_rx
        await transport.submit(read_holding_registers(1, 35, 1), poll=True)
        assert memory_gateway.connections == 2
        assert_paced(memory_gateway, trace)
    finally:
        await transport.close()


async def test_reconnect_after_elapsed_gap_has_no_stale_delay(memory_gateway):
    transport, _ = make_transport(memory_gateway)
    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        await transport.disconnect()
        await asyncio.sleep(0.09)
        with patch(
            "custom_components.hitachi_rs485.transport.asyncio.sleep",
            new=AsyncMock(side_effect=AssertionError("stale timestamp must not delay")),
        ):
            await transport.submit(read_holding_registers(1, 35, 1), poll=True)
        assert memory_gateway.connections == 2
    finally:
        await transport.close()


async def test_cancelled_caller_during_pacing_does_not_transmit(memory_gateway):
    transport, _ = make_transport(memory_gateway)
    entered = asyncio.Event()
    original_wait = transport._wait_for_transaction_turnaround

    async def notified_wait():
        entered.set()
        await original_wait()

    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        with patch.object(
            transport, "_wait_for_transaction_turnaround", side_effect=notified_wait
        ):
            task = asyncio.create_task(
                transport.submit(read_holding_registers(1, 35, 1), poll=True)
            )
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            await asyncio.sleep(0.09)
        assert len(memory_gateway.writes) == 1
        assert transport._worker is not None and not transport._worker.done()
        await transport.submit(read_holding_registers(1, 36, 1), poll=True)
        assert len(memory_gateway.writes) == 2
    finally:
        await transport.close()


async def test_worker_cancelled_during_pacing_does_not_transmit(memory_gateway):
    transport, _ = make_transport(memory_gateway)
    entered = asyncio.Event()
    original_wait = transport._wait_for_transaction_turnaround

    async def notified_wait():
        entered.set()
        await original_wait()

    await transport.submit(read_holding_registers(1, 64, 5), poll=True)
    with patch.object(
        transport, "_wait_for_transaction_turnaround", side_effect=notified_wait
    ):
        task = asyncio.create_task(
            transport.submit(read_holding_registers(1, 35, 1), poll=True)
        )
        await asyncio.wait_for(entered.wait(), 1)
        await transport.close()
        result = await asyncio.gather(task, return_exceptions=True)
    assert isinstance(result[0], asyncio.CancelledError)
    assert len(memory_gateway.writes) == 1
    assert transport._worker is None


async def test_disconnect_during_pacing_does_not_emit_tx_or_reconnect(memory_gateway):
    transport, trace = make_transport(memory_gateway)
    entered = asyncio.Event()
    original_wait = transport._wait_for_transaction_turnaround

    async def notified_wait():
        entered.set()
        await original_wait()

    try:
        await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        with patch.object(
            transport, "_wait_for_transaction_turnaround", side_effect=notified_wait
        ):
            task = asyncio.create_task(
                transport.submit(read_holding_registers(1, 35, 1), poll=True)
            )
            await asyncio.wait_for(entered.wait(), 1)
            await transport.disconnect()
            with pytest.raises(GatewayError):
                await task
        assert len(memory_gateway.writes) == 1
        assert memory_gateway.connections == 1
        assert [event["direction"] for event in trace.events].count("TX") == 1
    finally:
        await transport.close()


async def test_modbus_exception_frame_also_paces_next_transaction(memory_gateway):
    memory_gateway.modes.append("exception")
    transport, trace = make_transport(memory_gateway)

    try:
        with pytest.raises(DeviceException):
            await transport.submit(read_holding_registers(1, 64, 5), poll=True)
        assert transport.connected
        await transport.submit(read_holding_registers(1, 35, 1), poll=True)
        assert_paced(memory_gateway, trace)
    finally:
        await transport.close()
