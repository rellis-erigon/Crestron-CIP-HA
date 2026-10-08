"""Reading a Crestron panel's own project file.

A panel's compiled project is a zip holding `swf/Environment.xml`, and
that file describes every object on every page with the joins it binds.
Pulled off a running panel, it is the authoritative map of a system that
has no other documentation — far better than the alternative, which is
pressing buttons and watching which join moves.

The structure, worked out from a real panel:

    Crestron
      Properties
        Pages
          Page                    <- one per page and subpage
            Properties
              Children
                Child             <- an object
                  ObjectName      <- its name
                  ControlName     <- the kind of control it is
                  Properties
                    Left/Top/Width/Height
                    DigitalPressJoin / AnalogFeedbackJoin / ...
                    Children      <- nested objects, if any

Two traps worth naming, because both produce a confident wrong answer.

**An object's `Properties` contains its children's properties too.** Read
it whole and a button inherits the joins of everything inside it, so the
walk stops at a nested `Children`.

**An export is not a panel.** A `.vtz` or `.c3p` produced from an
unprogrammed project parses perfectly and yields almost nothing: its
`Pages` section holds *template* definitions for Page and Subpage whose
join groups are defaults. Two project files on one estate had 52 objects
each and every join at zero. So an empty result is reported as such
rather than as a failure, because the difference matters to whoever has
to go and find a better file.

The only trustworthy test for that is whether any control carries a
join. `PageCount` in `XPanel.ini` looked like a shortcut and is not one:
it reads 0 in every real panel dump taken off this estate's hardware,
including one with 27 digital joins. It is reported as information and
nothing is decided on it.
"""
from __future__ import annotations

import logging
import zipfile
import html
import re
from typing import Any

logger = logging.getLogger("crestron-cip.panel_reader")

ENVIRONMENT = "swf/Environment.xml"
XPANEL_INI = "XPanel.ini"

# Joins grouped by the bus they live on. The same number on two buses is
# two different signals — a zone's level is analog 211 while its volume-up
# button is digital 211 — so the bus has to travel with the number.
DIGITAL_JOINS = (
    "DigitalPressJoin", "DigitalJoin", "DigitalVisibilityJoin",
    "DigitalEnableJoin", "TransitionCompleteJoin", "HasFocusJoin",
    "EnterKeyPressJoin", "EscKeyPressJoin", "IsMovingJoin",
)
ANALOG_JOINS = (
    "AnalogFeedbackJoin", "AnalogModeJoin", "AnalogTouchJoin",
    "BackgroundAnalogJoin", "AnalogSelectJoin", "AnalogScrollJoin",
    "AnalogNumberOfItemsJoin", "AnalogTimeOffsetJoin",
)
SERIAL_JOINS = ("IndirectTextJoin", "SerialJoin", "SerialOutputJoin")

BUS_OF = {
    **{name: "digital" for name in DIGITAL_JOINS},
    **{name: "analog" for name in ANALOG_JOINS},
    **{name: "serial" for name in SERIAL_JOINS},
}

# Crestron reserves the top of the join space for the panel itself —
# brightness, standby, the setup page. They are real joins but they drive
# the panel rather than the program, so they are kept and flagged rather
# than offered as controls.
RESERVED_FROM = 17000

GEOMETRY = ("Left", "Top", "Width", "Height", "Z")

# Properties carrying the words printed on a control. VT Pro-e stores
# them as HTML with font markup, which is how the panel renders mixed
# sizes in one label.
TEXT_PROPERTIES = ("Label", "TextLabel", "Text")

_TAG_RE = re.compile(r"<[^>]+>")
# Crestron marks indirect text as <cips>JOIN?placeholder</cips>. The join is
# the serial join the processor writes the live words to; the placeholder is
# what the designer typed so the editor had something to show. A label can
# hold several, one per button state.
_CIPS_RE = re.compile(r"<cips>\s*(\d+)\s*\?(.*?)</cips>", re.I | re.S)
_FONT_SIZE_RE = re.compile(r'<FONT[^>]*\bsize="(\d+)"', re.I)
_FONT_COLOR_RE = re.compile(r'<FONT[^>]*\bcolor="(#[0-9A-Fa-f]{3,8})"', re.I)


def _words(markup: str) -> str:
    return " ".join(html.unescape(_TAG_RE.sub(" ", markup or "")).split())


