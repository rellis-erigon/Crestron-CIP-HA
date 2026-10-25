"""Crestron CIP wire protocol — framing, enums, and join record codecs.

Decoded by reverse-engineering the Crestron SmartGraphics / XPanel engine
(com.crestron.cnxconnection) — see CIP_protocol_spec.md.

Framing, enums, and the digital/analog/serial codecs are branch-verified against the
engine bytecode AND confirmed against live hardware (two processors on site). SmartObject
nesting is confirmed live too. Outbound join sends use the same framing but were not
fired at the live venue (read-only test) — exercise against a spare IPID.
"""
from __future__ import annotations

from enum import IntEnum


# ---------------------------------------------------------------------------
# Outer packet framing:  [type:1][length:2 big-endian][payload]
# ---------------------------------------------------------------------------
class PacketType(IntEnum):
    CONNECT = 0x01
    CONNECTION_RESPONSE = 0x02
    DISCONNECT = 0x03
    DISCONNECT_RESPONSE = 0x04
    DATA = 0x05
    CONNECT_DHCP = 0x0A
    AUTHENTICATE = 0x0B
    AUTHENTICATE_RESPONSE = 0x0C
    HEARTBEAT = 0x0D
    HEARTBEAT_RESPONSE = 0x0E
    PROGRAM_READY = 0x0F
    EXTENDED_DATA = 0x12
    CRPC_CONNECT = 0x13
    CRPC_DATA = 0x14
    DEVICE_ROUTER_CONNECT = 0x26
    DEVICE_ROUTER_CONNECT_RESPONSE = 0x27


class CresnetType(IntEnum):
    """Join-record type byte carried inside a DATA payload."""
    DIGITAL = 0x00
    X_GEN_ANALOG = 0x01
    COMMAND = 0x03
    DATE_TIME = 0x08
    FIRMWARE_SERIAL = 0x12
    ANALOG = 0x14
    SERIAL = 0x15
    RCB = 0x1E
    ENCAPSULATION = 0x20
    REPEAT_DIGITAL = 0x27
    UNMANGLED_SERIAL = 0x2A
    SERIAL_EXTENDED = 0x34
    ENCAPSULATION_EXTENDED = 0x35
    SMART_OBJECT = 0x38
    SMART_OBJECT_EXTENDED = 0x39


class CommandSubtype(IntEnum):
    UPDATE_REQUEST = 0x00
    END_OF_UPDATE = 0x16
    SELECTIVE_UPDATE_REQUEST = 0x17
    PARTIAL_UPDATE = 0x18
    END_OF_QUERY = 0x1C
    END_QUERY_RESPONSE = 0x1D
    CLEAR_ALL = 0x1F
    SMART_OBJECT_CLEAR = 0x2B


MODE_HEARTBEAT = 0x40
DEFAULT_PORT = 41794        # plain CIP
DEFAULT_TLS_PORT = 41796    # TLS CIP (seen in the DMPS3 .c3p)


def frame(ptype: int, payload: bytes = b"") -> bytes:
    """Wrap a payload in the CIP outer frame: [type][len BE][payload]."""
    n = len(payload)
    return bytes((ptype, (n >> 8) & 0xFF, n & 0xFF)) + payload


def iter_frames(buffer: bytearray):
    """Yield complete (type, payload) frames from *buffer*, consuming them.

    Leaves any partial trailing frame in the buffer for the next read.
    """
    while len(buffer) >= 3:
        length = (buffer[1] << 8) | buffer[2]
        if len(buffer) < 3 + length:
            break
        ptype = buffer[0]
        payload = bytes(buffer[3:3 + length])
        del buffer[:3 + length]
        yield ptype, payload


# ---------------------------------------------------------------------------
# Join record codecs (join numbers are 1-based in the API, 0-based on the wire)
# ---------------------------------------------------------------------------
def pack_digital(join: int, value: bool) -> bytes:
    """2 bytes, little-endian 15-bit join + inverted state bit (0x80 set = OFF)."""
    z = join - 1
    b0 = z & 0xFF
    b1 = ((z >> 8) & 0x7F) | (0x00 if value else 0x80)
    return bytes((b0, b1))


def unpack_digital(data: bytes) -> tuple[int, bool]:
    b0, b1 = data[0], data[1]
    join = ((b1 << 8) | b0) & 0x7FFF
    value = (b1 & 0x80) == 0          # bit set = OFF, clear = ON
    return join + 1, value


def pack_analog(join: int, value: int) -> bytes:
    """4 bytes big-endian: join (16-bit) + value (16-bit unsigned)."""
    z = join - 1
    v = value & 0xFFFF
    return bytes(((z >> 8) & 0xFF, z & 0xFF, (v >> 8) & 0xFF, v & 0xFF))


def unpack_analog(data: bytes) -> tuple[int, int]:
    join = ((data[0] << 8) | data[1]) + 1
    value = (data[2] << 8) | data[3]
    return join, value


def pack_serial(join: int, text: str, unicode: bool = False) -> bytes:
    """join (16-bit BE) + format byte (0x04 = UTF-16) + string bytes (no terminator)."""
    z = join - 1
    fmt = 0x04 if unicode else 0x00
    body = text.encode("utf-16-be") if unicode else text.encode("iso-8859-1", "replace")
    return bytes(((z >> 8) & 0xFF, z & 0xFF, fmt)) + body


def unpack_serial(data: bytes) -> tuple[int, str]:
    join = ((data[0] << 8) | data[1]) + 1
    fmt = data[2]
    body = data[3:]
    text = body.decode("utf-16-be", "replace") if (fmt & 0x04) else body.decode("iso-8859-1", "replace")
    return join, text


