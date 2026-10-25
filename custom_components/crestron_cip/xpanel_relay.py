"""Carry a browser XPanel's CIP connection to its processor.

The XPanel a panel project compiles to is Crestron's own Flash program. It
draws the panel, runs its page logic and speaks CIP itself, over a raw TCP
socket. A browser cannot open one, so the card runs it under Ruffle, which
turns each socket into a websocket; this view is the other end of that
websocket and opens the real TCP connection, byte for byte.

Nothing here reads or changes what passes through. The XPanel registers at
the config entry's IPID exactly as the wall panel does, so on a processor
that will not share an IPID (a CP4 has refused to) this needs an IPID of its
own in the program, as the integration itself does.
"""
from __future__ import annotations

import asyncio
import logging
import ssl

from aiohttp import WSMsgType, web
import voluptuous as vol

from homeassistant.components import websocket_api
from homeassistant.components.http import HomeAssistantView
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant, callback

from .const import CONF_IPID, CONF_SSL, DEFAULT_PORT, DEFAULT_TLS_PORT, DOMAIN

_LOGGER = logging.getLogger(__name__)

RELAY_URL = "/api/crestron_cip/xpanel/{entry_id}"


class XPanelRelayView(HomeAssistantView):
    """One websocket per running XPanel, one TCP connection behind each."""

    url = RELAY_URL
    name = "api:crestron_cip:xpanel"
    # The card signs the path (auth/sign_path): a websocket opened by Flash
    # under Ruffle cannot carry an Authorization header.
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self.hass = hass

    async def get(self, request: web.Request, entry_id: str) -> web.StreamResponse:
        entry = self.hass.config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN:
            return web.Response(status=404, text="no such Crestron CIP entry")

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

        async def upstream() -> None:
            async for msg in ws:
                if msg.type == WSMsgType.BINARY:
                    writer.write(msg.data)
                    await writer.drain()
                elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                    break

        async def downstream() -> None:
            while True:
                chunk = await reader.read(65536)
                if not chunk:
                    break
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


@websocket_api.websocket_command(
    {vol.Required("type"): "crestron_cip/xpanel_info", vol.Required("entry_id"): str})
@callback
def ws_xpanel_info(hass: HomeAssistant, connection: websocket_api.ActiveConnection,
                   msg: dict) -> None:
    """What the XPanel is told to connect to, and where its relay is.

    The XPanel takes its processor and IPID as page parameters, as the
    Crestron-hosted page gives them. The address it is given is only a name
    for the socket: the relay, not the browser, makes the connection.
    """
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN:
        connection.send_error(msg["id"], "not_found", "no such Crestron CIP entry")
        return
    use_ssl = entry.data.get(CONF_SSL, False)
    connection.send_result(msg["id"], {
        "host": entry.data[CONF_HOST],
        "port": entry.data.get(CONF_PORT) or (DEFAULT_TLS_PORT if use_ssl else DEFAULT_PORT),
        "ipid": int(entry.data[CONF_IPID]),
        "relay": RELAY_URL.format(entry_id=entry.entry_id),
    })
