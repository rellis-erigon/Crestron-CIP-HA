"""Analog and serial joins exposed as readings."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CrestronEntity
from .helpers import setup_kind


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    setup_kind(hass, entry, async_add_entities, "sensor", CrestronSensor)


class CrestronSensor(CrestronEntity, SensorEntity):
    @property
    def native_value(self):
        join = self.join
        if join is None:
            return None
        if join.signal == "s":
            # A serial join can carry more than a state machine will accept.
            text = "" if join.value is None else str(join.value)
            return text[:255]
        return self._scaled(join.value)

    @property
    def native_unit_of_measurement(self) -> str | None:
        join = self.join
        if join is None or join.signal == "s":
            return None
        return join.unit or None

    @property
    def device_class(self):
        join = self.join
        return (join.device_class or None) if join else None

    @property
    def state_class(self):
        join = self.join
        if join is None or join.signal != "a":
            # A serial join holds text; asking the recorder to keep
            # statistics for it fills the database with nothing.
            return None
        return SensorStateClass.MEASUREMENT
