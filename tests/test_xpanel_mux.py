"""Panels served from one connection, answered as the processor answers.

The frames here have the shape of a session read off a real processor and
an XPanel (DMPS, IPID 03), with the words changed: PROGRAM_READY first,
the processor's own CONNECTION_RESPONSE, and for the update request an
echo, the joins, END_OF_UPDATE and END_OF_QUERY. The engine sits on
"Loading Interface" until END_OF_UPDATE arrives, which is why the order is
tested and not just the content.
"""
import asyncio, functools, os, sys
print = functools.partial(print, flush=True)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pkg  # noqa: E402,F401

from crestron_cip import protocol as p
from crestron_cip.xpanel_mux import PanelLink, StateImage, XPanelMux

C = p.CresnetType


def data(ctype, record):
    return p.pack_cresnet(ctype, record)


def serial(join, text, fmt=0x03):
    z = join - 1
    return data(C.SERIAL, bytes((z >> 8, z & 0xFF, fmt)) + text.encode("latin-1"))


# The processor's dump, as it framed it: several digitals in one record.
DUMP = [
    serial(1, "Room One"),
    serial(1000, "Startup"),
    data(C.DIGITAL, p.pack_digital(7, True) + p.pack_digital(8, True) + p.pack_digital(31, True)),
    data(C.ANALOG, p.pack_analog(211, 45219)),
    data(C.SMART_OBJECT, bytes.fromhex("00000001") + bytes((7, C.SERIAL, 0, 30, 3)) + b"Pod"),
]


class Recorder:
    def __init__(self):
        self.frames = []
        self.closed = False

    async def send(self, ptype, body):
        self.frames.append((ptype, body))

    async def close(self):
        self.closed = True


def make_mux(upstream=None):
    sent = upstream if upstream is not None else []

    async def up(payload):
        sent.append(payload)

    mux = XPanelMux(up, lambda: bytes.fromhex("0000001f"))
    for payload in DUMP:
        mux.from_processor(payload)
    return mux, sent


def commands(frames):
    out = []
    for ptype, body in frames:
        if ptype == p.PacketType.DATA:
            for ctype, rec in p.iter_cresnet_records(body):
                if ctype == C.COMMAND:
                    out.append(rec[0])
    return out


def joins(frames):
    got = set()
    for ptype, body in frames:
        if ptype == p.PacketType.DATA:
            for ctype, rec in p.iter_cresnet_records(body):
                for kind, join, value, obj in p.split_object_joins(ctype, rec):
                    got.add((kind, join, value, obj))
    return got


async def a_panel_is_answered_as_the_processor_answered() -> None:
    mux, _ = make_mux()
    rec = Recorder()
    link = PanelLink(mux, rec.send, "test")
    await link.start()
    await link.feed(p.frame(p.PacketType.CONNECT, bytes.fromhex("00000000000340fffff101")))
    await link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00")))
    kinds = [t for t, _ in rec.frames]
    assert kinds[0] == p.PacketType.PROGRAM_READY and rec.frames[0][1] == b"\x02", rec.frames[0]
    assert rec.frames[1] == (p.PacketType.CONNECTION_RESPONSE, bytes.fromhex("0000001f"))
    # Echo, then the image, then END_OF_UPDATE and END_OF_QUERY, last.
    assert commands(rec.frames) == [0x00, 0x16, 0x1C], commands(rec.frames)
    assert rec.frames[-2][1] == data(C.COMMAND, b"\x16")
    got = joins(rec.frames)
    for want in [("serial", 1, "Room One", 0), ("serial", 1000, "Startup", 0),
                 ("digital", 7, True, 0), ("digital", 31, True, 0),
                 ("analog", 211, 45219, 0), ("serial", 31, "Pod", 1)]:
        assert want in got, (want, got)
    assert link.ready


async def a_join_back_at_its_default_is_not_replayed() -> None:
    mux, _ = make_mux()
    mux.from_processor(data(C.DIGITAL, p.pack_digital(31, False)))
    mux.from_processor(data(C.ANALOG, p.pack_analog(211, 0)))
    mux.from_processor(serial(1000, ""))
    payloads = mux.image.payloads()
    got = joins([(p.PacketType.DATA, b) for b in payloads])
    assert not any(j[1] in (31, 211, 1000) and j[3] == 0 for j in got), got
    assert ("digital", 7, True, 0) in got


async def presses_go_to_the_processor_and_heartbeats_do_not() -> None:
    sent = []
    mux, _ = make_mux(sent)
    rec = Recorder()
    link = PanelLink(mux, rec.send, "test")
    press = data(C.DIGITAL, p.pack_digital(12, True))
    await link.feed(p.frame(p.PacketType.DATA, press))
    await link.feed(p.frame(p.PacketType.HEARTBEAT, b"\x00\x00"))
    await link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x1d")))
    assert sent == [press], sent
    assert rec.frames == [(p.PacketType.HEARTBEAT_RESPONSE, b"\x00\x00")], rec.frames


async def feedback_reaches_every_ready_panel() -> None:
    mux, _ = make_mux()
    a, b, late = Recorder(), Recorder(), Recorder()
    for r in (a, b, late):
        link = PanelLink(mux, r.send, "t")
        mux.links.add(link)
        if r is not late:
            await link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00")))
    before = len(a.frames)
    change = data(C.DIGITAL, p.pack_digital(13, True))
    mux.from_processor(change)
    await asyncio.sleep(0)
    assert a.frames[before:] == [(p.PacketType.DATA, change)], a.frames[before:]
    assert (p.PacketType.DATA, change) in b.frames
    # A panel still connecting gets it in its replay instead.
    assert late.frames == []
    # The processor's own update markers are not passed on.
    mux.from_processor(data(C.COMMAND, b"\x16"))
    await asyncio.sleep(0)
    assert len(a.frames) == before + 1


async def losing_the_processor_closes_the_panels() -> None:
    mux, _ = make_mux()
    rec = Recorder()
    link = PanelLink(mux, rec.send, "t", close=rec.close)
    mux.links.add(link)
    mux.processor_lost()
    await asyncio.sleep(0)
    assert rec.closed and len(mux.image) == 0


async def a_serial_in_pieces_is_replayed_whole() -> None:
    image = StateImage()
    image.feed(serial(5, "Hello ", fmt=0x01))
    image.feed(serial(5, "world", fmt=0x02))
    texts = [rec[3:] for body in image.payloads()
             for _, rec in p.iter_cresnet_records(body)]
    assert texts == [b"Hello ", b"world"], texts
    image.feed(serial(5, "New", fmt=0x03))
    texts = [rec[3:] for body in image.payloads()
             for _, rec in p.iter_cresnet_records(body)]
    assert texts == [b"New"], texts


async def main() -> None:
    tests = [a_panel_is_answered_as_the_processor_answered,
             a_join_back_at_its_default_is_not_replayed,
             presses_go_to_the_processor_and_heartbeats_do_not,
             feedback_reaches_every_ready_panel,
             losing_the_processor_closes_the_panels,
             a_serial_in_pieces_is_replayed_whole]
    for t in tests:
        await t()
        print(f"  ok  {t.__name__}")
    print(f"PASSED {len(tests)} xpanel mux tests")


if __name__ == "__main__":
    asyncio.run(main())
