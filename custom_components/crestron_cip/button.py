"""Digital joins driven as a momentary press.

A panel button is a pulse, not a latch: SIMPL sees the join go high and
low again. Exposing these as switches left half the system latched on.
"""
from __future__ import annotations

from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import CrestronEntity
from .helpers import setup_kind


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    setup_kind(hass, entry, async_add_entities, "button", CrestronButton)


class CrestronButton(CrestronEntity, ButtonEntity):
    async def async_press(self, **kwargs: Any) -> None:
        join = self.join
        if join is None:
            return
        await self.coordinator.async_set(join, pulse=True)
