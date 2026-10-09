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
import io
import json
import logging
import time
import zipfile
from pathlib import Path, PurePosixPath

from aiohttp import web

from panel_apply import (
    DEFAULT_PORT, ApplyError, apply_panel, attach_processor,
)
from panel_faceplate import (
    build_faceplate, candidate_screens, main_screen, summarise_faceplate,
)
from panel_groups import propose
from panel_import import PanelError, read_objects, to_faceplate
from panel_reader import PanelReadError, read_panel
from cards import (
    MIXER_ROLES, describe as describe_roles, mixer_card, parse_roles,
    room_card,
)
from cip_client import CipConnection, SignalType
from console import Health, read_health
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
        self.health: dict[str, Health] = {}
        self.console: dict[str, dict] = {}
        self.health_interval = 300.0
        self._subscribers: set[asyncio.Queue] = set()
        self._tasks: list[asyncio.Task] = []

    def add_processor(self, name: str, host: str, ipid: int,
                      port: int | None = None) -> CipConnection:
        conn = CipConnection(host, ipid, name=name,
                             **({"port": port} if port else {}))
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

    def start_processor(self, name: str) -> bool:
        """Connect to a processor added after the hub was started.

        A panel import adds its processor there and then, so waiting for
        a restart to pick it up would make a one-click import a
        two-step one.
        """
        conn = self.connections.get(name)
        if conn is None:
            return False
        self._tasks.append(asyncio.create_task(conn.run()))
        return True

    async def start(self) -> None:
        for conn in self.connections.values():
            self._tasks.append(asyncio.create_task(conn.run()))
        self._tasks.append(asyncio.create_task(self._autosave()))
        if self.console:
            self._tasks.append(asyncio.create_task(self._poll_health()))

    async def _poll_health(self) -> None:
        """Read processor load and memory from the text console.

        Deliberately slow, and one processor at a time. Each reading is a
        full SSH login, and these processors do not enjoy having sessions
        opened at them in quick succession — during development a run of
        rapid logins was followed by one dropping off the network entirely.
        Minutes apart, sequential, is the safe shape.
        """
        # Let the CIP connections settle before adding SSH on top.
        await asyncio.sleep(30)
        while True:
            for name, credentials in self.console.items():
                try:
                    health = await read_health(
                        credentials["host"], credentials.get("username", ""),
                        credentials.get("password", ""),
                    )
                except Exception as err:  # noqa: BLE001 - never kill the loop
                    health = Health(error=str(err))
                self.health[name] = health
                if health.ok:
                    logger.debug(
                        "%s: cpu %.0f%%, memory %.0f%%",
                        name, health.cpu_percent or 0, health.memory_percent or 0,
                    )
                elif health.error:
                    logger.debug("%s health: %s", name, health.error)
                # Space the logins out rather than firing them together.
                await asyncio.sleep(10)
            await asyncio.sleep(self.health_interval)

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
        "precision": join.config.precision,
        "group": join.config.group,
        "notes": join.config.notes,
    }
    data["resolved_group"] = join.config.resolved_group(join.processor)
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
                "health": (hub.health[name].to_dict()
                           if name in hub.health else None),
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
        ("name", "kind", "enabled", "unit", "device_class", "scale",
         "notes", "group", "precision")
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


async def bulk_configure(request: web.Request) -> web.Response:
    """Apply one set of changes to many joins.

    Eighty joins is too many to click through one at a time, which is
    the same reason the Q-SYS bridge grew this.
    """
    hub: Hub = request.app["hub"]
    body = await request.json()
    processor = (body.get("processor") or "").strip()
    keys = body.get("keys") or []
    if not processor or not isinstance(keys, list) or not keys:
        raise web.HTTPBadRequest(reason="processor and a list of keys are required")

    changes = {
        k: body[k] for k in
        ("name", "kind", "enabled", "unit", "device_class", "scale",
         "notes", "group", "precision")
        if k in body
    }
    if not changes:
        raise web.HTTPBadRequest(reason="nothing to change")

    done, failed = hub.store.bulk_configure(processor, keys, **changes)
    hub.store.save()
    return web.json_response({"ok": True, "changed": done, "errors": failed})


