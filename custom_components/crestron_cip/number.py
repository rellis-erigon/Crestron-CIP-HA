"""Analog joins that can be set: volume, dim level, setpoint."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import ANALOG_MAX
from .entity import CrestronEntity
from .helpers import setup_kind


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    setup_kind(hass, entry, async_add_entities, "number", CrestronNumber)


class CrestronNumber(CrestronEntity, NumberEntity):
    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0

    @property
    def native_max_value(self) -> float:
        # An analog join is unsigned 16-bit on the wire but usually means
        # 0-100%, so the scale set in the add-on decides what the slider
        # actually spans.
        join = self.join
        return ANALOG_MAX * (join.scale if join else 1.0)

    @property
    def native_step(self) -> float:
        join = self.join
        return join.scale if join and join.scale else 1.0

    @property
    def native_value(self) -> float | None:
        join = self.join
        return self._scaled(join.value) if join else None

    @property
    def native_unit_of_measurement(self) -> str | None:
        join = self.join
        return (join.unit or None) if join else None

    async def async_set_native_value(self, value: float) -> None:
        join = self.join
        if join is None:
            return
        raw = round(value / join.scale) if join.scale else round(value)
        await self.coordinator.async_set(join, value=max(0, min(ANALOG_MAX, raw)))
