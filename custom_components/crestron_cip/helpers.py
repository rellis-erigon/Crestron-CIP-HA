"""Shared platform setup.

Every platform does the same thing: create entities for the joins of its
kind, and keep doing it, because joins are exposed from the add-on UI while
Home Assistant is running.
"""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import CrestronCoordinator


def setup_kind(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
    kind: str,
    factory,
) -> None:
    """Add entities for one join kind now, and whenever new ones appear."""
    coordinator: CrestronCoordinator = hass.data[DOMAIN][entry.entry_id]
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        fresh = coordinator.new_joins(kind, known)
        if not fresh:
            return
        known.update(join.store_key for join in fresh)
        async_add_entities(factory(coordinator, join) for join in fresh)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))
