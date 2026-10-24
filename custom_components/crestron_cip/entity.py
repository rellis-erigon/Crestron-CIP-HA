"""Shared base for Crestron CIP feedback entities."""
from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, SIGNAL_CONN, SIGNAL_VALUE
from .hub import CrestronHub


class CrestronJoinEntity(Entity):
    """Base for an entity bound to one feedback join."""

    _attr_should_poll = False
    _kind: str = ""

    def __init__(self, hub: CrestronHub, join: int) -> None:
        self._hub = hub
        self._join = join
        self._attr_unique_id = f"{hub.unique_id}_{self._kind}_{join}"
        self._attr_name = hub.name_for(self._kind, join)
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.unique_id)},
            name=hub.name,
            manufacturer="Crestron",
            model=f"CIP IPID 0x{hub.ipid:02X}",
            configuration_url=None,
        )

    @property
    def available(self) -> bool:
        return self._hub.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass,
            SIGNAL_VALUE.format(entry=self._hub.entry.entry_id, kind=self._kind, join=self._join),
            self._handle_value,
        ))
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_CONN.format(entry=self._hub.entry.entry_id),
            lambda _up: self.async_write_ha_state(),
        ))

    @callback
    def _handle_value(self, value) -> None:  # overridden
        self.async_write_ha_state()
