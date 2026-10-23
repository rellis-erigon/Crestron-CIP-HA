"""Numbers: one per analog join, auto-created. Readable feedback + writable (set_analog)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
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
        if kind == "analog":
            async_add_entities([CrestronAnalog(hub, join)])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW.format(entry=entry.entry_id), _add))
    for join in hub.known("analog"):
        _add("analog", join)


class CrestronAnalog(CrestronJoinEntity, NumberEntity):
    _kind = "analog"
    _attr_native_min_value = 0
    _attr_native_max_value = 65535
    _attr_native_step = 1
    _attr_mode = NumberMode.BOX
    # Changing the number writes the join to the processor. Writing has not
    # been tested on live hardware, so these start disabled.
    _attr_entity_registry_enabled_default = False

    @property
    def native_value(self) -> float | None:
        v = self._hub.client.analog.get(self._join)
        return float(v) if v is not None else None

    async def async_set_native_value(self, value: float) -> None:
        await self._hub.client.set_analog(self._join, int(value))
        self.async_write_ha_state()
