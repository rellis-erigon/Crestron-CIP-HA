"""Tests for the CIP codec and the XPanel handshake.

Byte layouts follow a working XPanel session; the handshake runs against a
fake processor so the discovery path is exercised without hardware.

The decoders take the payload — everything after the three-byte type/length
header — which is what arrives off the wire. payload_of() strips that header
from a frame the encoders built.
"""
import asyncio
import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cip_client import (  # noqa: E402
    REG_NOT_DEFINED, REG_SUCCESS, REQUEST_UPDATE,
    CipConnection, RegistrationError, SignalType,
    decode_analog, decode_digital, decode_serial,
    encode_analog, encode_digital, encode_serial,
    payload_of, probe_ipid,
)


# -- Codec ---------------------------------------------------------------

@pytest.mark.parametrize("join", [1, 2, 128, 255, 256, 1000, 4000])
@pytest.mark.parametrize("state", [True, False])
def test_digital_round_trips(join, state):
    assert decode_digital(payload_of(encode_digital(join, state))) == (join, state)


def test_digital_state_is_inverted_on_the_wire():
    """Bit 7 set means OFF — the opposite of the obvious reading."""
    assert payload_of(encode_digital(5, True))[5] & 0x80 == 0
    assert payload_of(encode_digital(5, False))[5] & 0x80 == 0x80


@pytest.mark.parametrize("join,value", [
    (1, 0), (1, 65535), (7, 32768), (300, 1234), (4000, 999),
])
def test_analog_round_trips(join, value):
    assert decode_analog(payload_of(encode_analog(join, value))) == (join, value)


@pytest.mark.parametrize("text", ["", "Zone 1", "22.5", "x" * 200])
def test_serial_round_trips(text):
    assert decode_serial(payload_of(encode_serial(9, text))) == (9, text)


def test_join_one_is_encoded_as_zero():
    """Off by one here shifts every join in the system."""
    assert payload_of(encode_analog(1, 0))[4:6] == b"\x00\x00"
    assert payload_of(encode_analog(2, 0))[4:6] == b"\x00\x01"


@pytest.mark.parametrize("frame", [
    encode_digital(3, True), encode_analog(3, 7), encode_serial(3, "hi"),
])
def test_length_header_matches_the_body(frame):
    assert struct.unpack(">H", frame[1:3])[0] == len(frame) - 3


# -- Handshake -----------------------------------------------------------

class FakeProcessor:
    """Speaks enough CIP to register a panel and dump a join space."""

    def __init__(self, known=(0x10,)):
        self.known = set(known)
        self.seen_ipids = []
        self.update_requested = False

    async def start(self):
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        return self.server.sockets[0].getsockname()[1]

    async def stop(self):
        self.server.close()
        await self.server.wait_closed()

    async def _handle(self, reader, writer):
        try:
            writer.write(b"\x0f\x00\x01\x02")
            await writer.drain()
            header = await reader.readexactly(3)
            body = await reader.readexactly(struct.unpack(">H", header[1:3])[0])
            ipid = body[5]
            self.seen_ipids.append(ipid)

            if ipid not in self.known:
                writer.write(b"\x02\x00\x03" + REG_NOT_DEFINED)
                await writer.drain()
                return

            writer.write(b"\x02\x00\x04" + REG_SUCCESS)
            await writer.drain()
            await reader.readexactly(len(REQUEST_UPDATE))
            self.update_requested = True

            for frame in (encode_digital(1, True), encode_digital(42, False),
                          encode_analog(7, 1234), encode_serial(3, "Zone 1")):
                writer.write(frame)
            writer.write(b"\x05\x00\x05\x00\x00\x02\x03\x1c")
            await writer.drain()
            await asyncio.sleep(1.5)
        except (asyncio.IncompleteReadError, ConnectionResetError):
            pass
        finally:
            writer.close()


@pytest.fixture
async def processor():
    proc = FakeProcessor()
    port = await proc.start()
    yield proc, port
    await proc.stop()


