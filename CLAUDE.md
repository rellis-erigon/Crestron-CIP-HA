# Crestron CIP for Home Assistant — handoff

Everything a new agent needs. The traps here cost real time to find; most are
not documented anywhere public.

## Shape

One custom integration, standard library only. Since 0.15.0 there is no
add-on; up to 0.14.3 there was, and that code is on the `addon-0.14.3`
branch. Its add-on held a mapping UI, panel-project readers and SSH health
polling that this version does not have. Look there before rewriting any of
them.

```
custom_components/crestron_cip/
  protocol.py        framing, enums, join codecs, SmartObject parsing
  client.py          asyncio CIP client: register, heartbeat, read, write
  hub.py             one client per config entry; dispatcher signals; reconnect
  project_loader.py  .c3p / panel .zip -> join map (labels, press joins)
  join_map.py        the join map model
  entity.py          base for join entities
  binary_sensor.py sensor.py number.py button.py
tests/               plain scripts; _pkg.py loads the package without HA
```

`protocol.py` and `client.py` import nothing from Home Assistant. Keep it
that way: the tests depend on it.

## The protocol, by observation

CIP is undocumented. This came from packet captures and from the XPanel
engine.

- TCP **41794** (41796 for TLS). Outer frame `[type:1][len:2 BE][payload]`.
- Register: type `0x01`, body `00 00 00 00 00 <ipid> 40`. Reply type `0x02`.
- **`ff ff 02` in the reply means the IPID is not defined in the
  processor's compiled program.** It does not mean "no IP table row".
  Adding an IP table entry alone will not make the connection work; the
  program needs a panel at that IPID.
- Heartbeat `0x0D` every 15 s, answered with `0x0E`.
- DATA payload: `[0x00][len:2 BE][cresnet type][records]`.
- Digital joins: two bytes, little-endian, **bit 7 of the high byte
  inverted** (set = off), and join 1 is encoded as 0.
- Analog: four bytes, big-endian join then value. Serial: join, a format
  byte (`0x04` = UTF-16), then the text with no terminator.
- SmartObject (`0x38`/`0x39`): a 4-byte object id, then items of
  `[len:1][type:1][record]`, recursed.

## The thing that shapes the whole design

**A processor only reports joins whose value is not the default.** Anything
at 0, false or empty is never sent. Two DMPS units returned 11 joins each
from programs with hundreds. So entities are created as joins arrive, and
mapping is done live: press something, watch which join moves.

## Non-obvious traps

- **An IPID can be shared on some processors and not others.** On a DMPS,
  two connections on one IPID both registered and both received feedback.
  On a CP4, a second connection on the panel's IPID left the real panel
  without data, and the processor later refused CIP until it was rebooted.
  Treat sharing as something to test per processor.
- **A new registration makes the processor re-dump to every client.** Do
  not read a duplicate dump as a reconnection.
- **A processor can stop accepting CIP for minutes and recover on its
  own.** Seen as ICMP replying while 41794 is refused. Do not send anyone to
  site before checking whether it comes back; the reconnect backoff is
  there for this.
- **`pip` cannot run inside the Claude Code add-on container.** The tests
  need no dependencies for that reason.

## Where things stand

- Registration, heartbeat, feedback and SmartObject lists are verified
  read-only against real processors.
- **Writing a join to a live system has never been tested.** Analog numbers
  start disabled for that reason. Test writes with someone present, on an
  IPID nothing else uses.
- Not yet run inside Home Assistant against a real processor: the reconnect
  path in `hub.py`.

## Conventions

Write comments that explain why, at the density of the surrounding code.
Commit messages say what changed and what it was like before. Run the tests
and `scripts/check-no-site-detail.py` before pushing.