async def autogroup(request: web.Request) -> web.Response:
    """Derive one device per item from a repeating join layout."""
    hub: Hub = request.app["hub"]
    body = await request.json()
    processor = (body.get("processor") or "").strip()
    if not processor:
        raise web.HTTPBadRequest(reason="processor is required")
    try:
        assigned = hub.store.autogroup(
            processor,
            stride=int(body.get("stride", 10)),
            start=int(body.get("start", 11)),
        )
    except ValueError as err:
        raise web.HTTPBadRequest(reason=str(err))
    hub.store.save()
    groups = sorted(set(assigned.values()))
    return web.json_response({
        "ok": True, "joins": len(assigned),
        "groups": groups, "group_count": len(groups),
    })


async def list_groups(request: web.Request) -> web.Response:
    """The devices these joins will appear as in Home Assistant."""
    hub: Hub = request.app["hub"]
    processor = request.query.get("processor", "")
    rows = (hub.store.for_processor(processor) if processor
            else list(hub.store.joins.values()))
    groups: dict[str, dict] = {}
    for join in rows:
        name = join.config.resolved_group(join.processor)
        entry = groups.setdefault(
            name, {"name": name, "processor": join.processor,
                   "joins": 0, "exposed": 0})
        entry["joins"] += 1
        if join.config.enabled:
            entry["exposed"] += 1
    return web.json_response({
        "groups": sorted(groups.values(), key=lambda g: g["name"].casefold())
    })


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


async def rediscover(request: web.Request) -> web.Response:
    """Ask each processor to dump its joins again.

    A processor only reports joins that are not at their default, so the
    picture we hold is whatever it happened to send. After someone has used
    the system — or a program has been reloaded — asking again is how new
    joins are found without restarting anything.
    """
    hub: Hub = request.app["hub"]
    body = await request.json() if request.can_read_body else {}
    wanted = (body.get("processor") or "").strip()

    if wanted and wanted not in hub.connections:
        raise web.HTTPNotFound(reason=f"unknown processor {wanted}")
    names = [wanted] if wanted else list(hub.connections)

    results = {}
    for name in names:
        conn = hub.connections[name]
        before = len(hub.store.for_processor(name))
        try:
            await conn.request_update()
        except (OSError, ConnectionError) as err:
            results[name] = {"ok": False, "error": str(err)}
            continue
        results[name] = {"ok": True, "joins_before": before}

    # The dump arrives asynchronously, so give it a moment before reporting
    # counts rather than returning numbers that are about to change.
    await asyncio.sleep(2.0)
    for name, result in results.items():
        if result.get("ok"):
            after = len(hub.store.for_processor(name))
            result["joins"] = after
            result["added"] = max(0, after - result.pop("joins_before", after))
    return web.json_response({"ok": True, "processors": results})


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


async def health(request: web.Request) -> web.Response:
    """Processor load and memory, as last read from the console."""
    hub: Hub = request.app["hub"]
    return web.json_response({
        "health": {name: reading.to_dict() for name, reading in hub.health.items()},
        "interval": hub.health_interval,
        "configured": sorted(hub.console),
    })


async def room_card_endpoint(request: web.Request) -> web.Response:
    """A room card for one processor, with join keys where entity ids go.

    The role assignment is a guess made from the names on the joins, so
    the reply carries the working as well as the card.
    """
    hub: Hub = request.app["hub"]
    processor = request.query.get("processor", "")
    joins = (
        hub.store.for_processor(processor) if processor
        else hub.store.exposed()
    )
    if processor and not joins:
        return web.json_response(
            {"error": f"no joins for {processor}"}, status=404)

    card = room_card(joins, processor)
    if card is None:
        return web.json_response(
            {"error": "no exposed join matched a room role — expose and "
                      "name joins such as Volume, Mute or Source first"},
            status=404,
        )
    return web.json_response({
        "card": card,
        "processor": processor,
        "keys": sorted(set(card["entities"].values())),
        **describe_roles(joins),
    })


