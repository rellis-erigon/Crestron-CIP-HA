"""Read processor health from the Crestron text console over SSH.

CIP carries joins and nothing else, so load and memory have to come from the
console. There is no library for this: the processors run an old SSH stack
that needs legacy key exchange enabled explicitly, and they present a plain
text console rather than a shell. So the system `ssh` binary is driven
directly and its output parsed.

Two things learned from the hardware:

  - The first `cpuload` of a session always reports ~100%. It is measuring
    the console session starting up. Sample twice and discard the first, or
    report a processor as pinned when it is sitting at 16%.
  - Prompts differ between models ("CP4>" against "DMPS3-4K-350-C>"), and
    matching them proved brittle, so the driver waits for the output to go
    quiet instead.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field

logger = logging.getLogger("crestron-cip.console")

# Legacy algorithms these processors still require.
SSH_OPTIONS = [
    "-tt",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "PubkeyAuthentication=no",
    "-o", "HostKeyAlgorithms=+ssh-rsa",
    "-o", "KexAlgorithms=+diffie-hellman-group14-sha1",
    "-o", "ConnectTimeout=10",
    "-o", "LogLevel=ERROR",
]

# Commands sent every poll. cpuload appears twice deliberately.
HEALTH_COMMANDS = ("cpuload", "cpuload", "ramfree", "uptime", "ver")

_CPU_RE = re.compile(r"CPU:\s*LOAD\s+(\d+)\s*%", re.I)
_MEM_PERCENT_RE = re.compile(r"(\d+)\s*percent of memory in use", re.I)
_MEM_TOTAL_RE = re.compile(r"(\d+)\s+total bytes of physical memory", re.I)
_MEM_USED_RE = re.compile(r"(\d+)\s+bytes actually used", re.I)
_MEM_FREE_RE = re.compile(r"(\d+)\s+bytes free", re.I)
_UPTIME_RE = re.compile(
    r"running for\s+(?:(\d+)\s+days?\s+)?(\d+):(\d+):(\d+)", re.I
)
_VERSION_RE = re.compile(r"\[v([\d.]+)")


@dataclass
class Health:
    """One processor's self-reported health."""

    cpu_percent: float | None = None
    memory_percent: float | None = None
    memory_total: int | None = None
    memory_used: int | None = None
    memory_free: int | None = None
    uptime_seconds: int | None = None
    firmware: str = ""
    error: str = ""
    at: float = field(default_factory=time.time)

    @property
    def ok(self) -> bool:
        return not self.error and self.cpu_percent is not None

    def to_dict(self) -> dict:
        return {
            "cpu_percent": self.cpu_percent,
            "memory_percent": self.memory_percent,
            "memory_total": self.memory_total,
            "memory_used": self.memory_used,
            "memory_free": self.memory_free,
            "uptime_seconds": self.uptime_seconds,
            "firmware": self.firmware,
            "error": self.error,
            "at": self.at,
            "ok": self.ok,
        }


def parse_health(output: str) -> Health:
    """Turn a console transcript into numbers.

    Everything is optional: a CP4 answers a slightly different set from a
    DMPS, and a partial reading is worth more than none.
    """
    health = Health()

    loads = [int(m) for m in _CPU_RE.findall(output)]
    if loads:
        # The first sample measures the session starting and always reads
        # near 100%. Anything after it is the real figure.
        useful = loads[1:] or loads
        health.cpu_percent = round(sum(useful) / len(useful), 1)

    match = _MEM_PERCENT_RE.search(output)
    if match:
        health.memory_percent = float(match.group(1))
    for regex, attribute in (
        (_MEM_TOTAL_RE, "memory_total"),
        (_MEM_USED_RE, "memory_used"),
        (_MEM_FREE_RE, "memory_free"),
    ):
        found = regex.search(output)
        if found:
            setattr(health, attribute, int(found.group(1)))

    # Derive the percentage when the processor gave byte counts but no
    # percentage of its own.
    if (health.memory_percent is None and health.memory_total
            and health.memory_used):
        health.memory_percent = round(
            health.memory_used / health.memory_total * 100, 1
        )

    match = _UPTIME_RE.search(output)
    if match:
        days = int(match.group(1) or 0)
        health.uptime_seconds = (
            days * 86400 + int(match.group(2)) * 3600
            + int(match.group(3)) * 60 + int(match.group(4))
        )

    match = _VERSION_RE.search(output)
    if match:
        health.firmware = match.group(1)

    return health


