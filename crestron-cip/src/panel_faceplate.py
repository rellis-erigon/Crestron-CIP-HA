"""Turning a real Crestron panel into a Home Assistant card.

`panel_reader` says what is on a panel: every control, where it sits, what
it says and which joins it binds. A faceplate says what a card draws:
regions in a coordinate space, each naming a *role* rather than an entity.
The two are the same shape, so the translation is mechanical — which is the
point. Nobody should have to lay out a card by hand to get back a screen
that already exists.

What this does **not** do is invent. A control whose purpose cannot be read
off the project file becomes a plate — visible, in the right place, doing
nothing — rather than a guess at a control. A panel is a safety-adjacent
interface; a button that does something other than what it says is worse
than a button that does nothing.

Three things make the result recognisable rather than merely correct:

* **Position is kept verbatim.** Panel pixels become faceplate units one
  for one, so the proportions an operator knows are preserved.
* **Plates are drawn first.** Regions paint in list order, so the panel's
  borders and fills have to be emitted before the text that sits on them.
* **Colour comes from the project.** A generated faceplate is the one place
  the engine honours explicit colour, because on a panel the colour *is*
  the label — people find the red button before they read it.
"""

from __future__ import annotations

import re
from typing import Any

# How a VT Pro-e control maps onto the faceplate engine's region kinds.
# Anything absent from here becomes a plate: present and inert.
CONTROL_KINDS: dict[str, str] = {
    "Advanced Button": "button",
    "Button": "button",
    "Liquid Gauge Vertical": "bar",
    "Liquid Gauge Horizontal": "bar",
    "Fader Slider Vertical": "fader",
    "Fader Slider Horizontal": "fader",
    "Formatted Text": "text",
    "Digital Date Time": "text",
    "Text": "text",
}

# Controls that are frame rather than function. Named so that a control
# missing from CONTROL_KINDS is distinguishable from one deliberately flat.
CHROME_CONTROLS = {"Border", "Fill Border", "Image Object", "Rectangle"}

# Panel font sizes are roughly twice the SVG size that reads the same on
# screen, because VT Pro-e measures them against a 1280-wide panel while
# the card is scaled to fit a dashboard column.
FONT_SCALE = 0.5

# Below this a control is a separator or a hairline, not something a
# faceplate should carry as its own region.
MIN_SIDE = 6


def _role(prefix: str, number: int | None, index: int) -> str:
    """A stable role name.

    Roles key the card's entity bindings, so they have to survive a
    re-import of the same panel. A join number is stable; a position is
    not, which is why an unjoined control falls back to its index rather
    than its coordinates.
    """
    return f"{prefix}_{number}" if number is not None else f"{prefix}_x{index}"


# Join properties in the order they should be preferred. A button's press
# join is what drives the system; its visibility join only says when the
# button is on screen, and binding an entity to that would be nonsense.
PREFERRED = {
    "digital": ("DigitalPressJoin", "DigitalJoin"),
    "analog": ("AnalogFeedbackJoin", "AnalogJoin", "AnalogTouchJoin"),
}


def _first_join(obj: dict[str, Any], bus: str) -> int | None:
    """The join a control is driven by, ignoring reserved ones.

    Joins from 17000 up are the panel's own — page flips, popup
    housekeeping — and never appear in the control program. A card cannot
    drive them and should not offer to, so a control whose only join is
    reserved reads here as having none.
    """
    joins = obj.get("joins") or {}
    for prop in PREFERRED.get(bus, ()):
        entry = joins.get(prop)
        if entry and entry.get("bus") == bus and not entry.get("reserved"):
            return entry.get("join")
    for entry in joins.values():
        if entry.get("bus") == bus and not entry.get("reserved"):
            return entry.get("join")
    return None


def _only_reserved(obj: dict[str, Any]) -> bool:
    joins = (obj.get("joins") or {}).values()
    return bool(joins) and all(e.get("reserved") for e in joins)


def _label_of(obj: dict[str, Any]) -> str:
    text = (obj.get("label") or "").strip()
    # Placeholder names a designer left behind are not labels. "Header
    # Text" and "Room Name" are prompts to themselves, and showing them on
    # a card reads as a value that failed to load.
    if re.fullmatch(r"(header|button|zone|room|source|mic)\s*(text|name)?", text, re.I):
        return ""
    return text


