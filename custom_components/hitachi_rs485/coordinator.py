"""One target-period scheduler, confirmed state and independent slave health."""

from __future__ import annotations

import asyncio
import logging
import math
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import BACKOFF, DOMAIN
from .models import Device, DeviceState
from .profiles import FAST, OPTIONAL_REGISTERS, TARGET, PollSpec, Register, command
from .protocol import ProtocolError
from .transport import GatewayError, OfflineError, Transport

_LOGGER = logging.getLogger(__name__)


def valid_poll_interval(value: float, minimum: float = 0.5) -> bool:
    return math.isfinite(value) and (value == 0 or value >= minimum)


class Coordinator(DataUpdateCoordinator[dict[int, DeviceState]]):
    def __init__(
        self,
        hass: HomeAssistant,
        transport: Transport,
        devices: tuple[Device, ...],
        fast: float = 2,
        slow: float = 30,
    ) -> None:
        super().__init__(hass, _LOGGER, name=DOMAIN)
        if not valid_poll_interval(fast) or not valid_poll_interval(slow, 10):
            raise ValueError("Invalid polling interval")
        self.transport, self.devices = transport, devices
        self.fast_poll_interval, self.slow_poll_interval = fast, slow
        self.states = {d.slave: DeviceState() for d in devices}
        self.gateway_available = True
        self.scanning = False
        self.runtime_poll_paused = False
        self._round_lock = asyncio.Lock()
        self._wake = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._backoff_index = 0
        self._round_times: deque[float] = deque(maxlen=20)
        self.async_set_updated_data(self.states)

    @property
    def polling_paused(self) -> bool:
        return self.fast_poll_interval == 0

    @property
    def background_polling_paused(self) -> bool:
        return self.polling_paused or self.runtime_poll_paused

    @property
    def last_fast_poll_round_ms(self) -> float:
        return self._round_times[-1] if self._round_times else 0

    @property
    def average_fast_poll_round_ms(self) -> float:
        return (
            sum(self._round_times) / len(self._round_times) if self._round_times else 0
        )

    def set_runtime_poll_paused(self, paused: bool) -> bool:
        if self.polling_paused:
            return False
        self.runtime_poll_paused = paused
        self._wake.set()
        return True

    def next_backoff(self) -> int:
        value = BACKOFF[min(self._backoff_index, len(BACKOFF) - 1)]
        self._backoff_index += 1
        return value

    def gateway_lost(self) -> None:
        self.gateway_available = False
        self.transport.discard_controls()
        for state in self.states.values():
            state.available = False
            state.communication_issue = "GatewayError"
        self._wake.set()
        self.async_set_updated_data(self.states)

    async def _read(self, device: Device, spec: PollSpec) -> dict[int, int]:
        response = await self.transport.submit(spec.request(device.slave), poll=True)
        return spec.decode(response)

    async def async_readback(self, device: Device) -> bool:
        state = self.states[device.slave]
        try:
            raw = await self._read(device, FAST)
        except GatewayError:
            self.gateway_lost()
            raise
        except (TimeoutError, ProtocolError) as error:
            state.consecutive_failures += 1
            state.communication_issue = type(error).__name__
            if state.consecutive_failures >= 3:
                state.available = False
            self.async_set_updated_data(self.states)
            return False
        state.raw.update(raw)
        state.observed_heat |= raw[Register.MODE_STATE] == 4
        state.available = True
        state.consecutive_failures = 0
        state.communication_issue = None
        self.async_set_updated_data(self.states)
        return True

    async def _optional_read(self, device: Device, spec: PollSpec) -> None:
        state = self.states[device.slave]
        try:
            raw = await self._read(device, spec)
        except GatewayError:
            self.gateway_lost()
            raise
        except (TimeoutError, ProtocolError) as error:
            state.optional_unavailable.add(spec.start)
            _LOGGER.debug(
                "Optional read slave %s 0x%04X: %s", device.slave, spec.start, error
            )
        else:
            state.raw.update(raw)
            state.optional_unavailable.discard(spec.start)

    async def async_poll_round(
        self, *, slow: bool = False, background: bool = False
    ) -> None:
        async with self._round_lock:
            started = asyncio.get_running_loop().time()
            successes = 0
            try:
                for device in self.devices:
                    if background and self.background_polling_paused:
                        break
                    if slow:
                        specs = [
                            TARGET,
                            *(
                                PollSpec(OPTIONAL_REGISTERS[c])
                                for c in device.capabilities
                                if c in OPTIONAL_REGISTERS
                            ),
                        ]
                        for spec in specs:
                            if background and self.background_polling_paused:
                                break
                            await self._optional_read(device, spec)
                    else:
                        successes += await self.async_readback(device)
            except GatewayError:
                return
            if not slow:
                self._round_times.append(
                    (asyncio.get_running_loop().time() - started) * 1000
                )
                if (
                    self.devices
                    and not successes
                    and not self.background_polling_paused
                ):
                    await self.transport.disconnect()
            self.async_set_updated_data(self.states)

    async def _async_update_data(self) -> dict[int, DeviceState]:
        await self.async_poll_round()
        return self.states

    async def async_command(self, slave: int, field: str, value: str | int) -> None:
        if self.scanning or not self.gateway_available:
            raise OfflineError("Gateway unavailable or scanner owns bus")
        device = next(d for d in self.devices if d.slave == slave)
        request = command(device, self.states[slave], field, value)
        try:
            result = await self.transport.submit(request, key=(slave, "climate", field))
            if result is None:
                return
            # A readback error preserves all previously confirmed values.
            await self.async_readback(device)
            await self._optional_read(device, TARGET)
            self.async_set_updated_data(self.states)
        except GatewayError:
            self.gateway_lost()
            raise

    async def _wait(self, timeout: float | None = None) -> None:
        try:
            async with asyncio.timeout(timeout):
                await self._wake.wait()
        except TimeoutError:
            pass
        self._wake.clear()

    async def _loop(self) -> None:
        fast_due = slow_due = 0.0
        loop = asyncio.get_running_loop()
        while not self._closed:
            if not self.gateway_available:
                await asyncio.sleep(self.next_backoff())
                try:
                    await self.transport.connect()
                except GatewayError:
                    continue
                self.gateway_available = True
                self._backoff_index = 0
                # Devices remain unavailable until a real read succeeds.
                fast_due = slow_due = 0.0
            if self.background_polling_paused:
                await self._wait()
                fast_due = slow_due = 0.0
                continue
            now = loop.time()
            if now >= fast_due:
                started = loop.time()
                await self.async_poll_round(background=True)
                fast_due = started + self.fast_poll_interval
            if (
                self.gateway_available
                and self.slow_poll_interval
                and loop.time() >= slow_due
            ):
                started = loop.time()
                await self.async_poll_round(slow=True, background=True)
                slow_due = started + self.slow_poll_interval
            if not self.gateway_available:
                continue
            due = min(fast_due, slow_due) if self.slow_poll_interval else fast_due
            await asyncio.sleep(0)
            await self._wait(max(0, due - loop.time()))

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="hitachi-poll")

    @asynccontextmanager
    async def pause(self) -> AsyncIterator[None]:
        async with self._round_lock:
            self.scanning = True
            try:
                yield
            finally:
                self.scanning = False
                self._wake.set()

    async def stop(self) -> None:
        self._closed = True
        if self._task:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None
