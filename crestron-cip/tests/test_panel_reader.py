"""Reading a panel's own project file into a join map.

The fixtures are the real shapes seen in a panel pulled off the hardware,
minus the twenty-five megabytes of Flash. Every one of them encodes a
mistake that was actually made while working this out.
"""
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import panel_reader as pr  # noqa: E402


def archive(environment: str, ini: str | None = None,
            encoding: str = "utf-16") -> bytes:
    """A panel archive holding one Environment.xml."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("swf/Environment.xml", environment.encode(encoding))
        if ini is not None:
            zf.writestr("XPanel.ini", ini)
        # The bulk of a real archive, which must be ignored.
        zf.writestr("swf/controls/BaseObjectsRSL.swf", b"\x00" * 64)
    return buffer.getvalue()


def child(name, control, left, top, props=""):
    return f"""
      <Child>
        <ControlName>{control}</ControlName>
        <ObjectName>{name}</ObjectName>
        <Properties>
          <Left>{left}</Left><Top>{top}</Top>
          <Width>108</Width><Height>64</Height>
          {props}
        </Properties>
      </Child>"""


def panel_xml(children: str, width=1280, height=800):
    return f"""<?xml version="1.0"?>
<Crestron>
  <Properties>
    <Width>{width}</Width><Height>{height}</Height>
    <Pages>
      <Page>
        <ObjectName>Main</ObjectName>
        <Properties>
          <Children>{children}</Children>
        </Properties>
      </Page>
    </Pages>
  </Properties>
