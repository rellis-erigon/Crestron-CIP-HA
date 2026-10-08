"""Finding the repeated blocks on a panel.

The first attempt clustered on geometry and produced one block of
thirty-five controls, because "near" is transitive along a strip and on a
dense page one chain swallows everything. These tests pin the approach
that works — join arithmetic, with geometry only corroborating — and the
failure that forced it.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import panel_groups as pg  # noqa: E402


def control(name, join, bus="digital", left=0, top=0, kind="Advanced Button"):
    prop = {"digital": "DigitalPressJoin",
            "analog": "AnalogFeedbackJoin",
            "serial": "IndirectTextJoin"}[bus]
    return {
        "page": "Main", "name": name, "control": kind,
        "left": left, "top": top, "width": 100, "height": 60,
        "joins": {prop: {"bus": bus, "join": join, "reserved": False}},
    }


def panel(controls, size=(1280, 800)):
    return {"size": size, "controls": controls, "objects": controls,
            "project": {}, "pages": ["Main"], "duplicates_collapsed": 0}


def zones(bases, offsets=(1, 2, 3), spread=True):
    """Blocks on a decade stride, drawn apart or on top of each other."""
    out = []
    for index, base in enumerate(bases):
        for offset in offsets:
            out.append(control(
                f"Button_{base + offset}", base + offset,
                left=(index * 120 if spread else 0) + offset * 5,
                top=150 + offset * 70))
    return out


# -- The thing it is for ------------------------------------------------

def test_three_zones_on_a_decade_stride_are_found():
    out = pg.propose(panel(zones([210, 220, 230])))
    family = out["families"][0]
    assert family["count"] == 3
    assert family["per_block"] == 3
    assert family["stride"] == 10
    assert family["offsets"] == [1, 2, 3]
    assert [b["base"] for b in family["blocks"]] == [210, 220, 230]


def test_an_even_stride_drawn_together_is_high_confidence():
    """Two independent signals agreeing is as much as can be had."""
    out = pg.propose(panel(zones([210, 220, 230])))
    assert out["families"][0]["confidence"] == "high"
    assert out["families"][0]["even_spacing"]
    assert out["families"][0]["drawn_together"]


def test_the_block_members_name_what_binds_each_join():
    out = pg.propose(panel(zones([210, 220])))
    block = out["families"][0]["blocks"][0]
    assert block["joins"] == [211, 212, 213]
    assert all(m["property"] == "DigitalPressJoin" for m in block["members"])


def test_two_families_of_different_shape_are_both_reported():
    """A panel has zones and a source select, and they are not the same
    block."""
    controls = zones([210, 220, 230]) + zones([100, 110], offsets=(1, 2))
    out = pg.propose(panel(controls))
    shapes = {(f["count"], f["per_block"]) for f in out["families"]}
    assert (3, 3) in shapes
    assert (2, 2) in shapes


# -- What it must not do ------------------------------------------------

def test_one_block_is_not_a_family():
    """A single block says nothing about a repeat."""
    assert pg.propose(panel(zones([210])))["families"] == []


def test_a_lone_join_in_a_decade_is_not_a_block():
    """A block needs at least two joins in it, or every scattered join
    becomes its own family."""
    controls = [control(f"B{n}", n) for n in (105, 215, 325, 435)]
    assert pg.propose(panel(controls))["families"] == []


def test_a_dense_page_does_not_collapse_into_one_block():
    """The failure that forced this design: geometric clustering gave one
    block of thirty-five because proximity is transitive along a strip."""
    controls = zones([210, 220, 230], spread=False)
    out = pg.propose(panel(controls))
    assert out["families"], "no family found at all"
    assert out["families"][0]["count"] == 3, "the three blocks merged"


def test_numbering_that_is_not_drawn_together_is_lower_confidence():
    """A pattern in the numbers with the controls scattered across the
    panel is more likely a habit than one block."""
    controls = []
    for index, base in enumerate([210, 220, 230]):
        for offset in (1, 2, 3):
            controls.append(control(f"B{base+offset}", base + offset,
                                    left=index * 400, top=index * 700))
    out = pg.propose(panel(controls, size=(200, 200)))
    assert out["families"][0]["confidence"] in ("medium", "low")


def test_nothing_invents_a_name():
    """Naming a zone is not in the file, and guessing is worse than
    asking."""
    out = pg.propose(panel(zones([210, 220])))
    text = repr(out)
    for invented in ("Zone 1", "zone_1", "Tower", "Bar", "Lounge"):
        assert invented not in text


# -- Buses stay apart ---------------------------------------------------

def test_a_family_is_found_within_one_bus_only():
    """Digital 211 and analog 211 are different signals; a family that
    mixed them would propose a block that cannot exist."""
    controls = zones([210, 220, 230]) + [
        control("Level210", 211, bus="analog", left=60),
        control("Level220", 221, bus="analog", left=180),
        control("Level230", 231, bus="analog", left=300),
    ]
    out = pg.propose(panel(controls))
    for family in out["families"]:
        buses = {m["property"] for b in family["blocks"] for m in b["members"]}
        assert len(buses) == 1, buses


def test_analog_levels_on_one_per_block_stay_unclaimed():
    """One join per decade is not a block, so the three levels are
    reported as loose rather than forced into a family."""
    controls = zones([210, 220, 230]) + [
        control("L1", 211, bus="analog"), control("L2", 221, bus="analog"),
        control("L3", 231, bus="analog"),
    ]
    out = pg.propose(panel(controls))
    unclaimed = {(u["bus"], u["join"]) for u in out["unclaimed"]}
    assert ("analog", 211) in unclaimed
    assert ("digital", 211) not in unclaimed


# -- Loose joins --------------------------------------------------------

def test_joins_outside_any_family_are_still_reported():
    """Page controls and one-offs matter; dropping them hides half the
    panel."""
    controls = zones([210, 220, 230]) + [
        control("Page1", 300), control("Page2", 301), control("Mute", 1),
    ]
    out = pg.propose(panel(controls))
    unclaimed = {u["join"] for u in out["unclaimed"]}
    assert {1} <= unclaimed


def test_an_empty_panel_proposes_nothing_without_failing():
    out = pg.propose(panel([]))
    assert out == {"families": [], "unclaimed": [], "controls": []}


def test_controls_with_no_geometry_do_not_break_the_proposal():
    """Position is optional in the file and absent on some objects."""
    controls = zones([210, 220])
    for c in controls:
        c["left"] = c["top"] = None
    assert pg.propose(panel(controls))["families"]


# -- Small panels ------------------------------------------------------
#
# A bar is one fader and four buttons. There is nothing repeated to
# detect, and returning only families left such a panel with nothing to
# build a card from — which breaks the whole point of the import.

def bar_panel():
    controls = [
        control("Fader", 1, bus="analog", left=74, top=57,
                kind="Fader Slider Vertical"),
        control("Btn1", 1, left=20, top=308),
        control("Btn2", 2, left=21, top=387),
        control("Btn3", 3, left=21, top=471),
        control("Btn4", 4, left=21, top=555),
    ]
    return panel(controls)


def test_a_panel_with_no_repeats_finds_no_family():
    assert pg.propose(bar_panel())["families"] == []


def test_but_every_control_is_still_returned():
    """The families are an accelerator, not the only route."""
    out = pg.propose(bar_panel())
    assert len(out["controls"]) == 5
    names = {c["name"] for c in out["controls"]}
    assert names == {"Fader", "Btn1", "Btn2", "Btn3", "Btn4"}


def test_a_controls_joins_carry_their_bus_and_property():
    out = pg.propose(bar_panel())
    fader = next(c for c in out["controls"] if c["name"] == "Fader")
    assert fader["joins"] == [
        {"property": "AnalogFeedbackJoin", "bus": "analog",
         "join": 1, "reserved": False}]


def test_controls_come_back_in_reading_order():
    """Top to bottom, left to right — the order somebody sees them in."""
    out = pg.propose(bar_panel())
    tops = [c["top"] for c in out["controls"]]
    assert tops == sorted(tops)


def test_a_big_panel_returns_both_families_and_controls():
    out = pg.propose(panel(zones([210, 220, 230])))
    assert out["families"]
    assert len(out["controls"]) == 9


def test_geometry_is_carried_so_a_card_can_be_laid_out():
    out = pg.propose(bar_panel())
    assert all(c["left"] is not None and c["width"] for c in out["controls"])
