"""Base entity for the Crestron CIP integration."""
from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CrestronCoordinator, JoinData


class CrestronEntity(CoordinatorEntity[CrestronCoordinator]):
    """One exposed join."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: CrestronCoordinator, join: JoinData) -> None:
        super().__init__(coordinator)
        self._store_key = join.store_key
        # Keyed on processor and join rather than the config entry, so
        # removing and re-adding the integration keeps the same entities.
        self._attr_unique_id = f"crestron_cip_{join.processor}_{join.key}"
        self._attr_name = join.name
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{DOMAIN}_{join.processor}")},
            name=join.processor,
            manufacturer="Crestron",
            model="Control processor",
        )

    @property
    def join(self) -> JoinData | None:
        if not self.coordinator.data:
            return None
        return self.coordinator.data.get(self._store_key)

    @property
    def available(self) -> bool:
        """Available only when the processor itself is registered.

        A join that has not changed since the add-on connected still holds
        its last value, so presence in the data alone says nothing about
        whether the processor is answering.
        """
        join = self.join
        return bool(
            self.coordinator.last_update_success and join and join.available
        )

    @property
    def extra_state_attributes(self) -> dict:
        join = self.join
        if join is None:
            return {}
        return {"processor": join.processor, "join": join.key}

    def _scaled(self, value) -> float | None:
        join = self.join
        if join is None or value is None:
            return None
        try:
            return float(value) * join.scale
        except (TypeError, ValueError):
            return None
