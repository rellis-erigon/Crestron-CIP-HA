"""Finding the repeated blocks on a panel, so a card is a zone not 45 joins.

A panel read straight off the hardware gives a flat list of controls with
join numbers and nowhere to put them. What a person sees instead is
structure: a strip per zone, each with a level and a few buttons, repeated
across the page. Recovering that structure is what turns an import into
something worth putting on a dashboard.

The panel does not record the grouping. Objects on the one real panel
tested sit flat at the page root with no nesting to lean on, and their
names are VT Pro-e's own — `Advanced Button_17` — because nobody renamed
them. So the grouping has to be inferred, and there are two independent
signals that agree when a guess is right:

**Join arithmetic**, which turned out to be the one that works. Repeated
blocks get consecutive join ranges from whoever wrote the program:
211/212/213, then 221/222/223, then 231/232/233. Finding the stride finds
the blocks.

**Geometry**, which corroborates rather than leads. Clustering on
proximity was tried first and is a trap: "near" is transitive along a
strip, so on a dense page one chain swallows everything. On the panel
tested it produced a single block of thirty-five controls. It is kept to
confirm that joins the arithmetic grouped are also drawn together, which
is what distinguishes a real block from a coincidence of numbering.

Nothing here invents names. A proposal says "three blocks of four, stride
ten" and leaves naming to somebody who knows which zone is which, because
that is not in the file and guessing it would be worse than asking.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("crestron-cip.panel_groups")

# How close two controls must be to count as one block, as a fraction of
# the panel's smaller dimension. Proportional rather than a pixel count,
# so it holds on a 1280x800 panel and on a 480x272 one.
GAP_FRACTION = 0.06

# A block of one control is a control, not a block.
MIN_BLOCK = 2


def _boxes(controls: list[dict]) -> list[dict]:
    return [c for c in controls
            if c.get("left") is not None and c.get("top") is not None]


def _gap(panel: dict, controls: list[dict]) -> float:
    width, height = panel.get("size") or (None, None)
    if not width or not height:
        # No project size: fall back to the controls' own extent, which is
        # the best available and still proportional.
        xs = [c["left"] for c in controls] or [0]
        ys = [c["top"] for c in controls] or [0]
        width, height = max(xs) or 1, max(ys) or 1
    return max(8.0, min(width, height) * GAP_FRACTION)


def _near(a: dict, b: dict, gap: float) -> bool:
    """Whether two control boxes are close enough to be one block."""
    ax1, ay1 = a["left"], a["top"]
    ax2 = ax1 + (a.get("width") or 0)
    ay2 = ay1 + (a.get("height") or 0)
    bx1, by1 = b["left"], b["top"]
    bx2 = bx1 + (b.get("width") or 0)
    by2 = by1 + (b.get("height") or 0)
    dx = max(0, max(ax1 - bx2, bx1 - ax2))
    dy = max(0, max(ay1 - by2, by1 - ay2))
    return dx <= gap and dy <= gap


def cluster(panel: dict) -> list[list[dict]]:
    """Group controls into blocks by how they are drawn.

    Union-find over "is near", so a column of five controls joins up even
    though the first and last are far apart — proximity is transitive
    along a strip, which is exactly how a strip looks.
    """
    controls = _boxes(panel.get("controls") or [])
    if not controls:
        return []
    gap = _gap(panel, controls)
    parent = list(range(len(controls)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(controls)):
        for j in range(i + 1, len(controls)):
            if _near(controls[i], controls[j], gap):
                a, b = find(i), find(j)
                if a != b:
                    parent[a] = b

    groups: dict[int, list[dict]] = {}
    for index, control in enumerate(controls):
        groups.setdefault(find(index), []).append(control)
    return sorted(
        groups.values(),
        key=lambda g: (min(c["top"] for c in g), min(c["left"] for c in g)),
    )


def _join_set(block: list[dict], bus: str | None = None) -> list[int]:
    found = set()
    for control in block:
        for detail in control["joins"].values():
            if detail["reserved"]:
                continue
            if bus is None or detail["bus"] == bus:
                found.add(detail["join"])
    return sorted(found)


# Strides a programmer plausibly used to lay out repeated blocks. A decade
# per block is overwhelmingly the convention; the others are here because
# a site that used them should not be told it has no structure.
CANDIDATE_STRIDES = (10, 20, 25, 50, 100)

# A family needs at least this many repeats before it is worth proposing.
MIN_REPEATS = 2


def _bus_joins(panel: dict, bus: str) -> dict[int, list[dict]]:
    """Non-reserved joins on one bus, and which controls bind each."""
    found: dict[int, list[dict]] = {}
    for control in panel.get("controls") or []:
        for key, detail in control["joins"].items():
            if detail["reserved"] or detail["bus"] != bus:
                continue
            found.setdefault(detail["join"], []).append(
                {"name": control["name"], "control": control["control"],
                 "property": key, "page": control["page"],
                 "left": control.get("left"), "top": control.get("top")}
            )
    return found


def _families_for_stride(joins: set[int], stride: int) -> list[dict]:
    """Repeated offset patterns at a given stride.

    A block is a base (211 -> 210) plus the offsets within it ({1,2,3}).
    Two bases carrying the same offsets are the same block repeated.
    """
    bases: dict[int, set[int]] = {}
    for join in joins:
        bases.setdefault((join // stride) * stride, set()).add(join % stride)

    by_pattern: dict[tuple, list[int]] = {}
    for base, offsets in bases.items():
        if len(offsets) < 2:
            continue
        by_pattern.setdefault(tuple(sorted(offsets)), []).append(base)

    families = []
    for offsets, found in by_pattern.items():
        if len(found) < MIN_REPEATS:
            continue
        found.sort()
        steps = {b - a for a, b in zip(found, found[1:])}
        families.append({
            "stride": stride,
            "bases": found,
            "offsets": list(offsets),
            "count": len(found),
            "even_spacing": len(steps) == 1,
            "spacing": steps.pop() if len(steps) == 1 else None,
        })
    return families


def _drawn_together(members: list[dict], panel: dict) -> bool:
    """Whether a family's controls are also near each other on the page.

    Corroboration only. Joins numbered in a pattern but scattered across
    the panel are more likely a numbering habit than one control block.
    """
    width, height = panel.get("size") or (None, None)
    span = min(width or 0, height or 0) or 0
    if not span:
        return True
    spread = max(
        (max(m["top"] for m in members) - min(m["top"] for m in members))
        if all(m.get("top") is not None for m in members) else 0,
        (max(m["left"] for m in members) - min(m["left"] for m in members))
        if all(m.get("left") is not None for m in members) else 0,
    )
    return spread <= span


def propose(panel: dict) -> dict:
    """Repeated control blocks on the panel, strongest first.

    Nothing here invents names. A proposal says "three blocks of three on
    a stride of ten, starting at 211" and leaves naming to somebody who
    knows which zone is which, because that is not in the file.
    """
    out: list[dict] = []
    for bus in ("digital", "analog", "serial"):
        bound = _bus_joins(panel, bus)
        if len(bound) < MIN_REPEATS * 2:
            continue
        seen_bases: set[tuple] = set()
        for stride in CANDIDATE_STRIDES:
            for family in _families_for_stride(set(bound), stride):
                key = (bus, tuple(family["bases"]), tuple(family["offsets"]))
                if key in seen_bases:
                    continue
                seen_bases.add(key)
                blocks = []
                for base in family["bases"]:
                    joins = sorted(base + off for off in family["offsets"])
                    members = [m for j in joins for m in bound.get(j, [])]
                    blocks.append({
                        "base": base, "joins": joins,
                        "members": members,
                    })
                members_flat = [m for b in blocks for m in b["members"]]
                together = _drawn_together(members_flat, panel)
                out.append({
                    "bus": bus,
                    "stride": family["stride"],
                    "count": family["count"],
                    "per_block": len(family["offsets"]),
                    "offsets": family["offsets"],
                    "blocks": blocks,
                    "even_spacing": family["even_spacing"],
                    "drawn_together": together,
                    "confidence": (
                        "high" if family["even_spacing"] and together
                        else "medium" if family["even_spacing"] else "low"
                    ),
                })

    # Biggest, most regular families first: that is the order somebody
    # wants to name them in.
    order = {"high": 0, "medium": 1, "low": 2}
    out.sort(key=lambda f: (order[f["confidence"]], -f["count"], -f["per_block"]))

    claimed = {
        (f["bus"], j)
        for f in out if f["confidence"] != "low"
        for b in f["blocks"] for j in b["joins"]
    }
    loose = []
    for bus in ("digital", "analog", "serial"):
        for join, members in sorted(_bus_joins(panel, bus).items()):
            if (bus, join) not in claimed:
                loose.append({"bus": bus, "join": join, "members": members})
    return {"families": out, "unclaimed": loose}
