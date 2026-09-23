# Crestron CIP Bridge

Connects to Crestron control processors the way an XPanel does, discovers the
joins they report, and lets you choose which become Home Assistant entities.

## Before you start

The bridge registers as an **XPanel**, so each processor needs one defined in
its **compiled program** at the IPID you configure here. An IP table entry on
its own is not enough — the processor will answer, then reject the
registration, because the program has no device at that ID.

If you cannot recompile, you can point the bridge at an IPID an existing
touchpanel already uses. Only one device may hold an IPID at a time, so the
panel will be knocked offline while the bridge is connected.

## Configuration

```yaml
processors:
  - name: Tower
    host: 192.168.33.14
    ipid: 16
  - name: Orchard
    host: 192.168.33.2
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
