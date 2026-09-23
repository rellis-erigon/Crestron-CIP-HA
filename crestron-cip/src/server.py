"""HTTP API and ingress UI for the Crestron CIP add-on.

Everything runs on one asyncio loop: the CIP connections stay open while the
web server serves from the same process, so the UI reflects join changes as
they happen rather than polling a snapshot.

A processor only reports joins whose value is not the default — anything
sitting at 0, false or empty is never sent. A panel with hundreds of joins
may therefore announce a dozen on connect, with the rest appearing as the
system is used. That is why this exposes a live event stream: the practical
way to map a system is to press a button and watch which join moves.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path

from aiohttp import web

from cip_client import CipConnection, SignalType
from join_store import KINDS_FOR_SIGNAL, VALID_KINDS, JoinStore

logger = logging.getLogger("crestron-cip.server")

# /app/static inside the add-on image; the repo copy when run from a checkout.
_PACKAGED = Path("/app/static")
STATIC_DIR = _PACKAGED if _PACKAGED.is_dir() else Path(__file__).parent.parent / "static"


class Hub:
    """Owns the processor connections and the join store."""

    def __init__(self, store: JoinStore) -> None:
        self.store = store
        self.connections: dict[str, CipConnection] = {}
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []

    def add_processor(self, name: str, host: str, ipid: int) -> CipConnection:
        conn = CipConnection(host, ipid, name=name)
        conn._on_join = lambda join, n=name: self._on_join(n, join)
        self.connections[name] = conn
        return conn

    def _on_join(self, processor: str, join) -> None:
        stored = self.store.observe(
            processor, join.signal.value, join.number, join.value,
        )
        self._publish({
            "type": "join",
            "processor": processor,
            "key": stored.key,
            "signal": stored.signal,
            "number": stored.number,
            "value": stored.value,
            "updates": stored.updates,
            "name": stored.config.name,
            "at": time.time(),
        })

    # -- live stream -----------------------------------------------------

    def subscribe(self) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue) -> None:
        self._subscribers.discard(queue)

    def _publish(self, event: dict) -> None:
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A browser that stopped reading must not stall the hub, so
                # its backlog is dropped rather than blocking every other
                # subscriber.
                self._subscribers.discard(queue)

    # -- lifecycle -------------------------------------------------------

    async def start(self) -> None:
        for conn in self.connections.values():
            self._tasks.append(asyncio.create_task(conn.run()))
        self._tasks.append(asyncio.create_task(self._autosave()))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for conn in self.connections.values():
            await conn.close()
        self.store.save_if_dirty()

    async def _autosave(self, interval: float = 30.0) -> None:
        """Persist periodically rather than on every join update.

        A busy panel can move joins many times a second; writing the store
        each time would spend the add-on's life in the filesystem.
        """
        while True:
            await asyncio.sleep(interval)
            try:
                if self.store.save_if_dirty():
                    logger.debug("Saved %d joins", len(self.store.joins))
            except OSError as err:
                logger.warning("Could not save the join store: %s", err)


# -- API -----------------------------------------------------------------

def _join_payload(join) -> dict:
    data = join.to_dict()
    data["config"] = {
        "name": join.config.name,
        "kind": join.config.kind,
        "enabled": join.config.enabled,
        "unit": join.config.unit,
        "device_class": join.config.device_class,
        "scale": join.config.scale,
        "notes": join.config.notes,
    }
    return data


async def status(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    return web.json_response({
        "processors": [
            {
                **conn.snapshot(),
                # snapshot() carries the joins themselves; the status view
                # only needs the counts.
                "joins": None,
                "stored_joins": len(hub.store.for_processor(name)),
            }
            for name, conn in hub.connections.items()
        ],
        "total_joins": len(hub.store.joins),
        "exposed_joins": len(hub.store.exposed()),
    })


async def list_joins(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    processor = request.query.get("processor", "")
    signal = request.query.get("signal", "")
    search = request.query.get("search", "").lower()
    only = request.query.get("only", "")

    joins = (hub.store.for_processor(processor) if processor
             else sorted(hub.store.joins.values(),
                         key=lambda j: (j.processor, j.signal, j.number)))
    if signal:
        joins = [j for j in joins if j.signal == signal]
    if only == "enabled":
        joins = [j for j in joins if j.config.enabled]
    elif only == "named":
        joins = [j for j in joins if j.config.name]
    if search:
        joins = [
            j for j in joins
            if search in j.key.lower()
            or search in j.config.name.lower()
            or search in str(j.value).lower()
        ]
    return web.json_response({
        "joins": [_join_payload(j) for j in joins],
        "total": len(joins),
    })


async def configure_join(request: web.Request) -> web.Response:
    hub: Hub = request.app["hub"]
    body = await request.json()
    processor = (body.get("processor") or "").strip()
    key = (body.get("key") or "").strip()
    if not processor or not key:
        raise web.HTTPBadRequest(reason="processor and key are required")

    changes = {
        k: body[k] for k in
        ("name", "kind", "enabled", "unit", "device_class", "scale", "notes")
        if k in body
    }
    try:
        join = hub.store.configure(processor, key, **changes)
    except KeyError:
        raise web.HTTPNotFound(reason=f"unknown join {processor}/{key}")
    except ValueError as err:
        raise web.HTTPBadRequest(reason=str(err))
    hub.store.save()
    return web.json_response({"ok": True, "join": _join_payload(join)})


async def set_join(request: web.Request) -> web.Response:
    """Write a value back to a processor."""
    hub: Hub = request.app["hub"]
    body = await request.json()
    processor = (body.get("processor") or "").strip()
    key = (body.get("key") or "").strip()
    conn = hub.connections.get(processor)
    if conn is None:
        raise web.HTTPNotFound(reason=f"unknown processor {processor}")
    if not conn.registered:
        raise web.HTTPServiceUnavailable(
            reason=f"{processor} is not connected")

    signal, number = key[:1], key[1:]
    if signal not in ("d", "a", "s") or not number.isdigit():
        raise web.HTTPBadRequest(reason=f"malformed join key {key!r}")
    number = int(number)

    try:
        if signal == "d":
            if body.get("pulse"):
                await conn.pulse_digital(number)
            else:
                await conn.set_digital(number, bool(body.get("value")))
        elif signal == "a":
            await conn.set_analog(number, int(body.get("value", 0)))
        else:
            await conn.set_serial(number, str(body.get("value", "")))
    except (OSError, ConnectionError) as err:
        raise web.HTTPServiceUnavailable(reason=str(err))
    return web.json_response({"ok": True})


async def events(request: web.Request) -> web.StreamResponse:
    """Server-sent events, one per join update.

    This is how a system gets mapped in practice: open the panel, press a
    button, and watch which join moves.
    """
    hub: Hub = request.app["hub"]
    response = web.StreamResponse(headers={
        "Content-Type": "text/event-stream",
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })
    await response.prepare(request)
    queue = hub.subscribe()
    try:
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), timeout=20)
            except asyncio.TimeoutError:
                # Keep the connection alive through proxies that time out.
                await response.write(b": keepalive\n\n")
                continue
            await response.write(
                f"data: {json.dumps(event)}\n\n".encode()
            )
    except (ConnectionResetError, asyncio.CancelledError):
        pass
    finally:
        hub.unsubscribe(queue)
    return response


async def integration_joins(request: web.Request) -> web.Response:
    """What the Home Assistant integration consumes: exposed joins only."""
    hub: Hub = request.app["hub"]
    return web.json_response({
        "joins": [
            {
                "processor": j.processor,
                "key": j.key,
                "signal": j.signal,
                "number": j.number,
                "value": j.value,
                "kind": j.config.resolved_kind(j.signal),
                "name": j.config.name or f"{j.processor} {j.key}",
                "unit": j.config.unit,
                "device_class": j.config.device_class,
                "scale": j.config.scale,
                "available": bool(
                    hub.connections.get(j.processor)
                    and hub.connections[j.processor].registered
                ),
            }
            for j in hub.store.exposed()
        ],
        "processors": {
            name: {"registered": conn.registered, "host": conn.host}
            for name, conn in hub.connections.items()
        },
    })


async def kinds(request: web.Request) -> web.Response:
    return web.json_response({
        "kinds": list(VALID_KINDS),
        "by_signal": {k: list(v) for k, v in KINDS_FOR_SIGNAL.items()},
    })


async def index(request: web.Request) -> web.Response:
    """Serve the panel for any path that is not an API call.

    Ingress composes the URL it forwards, and it does not always compose
    what you expect — a stray double slash once made the whole panel 404.
    Anything that is not /api is the page.
    """
    if request.path.startswith("/api/"):
        raise web.HTTPNotFound()
    page = STATIC_DIR / "index.html"
    if not page.exists():
        return web.Response(text="UI not installed", status=404)
    return web.FileResponse(page)


def build_app(hub: Hub) -> web.Application:
    app = web.Application()
    app["hub"] = hub
    app.add_routes([
        web.get("/", index),
        web.get("/api/status", status),
        web.get("/api/kinds", kinds),
        web.get("/api/joins", list_joins),
        web.post("/api/joins/configure", configure_join),
        web.post("/api/joins/set", set_join),
        web.get("/api/events", events),
        web.get("/api/integration/joins", integration_joins),
    ])
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", STATIC_DIR)
    # Last, so it only catches what nothing else claimed.
    app.router.add_get("/{tail:.*}", index)
    return app