def parse_label(markup: str) -> dict[str, Any]:
    """What a VT Pro-e label says, and how it says it.

    Labels are HTML because the panel renders mixed fonts and sizes in one
    string. Three things matter to a card: the words, the serial join the
    live words arrive on, and enough styling that a generated faceplate is
    recognisably the same screen.

    The placeholder inside a <cips> tag is design-time text, not a reading,
    so it is kept separately from static text — showing it as though it
    were live would be inventing a value.
    """
    markup = markup or ""
    indirect = [
        {"join": int(join), "placeholder": _words(text)}
        for join, text in _CIPS_RE.findall(markup)
    ]
    static = _words(_CIPS_RE.sub(" ", markup))
    # A multi-state button reads "Join <room> with <room>", its static words
    # wrapped around the indirect ones. Dropping either half loses the
    # sentence, so the display text keeps them in the order they were written.
    inline = _words(_CIPS_RE.sub(lambda m: f" {m.group(2)} ", markup))
    size = _FONT_SIZE_RE.search(markup)
    color = _FONT_COLOR_RE.search(markup)
    return {
        "text": inline,
        "static": static,
        "indirect": indirect,
        # VT Pro-e font sizes are tenths of a point on these panels: 56 is a
        # heading, 22 is body text.
        "size": int(size.group(1)) if size else None,
        "color": color.group(1) if color else None,
        "bold": "<B>" in markup.upper(),
    }


class PanelReadError(Exception):
    """The file is not a readable panel project."""


