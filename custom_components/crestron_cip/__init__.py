"""Crestron CIP integration for Home Assistant."""
from __future__ import annotations

import logging

import voluptuous as vol
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PORT
from pathlib import Path

from homeassistant.core import HomeAssistant, ServiceCall, ServiceResponse, SupportsResponse
from homeassistant.exceptions import ConfigEntryNotReady, ServiceValidationError
import homeassistant.helpers.config_validation as cv

from .const import (
    ATTR_DURATION, ATTR_ENTRY_ID, ATTR_JOIN, ATTR_NAME, ATTR_PATH, ATTR_SMART_OBJECT, ATTR_VALUE,
    CONF_IPID, CONF_PROJECT_FILE, CONF_SSL, DEFAULT_PORT, DEFAULT_TLS_PORT, DOMAIN,
    PANEL_DIR, PANEL_URL, PLATFORMS, SERVICE_IMPORT_PANEL, SERVICE_PRESS,
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
    await _sync_panels(hass, entry)
    # Adding or removing a room's panel starts or ends its registration
    # and its device; the processor's own connection is left alone.
    entry.async_on_unload(entry.add_update_listener(_sync_panels))
    _register_services(hass)
    if not hass.data.setdefault(f"{DOMAIN}_views", False):
        # A view cannot be removed again, so it is registered once and looks
        # the entry up per connection.
        from homeassistant.components import websocket_api

        from .xpanel_relay import XPanelRelayView, ws_xpanel_info, ws_xpanel_log
        hass.http.register_view(XPanelRelayView(hass))
        websocket_api.async_register_command(hass, ws_xpanel_info)
        websocket_api.async_register_command(hass, ws_xpanel_log)
        hass.data[f"{DOMAIN}_views"] = True
    return True


def _panel_device_name(processor: str, room: str) -> str:
    """A panel's device name: the room, under its processor's name.

    A panel named for its processor (an entry's one panel, from before
    rooms) is not named twice.
    """
    processor, room = processor.strip(), room.strip()
    if room.lower().startswith(processor.lower()):
        return f"{room} panel"
    return f"{processor} {room}"


async def _sync_panels(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """A device for each room's panel, and a registration at each one's IPID."""
    from homeassistant.helpers import device_registry as dr

    from .panel_store import panels_of
    from .xpanel_relay import panel_device_identifier

    hub: CrestronHub | None = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if hub is None:
        return
    panels = panels_of(entry.options, hub.ipid)
    await hub.async_sync_panels({int(p["ipid"]) for p in panels})
    registry = dr.async_get(hass)
    wanted = set()
    for panel in panels:
        ident = panel_device_identifier(entry.entry_id, panel["id"])
        wanted.add(ident)
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={ident},
            name=_panel_device_name(hub.name, panel["name"]),
            manufacturer="Crestron",
            model=f"XPanel IPID 0x{int(panel['ipid']):02X}",
            via_device=(DOMAIN, hub.unique_id),
        )
        name = _panel_device_name(hub.name, panel["name"])
        if device.name != name:
            # async_get_or_create keeps the name a device already had.
            registry.async_update_device(device.id, name=name)
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        ours = {i for i in device.identifiers if i[0] == DOMAIN and ":" in i[1]}
        if ours and not ours & wanted:
            registry.async_update_device(device.id, remove_config_entry_id=entry.entry_id)


async def async_remove_config_entry_device(hass: HomeAssistant, entry: ConfigEntry,
                                           device) -> bool:
    """Deleting a room panel's device removes the panel; the processor stays."""
    from .panel_store import without_panel

    for domain, ident in device.identifiers:
        entry_id, sep, panel_id = ident.partition(":")
        if domain == DOMAIN and sep and entry_id == entry.entry_id:
            hass.config_entries.async_update_entry(
                entry, options=without_panel(dict(entry.options),
                                             int(entry.data[CONF_IPID]), panel_id))
            return True
    return False


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        hub: CrestronHub = hass.data[DOMAIN].pop(entry.entry_id)
        await hub.async_stop()
    if not hass.data.get(DOMAIN):
        for svc in (SERVICE_PRESS, SERVICE_SET_DIGITAL, SERVICE_SET_ANALOG,
                    SERVICE_SET_SERIAL, SERVICE_REQUEST_UPDATE, SERVICE_IMPORT_PANEL):
            hass.services.async_remove(DOMAIN, svc)
    return ok


def _register_services(hass: HomeAssistant) -> None:
    if hass.services.has_service(DOMAIN, SERVICE_PRESS):
        return

    def _hubs(call: ServiceCall) -> list[CrestronHub]:
        eid = call.data.get(ATTR_ENTRY_ID)
        hubs = hass.data.get(DOMAIN, {})
        if eid:
            return [hubs[eid]] if eid in hubs else []
        return list(hubs.values())

    def _obj(call: ServiceCall) -> int:
        return int(call.data.get(ATTR_SMART_OBJECT, 0))

    async def press(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.pulse_digital(int(call.data[ATTR_JOIN]),
                                         float(call.data.get(ATTR_DURATION, 0.1)), obj=_obj(call))

    async def set_digital(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.set_digital(int(call.data[ATTR_JOIN]), bool(call.data[ATTR_VALUE]),
                                       obj=_obj(call))

    async def set_analog(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.set_analog(int(call.data[ATTR_JOIN]), int(call.data[ATTR_VALUE]),
                                      obj=_obj(call))

    async def set_serial(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.set_serial(int(call.data[ATTR_JOIN]), str(call.data[ATTR_VALUE]),
                                      obj=_obj(call))

    async def request_update(call: ServiceCall) -> None:
        for h in _hubs(call):
            await h.client.request_update()

    async def import_panel(call: ServiceCall) -> ServiceResponse:
        from .panel.importer import PanelImportError, write_panel

        path = Path(call.data[ATTR_PATH]).expanduser()
        # Files the user can put on the box: the config folder, or the
        # share and media folders on Home Assistant OS.
        allowed = [Path(hass.config.config_dir), Path("/share"), Path("/media")]
        resolved = path.resolve()
        if not any(resolved.is_relative_to(root) for root in allowed):
            raise ServiceValidationError(
                f"{path} is outside the config, share and media folders")
        if not resolved.is_file():
            raise ServiceValidationError(f"{path} does not exist")
        name = call.data.get(ATTR_NAME) or resolved.stem

        def run() -> dict:
            return write_panel(resolved.read_bytes(), name,
                               Path(hass.config.path(*PANEL_DIR)), PANEL_URL)

        try:
            result = await hass.async_add_executor_job(run)
        except PanelImportError as err:
            raise ServiceValidationError(f"{path.name}: {err}") from err
        _LOGGER.info("Imported panel %s: %d screens", name, len(result["screens"]))
        return result

    base = {vol.Optional(ATTR_ENTRY_ID): cv.string,
            # A join inside a SmartObject (a list, a keypad) is addressed to it.
            vol.Optional(ATTR_SMART_OBJECT): cv.positive_int}
    hass.services.async_register(DOMAIN, SERVICE_PRESS, press,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Optional(ATTR_DURATION): cv.positive_float}))
    hass.services.async_register(DOMAIN, SERVICE_SET_DIGITAL, set_digital,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Required(ATTR_VALUE): cv.boolean}))
    hass.services.async_register(DOMAIN, SERVICE_SET_ANALOG, set_analog,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Required(ATTR_VALUE): vol.All(vol.Coerce(int), vol.Range(0, 65535))}))
    hass.services.async_register(DOMAIN, SERVICE_SET_SERIAL, set_serial,
        vol.Schema({**base, vol.Required(ATTR_JOIN): cv.positive_int, vol.Required(ATTR_VALUE): cv.string}))
    hass.services.async_register(DOMAIN, SERVICE_REQUEST_UPDATE, request_update, vol.Schema(base))
    hass.services.async_register(
        DOMAIN, SERVICE_IMPORT_PANEL, import_panel,
        vol.Schema({vol.Required(ATTR_PATH): cv.string, vol.Optional(ATTR_NAME): cv.string}),
        supports_response=SupportsResponse.OPTIONAL)
