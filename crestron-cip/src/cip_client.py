"""Crestron-over-IP (CIP) client.

Connects to a control processor as an XPanel and mirrors its join space.

A processor exposes a touchpanel's joins over CIP on port 41794. Registering
with an IPID the program defines as an XPanel, then asking for an update,
makes the processor dump every join it drives for that panel — digitals,
analogs and serials with their current values — followed by an end-of-query
marker. That dump is the discovery mechanism: nothing has to be typed in by
hand, and the panel describes itself.

Registering with an IPID that a real panel is using takes that panel's place
and knocks it offline, so a dedicated IPID is added to the program for this.
An IPID the program does not define is refused harmlessly, which is what
makes probing safe.
"""
from __future__ import annotations

import asyncio
import logging
import struct
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger("crestron-cip.client")

CIP_PORT = 41794

# Packet types, as the first byte of every frame.
TYPE_REGISTER = 0x01
TYPE_REG_RESULT = 0x02
TYPE_DISCONNECT = 0x03
TYPE_DATA = 0x05
TYPE_HEARTBEAT = 0x0D
TYPE_HEARTBEAT_ALT = 0x0E
TYPE_REG_REQUEST = 0x0F
TYPE_SERIAL = 0x12

# Sub-type, at payload[3] of a data frame.
DATA_DIGITAL = 0x00
DATA_UPDATE = 0x03
DATA_DATETIME = 0x08
DATA_ANALOG = 0x14

# Stage markers inside an update sequence.
UPDATE_STANDARD = 0x00
UPDATE_PENULTIMATE = 0x16
UPDATE_END_OF_QUERY = 0x1C
UPDATE_END_ACK = 0x1D

# Registration verdicts.
REG_NOT_DEFINED = b"\xff\xff\x02"
REG_SUCCESS = b"\x00\x00\x00\x1f"

REQUEST_UPDATE = b"\x05\x00\x05\x00\x00\x02\x03\x00"
END_OF_QUERY_ACK = b"\x05\x00\x05\x00\x00\x02\x03\x1d"
HEARTBEAT = b"\x0d\x00\x02\x00\x00"

HEARTBEAT_INTERVAL = 15.0


class SignalType(str, Enum):
    DIGITAL = "d"
    ANALOG = "a"
    SERIAL = "s"


class RegistrationError(Exception):
    """The processor refused the IPID."""


@dataclass
class Join:
    """One join seen on the panel, with whatever the processor last sent."""

    signal: SignalType
    number: int
    value: bool | int | str | None = None
    updates: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return f"{self.signal.value}{self.number}"


# Packet prefixes, captured from a working XPanel session. Everything after
# the three-byte header is the "payload"; the decoders below index into that,
# not into the whole frame.
PREFIX_DIGITAL = b"\x05\x00\x06\x00\x00\x03\x00"
PREFIX_ANALOG = b"\x05\x00\x08\x00\x00\x05\x14"
PREFIX_SERIAL = b"\x12\x00\x00\x00\x00\x00\x00\x34"


def payload_of(frame: bytes) -> bytes:
    """Strip the type/length header, leaving what the decoders expect."""
    return frame[3:]


def decode_digital(payload: bytes) -> tuple[int, bool]:
    """Digital joins are byte-swapped, and bit 7 set means OFF."""
    join = (((payload[5] & 0x7F) << 8) | payload[4]) + 1
    state = ((payload[5] & 0x80) >> 7) ^ 0x01
    return join, bool(state)


def decode_analog(payload: bytes) -> tuple[int, int]:
    join = ((payload[4] << 8) | payload[5]) + 1
    value = (payload[6] << 8) | payload[7]
    return join, value


def decode_serial(payload: bytes) -> tuple[int, str]:
    join = ((payload[5] << 8) | payload[6]) + 1
    return join, payload[8:].decode("utf-8", "replace")