def _style_of(obj: dict[str, Any]) -> dict[str, Any]:
    style = obj.get("style") or {}
    out: dict[str, Any] = {}
    size = style.get("size")
    if size:
        out["size"] = max(8, round(size * FONT_SCALE))
    # Near-black is the engine's own default ink; carrying it explicitly
    # would override the theme and break dark mode for no gain.
    color = style.get("color")
    if color and color.lower() not in {"#000000", "#000"}:
        out["color"] = color
    return out


def region_for(obj: dict[str, Any], index: int) -> dict[str, Any] | None:
    """One control as one region, or None if it should not be drawn."""
    left, top = obj.get("left"), obj.get("top")
    width, height = obj.get("width") or 0, obj.get("height") or 0
    if left is None or top is None:
        return None
    if width < MIN_SIDE or height < MIN_SIDE:
        return None

    control = obj.get("control") or ""
    kind = CONTROL_KINDS.get(control)
    label = _label_of(obj)
    text_joins = obj.get("text_joins") or []

    base: dict[str, Any] = {
        "id": f"r{index}",
        "kind": kind or "plate",
        "x": left,
        "y": top,
        "w": width,
        "h": height,
        "role": "",
    }
    if obj.get("page"):
        base["page"] = obj["page"]

    if kind == "button":
        press = _first_join(obj, "digital")
        if press is None and _only_reserved(obj):
            # A button wired only to a reserved join flips a page on the
            # panel itself. Drawing it as a button would offer a press that
            # can never arrive anywhere, so it becomes inert chrome with
            # its label intact — the layout still reads correctly.
            base.update(kind="plate", text=label, radius=6,
                        border="currentColor", **_style_of(obj))
            return base
        base["role"] = _role("button", press, index)
        # A panel button is momentary. Modelling it as a toggle would
        # invert its meaning the moment someone held it.
        base["action"] = "press"
        base["target"] = base["role"]
        base["text"] = label
        base["radius"] = 6
        base.update(_style_of(obj))
        return base

    if kind == "bar":
        base["role"] = _role("level", _first_join(obj, "analog"), index)
        base["max"] = 65535  # Crestron analog joins are 16-bit.
        return base

    if kind == "fader":
        base["role"] = _role("fader", _first_join(obj, "analog"), index)
        base["max"] = 65535
        base["ticks"] = 0
        return base

    if kind == "text":
        # Indirect text is the live value; static text is a caption. A
        # control with both is captioned, and the caption is what a reader
        # needs when the value has not arrived yet.
        join = text_joins[0] if text_joins else None
        base["role"] = _role("text", join, index) if join is not None else ""
        base["text"] = label
        base["align"] = {
            "Center": "middle", "Right": "end", "Left": "start",
        }.get(obj.get("align") or "", "start")
        if base["align"] == "middle":
            pass  # x stays the box's left edge; textRegion offsets by w/2.
        base["placeholder"] = ""
        base.update(_style_of(obj))
        return base

    # Chrome, and anything unrecognised. A plate with neither fill nor
    # label draws an empty box, which is noise — skip it.
    base["kind"] = "plate"
    base["text"] = label
    base["radius"] = 4
    base.update(_style_of(obj))
    if control in CHROME_CONTROLS:
        base["border"] = "currentColor"
    elif not label:
        return None
    return base


def _area(region: dict[str, Any]) -> int:
    return (region.get("w") or 0) * (region.get("h") or 0)


