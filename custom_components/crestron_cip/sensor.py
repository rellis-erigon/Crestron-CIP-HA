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
    def _add(kind: str, join: int, obj: int = 0) -> None:
        if kind == "serial":
            async_add_entities([CrestronSerial(hub, join, obj)])
        elif kind == "analog":
            async_add_entities([CrestronAnalog(hub, join, obj)])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW.format(entry=entry.entry_id), _add))
    for kind in ("serial", "analog"):
        for join, obj in hub.known(kind):
            _add(kind, join, obj)


class CrestronSerial(CrestronJoinEntity, SensorEntity):
    _kind = "serial"

    @property
    def native_value(self) -> str | None:
        return self._value


class CrestronAnalog(CrestronJoinEntity, SensorEntity):
    """An analog join, read-only.

    Analog joins were writable numbers until 0.16.0. A number lets anyone
    with the entity in front of them write a raw 16-bit value to the
    processor; the panel never does that, it presses the program's own
    up/down joins and lets the program ramp. Writes now go through the
    `set_analog` action, deliberately.
    """

    _kind = "analog"

    @property
    def native_value(self) -> int | None:
        return self._value
