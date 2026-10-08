"""Reading a VT Pro-e .vtp project.

A .vtp is an OLE2 compound file holding one MFC-serialised stream. The
fixture builds a minimal valid container rather than shipping a real
1.8 MB project, and the property encoding is the one worked out from the
bytes: three length-prefixed strings in a row, label then value then
internal name.
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import vtp_reader as vr  # noqa: E402

def prop(label: str, value: str, name: str) -> bytes:
    """One property record, as VT Pro-e writes it: three length-prefixed
    strings in a row — label, value, internal name — then metadata."""
    def pstr(text: str) -> bytes:
        return bytes([len(text)]) + text.encode("ascii")
    return pstr(label) + pstr(value) + pstr(name) + b"\x00" * 8


OBJECT = (
    prop("Object Name", "Advanced Button_2", "ObjectName")
    + prop("Press Digital Join", "211", "DigitalPressJoin")
    + prop("Enable Digital Join", "0", "DigitalEnableJoin")
)
SECOND = (
    prop("Object Name", "Liquid Gauge", "ObjectName")
    + prop("Analog Feedback Join", "211", "AnalogFeedbackJoin")
)


def test_a_vtp_is_recognised_by_its_signature():
    assert vr.is_vtp(bytes.fromhex('d0cf11e0a1b11ae1') + b'\x00' * 64)
    assert not vr.is_vtp(b"PK\x03\x04" + b"\x00" * 32)


def test_the_property_encoding_is_read():
    """Label, value, internal name — the value is the middle one."""
    props = vr.read_properties(OBJECT)
    assert ("ObjectName", "Advanced Button_2") in [(n, v) for n, v, _o in props]
    assert ("DigitalPressJoin", "211") in [(n, v) for n, v, _o in props]


def test_objects_are_split_at_each_object_name():
    objects = vr.objects_from_contents(OBJECT + SECOND)
    assert [o["name"] for o in objects] == ["Advanced Button_2", "Liquid Gauge"]


def test_a_join_of_zero_is_not_collected():
    objects = vr.objects_from_contents(OBJECT)
    assert objects[0]["joins"] == {"DigitalPressJoin": 211}


def test_a_project_with_no_joins_is_named_unprogrammed():
    """Two panels on this estate had 52 objects and every join at zero.
    The file is fine; nobody assigned joins."""
    empty = b"".join(
        prop("Object Name", f"Multi-Mode Button_{n}", "ObjectName")
        + prop("Press Digital Join", "0", "DigitalPressJoin")
        for n in range(1, 5))
    summary = vr.describe(empty)
    assert summary["objects"] == 4
    assert summary["with_joins"] == 0
    assert summary["unprogrammed"]


def test_a_programmed_project_is_not_called_unprogrammed():
    summary = vr.describe(OBJECT + SECOND)
    assert summary["with_joins"] == 2
    assert summary["distinct_joins"] == 1    # both bind 211
    assert not summary["unprogrammed"]


def test_a_file_that_is_not_ole2_is_refused():
    with pytest.raises(vr.VtpError):
        vr.read_objects(b"PK\x03\x04" + b"\x00" * 64)


def test_a_truncated_compound_file_fails_as_a_vtp_error():
    """Not as an IndexError out of the sector arithmetic, which is what a
    caller cannot act on."""
    with pytest.raises(vr.VtpError):
        vr.read_objects(bytes.fromhex("d0cf11e0a1b11ae1") + b"\x00" * 600)


def _real_project() -> Path | None:
    """A real .vtp to test the container against, if one is to hand.

    Point CRESTRON_VTP at a project file, or drop one in tests/fixtures/.
    Real projects are megabytes and site-specific, so none is committed —
    and hand-building a valid OLE2 container for a fixture turned out to
    be more error-prone than the code it would be testing.
    """
    named = os.environ.get("CRESTRON_VTP")
    if named and Path(named).is_file():
        return Path(named)
    local = Path(__file__).resolve().parent / "fixtures"
    return next(iter(sorted(local.glob("*.vtp"))), None)


@pytest.mark.skipif(_real_project() is None,
                    reason="set CRESTRON_VTP to a .vtp to test the container")
def test_the_container_reads_a_real_project():
    """The OLE2 path, end to end on an actual VT Pro-e file."""
    objects = vr.read_objects(_real_project().read_bytes())
    assert objects, "no objects read from a real project"
    assert all("name" in o and "joins" in o for o in objects)


def test_noise_between_records_does_not_derail_the_walk():
    """A megabyte of mixed binary throws up plenty of byte sequences that
    look like a short string."""
    noisy = b"\x05\x00\x01\xff\x7f" + OBJECT + b"\xfe" * 9 + SECOND
    objects = vr.objects_from_contents(noisy)
    assert [o["name"] for o in objects] == ["Advanced Button_2", "Liquid Gauge"]