def build_faceplate(
    panel: dict[str, Any], *, page: str | None = None, name: str = "",
) -> dict[str, Any]:
    """A faceplate from a panel dump.

    `page` picks which of the panel's screens to draw; without it the
    busiest one is used, which is the one an operator thinks of as "the"
    screen. The screen is composed first — subpages offset to where their
    references place them — because a faceplate built from raw objects
    stacks every subpage in the top-left corner.
    """
    page = page or main_screen(panel)
    screen = compose_screen(panel, page) if page else {
        "size": panel.get("size"), "objects": panel.get("objects", []),
        "states": [], "ambiguous": [], "unresolved": [], "orphans": [],
    }
    width, height = screen["size"] or (None, None)

    regions: list[dict[str, Any]] = []
    for index, obj in enumerate(screen["objects"]):
        region = region_for(obj, index)
        if region is None:
            continue
        # A gated subpage is a state of the screen, not a page of its own
        # in the panel's sense. Regions with no state are always drawn.
        state = obj.get("state")
        if state:
            region["page"] = state
            # Everything from one subpage lives and dies together — hiding
            # half a popup leaves the other half floating.
            region["group"] = state
        else:
            region.pop("page", None)
        regions.append(region)

    # Plates first, largest first within them: a region paints over
    # whatever is already in the list, so background has to go down before
    # foreground or the panel's own fills hide its values.
    regions.sort(key=lambda r: (r["kind"] != "plate", -_area(r)))

    if not width or not height:
        width = max((r["x"] + (r["w"] or 0) for r in regions), default=1280)
        height = max((r["y"] + (r["h"] or 0) for r in regions), default=800)

    faceplate: dict[str, Any] = {
        "id": re.sub(r"[^a-z0-9]+", "-", (name or "panel").lower()).strip("-"),
        "name": name or "Imported panel",
        "card": "crestron-panel-card",
        "render": "svg",
        "size": [width, height],
        "regions": regions,
        "description": (
            f"Generated from a Crestron panel project: page {page}, "
            f"{len(regions)} regions, {len(screen['states'])} state(s)."
        ),
    }
    if screen["states"]:
        faceplate["pages"] = screen["states"]
    # Carried so the import screen can be honest about what was guessed.
    faceplate["import_notes"] = {
        "page": page,
        "ambiguous_references": screen["ambiguous"],
        "unresolved_references": screen["unresolved"],
        "unplaced_subpages": screen["orphans"],
    }
    return faceplate


def summarise_faceplate(faceplate: dict[str, Any]) -> dict[str, Any]:
    """What was made, for the import screen to show before anyone commits."""
    kinds: dict[str, int] = {}
    for region in faceplate["regions"]:
        kinds[region["kind"]] = kinds.get(region["kind"], 0) + 1
    roles = [r["role"] for r in faceplate["regions"] if r.get("role")]
    return {
        "size": faceplate["size"],
        "regions": len(faceplate["regions"]),
        "kinds": dict(sorted(kinds.items(), key=lambda kv: -kv[1])),
        "roles": len(roles),
        "bindable": sorted(set(roles)),
    }

def resolve_references(panel: dict[str, Any]) -> dict[str, Any]:
    """Which subpage definition each reference on a page points at.

    The project file does not say. A reference carries a name, a rectangle
    and a visibility join; a subpage definition carries only its own size,
    and every one of them is called "Subpage" because VT Pro-e never made
    anyone name them. Size is the only link there is.

    It is nearly enough. On a real panel six of eight references match a
    definition of a unique size. Where several share one — two confirmation
    popups both filling the screen — they are paired in document order,
    which is the order the designer created them in and therefore the order
    the references were added. That is an assumption, so it is reported:
    `ambiguous` names every reference resolved only by position in a tie.
    """
    by_size: dict[tuple[int, int], list[dict]] = {}
    for meta in panel.get("page_meta", []):
        if meta["kind"] != "subpage":
            continue
        by_size.setdefault((meta["width"], meta["height"]), []).append(meta)

    resolved: dict[str, str] = {}
    ambiguous: list[str] = []
    unresolved: list[str] = []

    # Grouped per owning page, because the same subpage is legitimately
    # referenced from several pages — a top bar belongs to all of them.
    per_page: dict[str, dict[tuple[int, int], list[dict]]] = {}
    for ref in panel.get("references", []):
        if ref["width"] is None or ref["height"] is None:
            continue  # A reference with no rectangle places nothing.
        per_page.setdefault(ref["page"], {}).setdefault(
            (ref["width"], ref["height"]), []
        ).append(ref)

    for page, sizes in per_page.items():
        for size, refs in sizes.items():
            candidates = by_size.get(size, [])
            refs = sorted(refs, key=lambda r: r["order"])
            for index, ref in enumerate(refs):
                key = f"{page}\u0000{ref['order']}"
                if index < len(candidates):
                    resolved[key] = candidates[index]["key"]
                    if len(refs) > 1 and len(candidates) > 1:
                        ambiguous.append(ref["name"] or key)
                else:
                    unresolved.append(ref["name"] or key)

    return {"resolved": resolved, "ambiguous": ambiguous, "unresolved": unresolved}


