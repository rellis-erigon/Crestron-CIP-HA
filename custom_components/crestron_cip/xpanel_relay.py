"""The websocket a browser XPanel's CIP socket arrives on.

The XPanel a panel project compiles to is Crestron's own Flash program. It
draws the panel, runs its page logic and speaks CIP itself, over a raw TCP
socket. A browser cannot open one, so the card runs it under Ruffle, which
turns each socket into a websocket; this view is the other end of it.

By default the panel is served from the integration's own connection
(xpanel_mux): one registration at the IPID however many panels are open,
which is what a processor that will not share an IPID needs. With
`?direct` the socket is instead carried to the processor byte for byte, as
a registration of its own; that is kept to compare the two.
"""
from __future__ import annotations

import asyncio
import hmac
import logging
import secrets
import ssl

from aiohttp import WSMsgType, web
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback

from . import protocol as p
from .const import CONF_IPID, CONF_SSL, DEFAULT_PORT, DEFAULT_TLS_PORT, DOMAIN

# The entry's key for its panels' websocket.
CONF_XPANEL_KEY = "xpanel_key"


def ensure_panel_key(hass: HomeAssistant, entry) -> str:
    """The entry's panel key, made once and kept with the entry."""
    key = entry.data.get(CONF_XPANEL_KEY)
    if not key:
        key = secrets.token_urlsafe(32)
        hass.config_entries.async_update_entry(entry, data={**entry.data, CONF_XPANEL_KEY: key})
    return key
from .panel_store import OPT_PANEL_URL, OPT_XPANEL_URL, find_panel, panels_of
from .xpanel_mux import PANEL_SILENCE_S, PanelLink

_LOGGER = logging.getLogger(__name__)

RELAY_URL = "/api/crestron_cip/xpanel/{entry_id}"


class _Tap:
    """The opening of a relayed session, frame by frame, at debug level.

    What a panel and its processor say to each other on connecting is the
    part nobody has documented; this is how it is read off a live session.
    Only the first frames each way are kept, then only non-join frames.
    """

    FIRST = 80

    def __init__(self, name: str) -> None:
        self.name = name
        self.buffers = {"panel->processor": bytearray(), "processor->panel": bytearray()}
        self.counts = {"panel->processor": 0, "processor->panel": 0}

    def feed(self, way: str, data: bytes) -> None:
        if not _LOGGER.isEnabledFor(logging.DEBUG):
            return
        buffer = self.buffers[way]
        buffer.extend(data)
        while len(buffer) >= 3:
            length = (buffer[1] << 8) | buffer[2]
            if len(buffer) < 3 + length:
                break
            frame = bytes(buffer[: 3 + length])
            del buffer[: 3 + length]
            self.counts[way] += 1
            if self.counts[way] <= self.FIRST or frame[0] not in (0x05, 0x0D, 0x0E):
                _LOGGER.debug("XPanel %s %s #%d type=0x%02X %s", self.name, way,
                              self.counts[way], frame[0], frame[3:].hex())


