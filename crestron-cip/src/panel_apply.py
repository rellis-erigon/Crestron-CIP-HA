"""Turn an imported panel into a working processor configuration.

A panel project says which joins exist, where they sit and what each
one does. That is the whole configuration, so importing one should not
leave anyone to type it in again: assigning a panel to a processor adds
the processor, creates its joins, names them, exposes them and groups
them into a device.
"""
from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from typing import Any

logger = logging.getLogger("crestron-cip.panel")

SUPERVISOR = "http://supervisor"
DEFAULT_PORT = 41794

# What a join becomes in Home Assistant, by signal. A panel button is
# momentary, so it is a button rather than a switch.
KIND_FOR_SIGNAL = {"d": "button", "a": "number", "s": "sensor"}


class ApplyError(Exception):
    """The panel could not be applied to a processor."""


def _supervisor(path: str, method: str = "GET", body: dict | None = None) -> dict:
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise ApplyError("no Supervisor token; cannot change the add-on options")
    request = urllib.request.Request(
        f"{SUPERVISOR}{path}", method=method,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as err:
        raise ApplyError(f"Supervisor refused: {err.code} {err.reason}")
    except OSError as err:
        raise ApplyError(f"cannot reach Supervisor: {err}")


def ensure_processor(name: str, host: str, ipid: int,
                     port: int = DEFAULT_PORT) -> str:
    """Add or update this processor in the add-on's own options.

    Returns what happened, so the caller can say whether a restart is
    coming. Matching is on host and IPID rather than name: that pair is
    the connection, and renaming one should not create a second.
    """
    info = _supervisor("/addons/self/info")
    options = dict(info["data"]["options"])
    processors = [dict(p) for p in options.get("processors") or []]

    for entry in processors:
        if entry.get("host") == host and int(entry.get("ipid", 0)) == ipid:
            changed = entry.get("name") != name or int(entry.get("port") or 0) != port
            entry["name"], entry["port"] = name, port
            if not changed:
                return "unchanged"
            options["processors"] = processors
            _supervisor("/addons/self/options", "POST", {"options": options})
            return "updated"

    processors.append({"name": name, "host": host, "ipid": ipid, "port": port})
    options["processors"] = processors
    _supervisor("/addons/self/options", "POST", {"options": options})
    return "added"


def panel_joins(faceplate: dict) -> list[tuple[str, int, str]]:
    """Every join the panel uses, as (signal, number, label)."""
    seen: dict[tuple[str, int], str] = {}
    for region in faceplate.get("regions", []):
        for key in (region.get("role"), region.get("target")):
            if not key or len(key) < 2 or key[0] not in "das":
                continue
            try:
                number = int(key[1:])
            except ValueError:
                continue
            label = region.get("text") or ""
            # A region with a caption names its join better than one
            # without, so a later labelled region wins.
            if (key[0], number) not in seen or label:
                seen[(key[0], number)] = label
    return [(signal, number, label)
            for (signal, number), label in sorted(seen.items())]


def apply_panel(store: Any, processor: str, faceplate: dict,
                expose: bool = True) -> dict:
    """Create, name, expose and group the joins a panel uses.

    The joins are created before any traffic arrives. CIP only reports
    joins that are off their default, so waiting for the processor to
    mention one would leave a panel half-configured until somebody
    pressed every button on it.
    """
    created = configured = 0
    for signal, number, label in panel_joins(faceplate):
        key = f"{signal}{number}"
        if f"{processor}/{key}" not in store.joins:
            store.observe(processor, signal, number, None)
            created += 1
        name = label.strip() or f"{SIGNAL_WORD[signal]} {number}"
        try:
            store.configure(processor, key,
                            name=name,
                            kind=KIND_FOR_SIGNAL[signal],
                            group=processor,
                            enabled=expose)
            configured += 1
        except (KeyError, ValueError) as err:
            logger.warning("Could not configure %s/%s: %s", processor, key, err)
    store.save()
    return {"created": created, "configured": configured}


SIGNAL_WORD = {"d": "Button", "a": "Level", "s": "Text"}
