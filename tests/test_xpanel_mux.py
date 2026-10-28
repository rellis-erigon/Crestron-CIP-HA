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
    await link.flushed()
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
    got = joins(mux.image.payloads())
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
    await link.flushed()
    assert sent == [press], sent
    assert rec.frames == [(p.PacketType.HEARTBEAT_RESPONSE, b"\x00\x00")], rec.frames


async def feedback_reaches_every_ready_panel() -> None:
    mux, _ = make_mux()
    a, b, late = Recorder(), Recorder(), Recorder()
    links = []
    for r in (a, b, late):
        link = PanelLink(mux, r.send, "t")
        mux.links.add(link)
        links.append(link)
        if r is not late:
            await link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00")))
    for link in links:
        await link.flushed()
    before = len(a.frames)
    change = data(C.DIGITAL, p.pack_digital(13, True))
    mux.from_processor(change)
    for link in links:
        await link.flushed()
    assert a.frames[before:] == [(p.PacketType.DATA, change)], a.frames[before:]
    assert (p.PacketType.DATA, change) in b.frames
    # A panel still connecting gets it in its replay instead.
    assert late.frames == []
    # The processor's own update markers are not passed on.
    mux.from_processor(data(C.COMMAND, b"\x16"))
    for link in links:
        await link.flushed()
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
    texts = [rec[3:] for _, body in image.payloads()
             for _, rec in p.iter_cresnet_records(body)]
    assert texts == [b"Hello ", b"world"], texts
    image.feed(serial(5, "New", fmt=0x03))
    texts = [rec[3:] for _, body in image.payloads()
             for _, rec in p.iter_cresnet_records(body)]
    assert texts == [b"New"], texts


async def a_new_panel_is_flipped_to_the_last_page() -> None:
    import crestron_cip.xpanel_mux as m
    m.PAGE_PULSE_S = 0
    mux, _ = make_mux()
    # The processor pulses page 13, later page 12; an unrelated button rises last.
    for join in (13, 12):
        mux.from_processor(data(C.DIGITAL, p.pack_digital(join, True)))
        mux.from_processor(data(C.DIGITAL, p.pack_digital(join, False)))
    mux.from_processor(data(C.DIGITAL, p.pack_digital(40, True)))
    rec = Recorder()
    link = PanelLink(mux, rec.send, "t", pages=frozenset({11, 12, 13, 14}))
    await link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00")))
    await link.flushed()
    after = rec.frames[[i for i, f in enumerate(rec.frames)
                        if f[1] == data(C.COMMAND, b"\x1c")][0] + 1:]
    assert after == [(p.PacketType.DATA, data(C.DIGITAL, p.pack_digital(12, True))),
                     (p.PacketType.DATA, data(C.DIGITAL, p.pack_digital(12, False)))], after
    # Without page joins, or before any flip, nothing is sent.
    rec2 = Recorder()
    link2 = PanelLink(mux, rec2.send, "t")
    await link2.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00")))
    await link2.flushed()
    assert rec2.frames[-1][1] == data(C.COMMAND, b"\x1c")


async def an_ipid_sent_extended_data_is_served_extended_data() -> None:
    """One IPID's joins came as EXTENDED_DATA, `00 00 <len16> <type> ...`.

    Read off a live DMPS: its second panel IPID got its serials that way
    while the first got plain DATA. The panel is given them as they came,
    in the replay and as they change.
    """
    room = bytes.fromhex("0000000e34000003") + b"Room Two!!"
    assert list(p.iter_records(room, True)) == [(0x34, bytes.fromhex("000003") + b"Room Two!!")]
    assert p.pack_record(0x34, bytes.fromhex("000003") + b"Room Two!!", True) == room

    async def up(*args):
        pass

    mux = XPanelMux(up, lambda: bytes.fromhex("0000001f"))
    mux.from_processor(room, p.PacketType.EXTENDED_DATA)
    mux.from_processor(data(C.DIGITAL, p.pack_digital(7, True)))
    rec = Recorder()
    link = PanelLink(mux, rec.send, "t")
    mux.links.add(link)
    await link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00")))
    await link.flushed()
    assert (p.PacketType.EXTENDED_DATA, room) in rec.frames, rec.frames
    assert (p.PacketType.DATA, data(C.DIGITAL, p.pack_digital(7, True))) in rec.frames
    before = len(rec.frames)
    change = bytes.fromhex("0000000a3400d203536f75726365")
    mux.from_processor(change, p.PacketType.EXTENDED_DATA)
    await link.flushed()
    assert rec.frames[before:] == [(p.PacketType.EXTENDED_DATA, change)], rec.frames[before:]


