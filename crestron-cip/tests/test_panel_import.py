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


# -- assigning a panel to a processor ------------------------------------

import panel_apply as pa  # noqa: E402


class FakeStore:
    """Enough of JoinStore to watch what apply_panel does."""

    def __init__(self):
        self.joins = {}
        self.saved = False

    def observe(self, processor, signal, number, value):
        key = f"{processor}/{signal}{number}"
        self.joins[key] = {"config": {}}
        return self.joins[key]

    def configure(self, processor, join_key, **changes):
        self.joins[f"{processor}/{join_key}"]["config"].update(changes)

    def save(self):
        self.saved = True


def test_every_join_the_panel_uses_is_listed():
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    found = {f"{s}{n}" for s, n, _ in pa.panel_joins(face)}
    assert found == {"a1", "d4", "s4"}


def test_a_reserved_join_is_not_configured():
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    assert not any(n >= pi.RESERVED_FROM for _, n, _ in pa.panel_joins(face))


def test_joins_are_created_before_any_traffic():
    """CIP only reports joins that are off their default, so waiting for
    the processor to mention one leaves the panel half-configured until
    somebody presses every button on it."""
    store = FakeStore()
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    counts = pa.apply_panel(store, "BGM", face)
    assert counts["created"] == 3
    assert set(store.joins) == {"BGM/a1", "BGM/d4", "BGM/s4"}
    assert store.saved


def test_each_join_gets_a_sensible_platform():
    store = FakeStore()
    pa.apply_panel(store, "BGM", pi.to_faceplate(pi.read_objects(archive(PROJECT))))
    # A panel button is momentary, so a button rather than a switch.
    assert store.joins["BGM/d4"]["config"]["kind"] == "button"
    assert store.joins["BGM/a1"]["config"]["kind"] == "number"
    assert store.joins["BGM/s4"]["config"]["kind"] == "sensor"


def test_the_panel_becomes_one_device():
    store = FakeStore()
    pa.apply_panel(store, "Reception", pi.to_faceplate(pi.read_objects(archive(PROJECT))))
    assert {j["config"]["group"] for j in store.joins.values()} == {"Reception"}


def test_joins_are_exposed_unless_asked_otherwise():
    store = FakeStore()
    face = pi.to_faceplate(pi.read_objects(archive(PROJECT)))
    pa.apply_panel(store, "BGM", face, expose=False)
    assert all(not j["config"]["enabled"] for j in store.joins.values())