</Crestron>"""


# -- The container ------------------------------------------------------

def test_a_utf16_environment_is_read():
    """VT Pro writes UTF-16 in some builds."""
    data = archive(panel_xml(child("B", "Button", 10, 10,
                                   "<DigitalPressJoin>5</DigitalPressJoin>")))
    panel = pr.read_panel(data)
    assert len(panel["controls"]) == 1


def test_a_utf8_environment_is_read():
    """And UTF-8 in others."""
    data = archive(panel_xml(child("B", "Button", 10, 10,
                                   "<DigitalPressJoin>5</DigitalPressJoin>")),
                   encoding="utf-8")
    assert len(pr.read_panel(data)["controls"]) == 1


def test_a_vtp_is_refused_with_the_reason():
    """It is an OLE2 project, not a zip, and there is a separate reader."""
    with pytest.raises(pr.PanelReadError) as err:
        pr.read_panel(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 64)
    assert ".vtp" in str(err.value)


def test_a_zip_without_an_environment_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("readme.txt", "nope")
    with pytest.raises(pr.PanelReadError) as err:
        pr.read_panel(buffer.getvalue())
    assert "Environment.xml" in str(err.value)


# -- The trap that produces a confident wrong answer --------------------

NESTED = """
      <Child>
        <ControlName>Subpage Reference</ControlName>
        <ObjectName>Container</ObjectName>
        <Properties>
          <Left>0</Left><Top>0</Top><Width>400</Width><Height>400</Height>
          <Children>
            <Child>
              <ControlName>Button</ControlName>
              <ObjectName>Inner</ObjectName>
              <Properties>
                <Left>10</Left><Top>10</Top>
                <Width>50</Width><Height>50</Height>
                <DigitalPressJoin>42</DigitalPressJoin>
              </Properties>
            </Child>
          </Children>
        </Properties>
      </Child>"""


def test_a_container_does_not_inherit_its_childrens_joins():
    """Reading Properties whole makes a container claim every join inside
    it and report itself as the control."""
    panel = pr.read_panel(archive(panel_xml(NESTED)))
    container = next(o for o in panel["objects"] if o["name"] == "Container")
    inner = next(o for o in panel["objects"] if o["name"] == "Inner")
    assert container["joins"] == {}
    assert inner["joins"]["DigitalPressJoin"]["join"] == 42


def test_a_nested_child_is_still_found():
    panel = pr.read_panel(archive(panel_xml(NESTED)))
    assert {o["name"] for o in panel["objects"]} == {"Container", "Inner"}


# -- Buses --------------------------------------------------------------

def test_the_same_number_on_two_buses_stays_two_signals():
    """A zone's level is analog 211 while its volume-up button is digital
    211. Collapsing them loses the distinction entirely."""
    children = (
        child("Up", "Advanced Button", 20, 150,
              "<DigitalPressJoin>211</DigitalPressJoin>")
        + child("Level", "Liquid Gauge Vertical", 65, 232,
                "<AnalogFeedbackJoin>211</AnalogFeedbackJoin>")
    )
    joins = pr.joins_of(pr.read_panel(archive(panel_xml(children))))
    assert {(j["bus"], j["join"]) for j in joins} == {
        ("digital", 211), ("analog", 211)}


def test_a_serial_join_is_recognised():
    panel = pr.read_panel(archive(panel_xml(
        child("T", "Text", 0, 0, "<IndirectTextJoin>9</IndirectTextJoin>"))))
    assert pr.summarise(panel)["serial_joins"] == 1


def test_a_join_of_zero_is_not_a_join():
    """Zero means unassigned, and a panel is full of them."""
    panel = pr.read_panel(archive(panel_xml(
        child("B", "Button", 0, 0,
              "<DigitalPressJoin>0</DigitalPressJoin>"
              "<DigitalEnableJoin>0</DigitalEnableJoin>"))))
    assert panel["controls"] == []
    assert len(panel["objects"]) == 1


# -- Reserved joins -----------------------------------------------------

def test_a_reserved_join_is_kept_but_flagged():
    """They drive the panel rather than the program, so they are not
    offered as controls — but hiding them makes a panel look emptier than
    it is."""
    panel = pr.read_panel(archive(panel_xml(
        child("Brightness", "Button", 0, 0,
              "<DigitalPressJoin>18495</DigitalPressJoin>"))))
    assert panel["controls"][0]["joins"]["DigitalPressJoin"]["reserved"]
    assert pr.joins_of(panel) == []
    assert len(pr.joins_of(panel, include_reserved=True)) == 1


# -- Duplicate draws ----------------------------------------------------

def test_identical_controls_drawn_twice_count_once():
    """A panel draws the same control once per page state. Three copies of
    one button is a drawing detail, not three controls."""
    one = child("A", "Button", 150, 153,
                "<DigitalPressJoin>221</DigitalPressJoin>")
    panel = pr.read_panel(archive(panel_xml(one + one + one)))
    assert len(panel["controls"]) == 1
    assert panel["duplicates_collapsed"] == 2


def test_two_controls_at_the_same_place_with_different_joins_both_count():
    """Overlapping is normal; only an identical draw is a duplicate."""
    children = (
        child("A", "Button", 150, 153, "<DigitalPressJoin>221</DigitalPressJoin>")
        + child("B", "Button", 150, 153, "<DigitalPressJoin>222</DigitalPressJoin>")
    )
    panel = pr.read_panel(archive(panel_xml(children)))
    assert len(panel["controls"]) == 2
    assert panel["duplicates_collapsed"] == 0


# -- Telling an empty export from a real panel --------------------------

def test_page_count_decides_nothing():
    """It looked like a shortcut for "this export is empty" and is not
    one: it reads 0 in every real panel dump taken off this estate,
    including one with 27 digital joins. Deciding on it told users their
    working panel was empty."""
    data = archive(panel_xml(child("B", "Button", 0, 0,
                                   "<DigitalPressJoin>5</DigitalPressJoin>")),
                   ini="[FileAttribute]\nPageCount=0\n")
    summary = pr.summarise(pr.read_panel(data))
    assert summary["declared_page_count"] == 0     # reported
    assert not summary["unprogrammed"]             # but not believed
    assert summary["controls"] == 1


def test_emptiness_is_decided_on_whether_anything_binds_a_join():
    empty = archive(panel_xml(child("B", "Button", 0, 0,
                                    "<DigitalPressJoin>0</DigitalPressJoin>")),
                    ini="[FileAttribute]\nPageCount=3\n")
    assert pr.summarise(pr.read_panel(empty))["unprogrammed"]


def test_objects_without_any_join_are_reported_as_unprogrammed():
    """Two panels on one estate had 52 objects each and every join at
    zero. The file is fine; nobody assigned joins."""
    children = "".join(
        child(f"Multi-Mode Button_{n}", "Advanced Button", n * 10, 10,
              "<DigitalPressJoin>0</DigitalPressJoin>")
        for n in range(1, 6))
    summary = pr.summarise(pr.read_panel(archive(panel_xml(children))))
    assert summary["objects"] == 5
    assert summary["controls"] == 0
    assert summary["unprogrammed"]


# -- Geometry and pages -------------------------------------------------

def test_geometry_is_carried_through():
    panel = pr.read_panel(archive(panel_xml(
        child("B", "Button", 21, 153, "<DigitalPressJoin>1</DigitalPressJoin>"))))
    control = panel["controls"][0]
    assert (control["left"], control["top"]) == (21, 153)
    assert (control["width"], control["height"]) == (108, 64)


def test_the_project_size_is_read():
    panel = pr.read_panel(archive(panel_xml(
        child("B", "Button", 0, 0, "<DigitalPressJoin>1</DigitalPressJoin>"),
        width=480, height=272)))
    assert panel["size"] == (480, 272)


def test_what_binds_a_join_is_reported():
    """Mapping a join back to the button that pressed it is the point."""
    panel = pr.read_panel(archive(panel_xml(
        child("Volume Up", "Advanced Button", 0, 0,
              "<DigitalPressJoin>211</DigitalPressJoin>"))))
    entry = pr.joins_of(panel)[0]
    assert entry["bound_by"][0]["name"] == "Volume Up"
    assert entry["bound_by"][0]["property"] == "DigitalPressJoin"


# -- labels, pages and references ---------------------------------------
#
# Added after the generator was built on top of this reader and three of
# these came back as wrong output rather than as a failing test.

def test_the_words_come_out_of_a_label():
    # VT Pro-e stores labels as HTML because the panel renders mixed fonts
    # and sizes in one string.
    parsed = pr.parse_label(
        '<P><FONT size="36" face="Crestron Unicode" color="#000000">'
        "Source Select</FONT></P>"
    )
    assert parsed["text"] == "Source Select"
    assert parsed["size"] == 36
    assert parsed["indirect"] == []


def test_indirect_text_names_the_join_it_arrives_on():
    # <cips>N?placeholder</cips> is Crestron's indirect text marker: N is
    # the serial join the processor writes to, and the placeholder is only
    # what the designer typed so the editor had something to draw.
    parsed = pr.parse_label('<FONT size="22"><cips>211?Source Name</cips></FONT>')
    assert parsed["indirect"] == [{"join": 211, "placeholder": "Source Name"}]
    assert parsed["static"] == ""


def test_a_multi_state_label_keeps_its_sentence_in_order():
    # A joining button reads "Join <room> with <room>" — static words
    # wrapped around indirect ones. Dropping either half loses the sense.
    parsed = pr.parse_label(
        "<cips>1006?Join</cips> <cips>1004?Room 1</cips> with <cips>1005?Room 2</cips>"
    )
    assert parsed["text"] == "Join Room 1 with Room 2"
    assert [i["join"] for i in parsed["indirect"]] == [1006, 1004, 1005]


def test_the_panel_size_falls_back_to_the_ini():
    # No real panel carries Width/Height on the project element, and a
    # faceplate cannot be laid out without the canvas.
    data = archive(
        panel_xml(child("B", "Button", 10, 10,
                        "<DigitalPressJoin>1</DigitalPressJoin>"))
        .replace("<Width>1280</Width><Height>800</Height>", ""),
        ini="[Startup]\nwidth=1280\nheight=800\n",
    )
    assert pr.read_panel(data)["size"] == (1280, 800)


def test_every_subpage_gets_its_own_key():
    # VT Pro-e never made anyone name a subpage, so every one of them is
    # called "Subpage". Under one key, nine of them put every confirmation
    # popup on top of the main screen.
    pages = "".join(
        f"""<Page><ControlName>Subpage</ControlName>
              <ObjectName>Subpage</ObjectName>
              <Properties><Width>1280</Width><Height>580</Height>
                <Children>{child(f"B{n}", "Button", 10, 10,
                                 f"<DigitalPressJoin>{n}</DigitalPressJoin>")}
                </Children></Properties></Page>"""
        for n in (1, 2, 3)
    )
    xml = f"""<?xml version="1.0"?>