class SlowRecorder(Recorder):
    """A browser that takes its time over every frame."""

    async def send(self, ptype, body):
        await asyncio.sleep(0.001 if body[-1:] in (b"\x01", b"\x81") else 0)
        self.frames.append((ptype, body))


async def updates_arrive_in_the_order_the_processor_sent_them() -> None:
    mux, _ = make_mux()
    rec = SlowRecorder()
    link = PanelLink(mux, rec.send, "t")
    mux.links.add(link)
    await link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00")))
    await link.flushed()
    before = len(rec.frames)
    # Join 2's frames are slow to send and join 3's quick: sent as separate
    # tasks, 3 overtook 2. One writer keeps the processor's order.
    changes = [data(C.DIGITAL, p.pack_digital(j, on)) for j, on in
               ((2, True), (3, True), (2, False), (3, False))]
    for change in changes:
        mux.from_processor(change)
    await link.flushed()
    assert [b for _, b in rec.frames[before:]] == changes, rec.frames[before:]


async def a_change_during_a_panels_load_is_not_lost() -> None:
    mux, _ = make_mux()
    rec = SlowRecorder()
    link = PanelLink(mux, rec.send, "t")
    mux.links.add(link)
    loading = asyncio.ensure_future(
        link.feed(p.frame(p.PacketType.DATA, data(C.COMMAND, b"\x00"))))
    await asyncio.sleep(0)
    # The processor moves while the panel is being sent its image.
    change = data(C.DIGITAL, p.pack_digital(44, True))
    mux.from_processor(change)
    await loading
    await link.flushed()
    assert ("digital", 44, True, 0) in joins(rec.frames), "the change was lost"


async def a_panel_that_stops_reading_is_closed_not_backlogged() -> None:
    import crestron_cip.xpanel_mux as m
    mux, _ = make_mux()

    class Stuck(Recorder):
        async def send(self, ptype, body):
            await asyncio.sleep(3600)

    rec = Stuck()
    link = PanelLink(mux, rec.send, "t", close=rec.close)
    link.ready = True
    mux.links.add(link)
    for i in range(m.PANEL_BACKLOG_MAX + 2):
        mux.from_processor(data(C.DIGITAL, p.pack_digital(100 + i % 50, bool(i % 2))))
    await asyncio.sleep(0)
    assert rec.closed and not link.ready
    link.stop()


async def main() -> None:
    tests = [a_panel_is_answered_as_the_processor_answered,
             a_join_back_at_its_default_is_not_replayed,
             presses_go_to_the_processor_and_heartbeats_do_not,
             feedback_reaches_every_ready_panel,
             losing_the_processor_closes_the_panels,
             a_serial_in_pieces_is_replayed_whole,
             a_new_panel_is_flipped_to_the_last_page,
             an_ipid_sent_extended_data_is_served_extended_data,
             updates_arrive_in_the_order_the_processor_sent_them,
             a_change_during_a_panels_load_is_not_lost,
             a_panel_that_stops_reading_is_closed_not_backlogged]
    for t in tests:
        await t()
        print(f"  ok  {t.__name__}")
    print(f"PASSED {len(tests)} xpanel mux tests")


if __name__ == "__main__":
    asyncio.run(main())
