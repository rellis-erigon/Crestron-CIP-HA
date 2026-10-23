"""Sensors: one per serial (text) feedback join, auto-created as joins are seen."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_NEW
from .entity import CrestronJoinEntity
from .hub import CrestronHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub: CrestronHub = hass.data[DOMAIN][entry.entry_id]

    @callback
    def _add(kind: str, join: int) -> None:
        if kind == "serial":
            async_add_entities([CrestronSerial(hub, join)])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW.format(entry=entry.entry_id), _add))
    for join in hub.known("serial"):
        _add("serial", join)


class CrestronSerial(CrestronJoinEntity, SensorEntity):
    _kind = "serial"

    @property
    def native_value(self) -> str | None:
        return self._hub.client.serial.get(self._join)