class XPanelRelayView(HomeAssistantView):
    """One websocket per running XPanel, one TCP connection behind each."""

    url = RELAY_URL
    name = "api:crestron_cip:xpanel"
    # Not Home Assistant's own auth: a panel left running must reconnect by
    # itself after Home Assistant restarts, and a signed path dies with the
    # run that signed it (it is signed with a per-run secret). The XPanel
    # cannot carry a header either. So each entry has a key of its own,
    # kept in its config entry and handed out only over the authenticated
    # websocket (xpanel_info); the websocket here must present it.
    requires_auth = False

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, entry_id: str) -> web.StreamResponse:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return web.Response(status=404, text="no such Crestron CIP entry")
        expected = entry.data.get(CONF_XPANEL_KEY)
        if not expected or not hmac.compare_digest(
                request.query.get("key", ""), expected):
            return web.Response(status=401, text="wrong or missing panel key")
        if "direct" in request.query:
            return await self._direct(request, entry)
        hub = self.hass.data.get(DOMAIN, {}).get(entry_id)
        if hub is None:
            return web.Response(status=503, text=f"{entry.title} is not set up")
        return await self._served(request, entry, hub)

    async def _served(self, request: web.Request, entry, hub) -> web.StreamResponse:
        """The panel served from the integration's own connection.

        `?panel=` names the room's panel, and so the IPID it is served at;
        without it the panel is at the entry's own IPID.
        """
        ipid = hub.ipid
        title = entry.title
        if panel_id := request.query.get("panel"):
            panel = find_panel(entry.options, hub.ipid, panel_id)
            if panel is None:
                return web.Response(status=404, text=f"{entry.title} has no panel {panel_id}")
            ipid = int(panel["ipid"])
            title = f"{entry.title} {panel['name']}"
        link_to = hub.link_for(ipid)
        if link_to is None or not link_to[0].connected:
            # The engine retries by itself; by then the processor may be back.
            return web.Response(status=503, text=f"{title} is not connected")
        mux = link_to[1]
        ws = web.WebSocketResponse(max_msg_size=0, heartbeat=None)
        await ws.prepare(request)
        tap = _Tap(title)
        count = len(mux.links) + 1

        async def send(ptype: int, body: bytes) -> None:
            frame = p.frame(ptype, body)
            tap.feed("processor->panel", frame)
            await ws.send_bytes(frame)

        # The panel's page joins (from its import), so a panel opened now
        # can be flipped to the page the processor last showed.
        pages = frozenset(
            int(j) for j in request.query.get("pages", "").split(",") if j.strip().isdigit())
        link = PanelLink(mux, send, f"{title} #{count}", close=ws.close, pages=pages)
        mux.links.add(link)
        _LOGGER.info("XPanel served for %s (%d open)", title, len(mux.links))
        try:
            await link.start()
            while True:
                try:
                    msg = await ws.receive(timeout=PANEL_SILENCE_S)
                except asyncio.TimeoutError:
                    _LOGGER.info("XPanel %s went silent", link.name)
                    break
                if msg.type != WSMsgType.BINARY:
                    break
                tap.feed("panel->processor", msg.data)
                if not await link.feed(msg.data):
                    break
        except (ConnectionResetError, RuntimeError) as err:
            _LOGGER.debug("XPanel %s dropped: %s", link.name, err)
        finally:
            mux.links.discard(link)
            await ws.close()
            _LOGGER.info("XPanel closed for %s (%d open)", title, len(mux.links))
        return ws

    async def _direct(self, request: web.Request, entry) -> web.StreamResponse:
        """The panel's socket carried to the processor as it is.

        A registration of its own at the IPID, so only for a processor that
        shares one; kept to compare against the served path.
        """
        use_ssl = entry.data.get(CONF_SSL, False)
        host = entry.data[CONF_HOST]
        port = entry.data.get(CONF_PORT) or (DEFAULT_TLS_PORT if use_ssl else DEFAULT_PORT)
        context = None
        if use_ssl:
            # Processors present self-signed certificates, as in client.py.
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE

        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port, ssl=context), 10)
        except (OSError, asyncio.TimeoutError) as err:
            _LOGGER.warning("XPanel relay could not reach %s:%s: %s", host, port, err)
            return web.Response(status=502, text=f"cannot reach {host}:{port}")

        ws = web.WebSocketResponse(max_msg_size=0)
        await ws.prepare(request)
        _LOGGER.info("XPanel relay open to %s:%s for %s", host, port, entry.title)

        tap = _Tap(entry.title)

        async def upstream() -> None:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    tap.feed("panel->processor", msg.data)
                    writer.write(msg.data)
                    await writer.drain()
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break

        async def downstream() -> None:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
                tap.feed("processor->panel", chunk)
                await ws.send_bytes(chunk)

        tasks = [asyncio.create_task(upstream()), asyncio.create_task(downstream())]
        try:
            await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            writer.close()
            await ws.close()
            _LOGGER.info("XPanel relay closed for %s", entry.title)
        return ws