def encode_digital(join: int, state: bool) -> bytes:
    """Build a digital-join frame.

    The join number is sent byte-swapped — low byte first — and the state is
    carried inverted in bit 7 of the high byte.
    """
    value = join - 1
    packed = (value // 256) + ((value % 256) * 256)
    if not state:
        packed |= 0x80
    return PREFIX_DIGITAL + struct.pack(">H", packed)


def encode_analog(join: int, value: int) -> bytes:
    return (PREFIX_ANALOG + struct.pack(">H", join - 1)
            + struct.pack(">H", value & 0xFFFF))


def encode_serial(join: int, text: str) -> bytes:
    data = text.encode("utf-8")
    frame = bytearray(PREFIX_SERIAL)
    # Two lengths travel in the header: the frame body and the inner payload.
    frame[2] = 8 + len(data)
    frame[6] = 4 + len(data)
    frame += struct.pack(">H", join - 1) + b"\x03" + data
    return bytes(frame)


RECONNECT_CAP = 120.0


def next_reconnect_delay(
    current: float, base: float = 10.0, cap: float = RECONNECT_CAP,
) -> float:
    """Widen the gap between reconnection attempts, up to a ceiling.

    The ceiling matters as much as the growth: a processor that comes back
    after an hour should be picked up within a couple of minutes, not left
    until some doubling sequence happens to come round again.
    """
    if current < base:
        return base
    return min(current * 2, cap)


class CipConnection:
    """One XPanel connection to one processor."""

    def __init__(
        self,
        host: str,
        ipid: int,
        name: str = "",
        port: int = CIP_PORT,
        on_join: Callable[[Join], None] | None = None,
    ) -> None:
        self.host = host
        self.ipid = ipid
        self.name = name or host
        self.port = port
        self._on_join = on_join

        self.joins: dict[str, Join] = {}
        self.connected = False
        self.registered = False
        # Set once the processor signals end-of-query, meaning the join dump
        # is complete and what we hold is the panel's full join space.
        self.discovery_complete = False
        self.last_error: str | None = None
        self.connected_since: float | None = None

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buf = b""

    # -- lifecycle -------------------------------------------------------

    async def connect(self, timeout: float = 10.0) -> None:
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(self.host, self.port), timeout,
        )
        self.connected = True
        self.connected_since = time.time()
        self.last_error = None
        logger.info("Connected to %s:%s as IPID 0x%02X",
                    self.host, self.port, self.ipid)

    async def close(self) -> None:
        self.connected = self.registered = False
        if self._writer is not None:
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except (OSError, asyncio.TimeoutError):
                pass
        self._reader = self._writer = None

    async def _send(self, data: bytes) -> None:
        if self._writer is None:
            raise ConnectionError("not connected")
        self._writer.write(data)
        await self._writer.drain()

    # -- protocol --------------------------------------------------------

    async def _read_frame(self, timeout: float) -> tuple[int, bytes] | None:
        """Return the next (type, payload), or None if the peer went away."""
        while True:
            if len(self._buf) >= 3:
                length = struct.unpack(">H", self._buf[1:3])[0]
                if len(self._buf) >= 3 + length:
                    ctype = self._buf[0]
                    payload = self._buf[3:3 + length]
                    self._buf = self._buf[3 + length:]
                    return ctype, payload
            assert self._reader is not None
            try:
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout)
            except asyncio.TimeoutError:
                return None
            if not chunk:
                return None
            self._buf += chunk

    def _record(self, signal: SignalType, number: int, value) -> Join:
        key = f"{signal.value}{number}"
        join = self.joins.get(key)
        if join is None:
            join = Join(signal=signal, number=number)
            self.joins[key] = join
        join.value = value
        join.updates += 1
        join.last_seen = time.time()
        if self._on_join is not None:
            self._on_join(join)
        return join

    async def _handle(self, ctype: int, payload: bytes) -> None:
        if ctype in (TYPE_HEARTBEAT, TYPE_HEARTBEAT_ALT):
            return

        if ctype == TYPE_REG_REQUEST:
            await self._send(
                b"\x01\x00\x0b\x00\x00\x00\x00\x00"
                + bytes([self.ipid])
                + b"\x40\xff\xff\xf1\x01"
            )
            return

        if ctype == TYPE_REG_RESULT:
            if payload == REG_NOT_DEFINED:
                raise RegistrationError(
                    f"IPID 0x{self.ipid:02X} is not defined in the program "
                    f"running on {self.host}"
                )
            if payload != REG_SUCCESS:
                raise RegistrationError(
                    f"IPID 0x{self.ipid:02X} refused by {self.host}: "
                    f"{payload.hex()}"
                )
            self.registered = True
            logger.info("Registered with %s as IPID 0x%02X", self.host, self.ipid)
            await self._send(REQUEST_UPDATE)
            return

        if ctype == TYPE_DISCONNECT:
            raise ConnectionError(f"{self.host} sent a disconnect")

        if ctype == TYPE_SERIAL:
            number, text = decode_serial(payload)
            self._record(SignalType.SERIAL, number, text)
            return

        if ctype != TYPE_DATA:
            logger.debug("Unhandled frame type 0x%02X from %s", ctype, self.host)
            return

        subtype = payload[3]
        if subtype == DATA_DIGITAL:
            number, state = decode_digital(payload)
            self._record(SignalType.DIGITAL, number, state)
        elif subtype == DATA_ANALOG:
            number, value = decode_analog(payload)
            self._record(SignalType.ANALOG, number, value)
        elif subtype == DATA_UPDATE:
            stage = payload[4]
            if stage == UPDATE_END_OF_QUERY:
                # The processor has finished dumping; acknowledge and start
                # the heartbeat that keeps the registration alive.
                await self._send(END_OF_QUERY_ACK)
                await self._send(HEARTBEAT)
                self.discovery_complete = True
                logger.info("Discovery complete on %s: %d joins",
                            self.host, len(self.joins))
        elif subtype == DATA_DATETIME:
            pass

    # -- public operations -----------------------------------------------

    async def discover(self, timeout: float = 20.0) -> dict[str, Join]:
        """Connect, register and capture the panel's whole join space.

        Returns once the processor signals end-of-query, so the result is
        the complete set of joins it drives for this panel.
        """
        await self.connect()
        deadline = time.time() + timeout
        while time.time() < deadline and not self.discovery_complete:
            frame = await self._read_frame(timeout=2.0)
            if frame is None:
                if self.discovery_complete:
                    break
                continue
            await self._handle(*frame)
        if not self.registered:
            raise RegistrationError(
                f"No registration result from {self.host} within {timeout:g}s"
            )
        return self.joins

    async def run(self, reconnect_delay: float = 10.0) -> None:
        """Stay connected, keep the join map current, reconnect on failure.

        A registration failure is not retried: an undefined IPID will still
        be undefined in ten seconds, and reconnecting in a loop would just
        hammer the processor.

        A *connection* failure is retried, but with a widening gap. One
        processor here began refusing TCP outright after an unclean
        disconnect, and a flat ten-second retry meant knocking on its door
        six times a minute for as long as it stayed shut.
        """
        delay = reconnect_delay
        while True:
            try:
                await self.discover()
                delay = reconnect_delay
                last_beat = time.time()
                while True:
                    frame = await self._read_frame(timeout=5.0)
                    if frame is not None:
                        await self._handle(*frame)
                    now = time.time()
                    if now - last_beat >= HEARTBEAT_INTERVAL:
                        await self._send(HEARTBEAT)
                        last_beat = now
            except RegistrationError as err:
                self.last_error = str(err)
                logger.error("%s", err)
                await self.close()
                return
            except (OSError, ConnectionError, asyncio.TimeoutError) as err:
                self.last_error = str(err)
                logger.warning("%s — reconnecting in %.0fs", err, delay)
                await self.close()
                await asyncio.sleep(delay)
                delay = next_reconnect_delay(delay, reconnect_delay)

    async def set_digital(self, join: int, state: bool) -> None:
        await self._send(encode_digital(join, state))
        self._record(SignalType.DIGITAL, join, state)

    async def pulse_digital(self, join: int, hold: float = 0.2) -> None:
        await self.set_digital(join, True)
        await asyncio.sleep(hold)
        await self.set_digital(join, False)

    async def set_analog(self, join: int, value: int) -> None:
        await self._send(encode_analog(join, value))
        self._record(SignalType.ANALOG, join, value)

    async def set_serial(self, join: int, text: str) -> None:
        await self._send(encode_serial(join, text))
        self._record(SignalType.SERIAL, join, text)

    def snapshot(self) -> dict:
        return {
            "host": self.host,
            "name": self.name,
            "ipid": self.ipid,
            "connected": self.connected,
            "registered": self.registered,
            "discovery_complete": self.discovery_complete,
            "connected_since": self.connected_since,
            "last_error": self.last_error,
            "join_count": len(self.joins),
            "joins": [
                {
                    "key": j.key, "signal": j.signal.value, "number": j.number,
                    "value": j.value, "updates": j.updates,
                    "last_seen": j.last_seen,
                }
                for j in sorted(
                    self.joins.values(), key=lambda j: (j.signal.value, j.number)
                )
            ],
        }


async def probe_ipid(host: str, ipid: int, port: int = CIP_PORT,
                     timeout: float = 8.0) -> tuple[bool, str]:
    """Ask whether a program defines an IPID, without staying connected.

    Safe only for an IPID expected to be undefined: if it IS defined and a
    panel is using it, registering takes that panel's place. The connection
    is dropped the moment the verdict is known.
    """
    conn = CipConnection(host, ipid, port=port)
    try:
        await conn.connect(timeout=timeout)
        deadline = time.time() + timeout
        while time.time() < deadline:
            frame = await conn._read_frame(timeout=2.0)
            if frame is None:
                continue
            ctype, payload = frame
            if ctype == TYPE_REG_REQUEST:
                await conn._send(
                    b"\x01\x00\x0b\x00\x00\x00\x00\x00"
                    + bytes([ipid]) + b"\x40\xff\xff\xf1\x01"
                )
            elif ctype == TYPE_REG_RESULT:
                if payload == REG_NOT_DEFINED:
                    return False, "not defined in the running program"
                if payload == REG_SUCCESS:
                    return True, "defined and accepted"
                return False, f"refused: {payload.hex()}"
        return False, "no registration result within timeout"
    finally:
        await conn.close()
