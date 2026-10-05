"""Build a room card from the joins on a processor.

CIP has no notion of a room, a device or a group — a processor exposes
numbered digital, analog and serial joins, and what any of them means is
whatever the programmer decided. The only description of intent in the
system is the name the user typed into this add-on when exposing a join.

So that is what this matches on. It is a guess, and it says so: every
role comes back with the join it chose, and anything it could not place
is listed rather than silently dropped.
"""
from __future__ import annotations

import re
from typing import Any, Iterable

CARD_TYPE = "custom:room-controller-card"
FACEPLATE = "av-room-controller"

# Ordered: the first pattern that matches a join name wins the role, and a
# join is only used once. "display mute" should be a mute, not a display,
# so the narrower roles are tried before the broader ones.
ROLE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("mute", re.compile(r"\bmute\b", re.I)),
    ("mic_live", re.compile(r"\bmic(rophone)?\b", re.I)),
    ("fault", re.compile(r"\b(fault|alarm|error)\b", re.I)),
    ("online", re.compile(r"\b(online|connected|comms)\b", re.I)),
    ("volume", re.compile(r"\b(volume|level|gain)\b", re.I)),
    ("source", re.compile(r"\b(source|input|route|selected)\b", re.I)),
    ("display_power", re.compile(
        r"\b(display|projector|screen|tv|power)\b", re.I)),
]

ROLES = [role for role, _ in ROLE_PATTERNS]


def _name_of(join: Any) -> str:
    return (join.config.name or "").strip() or f"{join.signal} {join.number}"


def match_roles(joins: Iterable[Any]) -> tuple[dict[str, Any], list[Any]]:
    """Assign exposed joins to roles by name. Returns (roles, leftovers)."""
    exposed = [j for j in joins if j.config.enabled]
    chosen: dict[str, Any] = {}
    taken: set[str] = set()

    for role, pattern in ROLE_PATTERNS:
        for join in exposed:
            if join.store_key in taken:
                continue
            if pattern.search(_name_of(join)):
                chosen[role] = join
                taken.add(join.store_key)
                break

    leftovers = [j for j in exposed if j.store_key not in taken]
    return chosen, leftovers


def room_card(joins: Iterable[Any], title: str = "") -> dict | None:
    """The card for a processor's joins, or None if nothing matched."""
    chosen, _ = match_roles(joins)
    if not chosen:
        return None
    card: dict[str, Any] = {
        "type": CARD_TYPE,
        "faceplate": FACEPLATE,
        "entities": {role: join.key for role, join in chosen.items()},
    }
    if title:
        card["name"] = title
    return card


def describe(joins: Iterable[Any]) -> dict:
    """What matched what, so a guess can be checked before it is trusted."""
    chosen, leftovers = match_roles(joins)
    return {
        "matched": {
            role: {"key": join.key, "name": _name_of(join),
                   "signal": join.signal, "number": join.number}
            for role, join in chosen.items()
        },
        "unmatched_roles": [r for r in ROLES if r not in chosen],
        "unused_joins": [
            {"key": j.key, "name": _name_of(j)} for j in leftovers
        ],
    }


# -- Zone mixer ----------------------------------------------------------

MIXER_CARD = "custom:audio-zone-card"
MIXER_FACEPLATE = "zone-mixer"
# The faceplate wraps past eight strips a row, so sixteen stays legible.
MAX_STRIPS = 16

def zone_strips(joins: Iterable[Any], stride: int = 10,
                start: int = 11) -> list[dict]:
    """The zones a processor exposes, derived from its join layout.

    Item N occupies `start + (N-1)*stride` upward: the first serial is
    the zone's label, the first analog its volume, the first digital its
    mute, and the next three digitals the source selects.
    """
    by_number: dict[tuple[str, int], Any] = {
        (j.signal, j.number): j for j in joins if j.config.enabled
    }
    bases = sorted({
        number for signal, number in by_number
        if number >= start and (number - start) % stride == 0
    })

    strips = []
    for base in bases:
        label = by_number.get(("s", base))
        volume = by_number.get(("a", base))
        if label is None and volume is None:
            continue
        strips.append({
            "base": base,
            "name": str(getattr(label, "value", "") or "").strip()
                    or f"Zone {1 + (base - start) // stride}",
            "volume": getattr(volume, "key", None),
            "mute": getattr(by_number.get(("d", base)), "key", None),
            "sources": [
                getattr(by_number.get(("d", base + n)), "key", None)
                for n in (1, 2, 3)
            ],
        })
    return strips


def mixer_card(joins: Iterable[Any], title: str = "",
               stride: int = 10, start: int = 11) -> tuple[dict | None, list[str]]:
    """A zone-mixer card for a processor's zones, and what was left out."""
    strips = zone_strips(joins, stride=stride, start=start)
    usable = [s for s in strips if s["volume"]]
    omitted = [s["name"] for s in strips if not s["volume"]]
    chosen, over = usable[:MAX_STRIPS], usable[MAX_STRIPS:]
    omitted += [s["name"] for s in over]
    if not chosen:
        return None, omitted

    entities: dict[str, str] = {}
    labels: dict[str, str] = {}
    for index, strip in enumerate(chosen, start=1):
        labels[f"zone{index}"] = strip["name"]
        entities[f"zone{index}_volume"] = strip["volume"]
        if strip["mute"]:
            entities[f"zone{index}_mute"] = strip["mute"]

    card = {
        "type": MIXER_CARD,
        "faceplate": MIXER_FACEPLATE,
        "title": title or "Zone Mixer",
        "options": {"zones": len(chosen), "eq": 0},
        "labels": labels,
        "entities": entities,
    }
    return card, omitted