async def test_discovery_captures_the_whole_join_space(processor):
    proc, port = processor
    conn = CipConnection("127.0.0.1", 0x10, port=port)
    joins = await conn.discover(timeout=12)
    # Capture before closing: close() correctly clears the registration.
    registered, complete = conn.registered, conn.discovery_complete
    await conn.close()

    assert registered
    assert complete, "end-of-query should complete discovery"
    assert proc.update_requested, "should have asked for an update"
    assert set(joins) == {"d1", "d42", "a7", "s3"}
    assert joins["d1"].value is True
    assert joins["d42"].value is False
    assert joins["a7"].value == 1234
    assert joins["s3"].value == "Zone 1"
    assert joins["a7"].signal is SignalType.ANALOG
    assert proc.seen_ipids[0] == 0x10


async def test_an_undefined_ipid_is_refused_clearly(processor):
    """The processor answers ff ff 02; that must not look like a timeout."""
    _proc, port = processor
    conn = CipConnection("127.0.0.1", 0x55, port=port)
    with pytest.raises(RegistrationError, match="not defined"):
        await conn.discover(timeout=8)
    await conn.close()


async def test_probe_reports_defined_and_undefined(processor):
    _proc, port = processor
    defined, why = await probe_ipid("127.0.0.1", 0x10, port=port)
    assert defined is True and "defined" in why
    undefined, why = await probe_ipid("127.0.0.1", 0x55, port=port)
    assert undefined is False and "not defined" in why


async def test_a_later_update_replaces_the_value(processor):
    _proc, port = processor
    conn = CipConnection("127.0.0.1", 0x10, port=port)
    await conn.discover(timeout=12)
    conn._record(SignalType.ANALOG, 7, 4321)
    await conn.close()
    assert len(conn.joins) == 4, "an update must not create a second join"
    assert conn.joins["a7"].value == 4321
    assert conn.joins["a7"].updates == 2


def test_reconnect_delay_widens_to_a_ceiling():
    from cip_client import RECONNECT_CAP, next_reconnect_delay

    delay = 10.0
    seen = [delay]
    for _ in range(6):
        delay = next_reconnect_delay(delay)
        seen.append(delay)
    assert seen[:4] == [10.0, 20.0, 40.0, 80.0]
    assert seen[-1] == RECONNECT_CAP
    # A processor that comes back must be picked up in reasonable time, so
    # the gap has to stop growing.
    assert next_reconnect_delay(RECONNECT_CAP) == RECONNECT_CAP


def test_reconnect_delay_never_drops_below_the_base():
    from cip_client import next_reconnect_delay

    assert next_reconnect_delay(0.0, base=10.0) == 10.0
    assert next_reconnect_delay(1.0, base=10.0) == 10.0


class _StubWriter:
    """Minimal asyncio StreamWriter stand-in that records what was sent."""

    def __init__(self, fail: bool = False) -> None:
        self.sent = b""
        self.closed = False
        self._fail = fail

    def write(self, data: bytes) -> None:
        if self._fail:
            raise ConnectionResetError("peer went away")
        self.sent += data

    async def drain(self) -> None:
        if self._fail:
            raise ConnectionResetError("peer went away")

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        pass


async def test_close_tells_the_processor_before_dropping():
    from cip_client import DISCONNECT, CipConnection

    conn = CipConnection("192.0.2.10", 0x03, name="P")
    writer = _StubWriter()
    conn._writer = writer
    conn.connected = conn.registered = True

    await conn.close()

    assert writer.sent == DISCONNECT
    assert writer.closed
    assert not conn.registered and not conn.connected


async def test_close_on_a_broken_socket_still_closes():
    """The disconnect is a courtesy; failing to send it must not raise."""
    from cip_client import CipConnection

    conn = CipConnection("192.0.2.10", 0x03, name="P")
    writer = _StubWriter(fail=True)
    conn._writer = writer
    conn.connected = conn.registered = True

    await conn.close()

    assert writer.closed
    assert not conn.registered


async def test_close_sends_nothing_when_never_registered():
    from cip_client import CipConnection

    conn = CipConnection("192.0.2.10", 0x03, name="P")
    writer = _StubWriter()
    conn._writer = writer
    conn.connected = True

    await conn.close()

    assert writer.sent == b""
    assert writer.closed
