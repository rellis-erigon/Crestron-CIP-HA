"""Config flow for the Crestron CIP integration."""
from __future__ import annotations

import logging
import os
from typing import Any

import aiohttp
import voluptuous as vol
from homeassistant.config_entries import ConfigFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import (
    ADDON_SLUG_SUFFIX,
    CONF_ADDON_URL,
    DEFAULT_ADDON_URL,
    DEFAULT_INGRESS_PORT,
    DOMAIN,
    SUPERVISOR_API,
)

_LOGGER = logging.getLogger(__name__)


async def async_discover_addon_url(hass: HomeAssistant) -> str | None:
    """Ask Supervisor where the Crestron CIP add-on is reachable.

    The add-on's hostname includes a per-install repository hash, so it
    cannot be guessed; asking is the only reliable way. Returns None when
    not running under Supervisor, or when the add-on is not installed.
    """
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        return None

    headers = {"Authorization": f"Bearer {token}"}
    session = async_get_clientsession(hass)
    timeout = aiohttp.ClientTimeout(total=10)

    try:
        async with session.get(
            f"{SUPERVISOR_API}/addons", headers=headers, timeout=timeout,
        ) as response:
            response.raise_for_status()
            addons = (await response.json()).get("data", {}).get("addons", [])

        slug = next(
            (a["slug"] for a in addons
             if a.get("slug", "").endswith(ADDON_SLUG_SUFFIX)),
            None,
        )
        if not slug:
            return None

        async with session.get(
            f"{SUPERVISOR_API}/addons/{slug}/info", headers=headers, timeout=timeout,
        ) as response:
            response.raise_for_status()
            info = (await response.json()).get("data", {})
    except (aiohttp.ClientError, TimeoutError, ValueError) as err:
        _LOGGER.debug("Add-on discovery via Supervisor failed: %s", err)
        return None

    host = info.get("hostname") or slug.replace("_", "-")
    port = info.get("ingress_port") or DEFAULT_INGRESS_PORT
    return f"http://{host}:{port}"


class CrestronCipConfigFlow(ConfigFlow, domain=DOMAIN):
    """Point the integration at the add-on."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        errors: dict[str, str] = {}
        exposed = processors = 0

        if user_input is not None:
            url = user_input[CONF_ADDON_URL].rstrip("/")
            await self.async_set_unique_id(url)
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            try:
                async with session.get(
                    f"{url}/api/integration/joins",
                    timeout=aiohttp.ClientTimeout(total=30),
                ) as response:
                    response.raise_for_status()
                    payload = await response.json()
                exposed = len(payload.get("joins", []))
                processors = len(payload.get("processors", {}))
            except (aiohttp.ClientError, TimeoutError, ValueError) as err:
                _LOGGER.debug("Could not reach the add-on at %s: %s", url, err)
                errors["base"] = "cannot_connect"

            if not errors:
                return self.async_create_entry(
                    title=f"Crestron CIP ({exposed} joins, "
                          f"{processors} processor{'s' if processors != 1 else ''})",
                    data={CONF_ADDON_URL: url},
                )

        suggested = await async_discover_addon_url(self.hass) or DEFAULT_ADDON_URL
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({
                vol.Required(CONF_ADDON_URL, default=suggested): str,
            }),
            errors=errors,
            description_placeholders={"url": suggested},
        )
