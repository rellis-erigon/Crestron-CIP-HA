"""A processor's panel, imported from Home Assistant's own UI.

The options flow takes the panel's .zip as an upload. It is imported for
the card (see panel.importer), and the file is kept beside the
configuration so it can be imported again when the importer improves,
without anyone finding the file a second time.
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
