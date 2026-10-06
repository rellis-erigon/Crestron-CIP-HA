"""Reading a Crestron panel project into a faceplate.

An XPanel is already the shape these cards render, so the panel someone
already has can become the card. The fixtures here are the real shapes
seen in a VT Pro build, minus the megabytes of Flash.
"""
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import panel_import as pi  # noqa: E402

PROJECT = """<?xml version="1.0"?>
<Crestron>
  <ObjectName>Project</ObjectName>
  <Properties><PositionAndSize><Left>0</Left><Top>0</Top>
    <Width>800</Width><Height>600</Height></PositionAndSize></Properties>
  <Page>
    <ObjectName>Page</ObjectName>
    <Properties><PositionAndSize><Left>0</Left><Top>0</Top>
      <Width>800</Width><Height>600</Height></PositionAndSize></Properties>
    <Subpage>
      <ObjectName>Subpage</ObjectName>
      <Properties><PositionAndSize><Left>10</Left><Top>20</Top>
        <Width>125</Width><Height>480</Height></PositionAndSize></Properties>
      <Button>
        <ObjectName>Advanced Button_2</ObjectName>
        <Properties><PositionAndSize><Left>0</Left><Top>100</Top>
          <Width>125</Width><Height>40</Height></PositionAndSize></Properties>
        <DigitalPressJoin>4</DigitalPressJoin>
        <IndirectTextJoin>4</IndirectTextJoin>
      </Button>
      <Fader>
        <ObjectName>Fader Slider Vertical</ObjectName>
        <Properties><PositionAndSize><Left>50</Left><Top>32</Top>
          <Width>32</Width><Height>288</Height></PositionAndSize></Properties>
        <AnalogFeedbackJoin>1</AnalogFeedbackJoin>
      </Fader>
      <Reserved>
        <ObjectName>Advanced Button</ObjectName>
        <Properties><PositionAndSize><Left>0</Left><Top>0</Top>
          <Width>80</Width><Height>30</Height></PositionAndSize></Properties>
        <DigitalPressJoin>18495</DigitalPressJoin>
      </Reserved>
    </Subpage>
  </Page>
</Crestron>
"""


def archive(xml: str, encoding: str = "utf-8") -> bytes:
    raw = xml.encode(encoding)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr(pi.ENVIRONMENT, raw)
    return buffer.getvalue()


def test_a_vtp_is_recognised_and_refused():
    with pytest.raises(pi.PanelError) as err:
        pi.read_objects(b"\xd0\xcf\x11\xe0rest of an OLE file")
    assert ".c3p" in str(err.value)


def test_something_else_entirely_is_refused():
    with pytest.raises(pi.PanelError):
        pi.read_objects(b"not a panel at all")


def test_an_archive_without_the_environment_is_refused():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as z:
        z.writestr("readme.txt", "hello")
    with pytest.raises(pi.PanelError):
        pi.read_objects(buffer.getvalue())


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_both_encodings_are_read(encoding):
    """VT Pro saves Environment.xml as UTF-16 in some builds, UTF-8 in
    others; the same project must read the same either way."""
    objects = pi.read_objects(archive(PROJECT, encoding))
    assert any(o["name"] == "Fader Slider Vertical" for o in objects)


def test_positions_are_absolute():
    """A child sits relative to its container, so the offsets accumulate
    or every control lands in the top-left corner."""
    objects = pi.read_objects(archive(PROJECT))
    fader = next(o for o in objects if o["name"] == "Fader Slider Vertical")
    assert (fader["x"], fader["y"]) == (60, 52)  # subpage 10,20 + 50,32


def test_a_fader_becomes_a_fader():
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    fader = next(r for r in face["regions"] if r["kind"] == "fader")
    assert fader["role"] == "a1"
    assert fader["action"] == "set_level"


def test_a_button_gets_its_caption_from_its_serial_join():
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    button = next(r for r in face["regions"] if r["kind"] == "button")
    assert button["target"] == "d4"
    caption = next(r for r in face["regions"] if r["id"] == "d4cap")
    assert caption["role"] == "s4"


def test_a_reserved_join_is_not_a_control():
    """Joins above 17000 drive the panel itself, not the program."""
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    assert not any("18495" in str(r.get("target", "")) for r in face["regions"])


def test_the_page_sets_the_canvas():
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    assert face["size"] == [800, 600]
