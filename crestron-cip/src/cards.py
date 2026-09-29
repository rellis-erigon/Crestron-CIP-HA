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
