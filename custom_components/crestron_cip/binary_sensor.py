"""Binary sensors: a connectivity sensor + one per digital feedback join (auto-created)."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.dispatcher import async_dispatcher_connect
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .const import DOMAIN, SIGNAL_CONN, SIGNAL_NEW
from .entity import CrestronJoinEntity
from .hub import CrestronHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub: CrestronHub = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([CrestronConnectivity(hub)])

    @callback
    def _add(kind: str, join: int, obj: int = 0) -> None:
        if kind == "digital":
            async_add_entities([CrestronDigital(hub, join, obj)])

    entry.async_on_unload(async_dispatcher_connect(hass, SIGNAL_NEW.format(entry=entry.entry_id), _add))
    for join, obj in hub.known("digital"):
        _add("digital", join, obj)


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


class CrestronDigital(CrestronJoinEntity, BinarySensorEntity, RestoreEntity):
    """A digital join, with when it last went high.

    A processor pulses a page's join to flip a panel to it, so by the time
    anybody looks the join is off again. When it last turned on is the
    only record of which page is showing, and it survives a restart.
    """

    _kind = "digital"
    _last_on: str | None = None

    @property
    def is_on(self) -> bool | None:
        return self._value

    @property
    def extra_state_attributes(self) -> dict[str, str]:
        attrs = super().extra_state_attributes
        # The hub's record is this session's; the restored one is from
        # before a restart. ISO timestamps in UTC sort as strings.
        last_on = max(filter(None, (self._hub.last_on.get((self._obj, self._join)), self._last_on)),
                      default=None)
        if last_on:
            attrs["last_on"] = last_on
        return attrs

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        if last is not None:
            self._last_on = last.attributes.get("last_on")
