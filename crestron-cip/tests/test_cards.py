"""Matching a processor's joins to the roles a room card draws."""
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cards  # noqa: E402


def join(name, key, signal="digital", number=1, enabled=True):
    stored = types.SimpleNamespace(
        key=key, signal=signal, number=number, store_key=key)
    stored.config = types.SimpleNamespace(name=name, enabled=enabled)
    return stored


def test_nothing_exposed_makes_no_card():
    assert cards.room_card([join("Volume", "a1", enabled=False)]) is None


def test_roles_are_matched_by_name():
    card = cards.room_card([
        join("Display Power", "d1"),
        join("Volume Level", "a1", "analog"),
        join("Selected Source", "s1", "serial"),
    ])
    assert card["entities"] == {
        "display_power": "d1", "volume": "a1", "source": "s1"}


def test_the_narrower_role_wins():
    """'Display Mute' is a mute. Matching 'display' first would have taken
    it for the display, and left the room with no mute at all."""
    card = cards.room_card([join("Display Mute", "d2"), join("Display", "d1")])
    assert card["entities"]["mute"] == "d2"
    assert card["entities"]["display_power"] == "d1"


def test_a_join_is_used_once():
    card = cards.room_card([join("Volume", "a1", "analog")])
    assert list(card["entities"].values()) == ["a1"]


def test_unmatched_joins_are_reported_not_dropped_silently():
    report = cards.describe([join("Volume", "a1", "analog"), join("Spare", "d9")])
    assert report["matched"]["volume"]["key"] == "a1"
    assert [j["key"] for j in report["unused_joins"]] == ["d9"]
    assert "mute" in report["unmatched_roles"]


def test_an_unnamed_join_falls_back_to_its_signal_and_number():
    """A join exposed without a name still has to be describable."""
    report = cards.describe([join("", "d5", "digital", 5)])
    assert report["unused_joins"][0]["name"] == "digital 5"


def test_word_boundaries_are_respected():
    """'Basement' contains 'ase'; a substring match would be a menace. The
    same care is why these patterns are anchored on word boundaries."""
    report = cards.describe([join("Basement Lighting", "d7")])
    assert report["unused_joins"][0]["key"] == "d7"
    assert not report["matched"]


def test_title_becomes_the_card_name():
    card = cards.room_card([join("Volume", "a1", "analog")], "Function 1")
    assert card["name"] == "Function 1"


# -- the mixer builder ---------------------------------------------------
#
# No two Crestron programs lay a zone strip out the same way, so what a
# channel carries is chosen in the GUI rather than hard-coded here.

def zone(store_joins, n, name, **offsets):
    base = 11 + (n - 1) * 10
    store_joins.append(join(name, f"s{base}"))
    store_joins[-1].signal, store_joins[-1].number = "s", base
    store_joins[-1].value = name
    for signal_offset, key in offsets.items():
        signal, offset = signal_offset[0], int(signal_offset[1:])
        j = join("", f"{signal}{base + offset}")
        j.signal, j.number, j.value = signal, base + offset, key
        store_joins.append(j)
    return store_joins


def test_role_spec_is_parsed():
    assert cards.parse_roles("volume:a:0,mute:d:0") == [
        ("volume", "a", 0), ("mute", "d", 0)]


def test_an_empty_spec_falls_back_to_the_default():
    assert cards.parse_roles("") == list(cards.DEFAULT_ROLES)


def test_an_unknown_role_is_refused():
    try:
        cards.parse_roles("loudness:a:0")
    except ValueError as err:
        assert "loudness" in str(err)
    else:
        raise AssertionError("expected a refusal")


def test_a_malformed_spec_is_refused():
    for bad in ("volume", "volume:a", "volume:x:0"):
        try:
            cards.parse_roles(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected {bad!r} to be refused")


def test_only_the_chosen_properties_are_bound():
    rows = []
    zone(rows, 1, "Alfesco", a0="vol", d0="mute", a1="pan")
    card, _ = cards.mixer_card(
        rows, roles=cards.parse_roles("volume:a:0,balance:a:1"))
    assert set(card["entities"]) == {"zone1_volume", "zone1_balance"}


def test_choosing_an_eq_role_turns_the_eq_section_on():
    rows = []
    zone(rows, 1, "Alfesco", a0="vol", a2="low")
    card, _ = cards.mixer_card(
        rows, roles=cards.parse_roles("volume:a:0,eq_low:a:2"))
    assert card["options"]["eq"] == 1


def test_a_strip_with_nothing_bound_is_reported_not_drawn():
    rows = []
    zone(rows, 1, "Alfesco", a0="vol")
    zone(rows, 2, "Gym")
    card, omitted = cards.mixer_card(rows, roles=cards.parse_roles("volume:a:0"))
    assert list(card["labels"].values()) == ["Alfesco"]
    assert omitted == ["Gym"]
