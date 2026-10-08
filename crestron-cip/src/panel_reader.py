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
join groups are defaults, and `XPanel.ini` says `PageCount=0`. Two
panels' project files on one estate had 52 objects each and every join at
zero. So an empty result is reported as such rather than as a failure,
with the reason, because the difference matters to whoever has to go and
find a better file.
"""
from __future__ import annotations

import logging
import zipfile
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

GEOMETRY = ("Left", "Top", "Width", "Height")


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

    Objects that are byte-identical in position and joins are collapsed:
    a panel draws the same control once per page state, and three copies
    of one button is a drawing detail rather than three controls.
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

    objects: list[dict] = []

    def visit(node: Any, page: str) -> None:
        if node.tag == "Page":
            page = (node.findtext("ObjectName") or "").strip() or page
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
            if joins or any(v is not None for v in geometry.values()):
                objects.append({
                    "page": page,
                    "name": (node.findtext("ObjectName") or "").strip(),
                    "control": (node.findtext("ControlName") or "").strip(),
                    "joins": joins,
                    **geometry,
                })
        for item in node:
            visit(item, page)

    visit(root, "")

    deduped, seen = [], set()
    for obj in objects:
        fingerprint = (
            obj["left"], obj["top"], obj["width"], obj["height"],
            obj["control"],
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
        "duplicates_collapsed": len(objects) - len(deduped),
    }


def summarise(panel: dict) -> dict:
    """What the file turned out to contain, in the terms a user needs.

    `unprogrammed` is the case worth calling out by name: the file is
    perfectly readable and has no joins in it, which means somebody needs
    to find a different file rather than debug this one.
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
        "looks_like_an_empty_export": page_count == 0,
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
