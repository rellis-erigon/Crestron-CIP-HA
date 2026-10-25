"""Asyncio Crestron CIP client — connect, register, heartbeat, send/receive joins.

Designed to drop into a Home Assistant integration (HA is asyncio). The client
emulates a touch panel / XPanel: it registers at an IPID, writes press/analog/serial
joins, and receives feedback joins from the processor.
"""
from __future__ import annotations

import asyncio
import logging
import ssl
from collections.abc import Awaitable, Callable
from typing import Optional

from . import protocol as p

_LOG = logging.getLogger(__name__)

# feedback callback: (kind, join, value, smart_object) where kind is one of
# "digital", "analog", "serial" and smart_object is 0 for an ordinary join.
FeedbackCb = Callable[[str, int, object, int], None | Awaitable[None]]


class CIPClient:
    def __init__(
        self,
        host: str,
        ipid: int,
        *,
        port: int | None = None,
        use_ssl: bool = False,
        heartbeat_interval: float = 15.0,
        on_feedback: Optional[FeedbackCb] = None,
        on_connect: Optional[Callable[[], None]] = None,
        on_disconnect: Optional[Callable[[], None]] = None,
    ) -> None:
        self.host = host
        self.ipid = ipid
        self.port = port if port is not None else (p.DEFAULT_TLS_PORT if use_ssl else p.DEFAULT_PORT)
        self.use_ssl = use_ssl
        self.heartbeat_interval = heartbeat_interval
        self.on_feedback = on_feedback
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect

        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._buf = bytearray()
        self._registered = asyncio.Event()
        self._registration_error: str | None = None
        self._last_rx = 0.0
        self._tasks: list[asyncio.Task] = []
        self._closing = False

        # last-known feedback state, so HA entities can read current values
        self.digital: dict[int, bool] = {}
        self.analog: dict[int, int] = {}
        self.serial: dict[int, str] = {}
        # SmartObject joins: (object, kind, join) -> value. A namespace of
        # their own; see protocol.split_object_joins.
        self.smart: dict[tuple[int, str, int], object] = {}

        # The processor's answer to our registration, which a panel served by
        # xpanel_mux is given as its own; and who else wants each DATA
        # payload as it came.
        self.connect_response: bytes = b""
        self.data_listeners: list[Callable[[bytes], None]] = []

        # ring buffer of recent inbound frames (type, hex) for empirical capture
        self.raw_frames: list[tuple[int, str]] = []

    # -- lifecycle ----------------------------------------------------------
    async def connect(self, timeout: float = 30.0) -> None:
        ctx = None
        if self.use_ssl:
            # Crestron processors typically present a self-signed cert.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        self._buf.clear()
        self._registered.clear()
        self._registration_error = None
        self._reader, self._writer = await asyncio.open_connection(self.host, self.port, ssl=ctx)
        self._closing = False
        self._last_rx = asyncio.get_running_loop().time()
        self._tasks = [asyncio.create_task(self._read_loop(), name="cip-read")]
        await self._send_registration()
        await asyncio.wait_for(self._registered.wait(), timeout)
        if self._registration_error:
            raise ConnectionRefusedError(self._registration_error)
        self._tasks.append(asyncio.create_task(self._heartbeat_loop(), name="cip-heartbeat"))
        if self.on_connect:
            self.on_connect()

    async def close(self) -> None:
        self._closing = True
        if self._writer is not None:
            try:
                self._writer.write(p.frame(p.PacketType.DISCONNECT))
                await self._writer.drain()
            except Exception:  # noqa: BLE001
                pass
        for t in self._tasks:
            t.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks = []
        if self._writer is not None:
            try:
                self._writer.close()
            except Exception:  # noqa: BLE001
                pass
        self._registered.clear()

    @property
    def connected(self) -> bool:
        return (self._registered.is_set() and not self._closing
                and self._registration_error is None)

    # -- registration / heartbeat ------------------------------------------
    async def _send_registration(self) -> None:
        # CONNECT body: 00 00 00 00 00 <IPID> 0x40(MODE_HEARTBEAT) — verified live.
        body = bytes((0x00, 0x00, 0x00, 0x00, 0x00, self.ipid & 0xFF, p.MODE_HEARTBEAT))
        await self._send(p.PacketType.CONNECT, body)

    def _on_registration_response(self, payload: bytes) -> None:
        # ff ff 02 means the IPID is not defined in the processor's program
        # (measured on site); the connection is useless, so fail it.
        if payload[:3] == b"\xff\xff\x02":
            self._registration_error = (
                f"IPID 0x{self.ipid:02X} is not defined in the program on {self.host}")
            self._registered.set()
            return
        self.connect_response = payload
        # payload[0] != 0 commonly signals a registration problem (e.g. IPID in use).
        if payload and payload[0] not in (0x00,):
            _LOG.warning("CIP registration response flagged: %r", payload.hex())
        self._registered.set()
        # Ask the processor to send the current state of every join.
        asyncio.create_task(self.request_update())

    async def _heartbeat_loop(self) -> None:
        try:
            loop = asyncio.get_running_loop()
            while not self._closing:
                await asyncio.sleep(self.heartbeat_interval)
                # The processor answers every heartbeat. Three intervals of
                # silence means it has gone without closing the socket, and the
                # read loop would otherwise wait forever.
                if loop.time() - self._last_rx > 3 * self.heartbeat_interval:
                    _LOG.warning("No reply from %s for %.0fs; dropping the connection",
                                 self.host, loop.time() - self._last_rx)
                    if self._writer is not None:
                        self._writer.close()
                    return
                await self._send(p.PacketType.HEARTBEAT)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("CIP heartbeat to %s failed: %s", self.host, exc)
            if self._writer is not None:
                self._writer.close()

    # -- outbound join writes ----------------------------------------------
    async def set_digital(self, join: int, value: bool, obj: int = 0) -> None:
        await self._send_join(p.CresnetType.DIGITAL, p.pack_digital(join, value), obj)
        self._store("digital", join, value, obj)

    async def pulse_digital(self, join: int, duration: float = 0.1, obj: int = 0) -> None:
        """Momentary press: assert, wait, release — the usual button action."""
        await self.set_digital(join, True, obj)
        await asyncio.sleep(duration)
        await self.set_digital(join, False, obj)

    async def set_analog(self, join: int, value: int, obj: int = 0) -> None:
        await self._send_join(p.CresnetType.ANALOG, p.pack_analog(join, value), obj)
        self._store("analog", join, value & 0xFFFF, obj)

    async def set_serial(self, join: int, text: str, unicode: bool = False, obj: int = 0) -> None:
        await self._send_join(p.CresnetType.SERIAL, p.pack_serial(join, text, unicode), obj)
        self._store("serial", join, text, obj)

    def value(self, kind: str, join: int, obj: int = 0) -> object:
        """The last value seen for a join, or None."""
        if obj:
            return self.smart.get((obj, kind, join))
        return {"digital": self.digital, "analog": self.analog,
                "serial": self.serial}[kind].get(join)

    def _store(self, kind: str, join: int, value: object, obj: int) -> None:
        if obj:
            self.smart[(obj, kind, join)] = value
        elif kind == "digital":
            self.digital[join] = value
        elif kind == "analog":
            self.analog[join] = value
        elif kind == "serial":
            self.serial[join] = value

    async def _send_join(self, cresnet_type: int, record: bytes, obj: int = 0) -> None:
        # DATA payload = Cresnet frame [0x00 prefix][len:2 BE][type][record],
        # wrapped by the [05][len:2 BE] outer frame. A SmartObject's join
        # goes inside a SmartObject record addressed to it.
        payload = (p.pack_smart_object(obj, cresnet_type, record) if obj
                   else p.pack_cresnet(cresnet_type, record))
        await self._send(p.PacketType.DATA, payload)

    async def send_data(self, payload: bytes) -> None:
        """Send a DATA payload as it is: a panel's joins, passed through."""
        await self._send(p.PacketType.DATA, payload)

    async def request_update(self) -> None:
        """Ask the processor to (re)send the current state of all joins."""
        await self._send(p.PacketType.DATA,
                         p.pack_cresnet(p.CresnetType.COMMAND, bytes((p.CommandSubtype.UPDATE_REQUEST,))))

    # -- inbound ------------------------------------------------------------
    async def _read_loop(self) -> None:
        assert self._reader is not None
        try:
            while not self._closing:
                chunk = await self._reader.read(4096)
                if not chunk:
                    break
                self._last_rx = asyncio.get_running_loop().time()
                self._buf.extend(chunk)
                for ptype, payload in p.iter_frames(self._buf):
                    await self._dispatch(ptype, payload)
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # noqa: BLE001
            _LOG.error("CIP read loop error: %s", exc)
        finally:
            if self.on_disconnect and not self._closing:
                self.on_disconnect()

    async def _dispatch(self, ptype: int, payload: bytes) -> None:
        self.raw_frames.append((ptype, payload.hex()))
        del self.raw_frames[:-200]
        _LOG.debug("CIP <- type=0x%02X payload=%s", ptype, payload.hex())
        if ptype in (p.PacketType.CONNECTION_RESPONSE, p.PacketType.DEVICE_ROUTER_CONNECT_RESPONSE):
            self._on_registration_response(payload)
        elif ptype == p.PacketType.HEARTBEAT:
            await self._send(p.PacketType.HEARTBEAT_RESPONSE)
        elif ptype in (p.PacketType.DATA, p.PacketType.EXTENDED_DATA):
            for listener in self.data_listeners:
                try:
                    listener(payload)
                except Exception as exc:  # noqa: BLE001
                    _LOG.debug("DATA listener failed: %s", exc)
            self._parse_data(payload)
        elif ptype == p.PacketType.PROGRAM_READY:
            _LOG.info("Processor reports PROGRAM_READY")
        elif ptype == p.PacketType.DISCONNECT:
            # The processor is ending the session. Close our side so the read
            # loop sees EOF and reports the drop, which lets the hub reconnect.
            if self._writer is not None:
                self._writer.close()

    def _parse_data(self, payload: bytes) -> None:
        try:
            for ctype, data in p.iter_cresnet_records(payload):
                for kind, join, value, obj in p.split_object_joins(ctype, data):
                    self._store(kind, join, value, obj)
                    self._emit(kind, join, value, obj)
        except Exception as exc:  # noqa: BLE001
            _LOG.debug("DATA parse skipped (%s): %s", exc, payload.hex())

    def _emit(self, kind: str, join: int, value: object, obj: int = 0) -> None:
        if not self.on_feedback:
            return
        res = self.on_feedback(kind, join, value, obj)
        if asyncio.iscoroutine(res):
            asyncio.create_task(res)

    # -- low-level send -----------------------------------------------------
    async def _send(self, ptype: int, payload: bytes = b"") -> None:
        if self._writer is None:
            raise RuntimeError("not connected")
        self._writer.write(p.frame(ptype, payload))
        await self._writer.drain()

    def _queue(self, ptype: int, payload: bytes = b"") -> None:
        asyncio.create_task(self._send(ptype, payload))
