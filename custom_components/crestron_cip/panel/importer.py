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

import hashlib
import io
import json
import re
import zipfile
import zlib
from pathlib import Path, PurePosixPath
from typing import Any

from .faceplate import build_faceplate, candidate_screens, main_screen
from .reader import RESERVED_FROM, read_panel

FORMAT = "crestron-panel"
ART_KEYS = ("src", "src_on", "icon")

# The bus each generated role prefix sits on. Mirrors ROLE_BUS in the card.
_ROLE_BUS = {"button": "digital", "lamp": "digital", "level": "analog",
             "fader": "analog", "text": "serial"}
_ROLE = re.compile(r"^(button|lamp|level|fader|text)_(\d+)$")


def _item_role(role: str, spec: dict, index: int) -> str:
    """The role a template's control takes in item `index` (from 1)."""
    match = _ROLE.match(role or "")
    if not match:
        return role
    prefix, number = match[1], int(match[2])
    bus = _ROLE_BUS[prefix]
    join = spec["starts"][bus] + (index - 1) * spec["increments"][bus] + (number - 1)
    return f"{prefix}_o{spec['smart_object']}_{join}"


def _expand_list(spec: dict, template: dict) -> dict:
    """A Subpage Reference List as every item it shows, joins offset."""
    items = []
    for index in range(1, spec["count"] + 1):
        regions = []
        for region in template["regions"]:
            item = dict(region)
            item["id"] = f"{region['id']}_i{index}"
            for key in ("role", "target", "visible_role"):
                if item.get(key):
                    item[key] = _item_role(item[key], spec, index)
            regions.append(item)
        items.append({"regions": regions})
    return {
        "x": spec["left"], "y": spec["top"], "w": spec["width"], "h": spec["height"],
        "orientation": spec["orientation"],
        "spacing": spec["spacing"], "margin": spec["margin"],
        "smart_object": spec["smart_object"],
        "background": spec.get("background"),
        "item_size": template["size"],
        "items": items,
    }


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

    by_uid = {m.get("uid"): m["key"] for m in panel["page_meta"] if m.get("uid")}
    templates = {by_uid.get(spec["subpage_uid"]) for spec in panel.get("lists", [])}

    screens: dict[str, dict] = {}
    for key in candidate_screens(panel):
        if key in templates:
            # A list's item is drawn inside the list, not as a screen.
            continue
        faceplate = build_faceplate(panel, page=key, name=f"{name} {key}")
        lists = []
        for spec in panel.get("lists", []):
            template_key = by_uid.get(spec["subpage_uid"])
            if spec["page"] != key or not template_key or spec["count"] < 1:
                continue
            template = build_faceplate(panel, page=template_key, name=template_key)
            if template["regions"]:
                lists.append(_expand_list(spec, template))
        if lists:
            faceplate["lists"] = lists
        if faceplate["regions"] or lists:
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
    def every_region(screen):
        yield from screen["regions"]
        for spec in screen.get("lists", []):
            for item in spec["items"]:
                yield from item["regions"]

    for screen in built["screens"].values():
        for region in every_region(screen):
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
        for region in every_region(screen):
            for key in ART_KEYS:
                if region.get(key):
                    if region[key] in written:
                        region[key] = written[region[key]]
                    else:
                        del region[key]
    return {"written": len(written), "missing": missing}


XPANEL_ENTRY = "container.swf"

# Ruffle has no flash.net.SecureSocket, and the engine names it in a type
# check (`socket is SecureSocket`) on every connection, which throws there.
# The constant is swapped, length for length, for a class Ruffle has; the
# check then answers "not secure", which is right: the browser's socket is
# plain and the relay holds any TLS to the processor.
_SECURE_SOCKET = b"\x0cSecureSocket"
_SECURE_SOCKET_STAND_IN = b"\x0cURLVariables"


def _patch_swf(data: bytes) -> bytes:
    """A SWF with its SecureSocket reference made safe under Ruffle."""
    if data[:3] == b"CWS":
        try:
            body = zlib.decompress(data[8:])
        except zlib.error:
            return data  # not a SWF this can read; serve it as it came
    elif data[:3] == b"FWS":
        body = data[8:]
    else:
        return data  # LZMA-compressed; none of Crestron's are
    if _SECURE_SOCKET not in body:
        return data
    body = body.replace(_SECURE_SOCKET, _SECURE_SOCKET_STAND_IN)
    if data[:3] == b"CWS":
        return data[:8] + zlib.compress(body, 9)
    return data[:8] + body


def _web_environment(data: bytes) -> bytes:
    """Environment.xml as a browser engine can read it.

    VTPro writes it in UTF-16 and names files with Windows backslashes;
    Flash Player coped with both, Ruffle with neither.
    """
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = data.decode("utf-16")
    else:
        text = data.decode("utf-8-sig", "replace")
    text = re.sub(r"(?<=[\w\])])\\(?=[\w \[])", "/", text)
    return text.encode("utf-8")


def export_xpanel(data: bytes, folder: Path, url: str) -> str | None:
    """Unpack the compiled XPanel, if the file carries one, ready to run.

    A panel's .zip holds Crestron's own browser XPanel: `container.swf`, the
    SmartGraphics engine, its theme and `Environment.xml`, side by side. Run
    as it is, it draws the panel and runs its page logic exactly as the
    panel does, so it is kept whole, with its folder names untouched (the
    engine asks for `themes/<Theme> [System]/...` by name). Three things are
    changed so it runs under Ruffle from a web server: see `_patch_swf` and
    `_web_environment`, and every file also answers to its lowercase name,
    because the engine asks for some that way and Windows never minded.

    Returns the URL of `container.swf`, versioned by its content so a
    browser cannot keep running an older import, or None when there is none.
    """
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return None
    with archive:
        names = archive.namelist()
        roots = [n[: -len(XPANEL_ENTRY)] for n in names
                 if PurePosixPath(n).name == XPANEL_ENTRY]
        if not roots:
            return None
        root = min(roots, key=len)
        target = folder / "xpanel"
        written: dict[PurePosixPath, bytes] = {}
        for name in names:
            if not name.startswith(root) or name.endswith("/"):
                continue
            rel = PurePosixPath(name[len(root):])
            # The archive is not ours: nothing may climb out of the folder.
            if rel.is_absolute() or ".." in rel.parts:
                continue
            body = archive.read(name)
            if rel.suffix.lower() == ".swf":
                body = _patch_swf(body)
            elif str(rel).lower() == "environment.xml":
                body = _web_environment(body)
            written[rel] = body
    for rel, body in written.items():
        for path in {rel, PurePosixPath(str(rel).lower())}:
            out = target.joinpath(*path.parts)
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(body)
    container = next(body for rel, body in written.items() if rel.name == XPANEL_ENTRY)
    version = hashlib.sha256(container).hexdigest()[:10]
    return f"{url}/xpanel/{XPANEL_ENTRY}?v={version}"


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
    xpanel_url = export_xpanel(data, folder, url)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "panel.json").write_text(json.dumps(built, separators=(",", ":")))
    return {
        "name": name,
        "panel_url": f"{url}/panel.json",
        "screens": list(built["screens"]),
        "page_joins": built["page_joins"],
        "default_screen": built["default_screen"],
        "artwork": art,
        # Crestron's own compiled XPanel, for crestron-xpanel-card.
        "xpanel_url": xpanel_url,
    }
