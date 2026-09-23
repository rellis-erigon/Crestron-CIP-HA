"""Digital joins exposed as read-only state."""
from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CrestronEntity
from .helpers import setup_kind


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    setup_kind(hass, entry, async_add_entities, "binary_sensor", CrestronBinarySensor)


class CrestronBinarySensor(CrestronEntity, BinarySensorEntity):
    @property
    def is_on(self) -> bool | None:
        join = self.join
        return None if join is None else bool(join.value)

    @property
    def device_class(self):
        join = self.join
        return join.device_class or None if join else None
