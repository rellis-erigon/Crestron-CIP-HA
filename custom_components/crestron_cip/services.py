"""Services for the Crestron processor bridge integration."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol
from homeassistant.core import (
    HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_RESCAN = "rescan"
ATTR_TARGET = "processor"


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register integration services once."""
    if hass.services.has_service(DOMAIN, SERVICE_RESCAN):
        return

    async def handle_rescan(call: ServiceCall) -> ServiceResponse:
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("The bridge is not set up")
        coordinator = next(iter(entries.values()))

        body = {}
        target = (call.data.get(ATTR_TARGET) or "").strip()
        if target:
            body[ATTR_TARGET] = target

        session = async_get_clientsession(hass)
        try:
            async with session.post(
                f"{coordinator.url}/api/rediscover", json=body,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                response.raise_for_status()
                result = await response.json()
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the add-on: {err}") from err

        # The add-on holds the connection, so it does the work; refreshing
        # here is what turns anything new into entities without a wait.
        await coordinator.async_request_refresh()
        return result

    hass.services.async_register(
        DOMAIN, SERVICE_RESCAN, handle_rescan,
        schema=vol.Schema({vol.Optional(ATTR_TARGET): str}),
        supports_response=SupportsResponse.ONLY,
    )


def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_RESCAN)
