"""A processor's panels, imported from Home Assistant's own UI.

A processor usually serves several touch panels, one per room, each at an
IPID of its own and often each with a project of its own. The options flow
takes each panel's .zip as an upload, with the room's name and IPID. It is
imported for the card (see panel.importer), and the file is kept beside
the configuration so it can be imported again when the importer improves,
without anyone finding the file a second time.

The panels are kept in the entry's options as a list. An entry from before
there could be several kept one panel in flat keys; panels_of reads that
as a list of one, at the entry's own IPID.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .const import DOMAIN, PANEL_DIR, PANEL_URL

if TYPE_CHECKING:  # the tests load this without Home Assistant
    from homeassistant.core import HomeAssistant

# Options a config entry keeps about its panel.
OPT_PANEL_NAME = "panel_name"
OPT_PANEL_URL = "panel_url"
OPT_XPANEL_URL = "xpanel_url"
OPT_PANEL_SOURCE = "panel_source"
OPT_PANELS = "panels"
# The flat keys an entry kept its one panel in, before OPT_PANELS.
LEGACY_KEYS = (OPT_PANEL_NAME, OPT_PANEL_URL, OPT_XPANEL_URL, OPT_PANEL_SOURCE)


def panels_of(options: dict[str, Any], ipid: int) -> list[dict[str, Any]]:
    """The entry's panels: id, name, ipid and where each was imported to."""
    from .panel.importer import slug

    panels = options.get(OPT_PANELS)
    if panels is not None:
        return [dict(panel) for panel in panels]
    if not (options.get(OPT_XPANEL_URL) or options.get(OPT_PANEL_URL)):
        return []
    name = options.get(OPT_PANEL_NAME) or "Panel"
    return [{
        "id": slug(name), "name": name, "ipid": int(ipid),
        OPT_PANEL_URL: options.get(OPT_PANEL_URL),
        OPT_XPANEL_URL: options.get(OPT_XPANEL_URL),
        OPT_PANEL_SOURCE: options.get(OPT_PANEL_SOURCE),
    }]


def with_panel(options: dict[str, Any], ipid: int, panel: dict[str, Any]) -> dict[str, Any]:
    """The options with this panel added, or put in place of its namesake."""
    panels = [p for p in panels_of(options, ipid) if p["id"] != panel["id"]]
    kept = {k: v for k, v in options.items() if k not in LEGACY_KEYS}
    return {**kept, OPT_PANELS: [*panels, panel]}


def without_panel(options: dict[str, Any], ipid: int, panel_id: str) -> dict[str, Any]:
    panels = [p for p in panels_of(options, ipid) if p["id"] != panel_id]
    kept = {k: v for k, v in options.items() if k not in LEGACY_KEYS}
    return {**kept, OPT_PANELS: panels}


def find_panel(options: dict[str, Any], ipid: int, panel_id: str | None) -> dict[str, Any] | None:
    """A panel by id; with no id, the first, as for a card from before rooms."""
    panels = panels_of(options, ipid)
    if panel_id is None:
        return panels[0] if panels else None
    return next((p for p in panels if p["id"] == panel_id), None)


def panel_record(room: str, ipid: int, imported: dict[str, Any]) -> dict[str, Any]:
    """What the options keep about one room's panel, from import_from_file."""
    from .panel.importer import slug

    return {
        "id": slug(room), "name": room, "ipid": int(ipid),
        OPT_PANEL_URL: imported[OPT_PANEL_URL],
        OPT_XPANEL_URL: imported.get(OPT_XPANEL_URL),
        OPT_PANEL_SOURCE: imported[OPT_PANEL_SOURCE],
    }


def source_dir(hass: HomeAssistant) -> Path:
    return Path(hass.config.path(DOMAIN))


def import_from_file(hass: HomeAssistant, source: Path, name: str) -> dict[str, Any]:
    """Import a panel file and keep a copy of it. Runs in the executor.

    Raises panel.importer.PanelImportError when the file has nothing to draw.
    """
    from .panel.importer import slug, write_panel

    data = source.read_bytes()
    result = write_panel(data, name, Path(hass.config.path(*PANEL_DIR)), PANEL_URL)
    keep = source_dir(hass) / f"{slug(name)}{source.suffix.lower() or '.zip'}"
    keep.parent.mkdir(parents=True, exist_ok=True)
    if source.resolve() != keep.resolve():
        shutil.copyfile(source, keep)
    return {
        OPT_PANEL_NAME: name,
        OPT_PANEL_URL: result["panel_url"],
        OPT_XPANEL_URL: result.get("xpanel_url"),
        OPT_PANEL_SOURCE: str(keep),
        "screens": result["screens"],
    }
