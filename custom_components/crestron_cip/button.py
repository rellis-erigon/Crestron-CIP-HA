"""Buttons for mapped DigitalPressJoins (from the project file). Press = momentary pulse."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_info import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .hub import CrestronHub


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    hub: CrestronHub = hass.data[DOMAIN][entry.entry_id]
    entities = [CrestronPressButton(hub, join, label) for join, label in hub.press_joins()]
    if entities:
        async_add_entities(entities)


class CrestronPressButton(ButtonEntity):
    _attr_should_poll = False

    def __init__(self, hub: CrestronHub, join: int, label: str) -> None:
        self._hub = hub
        self._join = join
        self._attr_unique_id = f"{hub.unique_id}_press_{join}"
        self._attr_name = f"{hub.name} {label}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, hub.unique_id)}, name=hub.name,
            manufacturer="Crestron", model=f"CIP IPID 0x{hub.ipid:02X}",
        )

    @property
    def available(self) -> bool:
        return self._hub.available

    async def async_press(self) -> None:
        await self._hub.client.pulse_digital(self._join)
