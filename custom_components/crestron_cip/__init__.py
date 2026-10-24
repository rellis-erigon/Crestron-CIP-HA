"""Crestron CIP integration for Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
import homeassistant.helpers.config_validation as cv

from .const import (
    ATTR_DURATION, ATTR_ENTRY_ID, ATTR_JOIN, ATTR_VALUE, CONF_IPID, CONF_PROJECT_FILE,
    CONF_SSL, DEFAULT_PORT, DEFAULT_TLS_PORT, DOMAIN, PLATFORMS, SERVICE_PRESS,
    SERVICE_REQUEST_UPDATE, SERVICE_SET_ANALOG, SERVICE_SET_DIGITAL, SERVICE_SET_SERIAL,
)
from .hub import CrestronHub

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    use_ssl = entry.data.get(CONF_SSL, False)
    port = entry.data.get(CONF_PORT) or (DEFAULT_TLS_PORT if use_ssl else DEFAULT_PORT)
    hub = CrestronHub(
        hass, entry,
        host=entry.data[CONF_HOST],
        ipid=int(entry.data[CONF_IPID]),
        port=port,
        use_ssl=use_ssl,
        name=entry.data.get(CONF_NAME, entry.title),
        project_file=entry.data.get(CONF_PROJECT_FILE) or None,
    )
    try:
        await hub.async_start()
    except Exception as exc:  # noqa: BLE001
        await hub.async_stop()
        raise ConfigEntryNotReady(f"Cannot connect to {hub.host}: {exc}") from exc

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        # A failed setup is never unloaded, so without this the connection
        # stays registered at the processor with nothing reading it.
        hass.data[DOMAIN].pop(entry.entry_id, None)
        await hub.async_stop()
        raise
    _register_services(hass)
    entry.async_on_unload(entry.add_update_listener(_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hub: CrestronHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_stop()
    if not hass.data.get(DOMAIN):
        for svc in (SERVICE_PRESS, SERVICE_SET_DIGITAL, SERVICE_SET_ANALOG,
                    SERVICE_SET_SERIAL, SERVICE_REQUEST_UPDATE):
            hass.services.async_remove(DOMAIN, svc)
    return ok


async def _reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_PRESS):
        return

    def _hubs(call: ServiceCall) -> list[CrestronHub]:
        eid = call.data.get(ATTR_ENTRY_ID)
        hubs = hass.data.get(DOMAIN, {})
        if eid:
            return [hubs[eid]] if eid in hubs else []
        return list(hubs.values())

    async def press(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.pulse_digital(int(call.data[ATTR_JOIN]), float(call.data.get(ATTR_DURATION, 0.1)))

    async def set_digital(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.set_digital(int(call.data[ATTR_JOIN]), bool(call.data[ATTR_VALUE]))

    async def set_analog(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.set_analog(int(call.data[ATTR_JOIN]), int(call.data[ATTR_VALUE]))

    async def set_serial(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.set_serial(int(call.data[ATTR_JOIN]), str(call.data[ATTR_VALUE]))

    async def request_update(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.request_update()

    base = {vol.Optional(ATTR_ENTRY_ID): cv.string}
    hass.services.async_register(DOMAIN, SERVICE_PRESS, press,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Optional(ATTR_DURATION): cv.positive_float}))
    hass.services.async_register(DOMAIN, SERVICE_SET_DIGITAL, set_digital,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Required(ATTR_VALUE): cv.boolean}))
    hass.services.async_register(DOMAIN, SERVICE_SET_ANALOG, set_analog,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Required(ATTR_VALUE): vol.All(vol.Coerce(int), vol.Range(0, 65535))}))
    hass.services.async_register(DOMAIN, SERVICE_SET_SERIAL, set_serial,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Required(ATTR_VALUE): cv.string}))
    hass.services.async_register(DOMAIN, SERVICE_REQUEST_UPDATE, request_update, vol.Schema(base))
