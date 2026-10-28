"""One connection to the processor, any number of XPanels.

A browser XPanel speaks CIP as a panel does. Carried byte for byte to the
processor, each open copy would be another registration at the IPID, and
some processors (a CP4, here) will not share one. So Home Assistant plays
processor to the panels instead, over the connection it already holds:

- It keeps an image of what the processor has said: the latest record for
  every join that is not at its default, as the processor's own bytes. A
  processor only ever reports the non-default, so the image is exactly
  what it would send a newly connected panel.
- A panel that connects is answered as the processor answered, measured
  on a live session: PROGRAM_READY, then the processor's own
  CONNECTION_RESPONSE, and for its update request the image followed by
  END_OF_UPDATE and END_OF_QUERY. The engine shows "Loading Interface"
  until END_OF_UPDATE arrives.
- What the processor sends afterwards goes to every panel; what a panel
  sends goes to the processor. Heartbeats stay on their own side.

Nothing here imports Home Assistant: the tests run it as it is.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from . import protocol as p

_LOG = logging.getLogger(__name__)

C = p.CresnetType
CMD = p.CommandSubtype

# What a processor said first on a live session, before anything was asked.
PROGRAM_READY_BODY = b"\x02"
# And what it answered a heartbeat with.
HEARTBEAT_REPLY_BODY = b"\x00\x00"
# How a panel is known to be gone. Not by its own heartbeats: a browser
# stops running the panel in a window that is not showing (Ruffle draws on
# animation frames, which a hidden page does not get), so a panel left in
# another browser falls silent while still wanted. The websocket is pinged
# instead, which a browser answers whether or not the page is showing, and
# a panel whose browser has really gone stops answering.
PANEL_PING_S = 30.0
# How long a replayed page pulse is held.
PAGE_PULSE_S = 0.1
# Frames a panel may have waiting. A browser that has stopped reading its
# websocket (a frozen tab, a phone gone to sleep mid-send) would otherwise
# be sent an ever longer backlog; past this it is closed, and reconnects to
# a fresh image, which is both quicker and correct.
PANEL_BACKLOG_MAX = 5000

_DIGITAL = (C.DIGITAL, C.REPEAT_DIGITAL)
_ANALOG = (C.ANALOG, C.X_GEN_ANALOG)
_SERIAL = (C.SERIAL, C.UNMANGLED_SERIAL, C.SERIAL_EXTENDED)
_SMART = (C.SMART_OBJECT, C.SMART_OBJECT_EXTENDED)


def _command(subtype: int) -> bytes:
    return p.pack_cresnet(C.COMMAND, bytes((subtype,)))


class StateImage:
    """The processor's side of the conversation, reduced to what is current.

    Keyed by (smart object, kind, join); each value is the records that set
    it, as (cresnet type, bytes, extended). A serial can arrive in pieces,
    so it is a list; everything else is one record. `extended` is whether
    the processor sent it as EXTENDED_DATA, which is how it is replayed: a
    processor can talk to one IPID in DATA and to another in EXTENDED_DATA,
    and the panel is given what its own IPID was given.
    """

    def __init__(self) -> None:
        self._records: dict[tuple, list[tuple[int, bytes]]] = {}

    def __len__(self) -> int:
        return len(self._records)

    def keys(self) -> list[tuple]:
        return list(self._records)

    def clear(self) -> None:
        self._records.clear()

    def feed(self, payload: bytes, extended: bool = False) -> None:
        """Take in a DATA (or EXTENDED_DATA) payload from the processor."""
        for ctype, data in p.iter_records(payload, extended):
            if ctype == C.COMMAND:
                if data[:1] in (bytes((CMD.CLEAR_ALL,)),):
                    self.clear()
                continue
            if extended and ctype in _SMART:
                # How an extended SmartObject frames its items has not been
                # seen yet, so the record is kept whole rather than guessed at.
                self._records[(b"", "raw", data)] = [(ctype, data, True)]
                continue
            self._take(ctype, data, None, extended)

    def _take(self, ctype: int, data: bytes, obj: bytes | None, extended: bool = False) -> None:
        def store(key: tuple, rtype: int, record: bytes, default: bool, append=False):
            full = (obj or b"", *key)
            if default:
                self._records.pop(full, None)
            elif append and full in self._records:
                self._records[full].append((rtype, record, extended))
            else:
                self._records[full] = [(rtype, record, extended)]

        if ctype in _DIGITAL:
            for k in range(0, len(data) - 1, 2):
                rec = data[k:k + 2]
                join = ((rec[1] << 8) | rec[0]) & 0x7FFF
                store(("d", join), C.DIGITAL, rec, default=bool(rec[1] & 0x80))
        elif ctype in _ANALOG:
            for k in range(0, len(data) - 3, 4):
                rec = data[k:k + 4]
                store(("a", rec[:2]), ctype, rec, default=rec[2:4] == b"\x00\x00")
        elif ctype in _SERIAL and len(data) >= 3:
            fmt = data[2]
            # Bit 0 starts a string and bit 1 ends it; a live processor sent
            # whole strings as 0x03. A piece without the start flag continues.
            starts = bool(fmt & 0x01) or not fmt & 0x03
            empty = len(data) == 3 and starts
            store(("s", data[:2]), ctype, data, default=empty, append=not starts)
        elif ctype in _SMART and len(data) >= 4:
            inner = data[:4]
            i = 4
            while i + 2 <= len(data):
                nlen = data[i]
                if nlen < 1:
                    break
                self._take(data[i + 1], data[i + 2:i + 1 + nlen], inner, extended)
                i += 1 + nlen
        else:
            # Date and time, and anything else: the latest one stands.
            store(("x", ctype), ctype, data, default=False)

    def payloads(self) -> list[tuple[int, bytes]]:
        """The image as (packet type, payload), one record each, ready to send."""
        out = []
        for (obj, *_), records in self._records.items():
            for rtype, record, extended in records:
                ptype = p.PacketType.EXTENDED_DATA if extended else p.PacketType.DATA
                if obj:
                    item = bytes((len(record) + 1, rtype)) + record
                    out.append((ptype, p.pack_record(C.SMART_OBJECT, obj + item, extended)))
                else:
                    out.append((ptype, p.pack_record(rtype, record, extended)))
        return out


Send = Callable[[int, bytes], Awaitable[None]]


class PanelLink:
    """One XPanel, as the processor sees it.

    Everything for the panel goes through one queue and one writer, so it
    arrives in the order it was meant: a page pulse after the joins it
    shows, a serial's pieces in sequence, the replay before what changed
    after it.
    """

    def __init__(self, mux: "XPanelMux", send: Send, name: str,
                 close: Callable[[], Awaitable[None]] | None = None,
                 pages: frozenset[int] = frozenset()) -> None:
        self.mux = mux
        self._send = send
        self.close = close
        # The digital joins that flip this panel's pages, from its project.
        self.pages = pages
        self.name = name
        self.ready = False
        self._buffer = bytearray()
        self._outbox: asyncio.Queue[tuple[int, bytes]] = asyncio.Queue(PANEL_BACKLOG_MAX)
        self._writer: asyncio.Task | None = None
        self._closing = False

    def put(self, ptype: int, body: bytes = b"") -> None:
        """Queue a frame for the panel, in order, without waiting."""
        if self._closing:
            return
        try:
            self._outbox.put_nowait((ptype, body))
        except asyncio.QueueFull:
            _LOG.warning("XPanel %s stopped reading (%d frames waiting); closing it "
                         "so it reconnects", self.name, PANEL_BACKLOG_MAX)
            self._shut()
            return
        if self._writer is None or self._writer.done():
            self._writer = asyncio.ensure_future(self._write())

    async def send(self, ptype: int, body: bytes = b"") -> None:
        self.put(ptype, body)

    async def flushed(self) -> None:
        """Wait until everything queued so far has been sent."""
        await self._outbox.join()

    async def _write(self) -> None:
        # Runs while there is something to send; `put` starts it again.
        # Nothing is awaited between the empty check and returning, so a
        # frame queued meanwhile is never stranded.
        while not self._outbox.empty():
            ptype, body = self._outbox.get_nowait()
            try:
                await self._send(ptype, body)
            except Exception as err:  # noqa: BLE001 - a gone browser
                _LOG.debug("XPanel %s did not take a frame: %s", self.name, err)
                self._outbox.task_done()
                self._shut()
                return
            self._outbox.task_done()

    def _shut(self) -> None:
        """Stop sending and close the panel's socket; it reconnects."""
        if self._closing:
            return
        self._closing = True
        self.ready = False
        while not self._outbox.empty():
            self._outbox.get_nowait()
            self._outbox.task_done()
        if self.close is not None:
            asyncio.ensure_future(self.close())

    def stop(self) -> None:
        """The socket has closed: drop what is waiting and end the writer."""
        self._closing = True
        self.ready = False
        if self._writer is not None:
            self._writer.cancel()

    async def start(self) -> None:
        await self.send(p.PacketType.PROGRAM_READY, PROGRAM_READY_BODY)

    async def feed(self, data: bytes) -> bool:
        """Take bytes from the panel. False when it has said goodbye."""
        self._buffer.extend(data)
        for ptype, body in p.iter_frames(self._buffer):
            if not await self._frame(ptype, body):
                return False
        return True

    async def _frame(self, ptype: int, body: bytes) -> bool:
        if ptype == p.PacketType.CONNECT:
            await self.send(p.PacketType.CONNECTION_RESPONSE, self.mux.connect_response())
        elif ptype == p.PacketType.DEVICE_ROUTER_CONNECT:
            await self.send(p.PacketType.DEVICE_ROUTER_CONNECT_RESPONSE,
                            self.mux.connect_response())
        elif ptype == p.PacketType.HEARTBEAT:
            await self.send(p.PacketType.HEARTBEAT_RESPONSE, HEARTBEAT_REPLY_BODY)
        elif ptype == p.PacketType.DISCONNECT:
            return False
        elif ptype in (p.PacketType.DATA, p.PacketType.EXTENDED_DATA):
            await self._data(body, ptype)
        return True

    async def _data(self, body: bytes, ptype: int = p.PacketType.DATA) -> None:
        extended = ptype == p.PacketType.EXTENDED_DATA
        for ctype, data in p.iter_records(body, extended):
            if ctype != C.COMMAND:
                # A press, a level, a text: the processor's to act on, in
                # the framing the panel sent it in.
                await self.mux.upstream(p.pack_record(ctype, data, extended), ptype)
                continue
            sub = data[0] if data else None
            if sub == CMD.UPDATE_REQUEST:
                # Answered as the processor answered it. The image is queued
                # and the panel marked ready in one step, with nothing
                # awaited between: a change the processor sends meanwhile
                # lands after the image, never in a gap where it was lost.
                self.put(p.PacketType.DATA, _command(CMD.UPDATE_REQUEST))
                self._replay()
                self.put(p.PacketType.DATA, _command(CMD.END_OF_UPDATE))
                self.put(p.PacketType.DATA, _command(CMD.END_OF_QUERY))
                self.ready = True
                await self._current_page()
            elif sub in (CMD.SELECTIVE_UPDATE_REQUEST, CMD.PARTIAL_UPDATE):
                # A page asking for its joins again as it loads.
                self._replay()
                self.put(p.PacketType.DATA, _command(CMD.END_OF_UPDATE))
            # END_QUERY_RESPONSE and the rest need no answer.

    async def _current_page(self) -> None:
        """Flip a newly connected panel to the page the processor last chose.

        A processor pulses a page's join and the panel latches the page, so
        a panel connecting later lands on its start page; a wall panel that
        reboots does the same. A dashboard opens panels all day, so the
        pulse is repeated to each new one, as the processor sent it.
        """
        join = self.mux.current_page(self.pages)
        if join is None:
            return
        await self.send(p.PacketType.DATA, p.pack_cresnet(C.DIGITAL, p.pack_digital(join, True)))
        await asyncio.sleep(PAGE_PULSE_S)
        if join not in self.mux.held_digitals():
            await self.send(p.PacketType.DATA,
                            p.pack_cresnet(C.DIGITAL, p.pack_digital(join, False)))

    def _replay(self) -> None:
        for ptype, payload in self.mux.image.payloads():
            self.put(ptype, payload)