<Crestron><Properties><Pages>{pages}</Pages></Properties></Crestron>"""
    panel = pr.read_panel(archive(xml, ini="[Startup]\nwidth=1280\nheight=800\n"))
    assert sorted(panel["pages"]) == ["Subpage", "Subpage 2", "Subpage 3"]
    assert len(panel["objects"]) == 3


def test_a_control_shared_by_two_pages_survives_on_both():
    # Collapsing duplicates across pages lost the control from every page
    # but the first, which is how a whole page went missing.
    same = child("B", "Button", 10, 10, "<DigitalPressJoin>1</DigitalPressJoin>")
    pages = "".join(
        f"""<Page><ObjectName>{name}</ObjectName>
              <Properties><Width>1280</Width><Height>800</Height>
                <Children>{same}</Children></Properties></Page>"""
        for name in ("Main", "Other")
    )
    xml = f"""<?xml version="1.0"?>
<Crestron><Properties><Pages>{pages}</Pages></Properties></Crestron>"""
    panel = pr.read_panel(archive(xml, ini="[Startup]\nwidth=1280\nheight=800\n"))
    assert {o["page"] for o in panel["objects"]} == {"Main", "Other"}


def test_a_repeated_control_on_one_page_is_still_collapsed():
    # The reason the dedupe exists: a panel draws the same button once per
    # state, and three copies is a drawing detail, not three controls.
    same = child("B", "Button", 10, 10, "<DigitalPressJoin>1</DigitalPressJoin>")
    data = archive(panel_xml(same * 3))
    assert len(pr.read_panel(data)["objects"]) == 1


def test_where_the_main_page_puts_its_subpages_is_read():
    # A reference is tagged <Subpage>, not <Child>. Reading only <Child>
    # left the main page looking empty.
    xml = """<?xml version="1.0"?>
