"""Codec + framing unit tests (plain asserts, no pytest needed)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _pkg  # noqa: E402,F401

from crestron_cip import protocol as p


def test_digital():
    assert p.pack_digital(1, True) == bytes([0x00, 0x00])
    assert p.pack_digital(1, False) == bytes([0x00, 0x80])
    assert p.pack_digital(300, True) == bytes([0x2B, 0x01])
    assert p.pack_digital(256, True) == bytes([0xFF, 0x00])
    for join in (1, 2, 255, 256, 4000, 32767):
        for val in (True, False):
            assert p.unpack_digital(p.pack_digital(join, val)) == (join, val)


def test_analog():
    assert p.pack_analog(5, 32000) == bytes([0x00, 0x04, 0x7D, 0x00])
    assert p.pack_analog(1, 0) == bytes([0x00, 0x00, 0x00, 0x00])
    for join in (1, 100, 2000, 65535):
        for val in (0, 1, 255, 32768, 65535):
            assert p.unpack_analog(p.pack_analog(join, val)) == (join, val)


def test_serial():
    assert p.pack_serial(10, "Hi") == bytes([0x00, 0x09, 0x00]) + b"Hi"
    assert p.unpack_serial(p.pack_serial(10, "Hi")) == (10, "Hi")
    assert p.unpack_serial(p.pack_serial(7, "Zone 1", unicode=True)) == (7, "Zone 1")
    assert p.pack_serial(7, "x", unicode=True)[2] & 0x04  # unicode flag set


def test_frame():
    f = p.frame(p.PacketType.DATA, b"\x01\x02\x03")
    assert f == bytes([0x05, 0x00, 0x03, 0x01, 0x02, 0x03])
    buf = bytearray(f + p.frame(p.PacketType.HEARTBEAT))
    frames = list(p.iter_frames(buf))
    assert frames == [(0x05, b"\x01\x02\x03"), (0x0D, b"")]
    assert len(buf) == 0  # fully consumed


def test_cresnet_single():
    rec = p.pack_digital(5, True)
    payload = p.pack_cresnet(p.CresnetType.DIGITAL, rec)
    assert payload == bytes([0x00, 0x00, 0x03, 0x00]) + rec  # 0x00 prefix + [len:2][type]
    got = list(p.iter_cresnet_records(payload))
    assert got == [(0x00, rec)]
    joins = [j for ct, d in got for j in p.split_joins(ct, d)]
    assert joins == [("digital", 5, True)]


def test_cresnet_multi_and_subheader():
    recs = p.pack_digital(5, True) + p.pack_digital(6, False)
    payload = p.pack_cresnet(p.CresnetType.DIGITAL, recs)  # 2 joins in one frame
    joins = [j for ct, d in p.iter_cresnet_records(payload) for j in p.split_joins(ct, d)]
    assert joins == [("digital", 5, True), ("digital", 6, False)]

    analog = p.pack_cresnet(p.CresnetType.ANALOG, p.pack_analog(9, 1234) + p.pack_analog(10, 5678))
    aj = [j for ct, d in p.iter_cresnet_records(analog) for j in p.split_joins(ct, d)]
    assert aj == [("analog", 9, 1234), ("analog", 10, 5678)]

    with_sub = b"\x00\x00" + p.pack_cresnet(p.CresnetType.SERIAL, p.pack_serial(3, "OK"))
    sj = [j for ct, d in p.iter_cresnet_records(with_sub) for j in p.split_joins(ct, d)]
    assert sj == [("serial", 3, "OK")]


def test_smartobject():
    # frame payloads captured from a live processor (type 0x38 SmartObject serial items)
    payload = bytes.fromhex("00001338000000010d15001e03506f6f6c2041726561")
    joins = [j for ct, d in p.iter_cresnet_records(payload) for j in p.split_joins(ct, d)]
    assert joins == [("serial", 31, "Pool Area")], joins
    payload2 = bytes.fromhex("00000d3800000001071500140347796d")
    j2 = [j for ct, d in p.iter_cresnet_records(payload2) for j in p.split_joins(ct, d)]
    assert j2 == [("serial", 21, "Gym")], j2


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print(f"  ok  {t.__name__}")
    print(f"PASSED {len(tests)} protocol tests")


if __name__ == "__main__":
    main()
