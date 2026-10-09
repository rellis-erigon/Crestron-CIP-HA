"""Turning a panel project into a card that looks like the panel.

Every fixture here is a shape that was actually read off hardware, and
most of them encode a mistake the generator made first: subpages stacked
in one corner, popups painted over the values, a page-flip button offered
as something Home Assistant could drive.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import panel_faceplate as pf  # noqa: E402


def obj(control="Advanced Button", left=10, top=20, width=100, height=50,
        label="", joins=None, text_joins=(), page="Main", style=None,
        align="", name="", artwork=None):
    return {
        "page": page, "page_path": page, "name": name, "control": control,
        "label": label, "style": style or {"size": 24, "color": None, "bold": False},
        "text_joins": list(text_joins), "align": align, "joins": joins or {},
        "artwork": artwork or {},
        "left": left, "top": top, "width": width, "height": height, "z": 1,
    }


def digital(number, prop="DigitalPressJoin"):
    return {prop: {"bus": "digital", "join": number,
                   "reserved": number >= 17000}}


def analog(number, prop="AnalogFeedbackJoin"):
    return {prop: {"bus": "analog", "join": number, "reserved": False}}


def panel(objects, page_meta=None, references=None, size=(1280, 800)):
    return {
        "size": size, "objects": objects,
        "page_meta": page_meta if page_meta is not None else [
            {"key": "Main", "name": "Main", "kind": "page",
             "width": 1280, "height": 800, "order": 0},
        ],
        "references": references or [],
    }


# -- one control, one region --------------------------------------------

def test_a_button_becomes_a_momentary_button():
    fp = pf.build_faceplate(panel([obj(joins=digital(101), label="Projector")]))
    region = next(r for r in fp["regions"] if r["kind"] == "button")
    assert region["role"] == "button_101"
    # A panel button is momentary. A toggle would invert its meaning the
    # moment somebody held it down.
    assert region["action"] == "press"
    assert region["text"] == "Projector"


def test_a_gauge_reads_the_full_analog_range():
    fp = pf.build_faceplate(panel([
        obj(control="Liquid Gauge Vertical", joins=analog(211)),
    ]))
    region = next(r for r in fp["regions"] if r["kind"] == "bar")
    assert region["role"] == "level_211"
    # Crestron analog joins are 16-bit. A bar assuming 0-100 would peg at
    # a few percent of travel and look broken.
    assert region["max"] == 65535


def test_position_is_kept_verbatim():
    # The proportions an operator already knows are the whole point.
    fp = pf.build_faceplate(panel([
        obj(left=871, top=120, width=409, height=580, joins=digital(1)),
    ]))
    region = next(r for r in fp["regions"] if r["kind"] == "button")
    assert (region["x"], region["y"], region["w"], region["h"]) == (871, 120, 409, 580)


def test_the_canvas_is_the_panels_own_size():
    fp = pf.build_faceplate(panel([obj(joins=digital(1))]))
    assert fp["size"] == [1280, 800]


# -- what must not be invented ------------------------------------------

def test_a_page_flip_button_is_not_offered_as_a_control():
    # Joins from 17000 up are the panel's own housekeeping. They never
    # appear in the control program, so no entity can ever drive them.
    fp = pf.build_faceplate(panel([
        obj(joins=digital(18495), label="Next"),
    ]))
    region = fp["regions"][0]
    assert region["kind"] == "plate"
    assert region["role"] == ""
    # It is still drawn, and still says what it said.
    assert region["text"] == "Next"


def test_an_unknown_control_is_drawn_but_does_nothing():
    fp = pf.build_faceplate(panel([
        obj(control="Subpage Reference List Horizontal", label="Zones"),
    ]))
    assert fp["regions"][0]["kind"] == "plate"
    assert fp["regions"][0]["role"] == ""


def test_a_designers_placeholder_is_not_shown_as_a_value():
    # "Room Name" is a prompt the designer left for themselves. On a card
    # it reads as a value that failed to load.
    fp = pf.build_faceplate(panel([
        obj(control="Formatted Text", label="Room Name", text_joins=[1]),
    ]))
    region = next(r for r in fp["regions"] if r["kind"] == "text")
    assert region["text"] == ""
    assert region["role"] == "text_1"


def test_static_text_is_kept_as_a_caption():
    fp = pf.build_faceplate(panel([
        obj(control="Formatted Text", label="Source Select"),
    ]))
    region = next(r for r in fp["regions"] if r["kind"] == "text")
    assert region["text"] == "Source Select"
    assert region["role"] == ""


def test_a_hairline_is_not_a_control():
    fp = pf.build_faceplate(panel([
        obj(control="Border", width=2, height=400),
        obj(joins=digital(7)),
    ]))
    assert [r["kind"] for r in fp["regions"]] == ["button"]


def test_a_visibility_join_is_not_what_a_button_does():
    # Binding an entity to a visibility join would make the entity decide
    # whether the button exists rather than what it does.
    joins = {**digital(305, "DigitalVisibilityJoin"), **digital(303)}
    fp = pf.build_faceplate(panel([obj(joins=joins)]))
    assert next(r for r in fp["regions"] if r["kind"] == "button")["role"] == "button_303"


# -- roles have to survive a re-import ----------------------------------

def test_roles_are_stable_across_two_reads():
    dump = panel([obj(joins=digital(101)), obj(joins=digital(102), left=200)])
    first = pf.build_faceplate(dump)
    second = pf.build_faceplate(dump)
    assert [r["role"] for r in first["regions"]] == [r["role"] for r in second["regions"]]


def test_an_unjoined_control_still_gets_a_distinct_role():
    fp = pf.build_faceplate(panel([
        obj(control="Formatted Text", label="A", text_joins=[]),
        obj(control="Liquid Gauge Vertical", left=300),
    ]))
    roles = [r["role"] for r in fp["regions"] if r["role"]]
    assert len(roles) == len(set(roles))


# -- composition: the thing that makes it look like the panel -----------

MAIN = {"key": "3.0-Main", "name": "3.0-Main", "kind": "page",
        "width": 1280, "height": 800, "order": 0}


def subpage(key, width, height, order):
    return {"key": key, "name": "Subpage", "kind": "subpage",
            "width": width, "height": height, "order": order}


def reference(name, left, top, width, height, join, order, page="3.0-Main"):
    return {"page": page, "name": name, "left": left, "top": top,
            "width": width, "height": height, "join": join, "order": order}


def test_a_subpages_controls_are_offset_to_where_it_is_placed():
    # A subpage's coordinates start again at 0,0. Left alone, every
    # subpage lands in the top-left corner on top of every other one.
    dump = panel(
        [obj(page="Subpage", left=21, top=5, joins=digital(211))],
        page_meta=[MAIN, subpage("Subpage", 409, 580, 1)],
        references=[reference("3.4-Vol", 871, 120, 409, 580, 34, 0)],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    region = next(r for r in fp["regions"] if r["kind"] == "button")
    assert (region["x"], region["y"]) == (871 + 21, 120 + 5)


def test_a_gated_subpage_becomes_a_state_of_the_screen():
    dump = panel(
        [obj(page="Subpage", joins=digital(1))],
        page_meta=[MAIN, subpage("Subpage", 1280, 580, 1)],
        references=[reference("3.5-Confirm", 0, 120, 1280, 580, 35, 0)],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    assert fp["pages"] == ["3.5-Confirm"]
    region = next(r for r in fp["regions"] if r["kind"] == "button")
    assert region["page"] == "3.5-Confirm"
    # Half a popup floating on its own is worse than none of it.
    assert region["group"] == "3.5-Confirm"


def test_an_always_visible_subpage_belongs_to_no_state():
    # A top bar is on screen whatever else is. Giving it a page would hide
    # it on every state but one.
    dump = panel(
        [obj(page="Subpage", joins=digital(1))],
        page_meta=[MAIN, subpage("Subpage", 1280, 120, 1)],
        references=[reference("TopBar", 0, 0, 1280, 120, None, 0)],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    assert "page" not in fp["regions"][0]
    assert "pages" not in fp


def test_one_subpage_serves_every_page_that_references_it():
    dump = panel(
        [obj(page="Bar", joins=digital(1))],
        page_meta=[MAIN, {"key": "2.0-Start", "name": "2.0-Start",
                          "kind": "page", "width": 1280, "height": 800,
                          "order": 1},
                   subpage("Bar", 1280, 120, 2)],
        references=[reference("TopBar", 0, 0, 1280, 120, None, 0),
                    reference("TopBar", 0, 0, 1280, 120, None, 1, "2.0-Start")],
    )
    for page in ("3.0-Main", "2.0-Start"):
        fp = pf.build_faceplate(dump, page=page)
        assert len(fp["regions"]) == 1, page


def test_same_sized_popups_are_paired_in_order_and_reported():
    # Two confirmation popups both fill the screen, and the project file
    # does not say which reference points at which. Document order is the
    # only ordering there is, so it is used and declared.
    dump = panel(
        [obj(page="A", label="Join?", control="Formatted Text"),
         obj(page="B", label="Shutdown?", control="Formatted Text")],
        page_meta=[MAIN, subpage("A", 1280, 580, 1), subpage("B", 1280, 580, 2)],
        references=[reference("3.5-Join", 0, 120, 1280, 580, 35, 0),
                    reference("3.6-Shutdown", 0, 120, 1280, 580, 36, 1)],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    by_page = {r["page"]: r["text"] for r in fp["regions"] if r.get("page")}
    assert by_page == {"3.5-Join": "Join?", "3.6-Shutdown": "Shutdown?"}
    assert sorted(fp["import_notes"]["ambiguous_references"]) == [
        "3.5-Join", "3.6-Shutdown",
    ]


def test_a_unique_size_is_not_reported_as_a_guess():
    dump = panel(
        [obj(page="Subpage", joins=digital(1))],
        page_meta=[MAIN, subpage("Subpage", 409, 580, 1)],
        references=[reference("3.4-Vol", 871, 120, 409, 580, 34, 0)],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    assert fp["import_notes"]["ambiguous_references"] == []


def test_a_subpage_nothing_places_is_reported_not_guessed():
    # A Subpage Reference List instantiates its subpage once per zone and
    # the project file records the list, not the instances. Inventing how
    # many there are and where they sit would invent a layout.
    dump = panel(
        [obj(page="Strip", joins=digital(1))],
        page_meta=[MAIN, subpage("Strip", 200, 640, 1)],
        references=[],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    assert fp["import_notes"]["unplaced_subpages"] == ["Strip"]
    assert fp["regions"] == []


def test_a_reference_with_no_rectangle_places_nothing():
    dump = panel(
        [obj(page="Subpage", joins=digital(1))],
        page_meta=[MAIN, subpage("Subpage", 409, 580, 1)],
        references=[reference("Nowhere", None, None, None, None, None, 0)],
    )
    assert pf.build_faceplate(dump, page="3.0-Main")["regions"] == []


# -- paint order --------------------------------------------------------

def test_background_is_drawn_before_what_sits_on_it():
    # Regions paint in list order. A plate emitted late covers the values.
    dump = panel([
        obj(control="Formatted Text", label="Volume", left=10, top=10,
            width=100, height=40),
        obj(control="Fill Border", left=0, top=0, width=1280, height=800),
    ])
    kinds = [r["kind"] for r in pf.build_faceplate(dump)["regions"]]
    assert kinds == ["plate", "text"]


def test_the_largest_plate_goes_down_first():
    dump = panel([
        obj(control="Fill Border", left=100, top=100, width=200, height=200),
        obj(control="Fill Border", left=0, top=0, width=1280, height=800),
    ])
    areas = [r["w"] * r["h"] for r in pf.build_faceplate(dump)["regions"]]
    assert areas == sorted(areas, reverse=True)


# -- which screen to open on --------------------------------------------

def test_the_busiest_page_is_the_one_a_card_opens_on():
    # An init page has a handful of controls and a splash screen none.
    dump = panel(
        [obj(page="1.0-Init", joins=digital(1)),
         obj(page="Busy", joins=digital(2)),
         obj(page="Busy", joins=digital(3), left=200),
         obj(page="Busy", joins=digital(4), left=400)],
        page_meta=[
            {"key": "1.0-Init", "name": "1.0-Init", "kind": "page",
             "width": 1280, "height": 800, "order": 0},
            {"key": "3.0-Main", "name": "3.0-Main", "kind": "page",
             "width": 1280, "height": 800, "order": 1},
            subpage("Busy", 1280, 680, 2),
        ],
        references=[reference("Body", 0, 120, 1280, 680, None, 0)],
    )
    assert pf.main_screen(dump) == "3.0-Main"


def test_a_panel_with_no_pages_still_produces_something():
    dump = panel([obj(joins=digital(1))], page_meta=[], references=[])
    fp = pf.build_faceplate(dump)
    assert fp["regions"]
    assert fp["size"] == [1280, 800]


# -- styling ------------------------------------------------------------

def test_colour_travels_because_on_a_panel_colour_is_the_label():
    fp = pf.build_faceplate(panel([
        obj(control="Formatted Text", label="MUTE",
            style={"size": 24, "color": "#C00000", "bold": True}),
    ]))
    assert fp["regions"][0]["color"] == "#C00000"


def test_near_black_is_left_to_the_theme():
    # Carrying the default ink explicitly would break dark mode for nothing.
    fp = pf.build_faceplate(panel([
        obj(control="Formatted Text", label="Volume",
            style={"size": 24, "color": "#000000", "bold": False}),
    ]))
    assert "color" not in fp["regions"][0]


def test_panel_font_sizes_are_scaled_to_the_card():
    fp = pf.build_faceplate(panel([
        obj(control="Formatted Text", label="Room",
            style={"size": 56, "color": None, "bold": True}),
    ]))
    assert fp["regions"][0]["size"] == 28


# -- the summary the import screen shows --------------------------------

def test_the_summary_counts_what_can_be_bound():
    fp = pf.build_faceplate(panel([
        obj(joins=digital(101)),
        obj(joins=digital(102), left=200),
        obj(control="Fill Border", left=0, top=0, width=800, height=600),
    ]))
    summary = pf.summarise_faceplate(fp)
    assert summary["kinds"] == {"button": 2, "plate": 1}
    assert summary["bindable"] == ["button_101", "button_102"]


# -- which screens are offered at all -----------------------------------

def test_an_unplaced_subpage_is_offered_as_a_screen():
    # A Subpage Reference List instantiates one subpage per zone, so the
    # subpage *is* the zone strip. On a small panel the pages hold nothing
    # but a top bar and this is the only thing worth drawing.
    dump = panel(
        [obj(page="Strip", joins=digital(1)),
         obj(page="Strip", joins=digital(2), left=200),
         obj(page="Main", joins=digital(9))],
        page_meta=[MAIN, {"key": "Main", "name": "Main", "kind": "page",
                          "width": 1280, "height": 800, "order": 1},
                   subpage("Strip", 200, 640, 2)],
        references=[],
    )
    assert "Strip" in pf.candidate_screens(dump)
    # And it is the busiest, so it is what a card opens on.
    assert pf.main_screen(dump) == "Strip"


def test_a_subpage_a_page_already_places_is_not_offered_twice():
    dump = panel(
        [obj(page="Body", joins=digital(1))],
        page_meta=[MAIN, subpage("Body", 1280, 680, 1)],
        references=[reference("Body", 0, 120, 1280, 680, None, 0)],
    )
    assert pf.candidate_screens(dump) == ["3.0-Main"]


def test_an_empty_subpage_is_not_offered():
    dump = panel(
        [],
        page_meta=[MAIN, subpage("Decor", 100, 100, 1)],
        references=[],
    )
    assert pf.candidate_screens(dump) == ["3.0-Main"]


def test_a_subpage_drawn_on_its_own_uses_its_own_size_and_origin():
    dump = panel(
        [obj(page="Strip", left=21, top=387, joins=digital(2))],
        page_meta=[subpage("Strip", 200, 640, 0)],
        references=[],
    )
    fp = pf.build_faceplate(dump, page="Strip")
    assert fp["size"] == [200, 640]
    region = next(r for r in fp["regions"] if r["kind"] == "button")
    # No reference places it, so nothing offsets it.
    assert (region["x"], region["y"]) == (21, 387)


# -- artwork and the colour behind it -----------------------------------

def test_a_buttons_artwork_travels_to_the_region():
    fp = pf.build_faceplate(panel([
        obj(joins=digital(101), artwork={
            "icon": "swf/images/airmedia.png",
            "image_on": "swf/images/BtnOnColour280x280.png",
        }),
    ]))
    region = next(r for r in fp["regions"] if r["kind"] == "button")
    assert region["icon"] == "swf/images/airmedia.png"
    assert region["src_on"] == "swf/images/BtnOnColour280x280.png"


def test_an_image_object_is_drawn_rather_than_skipped():
    # An unrecognised control with no label is normally dropped as noise.
    # A picture is the entire content of an Image Object.
    fp = pf.build_faceplate(panel([
        obj(control="Image Object", label="",
            artwork={"image": "swf/images/logo.png"}),
    ]))
    assert len(fp["regions"]) == 1
    assert fp["regions"][0]["src"] == "swf/images/logo.png"
    # No invented border around somebody's logo.
    assert "radius" not in fp["regions"][0]


def test_a_screen_that_paints_its_background_gets_a_backdrop():
    dump = panel(
        [obj(joins=digital(1))],
        page_meta=[{"key": "Main", "name": "Main", "kind": "page",
                    "width": 1280, "height": 800,
                    "background": "#ffffff", "opaque": True, "order": 0}],
    )
    fp = pf.build_faceplate(dump, page="Main")
    backdrop = fp["regions"][0]
    assert backdrop["id"] == "backdrop"
    assert backdrop["fill"] == "#ffffff"
    assert (backdrop["w"], backdrop["h"]) == (1280, 800)


def test_the_backdrop_is_drawn_before_everything_including_bigger_plates():
    dump = panel(
        [obj(control="Fill Border", left=0, top=0, width=1280, height=800)],
        page_meta=[{"key": "Main", "name": "Main", "kind": "page",
                    "width": 1280, "height": 800,
                    "background": "#ffffff", "opaque": True, "order": 0}],
    )
    fp = pf.build_faceplate(dump, page="Main")
    assert fp["regions"][0]["id"] == "backdrop"


def test_a_glass_subpage_paints_nothing():
    # Most subpages record a colour and never draw it. Painting it would
    # hide the page underneath.
    dump = panel(
        [obj(page="Sub", joins=digital(1))],
        page_meta=[MAIN, {"key": "Sub", "name": "Subpage", "kind": "subpage",
                          "width": 1280, "height": 580, "background": "#0071bc",
                          "opaque": False, "order": 1}],
        references=[reference("Body", 0, 120, 1280, 580, None, 0)],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    assert not [r for r in fp["regions"] if r["id"].startswith("backdrop")]


def test_an_opaque_popup_paints_where_it_is_placed_and_only_on_its_state():
    dump = panel(
        [obj(page="Sub", joins=digital(1))],
        page_meta=[MAIN, {"key": "Sub", "name": "Subpage", "kind": "subpage",
                          "width": 1280, "height": 580, "background": "#53514f",
                          "opaque": True, "order": 1}],
        references=[reference("3.5-Confirm", 0, 120, 1280, 580, 35, 0)],
    )
    fp = pf.build_faceplate(dump, page="3.0-Main")
    plate = next(r for r in fp["regions"] if r["id"].startswith("backdrop_"))
    assert (plate["x"], plate["y"]) == (0, 120)
    assert plate["fill"] == "#53514f"
    assert plate["page"] == "3.5-Confirm"


def test_a_pale_panel_reads_the_other_way_round():
    # A real panel is usually a pale screen with dark text, the opposite
    # of the dark instrument faces the hand-drawn faceplates emulate.
    light = panel([obj(joins=digital(1))], page_meta=[
        {"key": "Main", "name": "Main", "kind": "page", "width": 800,
         "height": 480, "background": "#ffffff", "opaque": True, "order": 0}])
    assert pf.build_faceplate(light, page="Main")["display"] == "positive"

    dark = panel([obj(joins=digital(1))], page_meta=[
        {"key": "Main", "name": "Main", "kind": "page", "width": 800,
         "height": 480, "background": "#0071bc", "opaque": True, "order": 0}])
    assert pf.build_faceplate(dark, page="Main")["display"] == "negative"


def test_crestron_blue_is_judged_dark():
    # A naive channel average calls #0071bc light and puts black text on
    # it. Luma weights green, which is what the eye does.
    assert pf._is_light("#0071bc") is False
    assert pf._is_light("#ffffff") is True
    assert pf._is_light("") is True
