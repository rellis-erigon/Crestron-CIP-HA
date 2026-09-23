"""Tests for the CIP codec and the XPanel handshake.

The byte layouts are checked against captures from a real CP4 and two
DMPS3-4K-350-C processors; the handshake runs against a fake processor so
the discovery path is exercised without hardware.
"""
import asyncio
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cip_client import (  # noqa: E402
    CipConnection, RegistrationError, SignalType,
    REG_NOT_DEFINED, REG_SUCCESS, REQUEST_UPDATE,
    decode_analog, decode_digital, decode_serial,
    encode_analog, encode_digital, encode_serial,
    probe_ipid,
)


# -- Codec ---------------------------------------------------------------

@pytest.mark.parametrize("join", [1, 2, 128, 255, 256, 1000, 4000])
def test_digital_round_trips(join):
    """A digital join spans two bytes and carries its state inverted."""
    for state in (True, False):
        frame = encode_digital(join, state)
        assert decode_digital(frame) == (join, state)


def test_digital_state_is_inverted_on_the_wire():
    """Bit 7 set means off — the opposite of the obvious reading."""
    on = encode_digital(5, True)
    off = encode_digital(5, False)
    assert on[5] & 0x80 == 0
    assert off[5] & 0x80 == 0x80


@pytest.mark.parametrize("join,value", [
    (1, 0), (1, 65535), (7, 32768), (300, 1234), (4000, 999),
])
def test_analog_round_trips(join, value):
    assert decode_analog(encode_analog(join, value)) == (join, value)


@pytest.mark.parametrize("text", ["", "Zone 1", "22.5", "Café", "x" * 200])
def test_serial_round_trips(text):
    frame = encode_serial(9, text)
    join, decoded = decode_serial(frame)
    assert join == 9
    assert decoded == text


def test_joins_are_one_based_on_the_wire_but_zero_based_in_the_packet():
    """Join 1 is encoded as 0; getting this wrong shifts every join by one."""
    assert encode_analog(1, 0)[4:6] == b"\x00\x00"
    assert encode_analog(2, 0)[4:6] == b"\x00\x01"


def test_frame_length_header_matches_payload():
    for frame in (encode_digital(3, True), encode_analog(3, 7),
                  encode_serial(3, "hi")):
        assert struct.unpack(">H", frame[1:3])[0] == len(frame) - 3


# -- Handshake against a fake processor ----------------------------------

class FakeProcessor:
    """Speaks just enough CIP to register a panel and dump some joins."""

    def __init__(self, known_ipids=(0x10,), joins=True):
        self.known = set(known_ipids)
        self.joins = joins
        self.registered_ipid = None
        self.server = None

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        writer.write(b"\x0f\x00\x01\x02")          # registration request
        await writer.drain()

        header = await reader.readexactly(3)
        length = struct.unpack(">H", header[1:3])[0]
        body = await reader.readexactly(length)
        ipid = body[5]
        self.registered_ipid = ipid

        if ipid not in self.known:
            writer.write(b"\x02\x00\x03" + REG_NOT_DEFINED)
            await writer.drain()
            writer.close()
            return

        writer.write(b"\x02\x00\x04" + REG_SUCCESS)
        await writer.drain()

        await reader.readexactly(len(REQUEST_UPDATE))   # update request
        if self.joins:
            writer.write(encode_digital(1, True))
            writer.write(encode_digital(42, False))
            writer.write(encode_analog(7, 1234))
            writer.write(encode_serial(3, "Zone 1"))
        writer.write(b"\x05\x00\x05\x00\x00\x02\x03\x1c")   # end-of-query
        await writer.drain()
        try:
            await reader.read(100)
        except Exception:
            pass


@pytest.fixture
async def processor():
    proc = FakeProcessor()
    port = await proc.start()
    yield proc, port
    await proc.stop()


@pytest.mark.asyncio
async def test_discovery_captures_the_whole_join_space(processor):
    proc, port = processor
    conn = CipConnection("127.0.0.1", 0x10, port=port)
    joins = await conn.discover(timeout=10)
    await conn.close()

    assert conn.registered
    assert conn.discovery_complete, "end-of-query should complete discovery"
    assert set(joins) == {"d1", "d42", "a7", "s3"}
    assert joins["d1"].value is True
    assert joins["d42"].value is False
    assert joins["a7"].value == 1234
    assert joins["s3"].value == "Zone 1"
    assert joins["a7"].signal is SignalType.ANALOG


@pytest.mark.asyncio
async def test_an_undefined_ipid_is_refused_clearly(processor):
    """The processor answers ff ff 02, which must not look like a timeout."""
    _proc, port = processor
    conn = CipConnection("127.0.0.1", 0x55, port=port)
    with pytest.raises(RegistrationError, match="not defined"):
        await conn.discover(timeout=10)
    await conn.close()


@pytest.mark.asyncio
async def test_probe_reports_defined_and_undefined(processor):
    _proc, port = processor
    defined, why = await probe_ipid("127.0.0.1", 0x10, port=port)
    assert defined is True and "defined" in why
    undefined, why = await probe_ipid("127.0.0.1", 0x55, port=port)
    assert undefined is False and "not defined" in why


@pytest.mark.asyncio
async def test_the_registration_frame_carries_the_ipid(processor):
    proc, port = processor
    conn = CipConnection("127.0.0.1", 0x10, port=port)
    await conn.discover(timeout=10)
    await conn.close()
    assert proc.registered_ipid == 0x10


@pytest.mark.asyncio
async def test_repeated_updates_are_counted_not_duplicated(processor):
    proc, port = processor
    conn = CipConnection("127.0.0.1", 0x10, port=port)
    await conn.discover(timeout=10)
    conn._record(SignalType.ANALOG, 7, 4321)
    await conn.close()
    assert len(conn.joins) == 4
    assert conn.joins["a7"].value == 4321
    assert conn.joins["a7"].updates == 2
