"""Read-only discovery under the transport's exclusive bus lease."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from .models import Device
from .profiles import (
    DISCOVERY,
    IDENTITY_REGISTERS,
    OPTIONAL_REGISTERS,
    PollSpec,
    classify,
    decode_identity,
)
from .protocol import DeviceException, ProtocolError
from .transport import Transport

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScanResult:
    device: Device
    supported: bool

    @property
    def slave(self) -> int:
        return self.device.slave


def validate_range(start: int, end: int) -> None:
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= 247:
        raise ValueError("Invalid scan range")


async def _probe(transport: Transport, slave: int, spec: PollSpec) -> dict[int, int]:
    try:
        response = await transport.submit(
            spec.request(slave), poll=True, exclusive=True
        )
        return spec.decode(response)
    except (TimeoutError, ProtocolError) as error:
        _LOGGER.debug("Slave %s probe 0x%04X failed: %s", slave, spec.start, error)
        return {}


async def scan(
    transport: Transport,
    start: int = 1,
    end: int = 247,
    progress: Callable[[int, int, int], None] | None = None,
) -> list[ScanResult]:
    validate_range(start, end)
    found = []
    async with transport.exclusive():
        for index, slave in enumerate(range(start, end + 1), 1):
            # Exception confirms presence but cannot establish a supported identity.
            try:
                response = await transport.submit(
                    DISCOVERY.request(slave), poll=True, exclusive=True
                )
            except DeviceException:
                raw = {}
            except TimeoutError, ProtocolError:
                if progress:
                    progress(index, end - start + 1, len(found))
                continue
            else:
                raw = DISCOVERY.decode(response)
            for register in IDENTITY_REGISTERS:
                raw.update(await _probe(transport, slave, PollSpec(register)))
            family, interface, supported = classify(raw)
            capabilities = []
            if supported:
                raw.update(await _probe(transport, slave, PollSpec(4)))
                for capability, register in OPTIONAL_REGISTERS.items():
                    result = await _probe(transport, slave, PollSpec(register))
                    if result:
                        capabilities.append(capability)
                        raw.update(result)
            series, version = decode_identity(raw)
            found.append(
                ScanResult(
                    Device(
                        slave,
                        family,
                        interface,
                        raw.get(5),
                        raw.get(6),
                        series,
                        version,
                        tuple(capabilities),
                        identity=raw,
                    ),
                    supported,
                )
            )
            if progress:
                progress(index, end - start + 1, len(found))
    return found
