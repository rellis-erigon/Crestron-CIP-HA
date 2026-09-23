"""Tests for parsing the Crestron console.

The transcripts here are real output from the processors, including their
quirks — which is the whole reason the parser looks the way it does.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from console import Health, parse_health, read_health  # noqa: E402

# A whole session, exactly as a DMPS prints it.
DMPS_SESSION = """
DMPS3-4K-350-C Console

DMPS3-4K-350-C>CPU:LOAD 99%

DMPS3-4K-350-C>CPU:LOAD 16%

DMPS3-4K-350-C>CPU:LOAD 14%

DMPS3-4K-350-C>32 percent of memory in use
432463872 total bytes of physical memory
137728000 bytes actually used
294735872 bytes free
0 bytes reclaimable

DMPS3-4K-350-C>The system has been running for 34 days 21:23:27.717
The system last started on: Thursday, August 20, 2026 at 11:22:40

DMPS3-4K-350-C>DMPS3-4K-350-C Cntrl Eng [v1.8001.4669.27251 (Oct 14 2021), #9312014A]
"""


def test_the_first_cpu_sample_is_discarded():
    """It measures the console session starting and always reads near 100%.

    Keeping it would report a processor sitting at 15% as pinned.
    """
    health = parse_health(DMPS_SESSION)
    assert health.cpu_percent == 15.0


def test_a_single_cpu_sample_is_used_rather_than_thrown_away():
    """One reading is poor, but better than reporting nothing at all."""
    health = parse_health("CPU:LOAD 42%")
    assert health.cpu_percent == 42.0


def test_memory_is_read_as_percentage_and_bytes():
    health = parse_health(DMPS_SESSION)
    assert health.memory_percent == 32.0
    assert health.memory_total == 432463872
    assert health.memory_used == 137728000
    assert health.memory_free == 294735872


def test_a_percentage_is_derived_when_only_bytes_are_given():
    health = parse_health(
        "1000 total bytes of physical memory\n250 bytes actually used"
    )
    assert health.memory_percent == 25.0


def test_uptime_is_seconds():
    health = parse_health(DMPS_SESSION)
    # 34 days, 21:23:27
    assert health.uptime_seconds == 34 * 86400 + 21 * 3600 + 23 * 60 + 27


def test_uptime_without_days_still_parses():
    health = parse_health("The system has been running for 02:15:30.1")
    assert health.uptime_seconds == 2 * 3600 + 15 * 60 + 30


def test_firmware_comes_from_the_version_banner():
    assert parse_health(DMPS_SESSION).firmware == "1.8001.4669.27251"


def test_a_partial_reading_is_kept():
    """A CP4 answers a slightly different set from a DMPS."""
    health = parse_health("27 percent of memory in use")
    assert health.memory_percent == 27.0
    assert health.cpu_percent is None
    assert not health.ok


def test_nothing_recognisable_is_not_ok():
    health = parse_health("Unknown command\n")
    assert not health.ok
    assert health.cpu_percent is None


def test_a_good_reading_is_ok():
    assert parse_health(DMPS_SESSION).ok


def test_to_dict_is_json_safe():
    data = parse_health(DMPS_SESSION).to_dict()
    assert data["cpu_percent"] == 15.0 and data["ok"] is True
    assert set(data) >= {"cpu_percent", "memory_percent", "uptime_seconds",
                         "firmware", "error", "at", "ok"}


async def test_no_credentials_is_reported_rather_than_attempted():
    health = await read_health("192.0.2.10", "", "")
    assert not health.ok
    assert "credentials" in health.error


async def test_a_failure_becomes_an_error_not_an_exception(monkeypatch):
    """Health must never be able to break the bridge."""
    import console

    async def boom(*args, **kwargs):
        raise OSError("network unreachable")

    monkeypatch.setattr(console, "_run_session", boom)
    health = await read_health("192.0.2.10", "user", "pass", timeout=5)
    assert not health.ok
    assert "unreachable" in health.error


async def test_a_password_never_survives_into_the_transcript(monkeypatch):
    import console

    async def echo(host, username, password, commands, idle):
        return f"login: {password}\nCPU:LOAD 5%\nCPU:LOAD 7%"

    monkeypatch.setattr(console, "_run_session", echo)
    health = await read_health("192.0.2.10", "user", "hunter2")
    # The scrub happens inside _run_session in production; here we only need
    # to know the parser never carries the transcript forward.
    assert health.cpu_percent == 7.0
    assert "hunter2" not in str(health.to_dict())
