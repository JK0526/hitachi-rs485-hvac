"""Real HA fixtures and an isolated, fragmented RTU gateway simulator."""

import asyncio
from dataclasses import dataclass, field

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hitachi_rs485.const import DOMAIN
from custom_components.hitachi_rs485.models import Device
from custom_components.hitachi_rs485.protocol import frame


@pytest.fixture(autouse=True)
def integrations(enable_custom_integrations):
    yield


@pytest.fixture
def device():
    return Device(
        1,
        unit_type=2,
        series="KWE",
        saanet_version="4.0",
        capabilities=(
            "energy",
            "current",
            "outdoor_temperature",
            "maintenance_runtime",
            "filter_runtime",
        ),
        identity={4: 1, 5: 2, 6: 28, 7: 0x4B57, 8: 0x4520, 9: 0x400},
    )


@pytest.fixture
def entry(hass, device):
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"host": "127.0.0.1", "port": 502},
        options={
            "fast_poll_interval": 0,
            "slow_poll_interval": 30,
            "scan_start": 1,
            "scan_end": 3,
            "devices": [device.as_dict()],
        },
    )
    entry.add_to_hass(hass)
    return entry


@dataclass
class Gateway:
    port: int = 0
    connections: int = 0
    requests: list = field(default_factory=list)
    registers: dict = field(
        default_factory=lambda: {
            0: 25,
            4: 1,
            5: 2,
            6: 28,
            7: 0x4B57,
            8: 0x4520,
            9: 0x400,
            10: 31,
            15: 17,
            32: 1,
            33: 0,
            34: 5,
            35: 24,
            56: 137,
            57: 20,
            58: 15,
            64: 1,
            65: 0,
            66: 5,
            67: 25,
            68: 0,
        }
    )
    modes: dict = field(default_factory=dict)
    absent: set = field(default_factory=set)
    handlers: set = field(default_factory=set)
    writers: set = field(default_factory=set)
    arrived: asyncio.Event = field(default_factory=asyncio.Event)
    release: asyncio.Event = field(default_factory=asyncio.Event)
    block: bool = False

    async def handle(self, reader, writer):
        task = asyncio.current_task()
        self.handlers.add(task)
        self.writers.add(writer)
        self.connections += 1
        try:
            while True:
                head = await reader.readexactly(6)
                if head[1] == 16:
                    count = await reader.readexactly(1)
                    request = head + count + await reader.readexactly(count[0] + 2)
                else:
                    request = head + await reader.readexactly(2)
                self.requests.append(request)
                self.arrived.set()
                if self.block:
                    await self.release.wait()
                slave, fn = request[:2]
                address = int.from_bytes(request[2:4], "big")
                value = int.from_bytes(request[4:6], "big")
                mode = self.modes.get(
                    (slave, fn, address), self.modes.get(slave, "normal")
                )
                if slave in self.absent or mode == "timeout":
                    continue
                if mode == "disconnect":
                    return
                if fn == 3:
                    values = [
                        self.registers.get(r, 0)
                        for r in range(address, address + value)
                    ]
                    response = frame(
                        bytes([slave, 3, value * 2])
                        + b"".join(v.to_bytes(2, "big") for v in values)
                    )
                elif fn == 6:
                    self.registers[address] = value
                    if address in (32, 33, 34):
                        self.registers[address + 32] = value
                    response = request
                else:
                    response = frame(request[:6])
                if isinstance(mode, int):
                    response = frame(bytes([slave, fn | 128, mode]))
                elif mode == "crc":
                    response = response[:-1] + bytes([response[-1] ^ 1])
                elif mode == "slave":
                    response = frame(bytes([slave + 1]) + response[1:-2])
                writer.write(response[:1])
                await writer.drain()
                await asyncio.sleep(0)
                writer.write(response[1:])
                await writer.drain()
        except asyncio.IncompleteReadError, ConnectionError:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except ConnectionError:
                pass
            self.handlers.discard(task)
            self.writers.discard(writer)


@pytest.fixture
async def gateway(socket_enabled):
    gateway = Gateway()
    server = await asyncio.start_server(gateway.handle, "127.0.0.1", 0)
    gateway.port = server.sockets[0].getsockname()[1]
    yield gateway
    server.close()
    tasks = list(gateway.handlers)
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await server.wait_closed()
