"""Working out what a panel's controls are actually connected to.

A generated faceplate knows a button is digital 213 and a bar is analog
211. That is enough to draw the panel and not enough to use it: the join
numbers mean nothing to anybody, and an analog join is usually feedback
only, so binding a fader to it gives a number that cannot be set.

The control program says what is really there. Three facts chain
together, and none of them needs anybody to type anything:

1. **Which room the panel is.** The program's device table maps each
   IP-ID to a model and the comment its programmer wrote — "Funct Rm 1
   TP". That is the only human name for a panel anywhere in the files.

2. **What the controls are.** Where audio is on a DSP driven by named
   control, the names survive compilation: `Func1MicGain`,
   `Func1ScrMute`. Those are what the panel's faders and mutes move.

3. **Which Home Assistant entity is which.** A bridge for that DSP has
   already made an entity per named control, and the names still match
   once punctuation and the role word are taken off.

What cannot be chained is which strip on the panel is which control,
because the panel project does not record it — the strip's label arrives
at run time over a serial join. They are matched in reading order,
preferring the panel's own room, which is right on every layout seen and
is a guess all the same. So every match carries its reasoning and the
import screen shows it, rather than quietly producing a card whose mute
button belongs to the room next door.
"""

from __future__ import annotations

import re
from typing import Any

# A volume strip on a Crestron panel is laid out one way almost
# everywhere: the level on an analog join, and three buttons on
# consecutive digitals from the same number — up, down, mute.
STRIP_OFFSETS = {"up": 0, "down": 1, "mute": 2}

# The word at the end of a named control says what it does. Gain and
# level are the same thing under two names.
ROLE_WORDS = {
    "gain": "level",
    "level": "level",
    "volume": "level",
    "vol": "level",
    "mute": "mute",
}

# Which Home Assistant domain each role should land in. A level has to be
# settable or the strip is an ornament.
DOMAIN_FOR_ROLE = {
    "level": ("number", "input_number"),
    "mute": ("switch", "input_boolean"),
}


def _normalise(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (text or "").lower())


def split_control(name: str) -> tuple[str, str]:
    """A named control split into what it belongs to and what it does.

    `Func1MicGain` is the gain of `Func1Mic`. The role word is taken off
    the end so the remainder can be matched against its siblings — a gain
    and a mute on the same thing share a base, and that is what makes
    them one strip.
    """
    lowered = _normalise(name)
    for word, role in ROLE_WORDS.items():
        if lowered.endswith(word):
            return lowered[: -len(word)], role
    return lowered, ""


def room_of(text: str) -> int | None:
    """The room number in a name, if it carries one.

    `Func1MicGain` is room 1 and `Funct Rm 2 TP` is room 2. Matched on
    the first run of digits, which is what these names are built from.
    """
    match = re.search(r"(\d+)", text or "")
    return int(match.group(1)) if match else None


# What a strip is for, read off the designer's own placeholder. The
# spelling is theirs: one real panel says "Srouce Name", which is why
# this matches on a fragment rather than a word.
KIND_PATTERNS = (
    ("mic", ("mic",)),
    ("source", ("sou", "srou", "scr", "src", "prog", "media")),
)


def classify(text: str) -> str:
    """Whether a label describes a microphone, a source, or neither."""
    lowered = (text or "").lower()
    for kind, fragments in KIND_PATTERNS:
        if any(fragment in lowered for fragment in fragments):
            return kind
    return ""


def find_strips(faceplate: dict[str, Any]) -> list[dict[str, Any]]:
    """The volume strips on a faceplate, in reading order.

    A strip is an analog level with digital buttons on the same number
    and the two after it. Anything without a level is not a strip — a
    lone pair of buttons is a source selector.
    """
    by_role = {r.get("role"): r for r in faceplate.get("regions", []) if r.get("role")}
    strips = []
    for region in faceplate.get("regions", []):
        if region.get("kind") not in ("bar", "fader"):
            continue
        match = re.fullmatch(r"(?:level|fader)_(\d+)", region.get("role", ""))
        if not match:
            continue
        base = int(match.group(1))
        strip: dict[str, Any] = {
            "base": base,
            "level_role": region["role"],
            "x": region.get("x", 0),
            "y": region.get("y", 0),
            "page": region.get("page"),
            "buttons": {},
            "label_role": "",
        }
        for action, offset in STRIP_OFFSETS.items():
            role = f"button_{base + offset}"
            if role in by_role:
                strip["buttons"][action] = role
        label = by_role.get(f"text_{base}")
        if label is not None:
            strip["label_role"] = f"text_{base}"
            # The live text arrives over a serial join at run time, so
            # the designer's placeholder is all there is at import.
            strip["kind"] = classify(label.get("hint") or label.get("text", ""))
        strip.setdefault("kind", "")
        strips.append(strip)
    # Reading order, which is the order the program declares its controls
    # in on every layout seen.
    strips.sort(key=lambda s: (s["x"], s["y"]))
    # One strip can appear on two states of the same screen; the first
    # occurrence is enough to bind it.
    seen, unique = set(), []
    for strip in strips:
        if strip["base"] in seen:
            continue
        seen.add(strip["base"])
        unique.append(strip)
    return unique


def panel_room(program: dict[str, Any], ipid: int | None) -> dict[str, Any]:
    """What the program calls the panel at this IP-ID."""
    for device in program.get("devices", []):
        if ipid is not None and device.get("ipid") == ipid:
            return {
                "ipid": device["ipid"],
                "name": device.get("comment") or device.get("model", ""),
                "room": room_of(device.get("comment", "")),
            }
    return {"ipid": ipid, "name": "", "room": None}


