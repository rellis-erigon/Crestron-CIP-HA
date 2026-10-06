"""Read a Crestron panel project and turn it into a faceplate.

An XPanel is already the shape the cards here render: positioned objects
that each name a join. So rather than anyone drawing a mimic by hand,
the panel they already have becomes the card.

Three containers are accepted:

  .c3p / .vtz   a zip whose `swf/Environment.xml` holds the object tree,
                saved as UTF-16 by some VT Pro builds and UTF-8 by others
  .vtp          an OLE2 compound file; the tree is in a `Contents` stream
                in a length-prefixed binary form

Only the first is parsed into regions. The `.vtp` form is recognised and
reported, because a compiled `.c3p` of the same project is easy to
produce and parsing the binary form properly is a project of its own.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
import zipfile
from typing import Any

logger = logging.getLogger("crestron-cip.panel")

ENVIRONMENT = "swf/Environment.xml"

# A join of 0 means "not assigned". Crestron's reserved joins start high
# and drive the panel itself rather than the program, so they are not
# control points and are skipped.
RESERVED_FROM = 17000


class PanelError(Exception):
    """The file is not a panel project we can read."""


def _decode(raw: bytes) -> str:
    """Environment.xml is UTF-16 in some builds and UTF-8 in others."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    text = raw.decode("utf-8", "replace")
    return text.lstrip("﻿")


def _int(node: ET.Element, tag: str) -> int:
    value = (node.findtext(tag) or "").strip()
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return 0


def _position(node: ET.Element) -> tuple[int, int, int, int]:
    box = node.find(".//PositionAndSize")
    if box is None:
        return 0, 0, 0, 0
    return _int(box, "Left"), _int(box, "Top"), _int(box, "Width"), _int(box, "Height")


def _join(node: ET.Element, tag: str) -> int:
    """A join assigned directly on this object, not on a child."""
    for child in node:
        if child.tag == tag:
            try:
                return int((child.text or "0").strip())
            except ValueError:
                return 0
    found = node.find(f"./Properties/{tag}")
    if found is not None:
        try:
            return int((found.text or "0").strip())
        except ValueError:
            return 0
    return 0


def read_objects(data: bytes) -> list[dict]:
    """Every positioned object in the project, with its joins."""
    try:
        archive = zipfile.ZipFile_io = zipfile.ZipFile  # noqa: F841
    except Exception:  # pragma: no cover - zipfile is stdlib
        raise
    import io

    if data[:4] == b"\xd0\xcf\x11\xe0":
        raise PanelError(
            "this is a .vtp source project; export the panel as .c3p or "
            ".vtz and load that instead"
        )
    if data[:2] != b"PK":
        raise PanelError("not a panel project (expected a .c3p or .vtz)")

    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        names = archive.namelist()
        if ENVIRONMENT not in names:
            raise PanelError(f"no {ENVIRONMENT} in the archive")
        root = ET.fromstring(_decode(archive.read(ENVIRONMENT)))

    objects: list[dict] = []

    def walk(node: ET.Element, depth: int, parent: str,
             ox: int = 0, oy: int = 0) -> None:
        name = node.findtext("ObjectName")
        here, dx, dy = parent, ox, oy
        if name:
            left, top, width, height = _position(node)
            # A child is placed relative to whatever contains it, so the
            # offsets accumulate down the tree.
            dx, dy = ox + left, oy + top
            objects.append({
                "name": name,
                "parent": parent,
                "depth": depth,
                "x": dx, "y": dy, "w": width, "h": height,
                "text": (node.findtext("./Properties/Text") or "").strip(),
                "press": _join(node, "DigitalPressJoin"),
                "analog": _join(node, "AnalogFeedbackJoin"),
                "indirect": _join(node, "IndirectTextJoin"),
                "serial": _join(node, "SerialJoin"),
            })
            here = name
        for child in node:
            walk(child, depth + 1, here, dx, dy)

    walk(root, 0, "")
    return objects


def describe(data: bytes) -> dict:
    """What is in the file, for the import screen."""
    objects = read_objects(data)
    controls = [o for o in objects if o["w"] and o["h"]]
    joined = [o for o in controls
              if any(o[k] for k in ("press", "analog", "indirect", "serial"))]
    return {
        "objects": len(objects),
        "controls": len(controls),
        "with_joins": len(joined),
        "names": sorted({o["name"] for o in objects}),
    }


# -- turning a panel into a faceplate ------------------------------------

def _role(signal: str, join: int) -> str:
    """Roles are named after the join, matching the add-on's own keys, so
    the card's entity map is filled straight from the join table."""
    return f"{signal}{join}"


def to_faceplate(objects: list[dict], name: str = "Panel") -> dict:
    """A faceplate definition from a parsed panel.

    Rendered by `crestron-panel-card`, which takes the definition inline
    rather than from the bundled catalogue: a panel is one site's, and
    shipping it to everyone would be absurd.
    """
    page = next((o for o in objects if o["name"] in ("Page", "Project")), None)
    width = (page or {}).get("w") or 800
    height = (page or {}).get("h") or 600

    regions: list[dict] = []
    for obj in objects:
        if not obj["w"] or not obj["h"]:
            continue
        base = {"x": obj["x"], "y": obj["y"], "w": obj["w"], "h": obj["h"]}

        analog = obj["analog"]
        if analog and analog < RESERVED_FROM:
            # Taller than wide is a fader; anything else reads as a bar.
            vertical = obj["h"] >= obj["w"]
            regions.append({
                **base,
                "id": f"a{analog}",
                "role": _role("a", analog),
                "kind": "fader" if vertical else "bar",
                "action": "set_level",
                "target": _role("a", analog),
                "min": 0, "max": 65535,
            })
            continue

        press = obj["press"]
        if press and press < RESERVED_FROM:
            # A generic object name is not a caption. Where the button has
            # no literal text its label arrives on a serial join at
            # runtime, so a text region is laid over it rather than a
            # guess being baked in.
            literal = obj["text"]
            if not literal and not obj["indirect"]:
                literal = "" if obj["name"].startswith("Advanced Button") else obj["name"]
            regions.append({
                **base,
                "id": f"d{press}",
                "role": "",
                "kind": "button",
                "text": literal,
                "action": "press",
                "target": _role("d", press),
            })
            caption = obj["indirect"]
            if caption and caption < RESERVED_FROM:
                regions.append({
                    "id": f"d{press}cap",
                    "role": _role("s", caption),
                    "kind": "text",
                    "x": obj["x"], "y": obj["y"] + obj["h"] // 2 - 8,
                    "w": obj["w"], "align": "middle",
                    "size": max(10, min(15, obj["h"] - 20)),
                    "placeholder": "",
                })
            # The press join is usually its own feedback, so a lamp on the
            # same join shows what the panel shows.
            regions.append({
                "id": f"d{press}fb",
                "role": _role("d", press),
                "kind": "lamp",
                "x": obj["x"] + 4, "y": obj["y"] + 4, "w": 10,
                "on": "#3ddc84", "off": "#1b2a21",
            })
            continue

        text_join = obj["indirect"] or obj["serial"]
        if text_join and text_join < RESERVED_FROM:
            regions.append({
                **base,
                "id": f"s{text_join}",
                "role": _role("s", text_join),
                "kind": "text",
                "align": "middle",
                "size": max(11, min(20, obj["h"] - 8)),
                "placeholder": "",
            })

    return {
        "id": "imported-panel",
        "name": name,
        "card": "crestron-panel-card",
        "render": "svg",
        "display": "negative",
        "size": [width, height],
        "regions": regions,
    }
