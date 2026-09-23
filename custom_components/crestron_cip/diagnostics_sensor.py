"""Processor load and memory, read from the Crestron text console.

CIP carries joins and nothing else, so these come from the add-on polling
each processor's console over SSH. They are diagnostic entities: useful when
a room is behaving oddly, not something to put on a dashboard.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorStateClass,
)
from homeassistant.const import EntityCategory, PERCENTAGE, UnitOfInformation
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import CrestronCoordinator


@dataclass(frozen=True)
class HealthMetric:
    key: str
    name: str
    unit: str | None
    device_class: SensorDeviceClass | None
    factor: float = 1.0
    precision: int | None = None


METRICS = (
    HealthMetric("cpu_percent", "CPU load", PERCENTAGE, None, precision=0),
    HealthMetric("memory_percent", "Memory used", PERCENTAGE, None, precision=0),
    HealthMetric(
        "memory_free", "Memory free", UnitOfInformation.MEGABYTES,
        SensorDeviceClass.DATA_SIZE, factor=1 / (1024 * 1024), precision=0,
    ),
    HealthMetric(
        "uptime_seconds", "Uptime", "d", None, factor=1 / 86400, precision=1,
    ),
)


def build_health_entities(coordinator: CrestronCoordinator) -> list:
    """One set of metrics per processor that reports any."""
    entities = []
    for name, info in (coordinator.processors or {}).items():
        if not isinstance(info, dict) or info.get("health") is None:
            continue
        for metric in METRICS:
            entities.append(CrestronHealthSensor(coordinator, name, metric))
    return entities


class CrestronHealthSensor(CoordinatorEntity[CrestronCoordinator], SensorEntity):
    """One health figure for one processor."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(
        self, coordinator: CrestronCoordinator, processor: str,
        metric: HealthMetric,
    ) -> None:
        super().__init__(coordinator)
        self._processor = processor
        self._metric = metric
        self._attr_unique_id = f"crestron_cip_{processor}_{metric.key}"
        self._attr_name = metric.name
        self._attr_native_unit_of_measurement = metric.unit
        if metric.device_class:
            self._attr_device_class = metric.device_class
        if metric.precision is not None:
            self._attr_suggested_display_precision = metric.precision
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, f"{DOMAIN}_{processor}")},
            name=processor,
            manufacturer="Crestron",
            model="Control processor",
        )

    @property
    def _health(self) -> dict | None:
        info = (self.coordinator.processors or {}).get(self._processor)
        if not isinstance(info, dict):
            return None
        return info.get("health")

    @property
    def available(self) -> bool:
        health = self._health
        # A processor that is up but whose console could not be read should
        # read unavailable rather than hold its last figure forever.
        return bool(
            self.coordinator.last_update_success and health and health.get("ok")
        )

    @property
    def native_value(self) -> float | None:
        health = self._health
        if not health:
            return None
        raw = health.get(self._metric.key)
        if raw is None:
            return None
        try:
            return round(float(raw) * self._metric.factor, 3)
        except (TypeError, ValueError):
            return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        health = self._health or {}
        return {
            "firmware": health.get("firmware", ""),
            "last_read": health.get("at"),
            "error": health.get("error", ""),
        }