async def mixer_roles(request: web.Request) -> web.Response:
    """The per-channel properties a mixer card can carry."""
    return web.json_response({"roles": list(MIXER_ROLES)})


async def mixer_card_endpoint(request: web.Request) -> web.Response:
    """A zone-mixer card for a processor's zones, with join keys in place
    of entity ids — the integration substitutes those."""
    hub: Hub = request.app["hub"]
    processor = request.query.get("processor", "")
    joins = (hub.store.for_processor(processor) if processor
             else hub.store.exposed())
    try:
        roles = parse_roles(request.query.get("roles", ""))
    except ValueError as err:
        raise web.HTTPBadRequest(reason=str(err))
    card, omitted = mixer_card(
        joins,
        title=request.query.get("title") or processor or "Zone Mixer",
        stride=int(request.query.get("stride", 10)),
        start=int(request.query.get("start", 11)),
        roles=roles,
        per_row=int(request.query.get("per_row", 8)),
    )
    if card is None:
        return web.json_response(
            {"error": "no zone has any of the chosen properties bound — "
                      "expose those joins first"},
            status=404,
        )
    return web.json_response({
        "card": card,
        "processor": processor,
        "keys": sorted(set(card["entities"].values())),
        "unbound_zones": omitted["unbound"],
        "over_cap_zones": omitted["over_cap"],
    })


PANEL_FILE = Path("/config/crestron-cip/panel.json")

# Where a panel's own artwork is put so a card can fetch it.
#
# Home Assistant serves /config/www at /local, which is a plain URL any
# dashboard can use. The alternatives were worse: the add-on's own HTTP
# server is behind an ingress token a card cannot construct, and inlining
# the images as data URIs would put a megabyte of base64 into the
# dashboard config for every card.
PANEL_IMAGE_DIR = Path("/config/www/crestron-panels")
PANEL_IMAGE_URL = "/local/crestron-panels"
ART_KEYS = ("src", "src_on", "icon")


def _safe_name(path: str) -> str:
    """A filename safe to write and to put in a URL.

    Only the base name is kept: a zip entry can say `../../something` and
    the archive is not ours. Spaces become underscores because the result
    goes into an href, and "Cover Photo.png" would otherwise need encoding
    at every point it is used.
    """
    base = PurePosixPath(path).name
    keep = [c if (c.isalnum() or c in "._-") else "_" for c in base]
    return "".join(keep).lstrip(".") or "image"


def export_artwork(archive: bytes, faceplate: dict, panel: str) -> dict:
    """Write out the images a faceplate refers to, and point it at them.

    The faceplate comes back referring to URLs instead of archive paths.
    A missing or unreadable image drops the reference rather than leaving
    a link to nothing: a control that draws its colour and its label is a
    smaller loss than one that renders as a browser's broken-image icon.
    """
    wanted: set[str] = set()
    for region in faceplate.get("regions", []):
        for key in ART_KEYS:
            if region.get(key):
                wanted.add(region[key])
    if not wanted:
        return {"written": 0, "missing": []}

    folder = _safe_name(panel) or "panel"
    target = PANEL_IMAGE_DIR / folder
    written: dict[str, str] = {}
    missing: list[str] = []
    try:
        with zipfile.ZipFile(io.BytesIO(archive)) as zf:
            inside = set(zf.namelist())
            target.mkdir(parents=True, exist_ok=True)
            for path in sorted(wanted):
                if path not in inside:
                    missing.append(path)
                    continue
                name = _safe_name(path)
                (target / name).write_bytes(zf.read(path))
                written[path] = f"{PANEL_IMAGE_URL}/{folder}/{name}"
    except (zipfile.BadZipFile, OSError) as err:
        logger.warning("Could not write panel artwork: %s", err)
        missing = sorted(wanted)
        written = {}

    for region in faceplate.get("regions", []):
        for key in ART_KEYS:
            path = region.get(key)
            if not path:
                continue
            if path in written:
                region[key] = written[path]
            else:
                region.pop(key)
    return {"written": len(written), "missing": missing}


