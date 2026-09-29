"""Services for the Crestron processor bridge integration."""
from __future__ import annotations

import logging

import aiohttp
import voluptuous as vol
from homeassistant.core import (
    HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import yaml

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SERVICE_RESCAN = "rescan"
SERVICE_GENERATE_CARD = "generate_room_card"
ATTR_TARGET = "processor"

# The platforms a join can become. A room role is bound to whichever one
# the join was configured as.
_PLATFORMS = ("switch", "number", "text", "sensor", "binary_sensor", "button")


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

    async def handle_generate_room_card(call: ServiceCall) -> ServiceResponse:
        """Build a room-controller-card from a processor's named joins.

        The add-on matches joins to roles by the names they were given and
        returns join keys; only the registry knows what those became here.
        """
        entries = hass.data.get(DOMAIN, {})
        if not entries:
            raise HomeAssistantError("The bridge is not set up")
        coordinator = next(iter(entries.values()))

        params = {}
        processor = (call.data.get(ATTR_TARGET) or "").strip()
        if processor:
            params[ATTR_TARGET] = processor

        session = async_get_clientsession(hass)
        try:
            async with session.get(
                f"{coordinator.url}/api/cards/room", params=params,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                payload = await response.json()
                if response.status != 200:
                    raise HomeAssistantError(
                        payload.get("error", f"Add-on returned {response.status}")
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(f"Could not reach the add-on: {err}") from err

        card = payload.get("card")
        if not card:
            raise HomeAssistantError("The add-on produced no card")

        registry = er.async_get(hass)
        resolved: dict[str, str] = {}
        missing: list[str] = []
        for role, key in card.get("entities", {}).items():
            entity_id = _entity_id_for_key(registry, processor, key)
            if entity_id:
                resolved[role] = entity_id
            else:
                missing.append(role)

        if not resolved:
            raise HomeAssistantError(
                "None of the matched joins have entities here — expose them "
                "in the add-on first"
            )
        card["entities"] = resolved

        return {
            "card": card,
            "yaml": yaml.safe_dump(
                card, default_flow_style=False, sort_keys=False,
                allow_unicode=True, width=10000,
            ),
            # The match is a guess from join names, so hand back what it
            # decided and what it could not place.
            "matched": payload.get("matched", {}),
            "unmatched_roles": payload.get("unmatched_roles", []),
            "unused_joins": payload.get("unused_joins", []),
            "roles_without_entities": sorted(missing),
        }

    hass.services.async_register(
        DOMAIN, SERVICE_GENERATE_CARD, handle_generate_room_card,
        schema=vol.Schema({vol.Optional(ATTR_TARGET): str}),
        supports_response=SupportsResponse.ONLY,
    )


def _entity_id_for_key(registry, processor: str, key: str) -> str | None:
    """Find what a join became, by the unique id the entity minted."""
    unique_id = f"{DOMAIN}_{processor}_{key}"
    for platform in _PLATFORMS:
        entity_id = registry.async_get_entity_id(platform, DOMAIN, unique_id)
        if entity_id:
            return entity_id
    return None


def async_unload_services(hass: HomeAssistant) -> None:
    hass.services.async_remove(DOMAIN, SERVICE_RESCAN)
