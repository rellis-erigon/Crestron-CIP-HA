"""Reading a VT Pro-e `.vtp` project — the editable panel file.

A `.vtp` is an OLE2 compound file with one stream, `Contents`, holding an
MFC-serialised project. There is no published format, so this was worked
out from the bytes.

The structure that matters is regular. Every property is three
length-prefixed ASCII strings in a row — a display label, the value, and
the internal name — followed by fixed-width metadata this ignores:

    0b "Object Name"   13 "Multi-Mode Button_2"   0a "ObjectName"
    ^^ label                ^^ value                   ^^ internal name

So the file reads as a flat list of (internal name, value) pairs, and an
object is the run of properties between one `ObjectName` and the next.
That is enough to recover every object, its position, and the joins it
binds — which is the whole point, because a `.vtz` of the same project
is the *compiled* panel and a site does not always have one.

Two things learned from real files, both worth knowing before trusting
output:

**A project can carry no joins at all.** Two panels pulled off this
estate have 52 named objects each and every join property at 0, with
VT Pro-e's auto-generated names ("Multi-Mode Button_2") untouched. They
are unprogrammed starting points. That is the same reason the processors
report almost no joins over CIP: a default is never sent.

**`Environment.xml` in a `.vtz` is not the pages.** It is the project
environment — themes, hardkeys, and the *template* definitions for Page
and Subpage, whose join groups are defaults rather than a real instance.
A `.vtz` with `PageCount=0` in its `XPanel.ini` has no page content, so
reading one and finding little is not a parser failure.
"""
from __future__ import annotations

import logging
import re
import struct
from typing import Iterator

logger = logging.getLogger("crestron-cip.vtp")

OLE_SIGNATURE = bytes.fromhex("d0cf11e0a1b11ae1")
CONTENTS = "Contents"

# An internal property name is an identifier. Anything else at that
# position means the three strings were a coincidence rather than a
# record, which happens often in a megabyte of mixed binary.
NAME_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]*\Z")

# Values that mean "not set". A join of 0 is unassigned, and VT Pro-e
# writes booleans as words.
UNSET = frozenset({"", "0", "false", "False"})


class VtpError(Exception):
    """The file is not a readable .vtp."""


def is_vtp(data: bytes) -> bool:
    return data[:8] == OLE_SIGNATURE


# -- The OLE2 container -------------------------------------------------
#
# Only enough of it to pull one stream out. Bringing in a dependency for
# this would mean a new wheel in the add-on image for 60 lines of
# structure, and the format has not changed since 1995.


def _read_contents(data: bytes) -> bytes:
    """The `Contents` stream from an OLE2 compound file."""
    if not is_vtp(data):
        raise VtpError("not an OLE2 compound file, so not a .vtp project")

    sector_size = 1 << struct.unpack_from("<H", data, 30)[0]
    mini_size = 1 << struct.unpack_from("<H", data, 32)[0]
    dir_start = struct.unpack_from("<I", data, 48)[0]
    mini_cutoff = struct.unpack_from("<I", data, 56)[0]

    def sector(index: int) -> bytes:
        start = 512 + index * sector_size
        return data[start:start + sector_size]

    # The FAT's own sector list lives in the header, then continues in
    # DIFAT sectors. A 1.8 MB project needs only the header entries.
    fat_sectors = [
        struct.unpack_from("<I", data, 76 + 4 * i)[0] for i in range(109)
    ]
    fat: list[int] = []
    for fs in fat_sectors:
        if fs >= 0xFFFFFFFA:
            break
        raw = sector(fs)
        fat.extend(struct.unpack_from(f"<{len(raw) // 4}I", raw))

    def chain(start: int) -> Iterator[int]:
        seen = set()
        cur = start
        while cur < 0xFFFFFFFA and cur not in seen and cur < len(fat):
            seen.add(cur)
            yield cur
            cur = fat[cur]

    def read_chain(start: int, size: int | None = None) -> bytes:
        out = b"".join(sector(i) for i in chain(start))
        return out[:size] if size else out

    directory = read_chain(dir_start)
    entries = []
    for off in range(0, len(directory) - 127, 128):
        entry = directory[off:off + 128]
        name_len = struct.unpack_from("<H", entry, 64)[0]
        if not name_len:
            continue
        name = entry[:max(0, name_len - 2)].decode("utf-16-le", "replace")
        entries.append({
            "name": name,
            "type": entry[66],
            "start": struct.unpack_from("<I", entry, 116)[0],
            "size": struct.unpack_from("<Q", entry, 120)[0],
        })

    target = next((e for e in entries if e["name"] == CONTENTS), None)
    if target is None:
        found = ", ".join(e["name"] for e in entries[:6]) or "nothing"
        raise VtpError(
            f"no {CONTENTS} stream in the project (found {found}). This may "
            "be a .vtx sound sidecar or another VT Pro file rather than a "
            "panel project."
        )

    if target["size"] >= mini_cutoff:
        return read_chain(target["start"], target["size"])

    # Small streams live in the mini-FAT inside the first storage's chain.
    root = next(e for e in entries if e["type"] == 5)
    mini_fat_start = struct.unpack_from("<I", data, 60)[0]
    raw_mini_fat = read_chain(mini_fat_start)
    mini_fat = list(struct.unpack_from(
        f"<{len(raw_mini_fat) // 4}I", raw_mini_fat))
    pool = read_chain(root["start"])
    out, cur = b"", target["start"]
    seen: set[int] = set()
    while cur < 0xFFFFFFFA and cur not in seen:
        seen.add(cur)
        out += pool[cur * mini_size:(cur + 1) * mini_size]
        cur = mini_fat[cur] if cur < len(mini_fat) else 0xFFFFFFFE
    return out[:target["size"]]