def parse_ipid(raw: str) -> int:
    """An IPID as somebody actually types it.

    Crestron writes IPIDs in hex and pads them to two digits — 03, 0A, 1F
    — and that is what is printed on the processor and in Toolbox, so it
    is what gets typed. `int(x, 0)` rejects "03" outright, because a
    leading zero means nothing to it, and the ValueError that came back
    read "invalid literal for int() with base 0: '03'". That was the real
    reason an assignment silently did nothing, with the field's own
    placeholder showing `03`.

    Hex is the right default: an IPID written 10 is sixteen, not ten.
    """
    text = str(raw).strip().lower()
    if not text:
        raise ValueError("no IPID given")
    if text.startswith("0x"):
        text = text[2:]
    if not text or not all(c in "0123456789abcdef" for c in text):
        raise ValueError(f"{raw!r} is not an IPID; expected something like 03 or 1F")
    value = int(text, 16)
    # CIP carries the IPID in one byte, and 0 is not a device.
    if not 1 <= value <= 0xFF:
        raise ValueError(f"IPID {raw!r} is outside 01-FF")
    return value


def check_host(host: str) -> None:
    """Refuse an address that cannot be what was meant.

    A mistyped octet — `1982.168.33.2` for `192.168.33.2` — otherwise
    becomes a connection attempt that fails later, somewhere the person
    who typed it will not see it.
    """
    text = host.strip()
    if not text:
        raise ValueError("no address given")
    # Only judge things shaped like dotted quads. A hostname is the
    # processor's business, not this function's.
    parts = text.split(".")
    if len(parts) == 4 and all(p.isdigit() for p in parts):
        for part in parts:
            if int(part) > 255:
                raise ValueError(
                    f"{host!r} is not a valid address: {part} is above 255 "
                    f"— check for a mistyped digit"
                )


async def upload_panel(request: web.Request) -> web.Response:
    """Read a .c3p or .vtz and keep the faceplate it describes.

    The archive reader is tried first, because it reads the panel's own
    layout — where every control sits, what it says, which subpage places
    it — and a card built from that is the screen the operator already
    knows. `panel_import` is the fallback for a `.vtp`, which carries the
    joins but no usable geometry.

    `page` picks which of the panel's screens to draw; without it the
    busiest one is used.
    """
    body = await request.read()
    if not body:
        raise web.HTTPBadRequest(reason="no file received")
    name = request.query.get("name", "Panel")
    page = request.query.get("page") or None

    panel: dict | None = None
    try:
        panel = read_panel(body)
        faceplate = build_faceplate(panel, page=page, name=name)
        source = "project"
    except PanelReadError:
        # Not an archive, or no Environment.xml in it. A .vtp still has
        # joins worth having, just nothing to lay them out with.
        try:
            faceplate = to_faceplate(read_objects(body), name)
            source = "joins-only"
        except PanelError as err:
            raise web.HTTPBadRequest(reason=str(err))
        except Exception as err:
            logger.warning("Could not read panel: %s", err)
            raise web.HTTPBadRequest(reason=f"could not read the panel: {err}")
    except Exception as err:  # malformed archives are the user's reality
        logger.warning("Could not read panel: %s", err)
        raise web.HTTPBadRequest(reason=f"could not read the panel: {err}")

    if not faceplate["regions"]:
        raise web.HTTPBadRequest(
            reason="no objects with joins in that project — it may be a "
                   "shell rather than a finished panel")

    assignment = {
        "host": (request.query.get("host") or "").strip(),
        "ipid": request.query.get("ipid", ""),
        "port": int(request.query.get("port") or DEFAULT_PORT),
    }
    # Pull the panel's own artwork out of the archive and point the
    # faceplate at it. Done before the faceplate is stored, so what is
    # saved is what a card can actually fetch.
    art = export_artwork(body, faceplate, name)

    PANEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    PANEL_FILE.write_text(json.dumps(
        {"name": name, "faceplate": faceplate, "processor": assignment}, indent=1))

    summary = summarise_faceplate(faceplate)
    result = {
        "ok": True, "name": name,
        "source": source,
        "size": faceplate["size"],
        "regions": summary["regions"],
        "kinds": summary["kinds"],
        "joins": summary["bindable"],
        "pages": faceplate.get("pages", []),
        "artwork": art,
    }
    if panel is not None:
        # Which screens there were to choose from, so the import page can
        # offer the others rather than silently picking one.
        result["screens"] = candidate_screens(panel)
        result["chosen_screen"] = page or main_screen(panel)
        # Everything the reader had to guess, said out loud.
        result["notes"] = faceplate.get("import_notes", {})
        # Repeated join arithmetic says which controls are one zone
        # repeated, which is what turns 39 controls into "3 zones".
        try:
            result["zones"] = propose(panel).get("families", [])
        except Exception as err:  # grouping is a convenience, never fatal
            logger.debug("Grouping declined: %s", err)
            result["zones"] = []

    # Assigning it is the whole point: a panel project already says what
    # every join is, so nobody should have to enter it a second time.
    if assignment["host"] and assignment["ipid"]:
        hub: Hub = request.app["hub"]
        try:
            ipid = parse_ipid(assignment["ipid"])
            port = int(assignment["port"])
            check_host(assignment["host"])
            change = attach_processor(hub, name, assignment["host"], ipid, port)
            counts = apply_panel(hub.store, name, faceplate)
        except (ApplyError, ValueError) as err:
            result["assign_error"] = str(err)
            return web.json_response(result)
        result.update({
            "processor": change,
            "joins_created": counts["created"],
            "joins_configured": counts["configured"],
        })
    return web.json_response(result)


