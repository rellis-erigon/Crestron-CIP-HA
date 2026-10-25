"""A whole panel project, every screen of it, ready for crestron-panel-card.

`faceplate.build_faceplate` draws one screen. A touch panel is several —
an init page, a start page, the main page — and which one is showing is
the processor's decision, made by pulsing the page's own join. So a panel
is imported as all of its screens plus the join behind each, and the card
follows whichever join the processor pulsed last.

Nothing here touches Home Assistant or the filesystem except
`write_panel`, which is handed its target directory.
"""
from __future__ import annotations

import io
import json
import re
import zipfile
from pathlib import Path, PurePosixPath
from typing import Any

from .faceplate import build_faceplate, candidate_screens, main_screen
from .reader import RESERVED_FROM, read_panel

FORMAT = "crestron-panel"
ART_KEYS = ("src", "src_on", "icon")


class PanelImportError(Exception):
    """The file is not a panel project this can draw."""


def slug(name: str) -> str:
    """A folder name safe to write and to put in a URL."""
    text = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return text or "panel"


def _safe_file(path: str) -> str:
    # Only the base name is kept: a zip entry can say `../../x`, and the
    # archive is not ours. Spaces would need encoding in every href.
    base = PurePosixPath(path).name
    keep = "".join(c if (c.isalnum() or c in "._-") else "_" for c in base)
    return keep.lstrip(".") or "image"


def build_panel(data: bytes, name: str) -> dict[str, Any]:
    """Every screen of a panel project, and the join that shows each.

    Raises PanelImportError when the file holds nothing to draw: a project
    exported before it was programmed parses perfectly and binds no joins,
    and a card built from it would be a blank frame.
    """
    try:
        panel = read_panel(data)
    except Exception as err:  # the reader names what was wrong
        raise PanelImportError(str(err)) from err
    if not panel["controls"]:
        raise PanelImportError(
            "no control in this project binds a join; it may be an export of "
            "an unprogrammed project rather than the panel's own file")

    screens: dict[str, dict] = {}
    for key in candidate_screens(panel):
        faceplate = build_faceplate(panel, page=key, name=f"{name} {key}")
        if faceplate["regions"]:
            screens[key] = faceplate
    if not screens:
        raise PanelImportError("no screen in this project draws anything")

    page_joins = {
        meta["key"]: meta["join"]
        for meta in panel["page_meta"]
        if meta["kind"] == "page" and meta.get("join")
        and meta["join"] < RESERVED_FROM and meta["key"] in screens
    }
    fallback = main_screen(panel)
    return {
        "format": FORMAT,
        "version": 1,
        "name": name,
        "size": list(panel["size"]),
        "screens": screens,
        # screen -> the digital join the processor pulses to show it.
        "page_joins": page_joins,
        # What to draw before the processor has flipped a page in front of
        # us. The busiest screen, because a blank init page reads as broken.
        "default_screen": fallback if fallback in screens else next(iter(screens)),
    }


def export_artwork(data: bytes, built: dict[str, Any], folder: Path, url: str) -> dict:
    """Write the images the screens use and point the regions at them.

    A missing image drops the reference rather than leaving a broken link:
    a control drawn in its colour with its label is the smaller loss.
    """
    wanted: set[str] = set()
    for screen in built["screens"].values():
        for region in screen["regions"]:
            wanted.update(region[k] for k in ART_KEYS if region.get(k))
    written: dict[str, str] = {}
    missing: list[str] = []
    if wanted:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                inside = set(archive.namelist())
                folder.mkdir(parents=True, exist_ok=True)
                for path in sorted(wanted):
                    if path not in inside:
                        missing.append(path)
                        continue
                    name = _safe_file(path)
                    (folder / name).write_bytes(archive.read(path))
                    written[path] = f"{url}/{name}"
        except (zipfile.BadZipFile, OSError):
            missing, written = sorted(wanted), {}
    for screen in built["screens"].values():
        for region in screen["regions"]:
            for key in ART_KEYS:
                if region.get(key):
                    if region[key] in written:
                        region[key] = written[region[key]]
                    else:
                        del region[key]
    return {"written": len(written), "missing": missing}


def write_panel(data: bytes, name: str, root: Path, url_root: str) -> dict[str, Any]:
    """Import a panel into `root/<slug>/`: its artwork and `panel.json`.

    `url_root` is where Home Assistant serves `root` from. Returns what was
    written, including the URL a card loads the panel from.
    """
    built = build_panel(data, name)
    folder_name = slug(name)
    folder = root / folder_name
    url = f"{url_root}/{folder_name}"
    art = export_artwork(data, built, folder, url)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "panel.json").write_text(json.dumps(built, separators=(",", ":")))
    return {
        "name": name,
        "panel_url": f"{url}/panel.json",
        "screens": list(built["screens"]),
        "page_joins": built["page_joins"],
        "default_screen": built["default_screen"],
        "artwork": art,
    }
