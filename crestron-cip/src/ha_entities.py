"""The entity ids Home Assistant currently has.

Binding a panel automatically needs to know what is there to bind to. An
add-on can ask Home Assistant directly — Supervisor proxies its API and
provides the token — so nobody has to copy entity ids into a form.

Only the ids are taken. The states are none of this add-on's business
and holding them would mean holding whatever happens to be in them.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp

logger = logging.getLogger("crestron-cip.ha_entities")

CORE_API = "http://supervisor/core/api/states"
# Supervisor has called this two things over the years and older base
# images still carry the old one. Both are checked rather than assumed,
# because the failure is silent: no token means no bindings, and a card
# that simply comes back empty gives no clue why.
TOKEN_VARS = ("SUPERVISOR_TOKEN", "HASSIO_TOKEN")

# Domains worth offering as a binding. A panel control is a level, a
# switch, a readout or a lamp; nothing here binds to a calendar.
USEFUL_DOMAINS = (
    "number", "input_number", "switch", "input_boolean", "light",
    "sensor", "binary_sensor", "media_player", "select", "input_select",
    "button", "input_button", "text", "input_text", "climate", "fan",
)


async def fetch(timeout: float = 10.0) -> list[str]:
    """Every entity id Home Assistant knows, or [] if it cannot be asked.

    Never raises. Automatic binding is a convenience on top of an import
    that has to work without it — a panel with no entities behind it yet
    is the normal case the first time somebody loads one.
    """
    token = next((os.environ[v] for v in TOKEN_VARS if os.environ.get(v)), "")
    if not token:
        # Name the variables rather than the values, so a misconfigured
        # add-on can be diagnosed from the log without leaking anything.
        logger.warning(
            "No Supervisor token in %s, so entities cannot be listed and "
            "nothing will bind automatically. The add-on needs "
            "homeassistant_api: true and a restart.",
            " or ".join(TOKEN_VARS),
        )
        return []
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                CORE_API,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as response:
                if response.status != 200:
                    logger.warning(
                        "Home Assistant returned %s listing entities",
                        response.status,
                    )
                    return []
                payload: Any = await response.json()
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        logger.warning("Could not list entities: %s", err)
        return []

    ids = []
    for item in payload if isinstance(payload, list) else []:
        entity = item.get("entity_id") if isinstance(item, dict) else None
        if isinstance(entity, str) and entity.split(".", 1)[0] in USEFUL_DOMAINS:
            ids.append(entity)
    return sorted(ids)