async def panel_card(request: web.Request) -> web.Response:
    """The stored panel as a card, with join keys where entity ids go."""
    if not PANEL_FILE.exists():
        return web.json_response(
            {"error": "no panel loaded — import a .c3p or .vtz first"},
            status=404)
    stored = json.loads(PANEL_FILE.read_text())
    faceplate = stored["faceplate"]
    roles = sorted({r["role"] for r in faceplate["regions"] if r.get("role")}
                   | {r["target"] for r in faceplate["regions"] if r.get("target")})
    return web.json_response({
        "card": {
            "type": "custom:crestron-panel-card",
            "title": stored.get("name") or "Panel",
            "panel": faceplate,
            "entities": {role: role for role in roles},
        },
        "keys": roles,
    })


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
                "precision": j.config.precision,
                "group": j.config.resolved_group(j.processor),
                "available": bool(
                    hub.connections.get(j.processor)
                    and hub.connections[j.processor].registered
                ),
            }
            for j in hub.store.exposed()
        ],
        "processors": {
            name: {
                "registered": conn.registered,
                "host": conn.host,
                "health": (hub.health[name].to_dict()
                           if name in hub.health else None),
            }
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


# A compiled panel project is mostly Flash runtime and theme artwork,
# so it arrives at tens of megabytes. aiohttp's default body limit is
# one, which rejects every real panel with a bare 413.
MAX_UPLOAD = 96 * 1024 * 1024


def build_app(hub: Hub) -> web.Application:
    app = web.Application(client_max_size=MAX_UPLOAD)
    app["hub"] = hub
    app.add_routes([
        web.get("/", index),
        web.get("/api/status", status),
        web.get("/api/kinds", kinds),
        web.get("/api/joins", list_joins),
        web.post("/api/joins/configure", configure_join),
        web.post("/api/joins/bulk", bulk_configure),
        web.post("/api/joins/autogroup", autogroup),
        web.get("/api/groups", list_groups),
        web.post("/api/joins/set", set_join),
        web.post("/api/rediscover", rediscover),
        web.get("/api/events", events),
        web.get("/api/health", health),
        web.get("/api/cards/room", room_card_endpoint),
        web.get("/api/cards/mixer", mixer_card_endpoint),
        web.post("/api/panel/upload", upload_panel),
        web.get("/api/cards/panel", panel_card),
        web.get("/api/cards/mixer/roles", mixer_roles),
        web.get("/api/integration/joins", integration_joins),
    ])
    if STATIC_DIR.is_dir():
        app.router.add_static("/static/", STATIC_DIR)
    # Last, so it only catches what nothing else claimed.
    app.router.add_get("/{tail:.*}", index)
    return app