def panel_device_identifier(entry_id: str, panel_id: str) -> tuple[str, str]:
    """A room panel's device, under its processor's."""
    return (DOMAIN, f"{entry_id}:{panel_id}")


def _resolve(hass: HomeAssistant, msg: dict) -> tuple[object, str | None] | None:
    """(entry, panel id) for a card's device, or for its entry and panel."""
    if device_id := msg.get("device_id"):
        from homeassistant.helpers import device_registry as dr

        device = dr.async_get(hass).async_get(device_id)
        if device is None:
            return None
        for domain, ident in device.identifiers:
            if domain != DOMAIN:
                continue
            entry_id, _, panel_id = ident.partition(":")
            entry = hass.config_entries.async_get_entry(entry_id)
            if entry is not None and entry.domain == DOMAIN:
                return entry, panel_id or msg.get("panel")
        return None
    entry = hass.config_entries.async_get_entry(msg.get("entry_id", ""))
    if entry is None or entry.domain != DOMAIN:
        return None
    return entry, msg.get("panel")


@websocket_api.websocket_command({
    vol.Required("type"): "crestron_cip/xpanel_info",
    vol.Exclusive("entry_id", "target"): str,
    vol.Exclusive("device_id", "target"): str,
    vol.Optional("panel"): str,
})
@callback
def ws_xpanel_info(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                   msg: dict) -> None:
    """What the XPanel is told to connect to, and where its relay is.

    The XPanel takes its processor and IPID as page parameters, as the
    Crestron-hosted page gives them. The address it is given is only a name
    for the socket: the relay, not the browser, makes the connection.

    A card names a room panel's device, or the processor's (whose first
    panel it then is), or an entry and a panel id.
    """
    found = _resolve(hass, msg)
    if found is None:
        connection.send_error(msg["id"], "not_found", "no such Crestron CIP processor or panel")
        return
    entry, panel_id = found
    entry_ipid = int(entry.data[CONF_IPID])
    panel = find_panel(entry.options, entry_ipid, panel_id)
    if panel_id and panel is None:
        connection.send_error(msg["id"], "not_found", f"{entry.title} has no panel {panel_id}")
        return
    use_ssl = entry.data.get(CONF_SSL, False)
    relay = RELAY_URL.format(entry_id=entry.entry_id)
    connection.send_result(msg["id"], {
        "entry_id": entry.entry_id,
        "host": entry.data[CONF_HOST],
        "port": entry.data.get(CONF_PORT) or (DEFAULT_TLS_PORT if use_ssl else DEFAULT_PORT),
        "ipid": int(panel["ipid"]) if panel else entry_ipid,
        "relay": relay,
        "key": ensure_panel_key(hass, entry),
        # The panel, if the processor has been given one; `panel` goes on
        # the relay's query, so the panel is served at its own IPID.
        "panel": panel["id"] if panel else None,
        "xpanel_url": panel.get(OPT_XPANEL_URL) if panel else None,
        "panel_url": panel.get(OPT_PANEL_URL) if panel else None,
        "name": (panel or {}).get("name") or entry.data.get("name") or entry.title,
        "panels": [{"id": p["id"], "name": p["name"], "ipid": int(p["ipid"])}
                   for p in panels_of(entry.options, entry_ipid)],
    })


@websocket_api.websocket_command({
    vol.Required("type"): "crestron_cip/xpanel_log",
    vol.Required("lines"): [str],
})
@callback
def ws_xpanel_log(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                  msg: dict) -> None:
    """What Ruffle printed in the browser, for a card set to `debug: true`.

    Ruffle reports an XPanel it cannot run only in the browser's console;
    this puts it in Home Assistant's log, where whoever is diagnosing it
    can read it without the console open.
    """
    for line in msg["lines"][:200]:
        _LOGGER.warning("XPanel browser: %s", line[:2000])
    connection.send_result(msg["id"])
