"""One entry, one bus, one transport; scanner reuses the running transport."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.core import HomeAssistant

from .coordinator import Coordinator
from .models import Device
from .scanner import ScanResult, scan
from .trace import ProtocolTraceBuffer
from .transport import GatewayError, Transport


@dataclass
class Hub:
    transport: Transport
    coordinator: Coordinator

    @classmethod
    def create(
        cls,
        hass: HomeAssistant,
        host: str,
        port: int,
        devices: tuple[Device, ...],
        fast: float = 2,
        slow: float = 30,
    ) -> Hub:
        transport = Transport(host, port, trace=ProtocolTraceBuffer())
        coordinator = Coordinator(hass, transport, devices, fast, slow)
        transport.on_connection_lost = coordinator.gateway_lost
        return cls(transport, coordinator)

    async def start(self) -> None:
        await self.transport.connect()
        # Poll=0 creates no background reads, including at setup.
        self.coordinator.start()

    async def scan(
        self,
        start: int,
        end: int,
        progress: Callable[[int, int, int], None] | None = None,
    ) -> list[ScanResult]:
        async with self.coordinator.pause():
            try:
                return await scan(self.transport, start, end, progress)
            except GatewayError:
                self.coordinator.gateway_lost()
                raise

    async def stop(self) -> None:
        try:
            await self.coordinator.stop()
        finally:
            await self.transport.close()
