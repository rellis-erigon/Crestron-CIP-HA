"""Reading a compiled Crestron program.

The fixtures are the shapes of a real archive pulled off a DMPS, minus
the 60 KB of compiled program around them.
"""
import io
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import program_reader as pg  # noqa: E402


def u16(*strings: str) -> bytes:
    """Strings as they sit in the binary: UTF-16, packed, no framing."""
    return b"\x00\x00".join(s.encode("utf-16-le") for s in strings)


def archive(**files: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for suffix, data in files.items():
            zf.writestr(f"NAME.{suffix}", data)
    return buffer.getvalue()


DSC = (
    "[DSC0003]\n"
    "Slot : 7.IP-ID : 03;8;Touchpanels(Enet);TSW-770;;TSW-770;-12\n"
    "Slot : 7.IP-ID : 04;11;Touchpanels(Enet);TSW-770;;TSW-770;-12\n"
    "Slot : 7.IP-ID : 11;14;;;;RoomView Connected Display;-12\n"
).encode()

CLOUD = (
    "[LG_CLOUD_INFORMATION]\n"
    "NumDevices=3\n"
    "DeviceIndex1=8\nDeviceType1=1\nDeviceName1=TSW-770\nDeviceComment1=Funct Rm 1 TP\n"
    "DeviceIndex2=11\nDeviceType2=1\nDeviceName2=TSW-770\nDeviceComment2=Funct Rm 2 TP\n"
    "DeviceIndex3=14\nDeviceType3=1\nDeviceName3=RoomView Connected Display\n"
    "DeviceComment3=Funct Rm 1 Proj\n"
).encode()

DIP = "[IPTable]\nid0=03\naddr0=127.0.0.1\nid1=21\naddr1=10.0.0.9\n".encode()


# -- the device table ---------------------------------------------------

def test_the_device_table_is_read():
    program = pg.read_program(archive(dsc=DSC, cloud=CLOUD))
    panels = [d for d in program["devices"] if d["model"] == "TSW-770"]
    assert [p["ipid"] for p in panels] == [3, 4]
    # The only human name for a panel anywhere in the files.
    assert panels[0]["comment"] == "Funct Rm 1 TP"


def test_an_ip_id_is_shown_the_way_crestron_writes_it():
    # IP-IDs are hex everywhere a person sees one — on the panel, in
    # Toolbox, on the label. Showing 17 for what the installer calls 11
    # invites a wrong entry.
    program = pg.read_program(archive(dsc=DSC, cloud=CLOUD))
    display = next(d for d in program["devices"] if d["ipid_hex"] == "11")
    assert display["ipid"] == 17


def test_the_model_is_preferred_over_the_category():
    # The .dsc has "Touchpanels(Enet)" where the .cloud has "TSW-770".
    program = pg.read_program(archive(dsc=DSC, cloud=CLOUD))
    assert program["devices"][0]["model"] == "TSW-770"
    assert program["devices"][0]["category"] == "Touchpanels(Enet)"


def test_addresses_come_from_the_ip_table():
    program = pg.read_program(archive(dsc=DSC, cloud=CLOUD, dip=DIP))
    assert program["devices"][0]["address"] == "127.0.0.1"


def test_a_panel_is_recognised_by_its_model():
    program = pg.read_program(archive(dsc=DSC, cloud=CLOUD))
    panels = pg.summarise(program)["panels"]
    assert [p["ipid_hex"] for p in panels] == ["03", "04"]


# -- what the binary gives up -------------------------------------------

HEADER = u16(
    "LeadingREM",
    "Program File: NAME.smw",
    "System Name:  Function Rooms",
    "Programmer:   JT",
    "Compiled On:  6/2/2022 1:38 PM",
    "Target Rack:  DMPS3-4K-350-C",
)


def test_the_header_says_what_it_runs_on():
    program = pg.read_program(archive(bin=HEADER))
    assert program["system"]["target_rack"] == "DMPS3-4K-350-C"
    assert program["system"]["system_name"] == "Function Rooms"
    assert program["system"]["programmer"] == "JT"


def test_named_controls_survive_compilation():
    # The single most useful thing in the file: what the faders move.
    data = HEADER + u16(
        "S-2.3.2.1.1:S-1", "Q_SYS_Named_Control_Object_V5_0.dll", "Func1MicGain",
        "S-2.3.2.1.2:S-1", "Q_SYS_Named_Control_Object_V5_0.dll", "Func1MicMute",
    )
    program = pg.read_program(archive(bin=data))
    assert program["named_controls"] == ["Func1MicGain", "Func1MicMute"]


def test_a_login_is_counted_and_never_read_back():
    # A connection module's parameters are its address and credentials,
    # and a real program has them in clear. They must not reach a card,
    # a log or a stored file.
    data = HEADER + u16(
        "S-1.4.1:S-1", "Q_SYS_Core_V5_0_0.dll", "theaccount", "thepassword!",
        "S-2.1.1", "Orchard Room 1",
    )
    program = pg.read_program(archive(bin=data))
    assert program["contains_credentials"] is True
    blob = repr(program)
    assert "thepassword!" not in blob
    assert "theaccount" not in blob
    assert any("clear text" in w for w in program["warnings"])


def test_the_labels_a_program_can_print_are_kept():
    data = HEADER + u16(
        "S-2.1.1", "Orchard Room 1", "Source", "Orchard Rm 1 Mic",
        "System is Starting up.  Please wait......",
        "S-2.1.2", "Slot-01", "IP-ID-03",
    )
    labels = pg.read_program(archive(bin=data))["labels"]
    assert "Orchard Room 1" in labels
    assert "Orchard Rm 1 Mic" in labels
    # Symbol paths and slot markers are structure, not words.
    assert not any(l.startswith(("S-", "Slot-", "IP-ID-")) for l in labels)


def test_a_trailing_byte_does_not_become_part_of_a_name():
    # Strings are packed against the next record, so a run often picks up
    # one extra character: "Source\"" for "Source".
    data = HEADER + u16("S-2.1.1", 'Source"', "Orchard Rm 1 Mic\"")
    labels = pg.read_program(archive(bin=data))["labels"]
    assert "Source" in labels
    assert "Orchard Rm 1 Mic" in labels


# -- being handed the wrong thing ---------------------------------------

def test_a_bare_binary_is_accepted():
    # Somebody will hand over the .bin rather than the archive.
    program = pg.read_program(HEADER)
    assert program["system"]["target_rack"] == "DMPS3-4K-350-C"


def test_something_that_is_not_a_program_is_refused():
    with pytest.raises(pg.ProgramReadError):
        pg.read_program(archive(txt=b"notes to self"))


def test_a_device_table_alone_is_still_worth_having():
    # Many compilers, many archives. Anything unparseable is skipped
    # rather than failed, because the device table alone names the rooms.
    program = pg.read_program(archive(dsc=DSC, cloud=CLOUD))
    assert len(program["devices"]) == 3
    assert any("no compiled program" in w for w in program["warnings"])


def test_a_program_with_no_device_table_says_so():
    program = pg.read_program(archive(bin=HEADER))
    assert any("no device table" in w for w in program["warnings"])
