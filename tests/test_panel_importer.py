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


def reference_list(subpage_uid, count, obj=1):
    return f"""
      <Child>
        <ControlName>Subpage Reference List Horizontal</ControlName>
        <ObjectName>Zones</ObjectName>
        <Properties>
          <PositionAndSize><Top>120</Top><Left>0</Left><Width>1276</Width><Height>650</Height></PositionAndSize>
          <ControlJoin>{obj}</ControlJoin>
          <ItemSpacing>10</ItemSpacing><Margin>5</Margin>
          <DigitalJoinIncrement>10</DigitalJoinIncrement>
          <AnalogJoinIncrement>10</AnalogJoinIncrement>
          <SerialJoinIncrement>10</SerialJoinIncrement>
          <DigitalTriListJoinGroup><StartJoinNumber>4011</StartJoinNumber></DigitalTriListJoinGroup>
          <AnalogTriListJoinGroup><StartJoinNumber>11</StartJoinNumber></AnalogTriListJoinGroup>
          <SerialTriListJoinGroup><StartJoinNumber>11</StartJoinNumber></SerialTriListJoinGroup>
          <Subpage Type="SubpageRef"><PageID>{subpage_uid}</PageID></Subpage>
          <NumSubpageReferences>{count}</NumSubpageReferences>
        </Properties>
      </Child>"""


def zone_strip():
    return """
      <Page uid="3">
        <ControlName>Subpage</ControlName>
        <ObjectName>Zone</ObjectName>
        <Properties>
          <Width>200</Width><Height>640</Height>
          <Children>
            <Child><ControlName>Advanced Button</ControlName><ObjectName>Mute</ObjectName>
              <Properties><Left>10</Left><Top>500</Top><Width>180</Width><Height>60</Height>
                <DigitalPressJoin>1</DigitalPressJoin></Properties></Child>
            <Child><ControlName>Fader Slider Vertical</ControlName><ObjectName>Level</ObjectName>
              <Properties><Left>70</Left><Top>80</Top><Width>60</Width><Height>400</Height>
                <AnalogFeedbackJoin>1</AnalogFeedbackJoin></Properties></Child>
          </Children>
        </Properties>
      </Page>"""


def test_a_reference_list_draws_every_item_with_its_own_joins():
    data = project(page("Page", 0, reference_list(3, 16) + button("Home", 5)) + zone_strip())
    built = importer.build_panel(data, "BGM")
    # The zone strip is drawn inside the list, not offered as a screen.
    assert list(built["screens"]) == ["Page"]
    zones = built["screens"]["Page"]["lists"][0]
    assert len(zones["items"]) == 16
    roles = lambda i: {r["role"] for r in zones["items"][i - 1]["regions"]}
    # Item n binds join j at start + (n - 1) * 10 + (j - 1), in object 1.
    assert roles(1) == {"button_o1_4011", "fader_o1_11"}
    assert roles(16) == {"button_o1_4161", "fader_o1_161"}
    assert zones["item_size"] == [200, 640]


def xpanel_zip(extra=None):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("Annotation.ini", "")
        archive.writestr("swf/container.swf", b"CWS")
        archive.writestr("swf/Environment.xml", "<Environment/>")
        archive.writestr("swf/themes/CT_Neo [System]/CT_Neo.swf", b"CWS")
        for name, body in (extra or {}).items():
            archive.writestr(name, body)
    return buffer.getvalue()


def test_the_compiled_xpanel_is_unpacked_whole_with_its_folder_names():
    root = Path(tempfile.mkdtemp())
    url = importer.export_xpanel(xpanel_zip(), root, "/local/crestron-panels/BGM")
    assert url.startswith("/local/crestron-panels/BGM/xpanel/container.swf?v=")
    # The engine asks for its theme by this exact name.
    assert (root / "xpanel" / "themes" / "CT_Neo [System]" / "CT_Neo.swf").is_file()
    assert (root / "xpanel" / "Environment.xml").is_file()
    # Only the XPanel's own folder comes along.
    assert not (root / "xpanel" / "Annotation.ini").exists()


def test_an_xpanel_entry_cannot_climb_out_of_its_folder():
    root = Path(tempfile.mkdtemp()) / "panel"
    importer.export_xpanel(xpanel_zip({"swf/../../evil.txt": "x"}), root, "/u")
    assert not (root.parent / "evil.txt").exists()
    assert not (root / "evil.txt").exists()


def test_a_project_without_an_xpanel_has_no_xpanel_url():
    data = project(page("Main", 13, button("Go", 6)))
    assert importer.export_xpanel(data, Path(tempfile.mkdtemp()), "/u") is None


def cws(body: bytes) -> bytes:
    import struct
    import zlib
    return b"CWS\x0e" + struct.pack("<I", len(body) + 8) + zlib.compress(body)


def test_the_engine_is_patched_so_ruffle_can_run_it():
    import zlib
    engine = b"\x09flash.net\x0cSecureSocket\x10_useSecureSocket"
    root = Path(tempfile.mkdtemp())
    importer.export_xpanel(xpanel_zip({"swf/container.swf": cws(engine)}), root, "/u")
    out = (root / "xpanel" / "container.swf").read_bytes()
    body = zlib.decompress(out[8:])
    # The class reference is swapped length for length; the field that
    # merely contains the word is not touched.
    assert body == b"\x09flash.net\x0cURLVariables\x10_useSecureSocket"
    assert out[:8] == cws(engine)[:8]


