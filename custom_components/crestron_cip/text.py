"""Serial joins that can be written: display labels, messages."""
from __future__ import annotations

from homeassistant.components.text import TextEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CrestronEntity
from .helpers import setup_kind


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    setup_kind(hass, entry, async_add_entities, "text", CrestronText)


class CrestronText(CrestronEntity, TextEntity):
    _attr_native_min = 0
    _attr_native_max = 255

    @property
    def native_value(self) -> str | None:
        join = self.join
        if join is None:
            return None
        return ("" if join.value is None else str(join.value))[:255]

    async def async_set_value(self, value: str) -> None:
        join = self.join
        if join is None:
            return
        await self.coordinator.async_set(join, value=value)
