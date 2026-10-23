"""Binary sensors: a connectivity sensor + one per digital feedback join (auto-created)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_info import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN, SIGNAL_CONN, SIGNAL_NEW
from .entity import CrestronJoinEntity
from .hub import CrestronHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub: CrestronHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CrestronConnectivity(hub)])

    @callback
    def _add(kind: str, join: int) -> None:
        if kind == "digital":
            async_add_entities([CrestronDigital(hub, join)])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW.format(entry=entry.entry_id), _add))
    for join in hub.known("digital"):
        _add("digital", join)


class CrestronConnectivity(BinarySensorEntity):
    _attr_should_poll = False
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_registry_enabled_default = True

    def __init__(self, hub: CrestronHub) -> None:
        self._hub = hub
        self._attr_unique_id = f"{hub.unique_id}_connected"
        self._attr_name = f"{hub.name} connection"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.unique_id)}, name=hub.name,
            manufacturer="Crestron", model=f"CIP IPID 0x{hub.ipid:02X}",
        )

    @property
    def is_on(self) -> bool:
        return self._hub.available

    async def async_added_to_hass(self) -> None:
        self.async_on_remove(async_dispatcher_connect(
            self.hass, SIGNAL_CONN.format(entry=self._hub.entry.entry_id),
            lambda _up: self.async_write_ha_state(),
        ))


class CrestronDigital(CrestronJoinEntity, BinarySensorEntity):
    _kind = "digital"

    @property
    def is_on(self) -> bool | None:
        return self._hub.client.digital.get(self._join)
