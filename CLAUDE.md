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
- **Hijacking an IPID knocks the real panel offline** for as long as the
  bridge holds it. Only one device may hold an IPID at a time. `cip_probe.py`
  is safe only against an IPID you believe is *undefined*.

## Where things stand

- CIP proven against three processors on the development site: one CP4
  running background music, which has no wired XPanel — IPID 4 registered but
  returned 0 joins — and two DMPS units driving function rooms.
- Real joins captured from both DMPS units, 11 each. That number is the
  point: the programs have hundreds.
- **Not yet verified live**: the add-on running as an add-on, against a real
  processor, end to end. It has been smoke-tested with a stubbed connection.
- IPID 10 was added to the IP tables but there is still no XPanel at that
  IPID in any compiled program, so it will not register. Either recompile
  with an XPanel, or point the bridge at an existing panel's IPID and accept
  that the panel drops while it is connected.
- The repository is **private**, so Supervisor cannot add it as an add-on
  store. It needs to be public, or the add-on copied into `/addons`.

## Conventions

Write comments that explain why, at the density of the surrounding code.
Commit messages say what changed and what it was like before. Run
`pytest crestron-cip/tests -q` before pushing; do not deploy ahead of CI.
