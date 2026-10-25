"""A minimal in-process CIP server that emulates a processor, for testing the client."""
from __future__ import annotations

import asyncio
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pkg  # noqa: E402,F401

from crestron_cip import protocol as p


class MockProcessor:
    def __init__(self) -> None:
        self.received_joins: list[tuple[str, int, object]] = []
        self.registered_ipid: int | None = None
        self.registered_body: bytes = b""
        self.registration_reply = bytes([0x00])
        self.answer_heartbeats = True
        self.server: asyncio.AbstractServer | None = None
        self.port: int = 0

    async def start(self) -> int:
        self.server = await asyncio.start_server(self._handle, "127.0.0.1", 0)
        self.port = self.server.sockets[0].getsockname()[1]
        return self.port

    async def stop(self) -> None:
        if self.server:
            self.server.close()
            try:
                await asyncio.wait_for(self.server.wait_closed(), 2)
            except (asyncio.TimeoutError, Exception):
                pass

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        buf = bytearray()
        try:
            while True:
                chunk = await reader.read(4096)
                if not chunk:
                    break
                buf.extend(chunk)
                for ptype, payload in p.iter_frames(buf):
                    await self._on_frame(ptype, payload, writer)
        except (asyncio.CancelledError, ConnectionError):
            pass

    async def _send(self, writer, ptype, payload=b"") -> None:
        writer.write(p.frame(ptype, payload))
        await writer.drain()

    async def _on_frame(self, ptype, payload, writer) -> None:
        if ptype == p.PacketType.CONNECT:
            self.registered_ipid = payload[5] if len(payload) >= 6 else None
            self.registered_body = payload
            await self._send(writer, p.PacketType.CONNECTION_RESPONSE, self.registration_reply)
        elif ptype == p.PacketType.HEARTBEAT and self.answer_heartbeats:
            await self._send(writer, p.PacketType.HEARTBEAT_RESPONSE)
        elif ptype == p.PacketType.DATA:
            for ct, data in p.iter_cresnet_records(payload):
                if ct == p.CresnetType.COMMAND and data[:1] == bytes([p.CommandSubtype.UPDATE_REQUEST]):
                    await self._send_feedback(writer)
                else:
                    for kind, join, value in p.split_joins(ct, data):
                        self.received_joins.append((kind, join, value))

    async def _send_feedback(self, writer) -> None:
        await self._send(writer, p.PacketType.DATA,
                         p.pack_cresnet(p.CresnetType.DIGITAL, p.pack_digital(45, True)))
        await self._send(writer, p.PacketType.DATA,
                         p.pack_cresnet(p.CresnetType.ANALOG, p.pack_analog(5, 32000)))
        await self._send(writer, p.PacketType.DATA,
                         p.pack_cresnet(p.CresnetType.SERIAL, p.pack_serial(3, "Zone 1")))
        await self._send(writer, p.PacketType.HEARTBEAT)
