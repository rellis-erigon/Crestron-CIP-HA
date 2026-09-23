"""Digital joins that can be driven as well as read."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CrestronEntity
from .helpers import setup_kind


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    setup_kind(hass, entry, async_add_entities, "switch", CrestronSwitch)


class CrestronSwitch(CrestronEntity, SwitchEntity):
    @property
    def is_on(self) -> bool | None:
        join = self.join
        return None if join is None else bool(join.value)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)

    async def _write(self, state: bool) -> None:
        join = self.join
        if join is None:
            return
        await self.coordinator.async_set(join, value=state)
        # The processor echoes the new state back over the event stream, so
        # nothing is written into the local state here. A program that
        # interlocks the join and refuses the change would otherwise leave
        # the switch showing something the room is not doing.
