"""Serializable identities and restart-local confirmed device state."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class HeatSupport(StrEnum):
    AUTO = "auto"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"


@dataclass(frozen=True)
class Device:
    slave: int
    family: str = "air_conditioner"
    interface: str = "RC-R02XT"
    unit_type: int | None = None
    capacity: int | None = None
    series: str = ""
    saanet_version: str = ""
    capabilities: tuple[str, ...] = ()
    heat: HeatSupport = HeatSupport.AUTO
    identity: dict[int, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.slave) is not int or not 1 <= self.slave <= 247:
            raise ValueError("Invalid slave")
        object.__setattr__(self, "heat", HeatSupport(self.heat))
        object.__setattr__(self, "capabilities", tuple(self.capabilities))
        object.__setattr__(
            self, "identity", {int(k): v for k, v in self.identity.items()}
        )

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["heat"] = self.heat.value
        result["capabilities"] = list(self.capabilities)
        result["identity"] = {str(k): v for k, v in self.identity.items()}
        return result

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Device:
        return cls(**data)


@dataclass
class DeviceState:
    raw: dict[int, int] = field(default_factory=dict)
    available: bool = False
    consecutive_failures: int = 0
    observed_heat: bool = False
    optional_unavailable: set[int] = field(default_factory=set)
    communication_issue: str | None = None


def devices_from_options(options: dict[str, Any]) -> tuple[Device, ...]:
    devices = tuple(Device.from_dict(d) for d in options.get("devices", []))
    if len({d.slave for d in devices}) != len(devices):
        raise ValueError("Duplicate slaves")
    if any(d.family != "air_conditioner" or d.interface != "RC-R02XT" for d in devices):
        raise ValueError("Unsupported configured device")
    return devices
