# Crestron CIP for Home Assistant — handoff

Everything a new agent needs. The traps here cost real time to find; most are
not documented anywhere public.

## Shape

Two halves, released as a matched pair. CI fails the build if the versions
drift (`.github/workflows/version-check.yml`).

```
crestron-cip/                 the add-on
  src/cip_client.py           CIP codec + connection + probe
  src/join_store.py           discovered joins vs. user intent, persisted
  src/server.py               aiohttp API + SSE stream
  src/main.py                 Supervisor options -> connections -> web server
  static/index.html           ingress UI, no build step, no dependencies
  tests/                      75 tests, pytest
custom_components/crestron_cip/   the integration
```

The add-on owns the protocol and the mapping UI. The integration is a thin
client: it creates entities and writes values back. Keep it that way — the
integration is the half that has to be reviewable.

## The protocol, by observation

CIP is undocumented. This was derived from packet captures and from what the
processors actually do.

- TCP **41794**. The bridge registers as an **XPanel** at a given IPID.
- Packet types: `0x01` register, `0x02` result, `0x03` disconnect, `0x05`
  data, `0x0D`/`0x0E` heartbeat, `0x0F` registration request, `0x12` serial.
- Registration reply we send:
  `01 00 0b 00 00 00 00 00 <ipid> 40 ff ff f1 01`
- **`ff ff 02` means the IPID is not defined in the processor's compiled
  program.** It does *not* mean "no IP table row". Proven: IP table entry 29
  existed and still returned `ff ff 02`, while IPID 4 — a `Gway` type with no
  table row of its own — registered fine. Adding an IP table entry alone will
  not make the bridge work; the program needs an XPanel at that IPID.
- Digital joins are byte-swapped with **bit 7 inverted**, and join 1 is
  encoded as 0. `decode_digital`/`encode_digital` carry the arithmetic.
- After `REQUEST_UPDATE` the processor dumps its joins and ends with `0x1C`.

## The thing that shapes the whole UI

**A processor only reports joins whose value is not the default.** Anything
sitting at 0, false or empty is never sent. Both DMPS units returned exactly
**11 joins** each on a full dump — room names, mic labels, source, system
messages — out of a program with hundreds.

So `discovery_complete` really means "initial dump complete", not "we know
the system". Mapping happens live: press something, watch which join moves.
That is why the API has an SSE stream and the UI has a "watch for changes"
button. Do not replace either with a periodic list refresh.

## Non-obvious traps

- **`close()` clears `registered`.** Assert connection state *before*
  closing, or the assertion passes for the wrong reason.
- **Do not name a module `select.py`** next to code that runs with that
  directory as cwd — it shadows the stdlib `select` and asyncio dies oddly.
  (The integration's platform is `select`-free for this reason; `number.py`
  and `text.py` are safe.)
- **The codec takes header-stripped payloads.** `payload_of(frame)` is
  `frame[3:]`. There are two test runners — `tests/run_tests.py` (stdlib,
  no dependencies) and the pytest suite. Changing the codec means changing
  both; shipping one updated and one not produced 20 failures.
- **`pip` cannot run inside the Claude Code add-on container.** It spawns a
  subprocess and exec of `/lib/ld-musl-x86_64.so.1` is denied. To get test
  dependencies, fetch wheels from PyPI with `urllib` and unzip them —
  `getwheels.py` in the session scratchpad does this. CI is unaffected.
- **An IPID can be shared. This is measured, not assumed.** Two connections
  to one processor on the same IPID both registered and both held for the
  duration of the test, each receiving feedback. An earlier version of this
  file claimed the opposite; that was inferred from the shape of the
  registration exchange and was simply wrong.
  So there is usually no recompile to do — the bridge rides alongside an
  existing panel. `cip_probe.py` is still only *safe* against an IPID you
  believe is undefined, since a probe that succeeds has registered.
- **A new registration makes the processor re-dump to every client.** The
  first connection saw a second full join dump the moment a second one
  attached. `JoinStore.observe()` is idempotent, so this costs nothing, but
  do not read a duplicate dump as a reconnection.

## A processor can stop accepting CIP

Observed on the CP4: it registered at IPID 4, held for about two and a half
minutes, then refused TCP on 41794 from the moment the add-on container was
killed for a version update — and kept refusing.

How to tell what you are looking at, without guessing:

| Symptom | Means |
|---------|-------|
| ICMP replies, 41794 refused, 22 open, 80/443 refused | The box is up and networked, but its program and web server are not running |
| ICMP replies, everything refused | Processor is booting, or the program has stopped |
| No ICMP | Powered off, or off the network |

A refusal is not the same as a rejected registration. `ff ff 02` means the
program has no device at that IPID; `ECONNREFUSED` means nothing is
listening at all.

**It recovered on its own after about twelve minutes**, with no
intervention, and registered on the next scheduled retry. Everything came
back together — CIP, web, telnet — which is what a processor restarting
looks like, not what a held IPID slot looks like. A held slot would have
refused registration while the port stayed open.

So: do not go to site for this, and do not assume an unclean disconnect
caused it. The timing was suggestive and nothing more — there was no prior
baseline for that processor's web ports, and a program can restart for its
own reasons. `close()` now sends a disconnect frame anyway, because the
courtesy costs three bytes.

The practical lesson is the backoff: a flat retry would have hammered a
booting processor 70-odd times. The widening one tried five times and caught
it within two minutes of the port reopening.

## Reading processor health

Load and memory are not in CIP. They come from the text console over SSH,
and getting that working took three attempts:

- `ssh` only accepts a password from a **controlling terminal**, and
  `pty.fork()` is what establishes one.
- Forking a pty from a **worker thread** is unreliable — one processor would
  answer and the next return an empty transcript.
- `openpty` plus `Popen` gives the child **no controlling terminal at all**,
  so every login is refused with "Permission denied".

So the session runs in a **child process**, which forks its pty from its own
main thread. The password goes in on stdin, never argv.

**The first `cpuload` of a session always reads ~100%.** It is measuring the
session starting. Sample twice, discard the first, or report a processor
idling at 16% as pinned.

**Poll slowly.** During development a run of rapid SSH logins to one
processor was followed by it dropping off the network entirely — no ICMP, no
ports. Causation was never proven, but the default is now five minutes,
sequential, ten seconds apart, and it should stay that way.

## Where things stand

- CIP proven against three processors on the development site: one CP4
  running background music, which has no wired XPanel — IPID 4 registered but
  returned 0 joins — and two DMPS units driving function rooms.
- Real joins captured from both DMPS units, 11 each. That number is the
  point: the programs have hundreds.
- **Verified live**: the full add-on stack — connections, store, API, event
  stream — run against both DMPS units at once. Both registered, both
  dumped their joins, all of it arrived over SSE, clean disconnect. What has
  *not* been exercised is the add-on running under Supervisor as a container,
  and the integration creating entities from it.
- IPID 10 was added to the IP tables but no program defines a panel there, so
  it will not register. It is also unnecessary: an existing panel's IPID can
  be shared, so use one of those.
- Still untested: writing a join to a live system. Every test so far has been
  read-only.

## Conventions

Write comments that explain why, at the density of the surrounding code.
Commit messages say what changed and what it was like before. Run
`pytest crestron-cip/tests -q` before pushing; do not deploy ahead of CI.
