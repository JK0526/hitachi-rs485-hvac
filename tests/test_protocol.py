import asyncio

import pytest

from custom_components.hitachi_rs485.protocol import (
    CRCError,
    DeviceException,
    ProtocolError,
    crc16,
    frame,
    read_holding_registers,
    receive,
    validate,
    write_multiple_registers,
    write_single_register,
)


def test_crc_and_frames():
    assert crc16(bytes.fromhex("01030000000a")) == 0xCDC5
    assert read_holding_registers(1, 0, 10).hex() == "01030000000ac5cd"
    assert write_single_register(1, 32, 1) == frame(bytes.fromhex("010600200001"))
    request = write_multiple_registers(1, 32, [1, 0, 5, 24])
    assert request == frame(bytes.fromhex("011000200004080001000000050018"))
    assert validate(request, frame(request[:6]))
    assert validate(write_single_register(1, 32, 1), write_single_register(1, 32, 1))
    assert validate(read_holding_registers(1, 0, 1), frame(bytes.fromhex("0103020019")))


@pytest.mark.parametrize(
    "args",
    [
        (0, 0, 1),
        (248, 0, 1),
        (True, 0, 1),
        (1, -1, 1),
        (1, 65535, 2),
        (1, 0, 0),
        (1, 0, 126),
        (1, 0, True),
    ],
)
def test_invalid_read(args):
    with pytest.raises(ValueError):
        read_holding_registers(*args)


@pytest.mark.parametrize("values", [[], [0] * 124, [-1], [65536], [True]])
def test_invalid_multiple(values):
    with pytest.raises(ValueError):
        write_multiple_registers(1, 32, values)


def test_invalid_write():
    with pytest.raises(ValueError):
        write_single_register(1, 0, 65536)
    with pytest.raises(ValueError):
        write_multiple_registers(1, 65535, [1, 2])


@pytest.mark.parametrize(
    "response,error",
    [
        (b"", ProtocolError),
        (b"abcde", CRCError),
        (frame(bytes.fromhex("0203020001")), ProtocolError),
        (frame(bytes.fromhex("0104020001")), ProtocolError),
        (frame(bytes.fromhex("01030400010002")), ProtocolError),
    ],
)
def test_invalid_response(response, error):
    with pytest.raises(error):
        validate(read_holding_registers(1, 0, 1), response)


@pytest.mark.parametrize("code", [1, 2, 3, 4, 6])
@pytest.mark.parametrize(
    "packet",
    [
        read_holding_registers(1, 0, 1),
        write_single_register(1, 32, 1),
        write_multiple_registers(1, 32, [1]),
    ],
)
def test_exceptions(code, packet):
    with pytest.raises(DeviceException) as exc:
        validate(packet, frame(bytes([1, packet[1] | 128, code])))
    assert exc.value.code == code


def test_echo_and_unsupported():
    with pytest.raises(ProtocolError):
        validate(write_single_register(1, 32, 1), write_single_register(1, 32, 0))
    with pytest.raises(ProtocolError):
        validate(
            write_multiple_registers(1, 32, [1]), frame(bytes.fromhex("011000210001"))
        )
    with pytest.raises(ProtocolError):
        validate(
            frame(bytes.fromhex("010700000001")), frame(bytes.fromhex("010700000001"))
        )


@pytest.mark.parametrize(
    "packet",
    [
        frame(bytes.fromhex("0103020019")),
        write_single_register(1, 32, 1),
        frame(bytes.fromhex("011000200001")),
        frame(bytes.fromhex("019002")),
    ],
)
async def test_fragmentation(packet):
    reader = asyncio.StreamReader()
    task = asyncio.create_task(receive(reader))
    for byte in packet:
        reader.feed_data(bytes([byte]))
        await asyncio.sleep(0)
    assert await task == packet


@pytest.mark.parametrize("packet", [b"\x01\x03\x03", b"\x01\x03\x00", b"\x01\x07"])
async def test_bad_receive(packet):
    reader = asyncio.StreamReader()
    reader.feed_data(packet)
    with pytest.raises(ProtocolError):
        await receive(reader)