# -- The property stream ------------------------------------------------


def _string_at(data: bytes, index: int) -> str | None:
    """A length-prefixed printable ASCII string, or None."""
    if index >= len(data):
        return None
    length = data[index]
    if length == 0 or length >= 0x80:
        return None
    segment = data[index + 1:index + 1 + length]
    if len(segment) != length:
        return None
    if not all(0x20 <= byte < 0x7F for byte in segment):
        return None
    return segment.decode("ascii")


def read_properties(contents: bytes) -> list[tuple[str, str, int]]:
    """Every (internal name, value, offset) in the stream, in order.

    The display label is read to find the record but thrown away: it is
    the words VT Pro-e shows in its property grid, and the internal name
    is what identifies the property.
    """
    out: list[tuple[str, str, int]] = []
    index, end = 0, len(contents) - 2
    while index < end:
        label = _string_at(contents, index)
        if label is None:
            index += 1
            continue
        after_label = index + 1 + len(label)
        value = _string_at(contents, after_label)
        if value is None:
            index = after_label
            continue
        after_value = after_label + 1 + len(value)
        name = _string_at(contents, after_value)
        if name is None or not NAME_RE.match(name):
            index = after_label
            continue
        out.append((name, value, index))
        index = after_value + 1 + len(name)
    return out


def objects_from_contents(contents: bytes) -> list[dict]:
    """The objects in an already-extracted `Contents` stream.

    Split from `read_objects` so the property walk can be tested on bytes
    directly. Hand-building a valid OLE2 container for a fixture is more
    error-prone than the code under test, and the container path is
    exercised against real project files instead.
    """
    properties = read_properties(contents)
    starts = [i for i, (name, value, _off) in enumerate(properties)
              if name == "ObjectName" and value.strip()]

    objects: list[dict] = []
    for position, start in enumerate(starts):
        stop = starts[position + 1] if position + 1 < len(starts) else len(properties)
        fields = {}
        for name, value, _off in properties[start:stop]:
            # First wins: a property repeats as label, value and schema
            # echo, and the first is the instance's own.
            fields.setdefault(name, value)
        objects.append({
            "name": properties[start][1],
            "properties": fields,
            "joins": {
                key: int(val) for key, val in fields.items()
                if "Join" in key and val not in UNSET and val.isdigit()
            },
        })
    return objects


def read_objects(data: bytes) -> list[dict]:
    """The panel's objects, read from a `.vtp` file."""
    return objects_from_contents(_read_contents(data))


def describe(data: bytes) -> dict:
    """A summary of what a project actually contains.

    Reports the count of objects carrying no join separately, because a
    project of unprogrammed objects is a real and confusing case: the
    file parses perfectly and yields nothing to bind.
    """
    objects = (objects_from_contents(data) if not is_vtp(data)
               else read_objects(data))
    with_joins = [o for o in objects if o["joins"]]
    every_join: set[int] = set()
    for obj in with_joins:
        every_join.update(obj["joins"].values())
    return {
        "objects": len(objects),
        "with_joins": len(with_joins),
        "without_joins": len(objects) - len(with_joins),
        "distinct_joins": len(every_join),
        "unprogrammed": bool(objects) and not with_joins,
    }