def compose_screen(panel: dict[str, Any], page: str) -> dict[str, Any]:
    """One page of the panel, flattened into absolute coordinates.

    A subpage's controls start again at 0,0, so they have to be offset by
    where the reference places them. Without this every subpage lands in
    the top-left corner on top of every other, which is what made the
    first generated faceplate an unreadable pile.

    Each gated subpage becomes a named state. The references with no
    visibility join are always on screen and belong to no state, which is
    how the engine already models always-on chrome.
    """
    links = resolve_references(panel)
    objects_by_page: dict[str, list[dict]] = {}
    for obj in panel.get("objects", []):
        objects_by_page.setdefault(obj["page"], []).append(obj)

    placed: list[dict[str, Any]] = []
    states: list[str] = []

    # The page's own controls sit directly on it, already absolute.
    for obj in objects_by_page.get(page, []):
        placed.append({**obj, "state": None, "source": page})

    for ref in sorted(
        (r for r in panel.get("references", []) if r["page"] == page),
        key=lambda r: r["order"],
    ):
        if ref["width"] is None:
            continue
        target = links["resolved"].get(f"{page}\u0000{ref['order']}")
        if target is None:
            continue
        state = ref["name"] if ref["join"] else None
        if state and state not in states:
            states.append(state)
        for obj in objects_by_page.get(target, []):
            placed.append({
                **obj,
                "left": (obj["left"] or 0) + (ref["left"] or 0),
                "top": (obj["top"] or 0) + (ref["top"] or 0),
                "state": state,
                "source": target,
                "visible_join": ref["join"],
            })

    meta = next((m for m in panel.get("page_meta", []) if m["key"] == page), None)
    size = (
        (meta["width"], meta["height"]) if meta and meta["width"] else panel.get("size")
    )
    # Subpages nothing places. A Subpage Reference List instantiates its
    # subpage dynamically — one per zone, tiled along the list — and the
    # project file records the list, not the instances. Guessing how many
    # there are and where they sit would invent a layout, so they are
    # reported for the import screen to ask about.
    claimed = set(links["resolved"].values()) | {page}
    orphans = [
        meta["key"]
        for meta in panel.get("page_meta", [])
        if meta["kind"] == "subpage"
        and meta["key"] not in claimed
        and any(
            o.get("text_joins")
            or any(not e.get("reserved") for e in (o.get("joins") or {}).values())
            for o in objects_by_page.get(meta["key"], [])
        )
    ]

    return {
        "page": page,
        "size": size,
        "objects": placed,
        "states": states,
        "ambiguous": links["ambiguous"],
        "unresolved": links["unresolved"],
        "orphans": orphans,
    }


def _binds_anything(objects: list[dict[str, Any]]) -> int:
    return sum(
        1 for o in objects
        if o.get("text_joins")
        or any(not e.get("reserved") for e in (o.get("joins") or {}).values())
    )


def candidate_screens(panel: dict[str, Any]) -> list[str]:
    """Every screen a card could be built from.

    The panel's real pages, plus any subpage no page places. An unplaced
    subpage is not a mistake: a Subpage Reference List instantiates one per
    zone, so the subpage is the zone strip itself and drawing it at its own
    origin is exactly right. On a small panel it is the only thing worth
    drawing — the pages hold nothing but a top bar.
    """
    placed = set(resolve_references(panel)["resolved"].values())
    objects_by_page: dict[str, list[dict]] = {}
    for obj in panel.get("objects", []):
        objects_by_page.setdefault(obj["page"], []).append(obj)

    screens = []
    for meta in panel.get("page_meta", []):
        if meta["kind"] == "page":
            screens.append(meta["key"])
        elif meta["key"] not in placed and _binds_anything(
            objects_by_page.get(meta["key"], [])
        ):
            screens.append(meta["key"])
    return screens


def main_screen(panel: dict[str, Any]) -> str | None:
    """The screen a card should default to.

    The busiest one, counting the controls that actually bind a join after
    composition. An init page has a handful and a splash screen none, so
    counting references or objects alone picks the wrong one.
    """
    best, best_score = None, -1
    for key in candidate_screens(panel):
        score = _binds_anything(compose_screen(panel, key)["objects"])
        if score > best_score:
            best, best_score = key, score
    return best
