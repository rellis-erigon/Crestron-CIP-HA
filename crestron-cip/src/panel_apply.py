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
from typing import Any

logger = logging.getLogger("crestron-cip.panel")

DEFAULT_PORT = 41794

# What a join becomes in Home Assistant, by signal. A panel button is
# momentary, so it is a button rather than a switch.
KIND_FOR_SIGNAL = {"d": "button", "a": "number", "s": "sensor"}


class ApplyError(Exception):
    """The panel could not be applied to a processor."""


def attach_processor(hub: Any, name: str, host: str, ipid: int,
                     port: int = DEFAULT_PORT) -> str:
    """Connect to the processor a panel was assigned to, now.

    Deliberately not done by editing the add-on's own options through
    Supervisor. That needs a token the add-on does not reliably get, and
    changing options makes Supervisor restart the add-on — which would
    kill the very request doing the importing. The assignment is kept
    in the panel store instead and re-applied at startup, so it
    survives a restart without depending on one.
    """
    existing = hub.connections.get(name)
    if existing is not None:
        if existing.host == host and existing.ipid == ipid:
            return "unchanged"
        raise ApplyError(
            f"a processor called {name!r} is already connected to "
            f"{existing.host} as IPID 0x{existing.ipid:02X}; rename the panel"
        )

    hub.add_processor(name, host, ipid, port)
    hub.start_processor(name)
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