# ---------------------------------------------------------------------------
# DATA payload = [0x00 prefix][length:2 BE][cresnet_type][records...]
#   length = len(records) + 1 (covers the type byte).
# Confirmed against live hardware (two processors on site): the processor prefixes every
# DATA payload with one 0x00 byte before the Cresnet frame. A single frame may
# pack several same-type joins back to back (digital 2B, analog 4B each); serial
# carries one variable-length record.
# ---------------------------------------------------------------------------
CRESNET_PREFIX = b"\x00"


def pack_cresnet(cresnet_type: int, records: bytes) -> bytes:
    n = len(records) + 1
    return CRESNET_PREFIX + bytes(((n >> 8) & 0xFF, n & 0xFF, cresnet_type)) + records


def _scan_cresnet(payload: bytes, start: int) -> list[tuple[int, bytes]] | None:
    """Parse consecutive Cresnet frames beginning at *start*; None if it doesn't fit."""
    out: list[tuple[int, bytes]] = []
    i = start
    end = len(payload)
    while i + 3 <= end:
        n = (payload[i] << 8) | payload[i + 1]
        if n < 1:
            return None
        ctype = payload[i + 2]
        data = payload[i + 3: i + 2 + n]
        if len(data) != n - 1:
            return None
        out.append((ctype, data))
        i += 2 + n
    return out if i == end and out else None


# EXTENDED_DATA (0x12) carries the same records with a longer header:
# two zero bytes, then the 16-bit length, as a live DMPS sent one IPID's
# joins (`00 00 00 12 34 ...`, an extended serial) while another IPID on
# the same processor got plain DATA.
EXTENDED_PREFIX = b"\x00\x00"


def pack_cresnet_extended(cresnet_type: int, records: bytes) -> bytes:
    n = len(records) + 1
    return EXTENDED_PREFIX + bytes(((n >> 8) & 0xFF, n & 0xFF, cresnet_type)) + records


def pack_record(cresnet_type: int, records: bytes, extended: bool = False) -> bytes:
    """A record framed for DATA, or for EXTENDED_DATA."""
    pack = pack_cresnet_extended if extended else pack_cresnet
    return pack(cresnet_type, records)


def iter_records(payload: bytes, extended: bool = False):
    """Records from a DATA or an EXTENDED_DATA payload."""
    if extended:
        recs = _scan_cresnet(payload, len(EXTENDED_PREFIX))
        if recs is not None:
            yield from recs
            return
    yield from iter_cresnet_records(payload)


def iter_cresnet_records(payload: bytes):
    """Yield (cresnet_type, record_bytes) from a DATA payload.

    Hardware prefixes DATA payloads with one 0x00 byte (offset 1); we also accept a
    bare frame (offset 0) so the in-process mock/tests and either framing parse. The
    scan uses whichever offset frames the whole payload cleanly.
    """
    for start in (1, 0, 2, 3, 4):
        recs = _scan_cresnet(payload, start)
        if recs is not None:
            yield from recs
            return


def split_joins(cresnet_type: int, data: bytes):
    """Split a Cresnet record into (kind, join, value) tuples, dropping
    which SmartObject a join belongs to. See `split_object_joins`."""
    for kind, join, value, _obj in split_object_joins(cresnet_type, data):
        yield kind, join, value


def pack_smart_object(obj: int, cresnet_type: int, record: bytes) -> bytes:
    """A join record addressed to SmartObject `obj`, as a DATA payload."""
    body = bytes(((obj >> 24) & 0xFF, (obj >> 16) & 0xFF, (obj >> 8) & 0xFF, obj & 0xFF,
                  len(record) + 1, cresnet_type)) + record
    return pack_cresnet(CresnetType.SMART_OBJECT, body)


def split_object_joins(cresnet_type: int, data: bytes, obj: int = 0):
    """Split a Cresnet record into (kind, join, value, smart_object) tuples.

    `smart_object` is 0 for an ordinary join. A SmartObject's joins are a
    namespace of their own: a list's serial 11 is not the program's serial
    11, and treating them as one makes two signals fight over one entity.

    Digital/analog records may pack several joins; serial is one record. SmartObject
    records nest further joins: [objid:4 BE] then items of [len:1][type:1][record],
    where the record is the next (len-1) bytes — recursed via this same function.
    (Confirmed against live hardware: consumes every captured SmartObject frame exactly.)
    """
    if cresnet_type in (CresnetType.DIGITAL, CresnetType.REPEAT_DIGITAL):
        for k in range(0, len(data) - 1, 2):
            yield ("digital", *unpack_digital(data[k:k + 2]), obj)
    elif cresnet_type in (CresnetType.ANALOG, CresnetType.X_GEN_ANALOG):
        for k in range(0, len(data) - 3, 4):
            yield ("analog", *unpack_analog(data[k:k + 4]), obj)
    elif cresnet_type in (CresnetType.SERIAL, CresnetType.UNMANGLED_SERIAL,
                          CresnetType.SERIAL_EXTENDED) and len(data) >= 3:
        yield ("serial", *unpack_serial(data), obj)
    elif cresnet_type in (CresnetType.SMART_OBJECT, CresnetType.SMART_OBJECT_EXTENDED) and len(data) >= 4:
        inner = int.from_bytes(data[:4], "big")
        i = 4  # past the 32-bit object id
        while i + 2 <= len(data):
            nlen = data[i]
            if nlen < 1:
                break
            ntype = data[i + 1]
            rec = data[i + 2: i + 1 + nlen]
            yield from split_object_joins(ntype, rec, inner)
            i += 1 + nlen
