"""Working out what a panel's controls are connected to.

The chain under test: a panel's IP-ID says which room it is, the room
says which named controls are its own, and the names say which Home
Assistant entities those are. Each link is cheap to get wrong in a way
that produces a plausible card doing the wrong thing — a mute button on
the room next door looks exactly like one on this room.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import panel_bindings as pb  # noqa: E402


def strip(base, kind="", x=0):
    """A volume strip as the faceplate generator emits one."""
    regions = [
        {"id": f"l{base}", "kind": "bar", "role": f"level_{base}",
         "x": x, "y": 100, "w": 20, "h": 180},
        {"id": f"u{base}", "kind": "button", "role": f"button_{base}",
         "x": x, "y": 40, "w": 60, "h": 40},
        {"id": f"d{base}", "kind": "button", "role": f"button_{base + 1}",
         "x": x, "y": 300, "w": 60, "h": 40},
        {"id": f"m{base}", "kind": "button", "role": f"button_{base + 2}",
         "x": x, "y": 350, "w": 60, "h": 40, "text": "Mute"},
    ]
    label = {"id": f"t{base}", "kind": "text", "role": f"text_{base}",
             "x": x, "y": 10, "w": 100, "h": 30}
    if kind:
        label["hint"] = kind
    regions.append(label)
    return regions


def faceplate(*groups):
    return {"regions": [r for g in groups for r in g]}


PROGRAM = {
    "devices": [
        {"ipid": 3, "ipid_hex": "03", "model": "TSW-770",
         "comment": "Funct Rm 1 TP", "category": "Touchpanels(Enet)"},
        {"ipid": 4, "ipid_hex": "04", "model": "TSW-770",
         "comment": "Funct Rm 2 TP", "category": "Touchpanels(Enet)"},
    ],
    "named_controls": [
        "Func1ScrGain", "Func1ScrMute", "Func1MicGain", "Func1MicMute",
        "Func1Init",
        "Func2ScrGain", "Func2ScrMute", "Func2MicGain", "Func2MicMute",
    ],
}

ENTITIES = [
    "number.func1scr_volume", "switch.func1scr_mute",
    "number.func1mic_volume", "switch.func1mic_mute",
    "number.func2scr_volume", "switch.func2scr_mute",
    "number.func2mic_volume", "switch.func2mic_mute",
    "sensor.func1mic_level",
]


# -- reading a control's name -------------------------------------------

def test_a_control_splits_into_what_it_is_and_what_it_does():
    assert pb.split_control("Func1MicGain") == ("func1mic", "level")
    assert pb.split_control("Func1MicMute") == ("func1mic", "mute")
    # Gain and level and volume are three names for one thing.
    assert pb.split_control("Zone3Volume")[1] == "level"


def test_a_control_with_no_role_word_is_not_a_strip():
    assert pb.split_control("Func1Init")[1] == ""


def test_only_groups_with_a_level_are_strips():
    # A bare mute belongs to something else.
    groups = pb.control_groups({"named_controls": ["RoomMute", "AmpGain"]})
    assert [g["base"] for g in groups] == ["amp"]


# -- which room a panel is ----------------------------------------------

def test_the_program_says_which_room_a_panel_is():
    room = pb.panel_room(PROGRAM, 3)
    assert room["name"] == "Funct Rm 1 TP"
    assert room["room"] == 1


def test_an_unknown_ip_id_is_not_guessed_at():
    assert pb.panel_room(PROGRAM, 9)["room"] is None


# -- matching a strip to a control --------------------------------------

def test_a_panels_own_source_and_microphone_come_first():
    fp = faceplate(strip(211, "Srouce Name", x=0), strip(221, "Mic Name", x=100))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=3)
    assert [s["control"] for s in out["strips"]] == ["func1scr", "func1mic"]


def test_the_third_strip_is_the_other_rooms_microphone():
    # A joined room shares one source and keeps a microphone each, so the
    # spare strip is the other room's mic. Matching on order alone gives
    # its source instead, which mutes the wrong thing.
    fp = faceplate(strip(211, "Srouce Name", x=0),
                   strip(221, "Mic Name", x=100),
                   strip(231, "Mic Name", x=200))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=3)
    assert [s["control"] for s in out["strips"]] == [
        "func1scr", "func1mic", "func2mic",
    ]


def test_the_other_panel_is_the_mirror_image():
    fp = faceplate(strip(211, "Srouce Name", x=0),
                   strip(221, "Mic Name", x=100),
                   strip(231, "Mic Name", x=200))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=4)
    assert [s["control"] for s in out["strips"]] == [
        "func2scr", "func2mic", "func1mic",
    ]


def test_the_designers_own_spelling_is_allowed_for():
    # One real panel labels its source strip "Srouce Name".
    assert pb.classify("Srouce Name") == "source"
    assert pb.classify("Mic Name") == "mic"
    assert pb.classify("Room Name") == ""


def test_a_strip_with_no_label_still_gets_a_control():
    # Order is a worse signal than the label, and better than nothing.
    fp = faceplate(strip(211, x=0), strip(221, x=100))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=3)
    assert all(s["control"] for s in out["strips"])


def test_no_control_is_used_twice():
    fp = faceplate(strip(211, "Mic Name", x=0), strip(221, "Mic Name", x=100),
                   strip(231, "Mic Name", x=200))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=3)
    controls = [s["control"] for s in out["strips"] if s["control"]]
    assert len(controls) == len(set(controls))


def test_every_match_carries_its_reasoning():
    # A binding is the one part of an import nobody can check later.
    fp = faceplate(strip(211, "Srouce Name"))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=3)
    assert "this panel" in out["strips"][0]["why"]


# -- finding the entity -------------------------------------------------

def test_a_control_resolves_to_the_entity_a_bridge_made():
    # The bridge calls it volume where the DSP calls it gain, so the base
    # is what matches and the domain settles the rest.
    assert pb.resolve_entity("Func1MicGain", "level", ENTITIES) == \
        "number.func1mic_volume"
    assert pb.resolve_entity("Func1MicMute", "mute", ENTITIES) == \
        "switch.func1mic_mute"


def test_a_level_must_be_settable():
    # sensor.func1mic_level matches the name and cannot be set, so a
    # strip bound to it would be an ornament.
    assert pb.resolve_entity("Func1MicGain", "level",
                             ["sensor.func1mic_level"]) == ""


def test_an_unknown_control_binds_to_nothing():
    assert pb.resolve_entity("Func9MicGain", "level", ENTITIES) == ""


def test_the_closest_name_wins():
    entities = ["number.func1mic_volume_preset_2", "number.func1mic_volume"]
    assert pb.resolve_entity("Func1MicGain", "level", entities) == \
        "number.func1mic_volume"


# -- what ends up on the card -------------------------------------------

def test_the_keys_are_wired_to_the_thing_the_bar_shows():
    fp = faceplate(strip(211, "Srouce Name"))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=3)
    pb.apply_changes(fp, out["regions"])
    by_role = {r["role"]: r for r in fp["regions"]}
    assert by_role["button_211"]["action"] == "level_up"
    assert by_role["button_211"]["target"] == "level_211"
    assert by_role["button_212"]["action"] == "level_down"
    assert by_role["button_213"]["action"] == "mute_toggle"


def test_the_bindings_name_entities_not_joins():
    fp = faceplate(strip(211, "Srouce Name"))
    out = pb.propose(fp, PROGRAM, ENTITIES, ipid=3)
    assert out["entities"]["level_211"] == "number.func1scr_volume"
    assert out["entities"]["button_213"] == "switch.func1scr_mute"


def test_nothing_is_wired_without_a_program():
    fp = faceplate(strip(211, "Srouce Name"))
    out = pb.propose(fp, None, ENTITIES, ipid=3)
    assert out["entities"] == {}
    assert out["regions"] == []


def test_nothing_is_wired_to_an_entity_that_does_not_exist():
    fp = faceplate(strip(211, "Srouce Name"))
    out = pb.propose(fp, PROGRAM, [], ipid=3)
    assert out["entities"] == {}
    # The strip is still reported, so the screen can say what is missing.
    assert out["strips"][0]["control"] == "func1scr"


def test_a_control_without_a_level_leaves_the_keys_alone():
    fp = faceplate(strip(211, "Srouce Name"))
    program = {**PROGRAM, "named_controls": ["Func1ScrMute"]}
    out = pb.propose(fp, program, ENTITIES, ipid=3)
    assert "level_211" not in out["entities"]


# -- finding strips at all ----------------------------------------------

def test_a_strip_needs_a_level():
    # Two buttons side by side are a source selector, not a fader.
    fp = {"regions": [
        {"id": "a", "kind": "button", "role": "button_101", "x": 0, "y": 0},
        {"id": "b", "kind": "button", "role": "button_102", "x": 80, "y": 0},
    ]}
    assert pb.find_strips(fp) == []


def test_a_strip_on_two_states_is_found_once():
    # The same strip appears on the joined and unjoined layouts.
    fp = faceplate(strip(211, "Srouce Name"), strip(211, "Srouce Name"))
    assert len(pb.find_strips(fp)) == 1


def test_strips_come_back_in_reading_order():
    fp = faceplate(strip(231, "Mic Name", x=200), strip(211, "Srouce Name", x=0))
    assert [s["base"] for s in pb.find_strips(fp)] == [211, 231]