def _decode(raw: bytes) -> str:
    """Environment.xml is UTF-16 in some builds and UTF-8 in others."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("utf-16", "replace")


def _environment(data: bytes) -> tuple[str, dict[str, str]]:
    """The environment XML and whatever XPanel.ini says about the project."""
    import io

    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as err:
        raise PanelReadError(
            "not a zip, so not a panel project. A .vtp is the editable "
            "VT Pro-e project and needs the .vtp reader; a .vtx is a sound "
            "sidecar and holds nothing."
        ) from err

    names = {n.lower(): n for n in archive.namelist()}
    target = names.get(ENVIRONMENT.lower())
    if target is None:
        raise PanelReadError(
            f"no {ENVIRONMENT} in the archive, so it is not a panel project"
        )

    info: dict[str, str] = {}
    ini = names.get(XPANEL_INI.lower())
    if ini:
        for line in _decode(archive.read(ini)).splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                info[key.strip()] = value.strip()
    return _decode(archive.read(target)), info


def _own_properties(child: Any) -> dict[str, str]:
    """A `Child`'s own properties, not those of anything nested in it.

    Stopping at `Children` is the whole trick. Without it a container
    inherits every join inside it and reports itself as the control.
    """
    props = child.find("Properties")
    if props is None:
        return {}
    found: dict[str, str] = {}

    def scan(node: Any, depth: int) -> None:
        for item in node:
            if item.tag == "Children":
                continue
            if len(item) == 0:
                found.setdefault(item.tag, (item.text or "").strip())
            elif depth < 4:
                scan(item, depth + 1)

    scan(props, 0)
    return found


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    text = value.strip()
    if text.lstrip("-").isdigit():
        return int(text)
    return None


def read_panel(data: bytes) -> dict:
    """Every object on the panel, with its joins and where it sits.

    Objects that are byte-identical in position and joins are collapsed
    *within a page*: a panel draws the same control once per page state,
    and three copies of one button is a drawing detail rather than three
    controls. Collapsing across pages instead loses the control from every
    page but the first, which is how a whole page went missing — the main
    page's objects were all dropped as duplicates of a subpage's.
    """
    import xml.etree.ElementTree as ET

    text, info = _environment(data)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as err:
        raise PanelReadError(f"{ENVIRONMENT} will not parse: {err}") from err

    project = root.find("Properties")
    size = (
        _as_int(project.findtext("Width")) if project is not None else None,
        _as_int(project.findtext("Height")) if project is not None else None,
    )
    if size == (None, None):
        # The project element does not carry it on any real panel seen;
        # XPanel.ini does, and a faceplate cannot be laid out without it.
        size = (_as_int(info.get("width")), _as_int(info.get("height")))

    objects: list[dict] = []
    # Every subpage in a real export is called "Subpage" — VT Pro-e does not
    # require naming them and nobody does. Nine of them under one key put
    # every confirmation popup on top of the main screen, so a page that
    # repeats a name is numbered.
    page_names: dict[str, int] = {}
    # What each page is and how big, and where the main page places its
    # subpages. A panel's look lives in this: the main screen is a frame of
    # subpage references, and the controls themselves are defined in
    # separate, anonymous subpages whose coordinates start again at 0,0.
    page_meta: list[dict] = []
    references: list[dict] = []

    def visit(node: Any, page: str, path: str = "") -> None:
        # A *reference* is tagged <Subpage>; a subpage *definition* is a
        # <Page> whose ControlName is Subpage. Reading only <Child> misses
        # the references entirely, which leaves the main page looking empty.
        if node.tag == "Subpage":
            own = _own_properties(node)
            references.append({
                "page": page,
                "name": (node.findtext("ObjectName") or "").strip(),
                "left": _as_int(own.get("Left")),
                "top": _as_int(own.get("Top")),
                "width": _as_int(own.get("Width")),
                "height": _as_int(own.get("Height")),
                # The join that shows this subpage. 0 means always visible —
                # the top and bottom bars of a screen, typically.
                "join": _as_int(own.get("DigitalJoin")) or None,
                "order": len(references),
            })
        if node.tag == "Page":
            name = (node.findtext("ObjectName") or "").strip()
            if name:
                seen_before = page_names.get(name, 0)
                page_names[name] = seen_before + 1
                page = name if not seen_before else f"{name} {seen_before + 1}"
                # Subpages nest inside the page that shows them, so a page
                # whose children are all subpages contributes no objects of
                # its own. Keeping the chain is what makes a page picker
                # readable: "3.0-Main / Subpage 7" rather than "Subpage 7".
                path = f"{path} / {page}" if path else page
                own = _own_properties(node)
                page_meta.append({
                    "key": page,
                    "name": name,
                    "kind": "subpage"
                    if (node.findtext("ControlName") or "").strip() == "Subpage"
                    else "page",
                    "width": _as_int(own.get("Width")),
                    "height": _as_int(own.get("Height")),
                    "order": len(page_meta),
                })
        if node.tag == "Child":
            props = _own_properties(node)
            joins: dict[str, dict] = {}
            for key, raw in props.items():
                bus = BUS_OF.get(key)
                number = _as_int(raw)
                if bus is None or not number:
                    continue
                joins[key] = {
                    "bus": bus,
                    "join": number,
                    "reserved": number >= RESERVED_FROM,
                }
            geometry = {k.lower(): _as_int(props.get(k)) for k in GEOMETRY}
            label = {"text": "", "static": "", "indirect": [],
                     "size": None, "color": None, "bold": False}
            for key in TEXT_PROPERTIES:
                if props.get(key):
                    label = parse_label(props[key])
                    if label["text"] or label["indirect"]:
                        break
            if joins or any(v is not None for v in geometry.values()):
                objects.append({
                    "page": page,
                    "page_path": path or page,
                    "name": (node.findtext("ObjectName") or "").strip(),
                    "control": (node.findtext("ControlName") or "").strip(),
                    "label": label["text"],
                    "style": {k: label[k] for k in ("size", "color", "bold")},
                    # Serial joins the processor writes live text to. These
                    # are text joins, not the digital/analog ones in `joins`.
                    "text_joins": [i["join"] for i in label["indirect"]],
                    "align": props.get("TextAlignment", ""),
                    "joins": joins,
                    **geometry,
                })
        for item in node:
            visit(item, page, path)

    visit(root, "")

    deduped, seen = [], set()
    for obj in objects:
        fingerprint = (
            obj["page"],
            obj["left"], obj["top"], obj["width"], obj["height"],
            obj["control"], obj["label"],
            tuple(sorted((k, v["join"]) for k, v in obj["joins"].items())),
        )
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        deduped.append(obj)

    with_joins = [o for o in deduped if o["joins"]]
    return {
        "size": size,
        "project": info,
        "objects": deduped,
        "controls": with_joins,
        "pages": sorted({o["page"] for o in deduped if o["page"]}),
        "page_meta": page_meta,
        "references": references,
        "duplicates_collapsed": len(objects) - len(deduped),
    }


def summarise(panel: dict) -> dict:
    """What the file turned out to contain, in the terms a user needs.

    `unprogrammed` is the case worth calling out by name: the file is
    perfectly readable and has no joins in it, which means somebody needs
    to find a different file rather than debug this one. It is decided on
    the content — whether anything binds a join — because the header
    field that looked like a shortcut reads 0 on real panels too.
    """
    controls = panel["controls"]
    by_bus: dict[str, set[int]] = {"digital": set(), "analog": set(), "serial": set()}
    reserved = 0
    for obj in controls:
        for detail in obj["joins"].values():
            if detail["reserved"]:
                reserved += 1
            else:
                by_bus[detail["bus"]].add(detail["join"])
    # Information only. It reads 0 on real panels as well as on empty
    # exports, so it decides nothing.
    page_count = _as_int(panel["project"].get("PageCount"))
    return {
        "objects": len(panel["objects"]),
        "controls": len(controls),
        "digital_joins": len(by_bus["digital"]),
        "analog_joins": len(by_bus["analog"]),
        "serial_joins": len(by_bus["serial"]),
        "reserved_joins": reserved,
        "pages": len(panel["pages"]),
        "unprogrammed": bool(panel["objects"]) and not controls,
        "declared_page_count": page_count,
    }


def joins_of(panel: dict, include_reserved: bool = False) -> list[dict]:
    """Every distinct (bus, join) the panel binds, with what binds it."""
    found: dict[tuple[str, int], dict] = {}
    for obj in panel["controls"]:
        for key, detail in obj["joins"].items():
            if detail["reserved"] and not include_reserved:
                continue
            entry = found.setdefault(
                (detail["bus"], detail["join"]),
                {"bus": detail["bus"], "join": detail["join"],
                 "reserved": detail["reserved"], "bound_by": []},
            )
            entry["bound_by"].append({
                "name": obj["name"], "control": obj["control"],
                "property": key, "page": obj["page"],
            })
    return sorted(found.values(), key=lambda e: (e["bus"], e["join"]))
