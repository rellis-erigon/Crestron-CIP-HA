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
