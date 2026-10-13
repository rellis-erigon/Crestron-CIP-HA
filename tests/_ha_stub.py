"""Minimal Home Assistant and aiohttp stand-ins, installed only when absent.

This repository had a pytest.ini and no tests, so the first thing a fix
needed was somewhere to prove it. Only what the integration actually
imports is stubbed; anything beyond that should fail loudly rather than be
quietly faked. Neither Home Assistant nor aiohttp can be installed in the
add-on container this is developed in, so the stubs are what CI-less local
runs have.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from enum import StrEnum


def _installed(name: str) -> bool:
    """True when the real package is importable, so no stub is wanted."""
    return importlib.util.find_spec(name) is not None


def homeassistant_available() -> bool:
    return _installed("homeassistant")


def _module(name: str, **attrs) -> types.ModuleType:
    module = types.ModuleType(name)
    for key, value in attrs.items():
        setattr(module, key, value)
    sys.modules[name] = module
    parent_name, _, child = name.rpartition(".")
    if parent_name and parent_name in sys.modules:
        setattr(sys.modules[parent_name], child, module)
    return module


# -- core ----------------------------------------------------------------


def callback(func):
    return func


class HomeAssistant:
    def __init__(self) -> None:
        self.data: dict = {}
        self.config_entries = _ConfigEntries()
        self.services = _Services()

    def async_add_executor_job(self, func, *args):
        async def run():
            return func(*args)

        return run()


class _ConfigEntries:
    def __init__(self) -> None:
        self.forwarded: list = []

    async def async_forward_entry_setups(self, entry, platforms) -> None:
        self.forwarded.append((entry, tuple(platforms)))

    async def async_unload_platforms(self, entry, platforms) -> bool:
        return True

    async def async_reload(self, entry_id) -> None:
        return None


class _Services:
    def __init__(self) -> None:
        self.registered: dict = {}

    def has_service(self, domain, service) -> bool:
        return (domain, service) in self.registered

    def async_register(self, domain, service, handler, **_kwargs) -> None:
        self.registered[(domain, service)] = handler

    def async_remove(self, domain, service) -> None:
        self.registered.pop((domain, service), None)


class ConfigEntry:
    def __init__(self, data=None, options=None, entry_id="entry_one") -> None:
        self.data = data or {}
        self.options = options or {}
        self.entry_id = entry_id
        self.runtime_data = None
        self.background_tasks: list = []

    def __class_getitem__(cls, _item):
        return cls

    def add_update_listener(self, listener):
        return lambda: None

    def async_on_unload(self, func) -> None:
        return None

    def async_create_background_task(self, hass, target, name, **_kwargs):
        # Nothing here runs the event stream, so close the coroutine rather
        # than leaving a "never awaited" warning behind.
        target.close()
        self.background_tasks.append(name)
        return None


# -- device registry -----------------------------------------------------


class _DeviceEntry:
    def __init__(self, kwargs, device_id) -> None:
        self.id = device_id
        self.kwargs = kwargs
        self.identifiers = kwargs.get("identifiers")
        self.name = kwargs.get("name")


class _DeviceRegistry:
    """Records what was registered, so a test can check a device exists."""

    def __init__(self) -> None:
        self.created: list = []
        self._by_identifiers: dict = {}

    def async_get_or_create(self, **kwargs) -> _DeviceEntry:
        key = frozenset(kwargs.get("identifiers") or ())
        if key in self._by_identifiers:
            return self._by_identifiers[key]
        entry = _DeviceEntry(kwargs, f"device_{len(self.created) + 1}")
        self.created.append(kwargs)
        self._by_identifiers[key] = entry
        return entry

    def async_get_device(self, identifiers=None, **_kwargs):
        return self._by_identifiers.get(frozenset(identifiers or ()))


_REGISTRIES: dict = {}


def device_registry(hass) -> _DeviceRegistry:
    return _REGISTRIES.setdefault(id(hass), _DeviceRegistry())


# -- entities ------------------------------------------------------------


class _EntityBase:
    _attr_has_entity_name = False
    _attr_name = None
    _attr_unique_id = None
    _attr_device_info = None
    _attr_icon = None
    _attr_entity_category = None
    _attr_native_value = None
    _attr_suggested_display_precision = None

    def __class_getitem__(cls, _item):
        return cls

    @property
    def device_info(self):
        return self._attr_device_info

    @property
    def unique_id(self):
        return self._attr_unique_id

    @property
    def name(self):
        return self._attr_name


class DataUpdateCoordinator:
    def __init__(self, hass, logger=None, *, name=None, update_interval=None,
                 **_kwargs) -> None:
        self.hass = hass
        self.logger = logger
        self.name = name
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True

    def __class_getitem__(cls, _item):
        return cls

    async def async_config_entry_first_refresh(self) -> None:
        self.data = await self._async_update_data()

    async def async_request_refresh(self) -> None:
        self.data = await self._async_update_data()

    def async_set_updated_data(self, data) -> None:
        self.data = data

    def async_update_listeners(self) -> None:
        return None


class CoordinatorEntity(_EntityBase):
    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, _item):
        return cls


class EntityCategory(StrEnum):
    CONFIG = "config"
    DIAGNOSTIC = "diagnostic"


class NumberMode(StrEnum):
    AUTO = "auto"
    BOX = "box"
    SLIDER = "slider"


class SensorDeviceClass(StrEnum):
    DATA_SIZE = "data_size"
    DURATION = "duration"
    TEMPERATURE = "temperature"
    TIMESTAMP = "timestamp"


class SensorStateClass(StrEnum):
    MEASUREMENT = "measurement"
    TOTAL = "total"
    TOTAL_INCREASING = "total_increasing"


class _EntityRegistryEntry:
    def __init__(self, entity_id, unique_id, platform) -> None:
        self.entity_id = entity_id
        self.unique_id = unique_id
        self.platform = platform
        self.disabled_by = None


class _EntityRegistry:
    def __init__(self) -> None:
        self.entities: dict = {}

    def async_get_entity_id(self, domain, platform, unique_id):
        for entity_id, entry in self.entities.items():
            if entry.unique_id == unique_id and entry.platform == platform:
                return entity_id
        return None

    def async_get(self, entity_id):
        return self.entities.get(entity_id)

    def async_remove(self, entity_id) -> None:
        self.entities.pop(entity_id, None)

    def async_update_entity(self, entity_id, **kwargs):
        return self.entities.get(entity_id)


_ENTITY_REGISTRIES: dict = {}


def entity_registry(hass) -> _EntityRegistry:
    return _ENTITY_REGISTRIES.setdefault(id(hass), _EntityRegistry())


class _UnitOfInformation(StrEnum):
    BYTES = "B"
    KILOBYTES = "kB"
    MEGABYTES = "MB"


class Platform(StrEnum):
    BINARY_SENSOR = "binary_sensor"
    BUTTON = "button"
    NUMBER = "number"
    SELECT = "select"
    SENSOR = "sensor"
    SWITCH = "switch"
    TEXT = "text"


# -- aiohttp -------------------------------------------------------------


class _ClientError(Exception):
    pass


class _ClientTimeout:
    def __init__(self, total=None, sock_read=None, **_kwargs) -> None:
        self.total = total
        self.sock_read = sock_read


def _stub_aiohttp() -> None:
    """Stand in for aiohttp, which the integration imports at module level.

    Only `ClientTimeout` and the error classes are needed. The add-on tests
    in this repository need the real package, so anything else asked of this
    module says so rather than failing as a puzzling missing attribute.
    """
    module = _module("aiohttp", ClientTimeout=_ClientTimeout,
                     ClientError=_ClientError,
                     ClientResponseError=type("ClientResponseError",
                                              (_ClientError,), {}),
                     ClientSession=object)

    def __getattr__(name: str):
        raise ImportError(
            f"aiohttp.{name} was asked for, but aiohttp is not installed and "
            f"tests/_ha_stub.py only stands in for the little of it the "
            f"integration uses. Install the real aiohttp to run the add-on "
            f"tests."
        )

    module.__getattr__ = __getattr__


def install() -> None:
    if not _installed("aiohttp"):
        _stub_aiohttp()

    if homeassistant_available():
        return

    _module("homeassistant")
    _module("homeassistant.const", Platform=Platform,
            EntityCategory=EntityCategory, PERCENTAGE="%",
            UnitOfInformation=_UnitOfInformation)
    _module("homeassistant.core", HomeAssistant=HomeAssistant,
            callback=callback, ServiceCall=object, ServiceResponse=dict,
            SupportsResponse=type("SupportsResponse", (), {"ONLY": "only", "OPTIONAL": "optional"}))
    _module("homeassistant.config_entries", ConfigEntry=ConfigEntry,
            ConfigFlow=object, OptionsFlow=object, ConfigFlowResult=dict)
    _module("homeassistant.exceptions", ConfigEntryNotReady=Exception,
            HomeAssistantError=Exception, ServiceValidationError=Exception)
    _module("homeassistant.data_entry_flow", FlowResult=dict)

    _module("homeassistant.components")
    for name, cls in (("sensor", "SensorEntity"), ("switch", "SwitchEntity"),
                      ("number", "NumberEntity"), ("select", "SelectEntity"),
                      ("text", "TextEntity"), ("button", "ButtonEntity"),
                      ("binary_sensor", "BinarySensorEntity")):
        _module(f"homeassistant.components.{name}", **{cls: _EntityBase})
    sys.modules["homeassistant.components.number"].NumberMode = NumberMode
    sensor = sys.modules["homeassistant.components.sensor"]
    sensor.SensorStateClass = SensorStateClass
    sensor.SensorDeviceClass = SensorDeviceClass

    _module("homeassistant.helpers")
    _module("homeassistant.helpers.update_coordinator",
            DataUpdateCoordinator=DataUpdateCoordinator,
            UpdateFailed=type("UpdateFailed", (Exception,), {}),
            CoordinatorEntity=CoordinatorEntity)
    _module("homeassistant.helpers.device_registry", DeviceInfo=dict,
            async_get=device_registry)
    _module("homeassistant.helpers.entity_registry",
            async_get=entity_registry, RegistryEntry=_EntityRegistryEntry,
            async_entries_for_config_entry=lambda reg, entry_id: [])
    _module("homeassistant.helpers.entity_platform",
            AddEntitiesCallback=object,
            AddConfigEntryEntitiesCallback=object)
    _module("homeassistant.helpers.aiohttp_client",
            async_get_clientsession=lambda hass: None)
    _module("homeassistant.helpers.config_validation")
    _module("homeassistant.helpers.selector")