class XPanelMux:
    """The panels of one config entry, and the image they are served from."""

    def __init__(self, upstream: Callable[..., Awaitable[None]],
                 connect_response: Callable[[], bytes]) -> None:
        self._upstream = upstream
        self._connect_response = connect_response
        self.image = StateImage()
        self.links: set[PanelLink] = set()
        # digital join -> the order it last went high in, to know which page
        # the processor flipped to last.
        self._rises: dict[int, int] = {}
        self._rise_count = 0

    def connect_response(self) -> bytes:
        # The processor's own answer to this IPID, so the panel negotiates
        # what it would have; a live DMPS sent 00 00 00 1f.
        return self._connect_response() or b"\x00\x00\x00\x1f"

    async def upstream(self, payload: bytes, ptype: int = p.PacketType.DATA) -> None:
        if ptype == p.PacketType.DATA:
            await self._upstream(payload)
        else:
            await self._upstream(payload, ptype)

    def held_digitals(self) -> set[int]:
        """The ordinary digital joins the processor is holding high."""
        return {key[2] + 1 for key in self.image.keys() if key[0] == b"" and key[1] == "d"}

    def current_page(self, pages: frozenset[int]) -> int | None:
        """Of `pages`, the join the processor raised most recently."""
        seen = [(self._rises[j], j) for j in pages if j in self._rises]
        return max(seen)[1] if seen else None

    def _note_rises(self, payload: bytes, extended: bool = False) -> None:
        held = self.held_digitals()
        for ctype, data in p.iter_records(payload, extended):
            if ctype not in _DIGITAL:
                continue
            for k in range(0, len(data) - 1, 2):
                join, on = p.unpack_digital(data[k:k + 2])
                if on and join not in held:
                    self._rise_count += 1
                    self._rises[join] = self._rise_count

    def from_processor(self, payload: bytes, ptype: int = p.PacketType.DATA) -> None:
        """A DATA or EXTENDED_DATA payload the processor sent: remember it, pass it on."""
        extended = ptype == p.PacketType.EXTENDED_DATA
        self._note_rises(payload, extended)
        self.image.feed(payload, extended)
        records = list(p.iter_records(payload, extended))
        if not any(ctype == C.COMMAND for ctype, _ in records):
            out = [payload]  # as the processor framed it
        else:
            # Its update markers answer our own request, not a panel's.
            out = [p.pack_record(ctype, data, extended) for ctype, data in records
                   if ctype != C.COMMAND]
        for link in list(self.links):
            if link.ready:
                for body in out:
                    link.put(ptype, body)

    def processor_lost(self) -> None:
        """The image is stale from here; the next connection rebuilds it.

        The panels are closed rather than left showing what may no longer
        be true. Each reconnects by itself, as it would to a processor that
        rebooted, and is served afresh once the processor is back.
        """
        self.image.clear()
        for link in list(self.links):
            link._shut()
