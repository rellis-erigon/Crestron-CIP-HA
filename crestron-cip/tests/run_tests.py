"""Stdlib test runner for the CIP client.

pytest is not installable in this container, so the same assertions run
here. tests/test_cip_client.py holds the pytest version for CI.
"""
import asyncio
import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cip_client import (  # noqa: E402
    REG_NOT_DEFINED, REG_SUCCESS, REQUEST_UPDATE,
    CipConnection, RegistrationError, SignalType,
    decode_analog, decode_digital, decode_serial,
    encode_analog, encode_digital, encode_serial,
    payload_of, probe_ipid,
)

passed = failed = 0


def check(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  PASS  {name}")
    except AssertionError as exc:
        failed += 1
        print(f"  FAIL  {name}: {exc}")


# -- codec ---------------------------------------------------------------

def t_digital():
    for join in (1, 2, 128, 255, 256, 1000, 4000):
        for state in (True, False):
            got = decode_digital(payload_of(encode_digital(join, state)))
            assert got == (join, state), f"join {join} state {state} -> {got}"


def t_digital_inversion():
    assert payload_of(encode_digital(5, True))[5] & 0x80 == 0
    assert payload_of(encode_digital(5, False))[5] & 0x80 == 0x80


def t_analog():
    for join, value in ((1, 0), (1, 65535), (7, 32768), (300, 1234), (4000, 999)):
        got = decode_analog(payload_of(encode_analog(join, value)))
        assert got == (join, value), f"{join},{value} -> {got}"


def t_serial():
    for text in ("", "Zone 1", "22.5", "x" * 200):
        got = decode_serial(payload_of(encode_serial(9, text)))
        assert got == (9, text), f"{text!r} -> {got!r}"


def t_zero_based():
    assert payload_of(encode_analog(1, 0))[4:6] == b"\x00\x00"
    assert payload_of(encode_analog(2, 0))[4:6] == b"\x00\x01"


def t_length_header():
    for frame in (encode_digital(3, True), encode_analog(3, 7),
                  encode_serial(3, "hi")):
        declared = struct.unpack(">H", frame[1:3])[0]
        assert declared == len(frame) - 3, frame.hex()


# -- handshake -----------------------------------------------------------

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


async def async_tests():
    global passed, failed
    proc = FakeProcessor()
    port = await proc.start()

    conn = CipConnection("127.0.0.1", 0x10, port=port)
    joins = await conn.discover(timeout=12)
    # Capture before closing: close() correctly clears the registration.
    was_registered = conn.registered
    was_complete = conn.discovery_complete
    await conn.close()
    try:
        assert was_registered, "should have registered"
        assert was_complete, "end-of-query should complete discovery"
        assert proc.update_requested, "should have asked for an update"
        assert set(joins) == {"d1", "d42", "a7", "s3"}, sorted(joins)
        assert joins["d1"].value is True
        assert joins["d42"].value is False
        assert joins["a7"].value == 1234
        assert joins["s3"].value == "Zone 1"
        assert joins["a7"].signal is SignalType.ANALOG
        assert proc.seen_ipids[0] == 0x10
        passed += 1
        print("  PASS  discovery captures the whole join space")
    except AssertionError as exc:
        failed += 1
        print(f"  FAIL  discovery: {exc}")

    undefined = CipConnection("127.0.0.1", 0x55, port=port)
    try:
        await undefined.discover(timeout=8)
        failed += 1
        print("  FAIL  an undefined IPID should raise")
    except RegistrationError as exc:
        passed += 1
        print(f"  PASS  undefined IPID refused: {str(exc)[:44]}...")
    finally:
        await undefined.close()

    is_defined, _ = await probe_ipid("127.0.0.1", 0x10, port=port)
    not_defined, _ = await probe_ipid("127.0.0.1", 0x55, port=port)
    if is_defined and not not_defined:
        passed += 1
        print("  PASS  probe distinguishes defined from undefined")
    else:
        failed += 1
        print(f"  FAIL  probe: {is_defined=} {not_defined=}")

    await proc.stop()


print("codec:")
for name, fn in (
    ("digital round trip", t_digital),
    ("digital OFF sets bit 7", t_digital_inversion),
    ("analog round trip", t_analog),
    ("serial round trip", t_serial),
    ("join 1 encodes as 0", t_zero_based),
    ("length header matches body", t_length_header),
):
    check(name, fn)

print("\nhandshake against a fake processor:")
asyncio.run(async_tests())

print(f"\n{passed} passed, {failed} failed")
sys.exit(1 if failed else 0)