<Crestron><Properties><Pages>
  <Page><ObjectName>3.0-Main</ObjectName><Properties>
    <Width>1280</Width><Height>800</Height>
    <Children>
      <Subpage>
        <ControlName>Subpage Reference</ControlName>
        <ObjectName>3.4-Vol-Ctrl</ObjectName>
        <Properties>
          <Left>871</Left><Top>120</Top>
          <Width>409</Width><Height>580</Height>
          <DigitalJoin>34</DigitalJoin>
        </Properties>
      </Subpage>
    </Children>
  </Properties></Page>
</Pages></Properties></Crestron>"""
    panel = pr.read_panel(archive(xml, ini="[Startup]\nwidth=1280\nheight=800\n"))
    assert len(panel["references"]) == 1
    ref = panel["references"][0]
    assert ref["name"] == "3.4-Vol-Ctrl"
    assert (ref["left"], ref["top"], ref["width"], ref["height"]) == (871, 120, 409, 580)
    assert ref["join"] == 34
    assert ref["page"] == "3.0-Main"


def test_a_subpage_is_recorded_as_a_subpage_not_a_page():
    xml = """<?xml version="1.0"?>
<Crestron><Properties><Pages>
  <Page><ControlName>Subpage</ControlName><ObjectName>Subpage</ObjectName>
    <Properties><Width>409</Width><Height>580</Height><Children/></Properties>
  </Page>
