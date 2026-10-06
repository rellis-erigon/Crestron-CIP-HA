"""Crestron CIP add-on entry point."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import sys
from pathlib import Path

from aiohttp import web

from join_store import STORE_FILE, JoinStore
from server import Hub, build_app

OPTIONS_PATH = Path("/data/options.json")
MANIFEST_PATH = Path("/app/config.yaml")


def _version() -> str:
    """The add-on's version, from the build arg or the manifest it shipped with.

    Reading one `version:` line does not justify a YAML dependency, and the
    add-on has no other use for one.
    """
    from_build = os.environ.get("CRESTRON_CIP_VERSION")
    if from_build and from_build != "":
        return from_build
    try:
        for line in MANIFEST_PATH.read_text().splitlines():
            if line.startswith("version:"):
                return line.split(":", 1)[1].strip().strip('"\'')
    except OSError:
        pass
    return "unknown"


VERSION = _version()

logger = logging.getLogger("crestron-cip")


def load_options() -> dict:
    try:
        return json.loads(OPTIONS_PATH.read_text())
    except FileNotFoundError:
        logger.error("No options.json at %s", OPTIONS_PATH)
        sys.exit(1)
    except json.JSONDecodeError as err:
        logger.error("options.json is not valid JSON: %s", err)
        sys.exit(1)


PANEL_FILE = Path("/config/crestron-cip/panel.json")


def _attach_panels(hub: Hub) -> None:
    """Re-apply a panel's processor assignment at startup.

    The assignment lives in the panel store rather than the add-on
    options, because changing those makes Supervisor restart the add-on
    — which would kill the request doing the import. The cost is that
    it has to be re-applied here.
    """
    try:
        stored = json.loads(PANEL_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return
    processor = stored.get("processor") or {}
    name, host = stored.get("name"), processor.get("host")
    if not (name and host and processor.get("ipid")):
        return
    if name in hub.connections:
        return
    try:
        ipid = int(str(processor["ipid"]), 0)
        port = int(processor.get("port") or 41794)
    except (TypeError, ValueError) as err:
        logger.warning("Panel %s has a bad processor address: %s", name, err)
        return
    hub.add_processor(name, host, ipid, port)
    logger.info("Panel %s brings processor %s:%d as IPID 0x%02X",
                name, host, port, ipid)


def setup_logging(level_name: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level_name.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )


async def run() -> None:
    options = load_options()
    setup_logging(options.get("log_level", "info"))
    logger.info("Crestron CIP bridge v%s starting", VERSION)

    store = JoinStore(STORE_FILE)
    store.load()
    hub = Hub(store)

    processors = options.get("processors") or []
    if not processors:
        logger.warning(
            "No processors configured — add one with its host and the IPID "
            "of an XPanel defined in its program"
        )
    for entry in processors:
        host = (entry.get("host") or "").strip()
        if not host:
            continue
        name = (entry.get("name") or host).strip()
        ipid = entry.get("ipid", 16)
        if isinstance(ipid, str):
            ipid = int(ipid, 0)
        port = entry.get("port")
        hub.add_processor(name, host, int(ipid), int(port) if port else None)
        logger.info("Processor %s at %s:%s as IPID 0x%02X",
                    name, host, port or "default", ipid)

        # Console credentials are optional: without them the bridge simply
        # reports no load or memory figures.
        if entry.get("console_username"):
            hub.console[name] = {
                "host": host,
                "username": entry.get("console_username", ""),
                "password": entry.get("console_password", ""),
            }

    hub.health_interval = float(options.get("health_interval", 300))
    if hub.console:
        logger.info(
            "Reading console health from %d processor(s) every %.0fs",
            len(hub.console), hub.health_interval,
        )

    # Panels assigned to a processor bring that processor with them.
    # The assignment lives in the panel store rather than the add-on
    # options, so it is re-applied here rather than by Supervisor.
    _attach_panels(hub)

    await hub.start()

    port = int(os.environ.get("INGRESS_PORT", "8099"))
    runner = web.AppRunner(build_app(hub))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", port).start()
    logger.info("Web UI on port %d", port)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()

    logger.info("Shutting down")
    await hub.stop()
    await runner.cleanup()


if __name__ == "__main__":
    asyncio.run(run())
