"""The processor device, and how a zone hangs off it.

A join either sits on its processor's own device or on a zone device
belonging to it. The zone expressed that belonging with `via_device`, which
Home Assistant removes in 2027.8. The processor device happened to exist —
sibling entities imply it — but relying on that is what made the deprecated
call look harmless, and nothing guaranteed it: a processor whose every join
carries a group has no entity left to imply one.
"""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers import device_registry as dr

from custom_components.crestron_cip.const import CONF_ADDON_URL, DOMAIN
from custom_components.crestron_cip.coordinator import (
    CrestronCoordinator,
    JoinData,
)
from custom_components.crestron_cip.switch import CrestronSwitch

PROCESSORS = {
    "Station1": {"registered": True, "host": "192.0.2.10", "health": {}},
    "Station2": {"registered": False, "host": "", "health": None},
}


def _join(processor="Station1", group="Main Hall", **kwargs):
    return JoinData(
        processor=processor,
        key="d1",
        signal="digital",
        number=1,
        kind="switch",
        name="Power",
        group=group,
        **kwargs,
    )


@pytest.fixture
def hass():
    return HomeAssistant()


@pytest.fixture
def coordinator(hass):
    entry = ConfigEntry(data={CONF_ADDON_URL: "http://192.0.2.20:8098"})
    coordinator = CrestronCoordinator(hass, entry)
    coordinator.processors = dict(PROCESSORS)
    return coordinator


def _registered(hass, name):
    for kwargs in dr.async_get(hass).created:
        if kwargs.get("name") == name:
            return kwargs
    return None


# -- the processor device exists ------------------------------------------


def test_a_device_is_registered_for_each_processor(hass, coordinator):
    coordinator.async_register_processor_devices()

    created = dr.async_get(hass).created
    assert {kwargs["name"] for kwargs in created} == {"Station1", "Station2"}


def test_processor_device_keeps_its_established_identifier(hass, coordinator):
    """Unchanged on purpose.

    Entities already imply this device with exactly these identifiers, so
    anything else would strand the devices people have named and placed.
    """
    coordinator.async_register_processor_devices()

    station1 = _registered(hass, "Station1")
    assert station1["identifiers"] == {(DOMAIN, f"{DOMAIN}_Station1")}
    assert station1["manufacturer"] == "Crestron"
    assert station1["model"] == "Control processor"
    assert station1["config_entry_id"] == coordinator.entry.entry_id


def test_processor_device_links_to_its_web_interface(hass, coordinator):
    coordinator.async_register_processor_devices()

    assert _registered(hass, "Station1")["configuration_url"] == (
        "http://192.0.2.10"
    )


def test_processor_without_a_host_has_no_configuration_url(hass, coordinator):
    coordinator.async_register_processor_devices()

    assert _registered(hass, "Station2").get("configuration_url") is None


def test_registering_twice_does_not_duplicate(hass, coordinator):
    coordinator.async_register_processor_devices()
    coordinator.async_register_processor_devices()

    assert len(dr.async_get(hass).created) == 2


def test_processor_device_ids_are_recorded_for_the_entities(coordinator):
    coordinator.async_register_processor_devices()

    assert set(coordinator.processor_device_ids) == {"Station1", "Station2"}
    assert all(coordinator.processor_device_ids.values())


def test_processor_device_ids_start_empty(coordinator):
    """An entity added before the first refresh must not raise."""
    assert coordinator.processor_device_ids == {}


# -- the zone links to it by registry id ---------------------------------


def test_zone_links_to_its_processor_by_id(coordinator):
    coordinator.async_register_processor_devices()
    entity = CrestronSwitch(coordinator, _join())

    assert (
        entity.device_info["via_device_id"]
        == coordinator.processor_device_ids["Station1"]
    )


def test_zone_never_uses_the_deprecated_key(coordinator):
    """`via_device` is removed in Home Assistant 2027.8."""
    coordinator.async_register_processor_devices()
    entity = CrestronSwitch(coordinator, _join())

    assert "via_device" not in entity.device_info


def test_zone_of_an_unknown_processor_is_left_unlinked(coordinator):
    """A join can arrive for a processor the last refresh did not list."""
    entity = CrestronSwitch(coordinator, _join(processor="Station9"))

    assert "via_device_id" not in entity.device_info
    assert "via_device" not in entity.device_info


def test_zone_device_is_still_keyed_per_processor(coordinator):
    """Two processors may both have a zone called Main Hall."""
    coordinator.async_register_processor_devices()
    a = CrestronSwitch(coordinator, _join(processor="Station1"))
    b = CrestronSwitch(coordinator, _join(processor="Station2"))

    assert a.device_info["identifiers"] != b.device_info["identifiers"]
    assert a.device_info["via_device_id"] != b.device_info["via_device_id"]


def test_zone_keeps_its_other_device_details(coordinator):
    coordinator.async_register_processor_devices()
    info = CrestronSwitch(coordinator, _join()).device_info

    assert info["name"] == "Main Hall"
    assert info["manufacturer"] == "Crestron"
    assert info["model"] == "Zone"


# -- a join on the processor itself --------------------------------------


def test_ungrouped_join_sits_on_the_processor_device(coordinator):
    """It is the processor device, so it must not be linked to itself."""
    coordinator.async_register_processor_devices()
    info = CrestronSwitch(coordinator, _join(group="")).device_info

    assert info["identifiers"] == {(DOMAIN, f"{DOMAIN}_Station1")}
    assert "via_device_id" not in info
    assert "via_device" not in info


def test_join_grouped_under_the_processor_name_is_the_processor(coordinator):
    coordinator.async_register_processor_devices()
    info = CrestronSwitch(coordinator, _join(group="Station1")).device_info

    assert info["identifiers"] == {(DOMAIN, f"{DOMAIN}_Station1")}
    assert "via_device_id" not in info


def test_the_processor_device_is_described_the_same_way_everywhere(
    hass, coordinator
):
    """The coordinator and the entity must agree, or two devices appear."""
    coordinator.async_register_processor_devices()
    info = CrestronSwitch(coordinator, _join(group="")).device_info

    registered = _registered(hass, "Station1")
    for field in ("identifiers", "name", "manufacturer", "model"):
        assert info[field] == registered[field]
