"""Read-only discovery under the transport's exclusive bus lease."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum

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


class ProbeStatus(StrEnum):
    """A failed probe is not necessarily an unsupported capability."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    TRANSIENT_FAILURE = "transient_failure"


@dataclass(frozen=True)
class ScanResult:
    device: Device
    supported: bool
    capability_probes: dict[str, ProbeStatus] = field(default_factory=dict)
    identity_errors: dict[int, str] = field(default_factory=dict)

    @property
    def slave(self) -> int:
        return self.device.slave


def validate_range(start: int, end: int) -> None:
    if type(start) is not int or type(end) is not int or not 1 <= start <= end <= 247:
        raise ValueError("Invalid scan range")


async def _probe(
    transport: Transport, slave: int, spec: PollSpec
) -> tuple[dict[int, int], ProbeStatus]:
    try:
        response = await transport.submit(
            spec.request(slave), poll=True, exclusive=True
        )
        return spec.decode(response), ProbeStatus.SUPPORTED
    except DeviceException as error:
        _LOGGER.debug("Slave %s probe 0x%04X failed: %s", slave, spec.start, error)
        status = (
            ProbeStatus.UNSUPPORTED
            if error.code == 2
            else ProbeStatus.TRANSIENT_FAILURE
        )
        return {}, status
    except (TimeoutError, ProtocolError) as error:
        _LOGGER.debug("Slave %s probe 0x%04X failed: %s", slave, spec.start, error)
        return {}, ProbeStatus.TRANSIENT_FAILURE


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
            identity_errors = {}
            for register in IDENTITY_REGISTERS:
                try:
                    response = await transport.submit(PollSpec(register).request(slave), poll=True, exclusive=True)
                    raw.update(PollSpec(register).decode(response))
                except DeviceException as error:
                    identity_errors[register] = f"exception {error.code}"
                except TimeoutError:
                    identity_errors[register] = "timeout"
                except ProtocolError as error:
                    identity_errors[register] = type(error).__name__
            family, interface, supported = classify(raw)
            capabilities = []
            capability_probes = {}
            if supported:
                for capability, register in OPTIONAL_REGISTERS.items():
                    values, status = await _probe(transport, slave, PollSpec(register))
                    capability_probes[capability] = status
                    if status == ProbeStatus.SUPPORTED:
                        capabilities.append(capability)
                        raw.update(values)
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
                    capability_probes,
                    identity_errors,
                )
            )
            if progress:
                progress(index, end - start + 1, len(found))
    return found
