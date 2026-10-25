"""Importing a whole panel: every screen, and the join that shows each.

The page joins are the point. A processor pulses a page's join to flip the
panel to it, and a card can only follow along if the import says which
join belongs to which screen.
"""
import io
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pkg  # noqa: E402,F401

import pytest  # noqa: E402

from crestron_cip.panel import importer  # noqa: E402


def page(name, join, children, control="Page"):
    return f"""
      <Page>
        <ControlName>{control}</ControlName>
        <ObjectName>{name}</ObjectName>
        <Properties>
          <DigitalJoin>{join}</DigitalJoin>
          <Children>{children}</Children>
        </Properties>
      </Page>"""


def button(name, join, art=""):
    return f"""
      <Child>
        <ControlName>Button</ControlName>
        <ObjectName>{name}</ObjectName>
        <Properties>
          <Left>10</Left><Top>10</Top><Width>100</Width><Height>60</Height>
          <DigitalPressJoin>{join}</DigitalPressJoin>
          {art}
        </Properties>
      </Child>"""


def project(pages, files=None):
    xml = f"""<?xml version="1.0"?>
<Crestron><Properties><Width>1280</Width><Height>800</Height>
  <Pages>{pages}</Pages></Properties></Crestron>"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("swf/Environment.xml", xml.encode("utf-16"))
        for name, data in (files or {}).items():
            zf.writestr(name, data)
    return buffer.getvalue()


TWO_PAGES = project(
    page("2.0-Start", 12, button("Start", 1))
    + page("3.0-Main", 13, button("Volume up", 211) + button("Volume down", 212))
)


def test_every_page_becomes_a_screen():
    built = importer.build_panel(TWO_PAGES, "Room")
    assert set(built["screens"]) == {"2.0-Start", "3.0-Main"}


def test_each_screen_carries_the_join_that_shows_it():
    built = importer.build_panel(TWO_PAGES, "Room")
    assert built["page_joins"] == {"2.0-Start": 12, "3.0-Main": 13}


def test_the_default_is_the_busiest_screen():
    built = importer.build_panel(TWO_PAGES, "Room")
    assert built["default_screen"] == "3.0-Main"


def test_a_page_on_a_reserved_join_is_not_followed():
    # Joins from 17000 up are the panel's own; the processor never sends them.
    data = project(page("Setup", 17100, button("Back", 5))
                   + page("Main", 13, button("Go", 6)))
    assert importer.build_panel(data, "Room")["page_joins"] == {"Main": 13}


def test_an_unprogrammed_export_is_refused_with_the_reason():
    data = project(page("Main", 0, """
      <Child><ControlName>Button</ControlName><ObjectName>B</ObjectName>
        <Properties><Left>1</Left><Top>1</Top><Width>5</Width><Height>5</Height>
        </Properties></Child>"""))
    with pytest.raises(importer.PanelImportError) as err:
        importer.build_panel(data, "Room")
    assert "binds a join" in str(err.value)


def test_write_panel_puts_the_panel_and_its_artwork_where_a_card_can_load_them():
    art = "<BackgroundImage>Images/Key.png</BackgroundImage>"
    data = project(page("Main", 13, button("Go", 6, art)),
                   files={"Images/Key.png": b"\x89PNG fake"})
    root = Path(tempfile.mkdtemp())
    result = importer.write_panel(data, "Orchard Room", root, "/local/crestron-panels")
    assert result["panel_url"] == "/local/crestron-panels/Orchard_Room/panel.json"
    saved = json.loads((root / "Orchard_Room" / "panel.json").read_text())
    assert saved["format"] == "crestron-panel"
    assert saved["page_joins"] == {"Main": 13}


def test_a_folder_name_cannot_escape_the_panel_directory():
    assert importer.slug("../../etc") == "etc"
    assert importer.slug("a/b c") == "a_b_c"