async def read_health(
    host: str, username: str, password: str,
    commands: tuple[str, ...] = HEALTH_COMMANDS,
    idle: float = 2.0, timeout: float = 150.0,
) -> Health:
    """Open a console session, run the commands, and parse what comes back."""
    if not username:
        return Health(error="no console credentials configured")

    try:
        transcript = await asyncio.wait_for(
            _run_session(host, username, password, commands, idle), timeout
        )
    except asyncio.TimeoutError:
        return Health(error="console session timed out")
    except FileNotFoundError:
        return Health(error="ssh is not available in this image")
    except Exception as err:  # noqa: BLE001 - health must never break the bridge
        return Health(error=str(err))

    health = parse_health(transcript)
    if not health.ok and not health.error:
        health.error = "console returned nothing recognisable"
    return health


async def _run_session(
    host: str, username: str, password: str,
    commands: tuple[str, ...], idle: float,
) -> str:
    """Run the console session in a child process and collect its transcript.

    This looks like more machinery than it should be, and the reason is
    specific: `ssh` only accepts a password from a controlling terminal, and
    `pty.fork()` is what establishes one. Forking a pty from a worker thread
    was unreliable — one processor would answer and the next return nothing
    — and `openpty` plus `Popen` gives the child no controlling terminal at
    all, so every login was refused.

    A child process forks its pty from its own main thread, which is the
    arrangement that actually works. The password goes in on stdin rather
    than argv, so it never appears in a process listing.
    """
    import os
    import sys

    process = await asyncio.create_subprocess_exec(
        sys.executable, os.path.abspath(__file__),
        host, username, str(idle), *commands,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await process.communicate((password + "\n").encode())
    return stdout.decode(errors="replace")


_PASSWORD_PROMPT = re.compile(r"[Pp]assword:\s*$")


def _session_blocking(
    host: str, username: str, password: str,
    commands: tuple[str, ...], idle: float,
) -> str:
    """Drive one console session over a pty. Runs in its own process."""
    import os
    import pty
    import select

    overall = 30.0 + 14.0 * len(commands)
    argv = ["ssh", *SSH_OPTIONS, f"{username}@{host}"]

    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.execvp("ssh", argv)
        finally:
            os._exit(127)

    buffer = ""
    collected: list[str] = []
    queue = list(commands)
    sends = 0
    now = time.time()
    last_rx = last_tx = now
    deadline = now + overall

    try:
        while time.time() < deadline:
            ready, _, _ = select.select([fd], [], [], 0.5)
            now = time.time()
            if ready:
                try:
                    chunk = os.read(fd, 65536).decode("utf-8", "replace")
                except OSError:
                    # The child exited; a pty reports EIO rather than EOF.
                    break
                if not chunk:
                    break
                buffer += chunk
                collected.append(chunk)
                last_rx = now
                continue

            if (_PASSWORD_PROMPT.search(buffer[-200:]) and sends < 2
                    and now - last_tx > 1.0):
                os.write(fd, (password + "\r").encode())
                sends, last_tx, buffer = sends + 1, now, ""
                continue

            # Quiet, and authenticated: it is waiting for the next command.
            if sends and now - last_rx > idle and now - last_tx > idle:
                if queue:
                    os.write(fd, (queue.pop(0) + "\r").encode())
                    last_tx = now
                else:
                    break
    finally:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            pass

    transcript = "".join(collected)
    # A password must never survive into a log or an API response.
    return transcript.replace(password, "********") if password else transcript


if __name__ == "__main__":
    # Child-process entry point. Password arrives on stdin so that it never
    # appears in a process listing.
    import sys

    _host, _user, _idle, *_commands = sys.argv[1:]
    _password = sys.stdin.readline().rstrip("\n")
    sys.stdout.write(
        _session_blocking(_host, _user, _password, tuple(_commands), float(_idle))
    )
