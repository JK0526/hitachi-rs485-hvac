"""Authenticated manual RTU requests with documented Hitachi write boundaries."""

from .profiles import FAN_CODES, MODE_CODES, command
from .protocol import frame, read_holding_registers, write_multiple_registers


def manual_request(
    text: str, auto_crc: bool, coordinator, confirmed: bool = False
) -> bytes:
    if len(text) > 1024:
        raise ValueError("HEX input is too long")
    packet = bytes.fromhex(text)
    if not auto_crc:
        if len(packet) < 8 or frame(packet[:-2]) != packet:
            raise ValueError("Invalid CRC or incomplete packet")
        body = packet[:-2]
    else:
        body = packet
    if len(body) < 6:
        raise ValueError("Incomplete request")
    slave, function = body[:2]
    start = int.from_bytes(body[2:4], "big")
    value = int.from_bytes(body[4:6], "big")
    if not 1 <= slave <= 247:
        raise ValueError("Broadcast and invalid slave addresses are prohibited")
    if function == 3:
        if len(body) != 6:
            raise ValueError("Invalid read length")
        return read_holding_registers(slave, start, value)
    if function not in (6, 16):
        raise ValueError("Only FC03, FC06 and FC10 are supported")
    if not confirmed:
        raise ValueError("Explicit write confirmation is required")
    device = next((d for d in coordinator.devices if d.slave == slave), None)
    if device is None:
        raise ValueError("Writes require a configured supported device")
    if function == 6:
        if len(body) != 6:
            raise ValueError("Invalid write length")
        values = [value]
    else:
        if not 1 <= value <= 4 or len(body) != 7 + value * 2 or body[6] != value * 2:
            raise ValueError("Invalid write count or byte count")
        values = [
            int.from_bytes(body[i : i + 2], "big") for i in range(7, len(body), 2)
        ]
    for address, raw in enumerate(values, start):
        if address == 0x20:
            field, decoded = "power", raw
        elif address in (0x21, 0x22):
            field = "mode" if address == 0x21 else "fan"
            codes = MODE_CODES if address == 0x21 else FAN_CODES
            decoded = next((name for name, code in codes.items() if code == raw), None)
        elif address == 0x23:
            field, decoded = "temperature", raw
        else:
            raise ValueError("Unknown, reserved and reset writes are prohibited")
        command(device, coordinator.states[slave], field, decoded)
    return (
        frame(body) if function == 6 else write_multiple_registers(slave, start, values)
    )
