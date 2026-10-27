"""SB-12024 v6.0 printed pp21-24: all Hitachi semantics live here."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import IntEnum

from .models import Device, DeviceState, HeatSupport
from .protocol import read_holding_registers, write_single_register

_LOGGER = logging.getLogger(__name__)


class Register(IntEnum):
    PROBE = 0x00
    HEAT = 0x04
    UNIT_TYPE = 0x05
    CAPACITY = 0x06
    SERIES_1 = 0x07
    SERIES_2 = 0x08
    SAANET = 0x09
    OUTDOOR_TEMPERATURE = 0x0A
    CURRENT = 0x0F
    POWER = 0x20
    MODE = 0x21
    FAN = 0x22
    TARGET = 0x23
    SWING_VERTICAL = 0x2A
    SWING_HORIZONTAL = 0x2B
    ENERGY = 0x38
    MAINTENANCE_RUNTIME = 0x39
    FILTER_RUNTIME = 0x3A
    POWER_STATE = 0x40
    MODE_STATE = 0x41
    FAN_STATE = 0x42
    INDOOR_TEMPERATURE = 0x43
    ERROR = 0x44


MODE_CODES = {"cool": 0, "dry": 1, "fan_only": 2, "auto": 3, "heat": 4}
FAN_CODES = {"auto": 5, "high": 4, "medium": 3, "low": 1, "quiet": 7}
UNIT_TYPES = {1: "RAD", 2: "RAS", 3: "RA", 4: "CRD", 5: "CRS", 6: "RPI"}
OPTIONAL_REGISTERS = {
    "outdoor_temperature": Register.OUTDOOR_TEMPERATURE,
    "current": Register.CURRENT,
    "energy": Register.ENERGY,
    "maintenance_runtime": Register.MAINTENANCE_RUNTIME,
    "filter_runtime": Register.FILTER_RUNTIME,
}


@dataclass(frozen=True)
class PollSpec:
    start: int
    count: int = 1

    def request(self, slave: int) -> bytes:
        return read_holding_registers(slave, self.start, self.count)

    def decode(self, response: bytes) -> dict[int, int]:
        return {
            self.start + i: int.from_bytes(response[3 + i * 2 : 5 + i * 2], "big")
            for i in range(self.count)
        }


FAST = PollSpec(Register.POWER_STATE, 5)
TARGET = PollSpec(Register.TARGET)
DISCOVERY = PollSpec(Register.PROBE)
IDENTITY_REGISTERS = tuple(range(5, 10))


def supports_heat(device: Device, state: DeviceState) -> bool:
    if device.heat != HeatSupport.AUTO:
        return device.heat == HeatSupport.SUPPORTED
    return state.observed_heat or device.identity.get(Register.HEAT) == 1


def hvac_mode(state: DeviceState) -> str | None:
    if state.raw.get(Register.POWER_STATE) == 0:
        return "off"
    if state.raw.get(Register.POWER_STATE) != 1:
        return None
    return next(
        (k for k, v in MODE_CODES.items() if v == state.raw.get(Register.MODE_STATE)),
        None,
    )


def fan_mode(raw: int | None) -> str | None:
    result = next((k for k, v in FAN_CODES.items() if v == raw), None)
    if raw is not None and result is None:
        _LOGGER.debug("Unknown fan code %s", raw)
    return result


def energy_kwh(raw: int | None) -> float | None:
    return raw / 10 if raw is not None and 0 <= raw <= 40000 else None


def command(device: Device, state: DeviceState, field: str, value: str | int) -> bytes:
    """Only documented climate writes; counters and arbitrary writes cannot enter."""
    if field == "power":
        if type(value) is not int or value not in (0, 1):
            raise ValueError("Invalid power")
        register, raw = Register.POWER, value
    elif field == "mode":
        if value not in MODE_CODES or (
            value == "heat" and not supports_heat(device, state)
        ):
            raise ValueError("Unsupported mode")
        if value == "auto" and device.unit_type == 6:
            raise ValueError("RPI has no auto mode")
        register, raw = Register.MODE, MODE_CODES[value]
    elif field == "fan":
        if value not in FAN_CODES:
            raise ValueError("Unsupported fan")
        # Commercial fans use additional documented values; quiet is residential only.
        if device.unit_type == 6 and value == "quiet":
            raise ValueError("Commercial quiet is undocumented")
        register, raw = Register.FAN, FAN_CODES[value]
    elif field == "temperature":
        if type(value) is not int or not 16 <= value <= 32:
            raise ValueError("Temperature must be an integer 16..32")
        register, raw = Register.TARGET, value
    else:
        raise ValueError("Unsupported field")
    return write_single_register(device.slave, register, raw)


def decode_identity(raw: dict[int, int]) -> tuple[str, str]:
    series = (
        b"".join(raw.get(r, 0).to_bytes(2, "big") for r in (7, 8))
        .decode("ascii", errors="replace")
        .strip("\x00 ")
    )
    version = raw.get(9)
    return series, f"{version >> 8}.{version & 255}" if version is not None else ""


def classify(raw: dict[int, int]) -> tuple[str, str, bool]:
    unit = raw.get(5)
    if unit == 32:
        return "heat_exchanger", "unknown", False
    if unit in (7, 8):
        return "dehumidifier", "RC-R02XT", False
    if all(r in raw for r in IDENTITY_REGISTERS) and unit in UNIT_TYPES:
        return "air_conditioner", "RC-R02XT", True
    return "unknown_legacy", "unknown", False