def test_environment_is_utf8_with_web_paths():
    env = "<Path>themes\\Int_CT_Neo [System]\\Int_CT_Neo.xml</Path>".encode("utf-16")
    root = Path(tempfile.mkdtemp())
    importer.export_xpanel(xpanel_zip({"swf/Environment.xml": env}), root, "/u")
    text = (root / "xpanel" / "Environment.xml").read_bytes()
    assert text == b"<Path>themes/Int_CT_Neo [System]/Int_CT_Neo.xml</Path>"


def test_every_file_also_answers_to_its_lowercase_name():
    root = Path(tempfile.mkdtemp())
    importer.export_xpanel(xpanel_zip({"swf/controls/BaseObjectsRSL.swf": b"CWS"}), root, "/u")
    assert (root / "xpanel" / "controls" / "baseobjectsrsl.swf").is_file()
    assert (root / "xpanel" / "controls" / "BaseObjectsRSL.swf").is_file()


def test_a_new_import_gets_a_new_url():
    a = importer.export_xpanel(xpanel_zip(), Path(tempfile.mkdtemp()), "/u")
    b = importer.export_xpanel(xpanel_zip({"swf/container.swf": b"CWS2"}),
                               Path(tempfile.mkdtemp()), "/u")
    assert a != b


def test_a_panel_given_in_the_ui_is_imported_and_kept():
    """What Configure does with an uploaded panel file."""
    from crestron_cip import panel_store

    config = Path(tempfile.mkdtemp())

    class Config:
        config_dir = str(config)

        def path(self, *parts):
            return str(config.joinpath(*parts))

    class Hass:
        pass

    hass = Hass()
    hass.config = Config()
    upload = Path(tempfile.mkdtemp()) / "upload.zip"
    upload.write_bytes(project(page("Main", 13, button("Go", 6)),
                               files={"swf/container.swf": b"CWS"}))
    options = panel_store.import_from_file(hass, upload, "Tower Room")
    assert options["panel_url"] == "/local/crestron-panels/Tower_Room/panel.json"
    assert options["xpanel_url"].startswith("/local/crestron-panels/Tower_Room/xpanel/container.swf?v=")
    assert (config / "www" / "crestron-panels" / "Tower_Room" / "panel.json").is_file()
    # The file itself is kept, so it can be imported again later.
    kept = Path(options["panel_source"])
    assert kept == config / "crestron_cip" / "Tower_Room.zip" and kept.is_file()


def test_a_processor_keeps_one_panel_per_room():
    """Configure adds rooms' panels; each keeps the IPID it was given."""
    from crestron_cip import panel_store as ps

    imported = {"panel_url": "/p/a.json", "xpanel_url": "/p/a.swf", "panel_source": "/k/a.zip"}
    room1 = ps.panel_record("Room 1", 3, imported)
    room2 = ps.panel_record("Room 2", 4, {**imported, "xpanel_url": "/p/b.swf"})
    options = ps.with_panel(ps.with_panel({}, 3, room1), 3, room2)
    assert [(p["id"], p["ipid"]) for p in ps.panels_of(options, 3)] == [("Room_1", 3), ("Room_2", 4)]
    assert ps.find_panel(options, 3, "Room_2")["xpanel_url"] == "/p/b.swf"
    # No panel named: the first, as for a card from before rooms.
    assert ps.find_panel(options, 3, None)["id"] == "Room_1"
    # The same room again replaces its panel rather than adding a second.
    again = ps.with_panel(options, 3, ps.panel_record("Room 1", 5, imported))
    assert [(p["id"], p["ipid"]) for p in ps.panels_of(again, 3)] == [("Room_2", 4), ("Room_1", 5)]
    gone = ps.without_panel(again, 3, "Room_2")
    assert [p["id"] for p in ps.panels_of(gone, 3)] == ["Room_1"]
    assert ps.find_panel(gone, 3, "Room_2") is None


def test_an_entry_from_before_rooms_reads_as_one_panel():
    """The flat keys of one panel become a list of one, at the entry's IPID."""
    from crestron_cip import panel_store as ps

    old = {"panel_name": "Function Room", "panel_url": "/p/f.json",
           "xpanel_url": "/p/f.swf", "panel_source": "/k/f.zip"}
    [panel] = ps.panels_of(old, 3)
    assert (panel["id"], panel["name"], panel["ipid"], panel["xpanel_url"]) == \
        ("Function_Room", "Function Room", 3, "/p/f.swf")
    assert ps.panels_of({}, 3) == []
    # Adding a room keeps the old panel and drops the flat keys.
    room = ps.panel_record("Room 2", 4, {"panel_url": "/p/2.json", "panel_source": "/k/2.zip"})
    new = ps.with_panel(old, 3, room)
    assert set(new) == {"panels"}
    assert [p["id"] for p in new["panels"]] == ["Function_Room", "Room_2"]
