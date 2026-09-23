# Crestron CIP Bridge

Connects to Crestron control processors the way an XPanel does, discovers the
joins they report, and lets you choose which become Home Assistant entities.

## Before you start

The bridge registers as an **XPanel**, so each processor needs a panel
defined in its **compiled program** at the IPID you configure here. An IP
table entry on its own is not enough — the processor will answer, then reject
the registration, because the program has no device at that ID.

**You can usually share an IPID with an existing panel.** Two connections on
one IPID have been measured holding at the same time: both registered, both
receiving the same feedback, neither displacing the other. So in most cases
there is no SIMPL work to do — point the bridge at an IPID a panel already
uses and leave the panel where it is.

Two things follow from how the processor behaves here:

- When a new connection registers, the processor re-sends its joins to
  *every* attached client. A duplicate dump is normal and harmless.
- Both ends can drive the system. The bridge writing a join looks exactly
  like the panel doing it, so an automation and a person pressing the panel
  can contend. That is a design question for your automations, not a fault.

Confirm sharing on your own system before relying on it. A panel that drops
is not subtle, but it is also not something you want to discover during an
event.

## Configuration

```yaml
processors:
  - name: Main Hall
    host: 192.0.2.10
    ipid: 16
  - name: Meeting Room
    host: 192.0.2.11
    ipid: 16
log_level: info
```

| Option | Meaning |
|--------|---------|
| `name` | What the processor is called in Home Assistant. Each becomes a device. |
| `host` | The processor's IP address. CIP is TCP port 41794. |
| `ipid` | The IPID of the XPanel in that processor's program, 2–254. |
| `log_level` | `debug` logs every join that moves, which is a lot on a busy system. |

## Finding your joins

A processor only reports joins whose value is **not** the default. A panel
with hundreds of joins may announce a dozen on connect — the rest appear as
the system is used.

So mapping a system is done live:

1. Open the add-on panel.
2. Press **Watch for changes** to clear the highlight.
3. Press something on the touchpanel, or in the room.
4. The join that moved appears at the top, highlighted.
5. Name it, choose what it should become, and switch it on.

## What a join can become

| Signal | Kinds |
|--------|-------|
| Digital (`d`) | Binary sensor, Switch, Button |
| Analog (`a`) | Sensor, Number |
| Serial (`s`) | Sensor, Text |

A **button** pulses the join — press and release, the way a panel button
behaves. A **switch** holds it. Most panel buttons want the former; using a
switch leaves the program latched on.

Analog joins are 0–65535 on the wire. If a join really means 0–100%, set the
scale to `0.0015259` (100 ÷ 65535) and a unit of `%`.

## Nothing is lost on rediscovery

What you decide about a join — its name, kind and whether it is exposed — is
kept separately from what the processor reports, and survives restarts,
reconnections and program reloads.