def control_groups(program: dict[str, Any]) -> list[dict[str, Any]]:
    """Named controls gathered into the things they belong to.

    A gain and a mute sharing a base are one control group, which is what
    a strip drives. Declaration order is kept, because that is the only
    ordering the program offers and strips are matched against it.
    """
    groups: dict[str, dict[str, Any]] = {}
    for name in program.get("named_controls", []):
        base, role = split_control(name)
        if not base or not role:
            continue
        group = groups.setdefault(base, {
            "base": base, "room": room_of(name), "controls": {},
            # What the control is for, from the part of its name that is
            # not the room number: `func1mic` is a microphone.
            "kind": classify(re.sub(r"\d+", " ", base)),
            "order": len(groups),
        })
        group["controls"].setdefault(role, name)
    # Only groups with a level are strips; a bare mute is something else.
    return [g for g in sorted(groups.values(), key=lambda g: g["order"])
            if "level" in g["controls"]]


def match_strips(
    strips: list[dict[str, Any]],
    groups: list[dict[str, Any]],
    room: int | None,
) -> list[dict[str, Any]]:
    """Pair each strip with the control group it most likely drives.

    Own room first, in declaration order, then everything else. A panel
    in room 1 shows its own source and microphone and then the other
    room's microphone, and that is the order its strips are in.
    """
    available = list(groups)

    def take(test) -> dict[str, Any] | None:
        for group in available:
            if test(group):
                available.remove(group)
                return group
        return None

    matched = []
    for strip in strips:
        kind = strip.get("kind")
        mine = room is not None
        # Best first: the panel's own room and the right sort of control.
        # A joined room shows one shared source and a microphone each, so
        # the third strip is the other room's microphone — not its
        # source, which matching on order alone would have picked.
        group, why = None, ""
        if kind and mine:
            group = take(lambda g: g["room"] == room and g["kind"] == kind)
            why = f"this panel, a {kind}"
        if group is None and kind:
            group = take(lambda g: g["kind"] == kind)
            why = f"another room, a {kind}"
        if group is None and mine:
            group = take(lambda g: g["room"] == room)
            why = "this panel, next in program order"
        if group is None:
            group = take(lambda g: True)
            why = "next in program order"
        matched.append({
            **strip,
            "group": group,
            "why": why if group else "no control left to match",
        })
    return matched


def resolve_entity(control: str, role: str, entities: list[str]) -> str:
    """The Home Assistant entity for a named control, or "".

    A bridge names its entities after the control, so `Func1MicGain`
    becomes something like `number.func1mic_volume`. The role word
    differs between the two — a DSP calls it gain, Home Assistant calls
    it volume — so the base is what is matched and the domain decides
    the rest.
    """
    base, _ = split_control(control)
    if not base:
        return ""
    allowed = DOMAIN_FOR_ROLE.get(role, ())
    best, best_score = "", 0
    for entity in entities:
        domain, _, object_id = entity.partition(".")
        if domain not in allowed:
            continue
        normalised = _normalise(object_id)
        if not normalised.startswith(base):
            continue
        # The shortest remainder is the closest name: `func1mic_volume`
        # beats `func1mic_volume_preset_2` for `Func1MicGain`.
        score = 1000 - len(normalised)
        if score > best_score:
            best, best_score = entity, score
    return best


def propose(
    faceplate: dict[str, Any],
    program: dict[str, Any] | None,
    entities: list[str],
    ipid: int | None = None,
) -> dict[str, Any]:
    """Everything that can be bound without anybody typing an entity id.

    Returns the role → entity map to put on the card, the region changes
    that make the strip's keys work, and a line of reasoning per strip so
    the import screen can show its work.
    """
    if not program:
        return {"entities": {}, "regions": [], "strips": [], "room": {}}

    room = panel_room(program, ipid)
    strips = find_strips(faceplate)
    matched = match_strips(strips, control_groups(program), room.get("room"))

    bindings: dict[str, str] = {}
    changes: list[dict[str, Any]] = []
    report: list[dict[str, Any]] = []

    for strip in matched:
        group = strip["group"]
        entry = {
            "base": strip["base"],
            "why": strip["why"],
            "control": group["base"] if group else "",
            "level": "",
            "mute": "",
        }
        if group:
            level_name = group["controls"].get("level", "")
            mute_name = group["controls"].get("mute", "")
            level = resolve_entity(level_name, "level", entities) if level_name else ""
            mute = resolve_entity(mute_name, "mute", entities) if mute_name else ""
            entry["level"] = level
            entry["mute"] = mute
            entry["level_control"] = level_name
            entry["mute_control"] = mute_name

            if level:
                bindings[strip["level_role"]] = level
                # The up and down keys step the thing the bar shows. On
                # the panel they drive the same control through the
                # program; here they drive it directly, because the
                # digital joins behind them are not exposed and need not
                # be.
                for action in ("up", "down"):
                    role = strip["buttons"].get(action)
                    if role:
                        changes.append({
                            "role": role,
                            "action": f"level_{action}",
                            "target": strip["level_role"],
                        })
            if mute and strip["buttons"].get("mute"):
                role = strip["buttons"]["mute"]
                bindings[role] = mute
                changes.append({
                    "role": role, "action": "mute_toggle", "target": role,
                })
        report.append(entry)

    return {
        "entities": bindings,
        "regions": changes,
        "strips": report,
        "room": room,
    }


def apply_changes(faceplate: dict[str, Any], changes: list[dict[str, Any]]) -> int:
    """Put the proposed actions onto the faceplate's regions."""
    by_role: dict[str, list[dict[str, Any]]] = {}
    for region in faceplate.get("regions", []):
        if region.get("role"):
            by_role.setdefault(region["role"], []).append(region)
    applied = 0
    for change in changes:
        for region in by_role.get(change["role"], []):
            region["action"] = change["action"]
            region["target"] = change["target"]
            applied += 1
    return applied