</Pages></Properties></Crestron>"""
    panel = pr.read_panel(archive(xml, ini="[Startup]\nwidth=1280\nheight=800\n"))
    meta = panel["page_meta"][0]
    assert meta["kind"] == "subpage"
    assert (meta["width"], meta["height"]) == (409, 580)


# -- artwork ------------------------------------------------------------

def art_button(press, icon=None, image_on=None, theme_state="18"):
    """An Advanced Button with per-state artwork, as VT Pro-e writes it."""
    def block(tag, icon_path, image_path):
        return f"""
          <{tag}>
            {f'<IconType><UseCustom><Image><FilePath>{icon_path}</FilePath>'
               '</Image></UseCustom></IconType>' if icon_path else ''}
            {f'<ImageType><UseImage><Image><FilePath>{image_path}</FilePath>'
               '</Image></UseImage></ImageType>'
             if image_path else
             f'<ImageType><UseState><State>{theme_state}</State></UseState></ImageType>'}
          </{tag}>"""
    return f"""
      <Child>
        <ControlName>Advanced Button</ControlName>
        <ObjectName>B{press}</ObjectName>
        <Properties>
          <Left>10</Left><Top>10</Top><Width>280</Width><Height>280</Height>
          <DigitalPressJoin>{press}</DigitalPressJoin>
          <Modes><Mode>
            {block("NormalModeState", icon, None)}
            {block("SelectedModeState", icon, image_on)}
          </Mode></Modes>
        </Properties>
      </Child>"""


def test_a_buttons_icon_and_lit_face_are_both_read():
    data = archive(panel_xml(art_button(
        101, icon=r"images\airmedia.png",
        image_on=r"images\BtnOnColour280x280.png")))
    art = pr.read_panel(data)["objects"][0]["artwork"]
    assert art["icon"] == "swf/images/airmedia.png"
    # The selected state is what the button looks like when it is lit,
    # which is the only change of appearance a card can reproduce.
    assert art["image_on"] == "swf/images/BtnOnColour280x280.png"


def test_a_theme_background_yields_no_file():
    # Theme artwork is compiled inside a SWF. There is nothing to extract,
    # and inventing a path would leave the card linking to nothing.
    data = archive(panel_xml(art_button(101, icon=r"images\hdmi-icon.png")))
    art = pr.read_panel(data)["objects"][0]["artwork"]
    assert "image" not in art and "image_on" not in art
    assert art["icon"] == "swf/images/hdmi-icon.png"


def test_a_windows_path_becomes_the_name_inside_the_archive():
    data = archive(panel_xml(art_button(101, icon=r"images\sub\thing.png")))
    assert pr.read_panel(data)["objects"][0]["artwork"]["icon"] == \
        "swf/images/sub/thing.png"


def test_a_doubled_separator_does_not_become_a_path_that_matches_nothing():
    data = archive(panel_xml(art_button(101, icon="images//logo.png")))
    assert pr.read_panel(data)["objects"][0]["artwork"]["icon"] == \
        "swf/images/logo.png"


def test_an_image_object_carries_its_picture_directly():
    child = """
      <Child>
        <ControlName>Image Object</ControlName>
        <ObjectName>Logo</ObjectName>
        <Properties>
          <Left>0</Left><Top>0</Top><Width>246</Width><Height>119</Height>
          <Image><FilePath>images\\logo.png</FilePath></Image>
        </Properties>
      </Child>"""
    art = pr.read_panel(archive(panel_xml(child)))["objects"][0]["artwork"]
    assert art == {"image": "swf/images/logo.png"}


def test_a_container_does_not_take_its_childrens_artwork():
    # Read whole, a border would claim the icon of the button sitting on
    # it — the same trap the join reader already had to avoid.
    nested = f"""
      <Child>
        <ControlName>Fill Border</ControlName>
        <ObjectName>Frame</ObjectName>
        <Properties>
          <Left>0</Left><Top>0</Top><Width>600</Width><Height>400</Height>
          <Children>{art_button(101, icon="images/airmedia.png")}</Children>
        </Properties>
      </Child>"""
    objects = pr.read_panel(archive(panel_xml(nested)))["objects"]
    frame = next(o for o in objects if o["control"] == "Fill Border")
    button = next(o for o in objects if o["control"] == "Advanced Button")
    assert frame["artwork"] == {}
    assert button["artwork"]["icon"] == "swf/images/airmedia.png"


def test_two_buttons_differing_only_in_artwork_both_survive():
    # The dedupe collapses identical controls. Two source keys at the same
    # place with the same join but different icons are still two keys.
    a = art_button(101, icon=r"images\airmedia.png")
    b = art_button(101, icon=r"images\hdmi-icon.png")
    assert len(pr.read_panel(archive(panel_xml(a + b)))["objects"]) == 2


def test_a_page_background_colour_is_read_as_css():
    xml = """<?xml version="1.0"?>
<Crestron><Properties><Pages>
  <Page><ObjectName>Main</ObjectName><Properties>
    <Width>1280</Width><Height>800</Height>
    <BackgroundColor>0x0071bc</BackgroundColor>
    <DisplayBackgroundColor>true</DisplayBackgroundColor>
    <Children/>
  </Properties></Page>
</Pages></Properties></Crestron>"""
    meta = pr.read_panel(archive(xml, ini="[Startup]\nwidth=1280\nheight=800\n"))["page_meta"][0]
    assert meta["background"] == "#0071bc"
    assert meta["opaque"] is True


def test_a_subpage_that_does_not_draw_its_colour_is_not_opaque():
    # Most subpages record a colour and never paint it — they are glass
    # over the page behind. Painting it would hide the page.
    xml = """<?xml version="1.0"?>
<Crestron><Properties><Pages>
  <Page><ControlName>Subpage</ControlName><ObjectName>Subpage</ObjectName>
    <Properties><Width>400</Width><Height>300</Height>
      <BackgroundColor>0x0071bc</BackgroundColor>
      <DisplayBackgroundColor>false</DisplayBackgroundColor>
      <Children/></Properties>
  </Page>
</Pages></Properties></Crestron>"""
    meta = pr.read_panel(archive(xml, ini="[Startup]\nwidth=1280\nheight=800\n"))["page_meta"][0]
    assert meta["opaque"] is False
