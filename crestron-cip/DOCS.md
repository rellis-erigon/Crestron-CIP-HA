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
| `host` | The processor's IP address. |
| `ipid` | The IPID of the XPanel in that processor's program, 2–254. |
| `port` | Optional. CIP is usually 41794; a panel project states which. |
| `log_level` | `debug` logs every frame, which is a lot on a busy system. |

You do not have to fill this in by hand. Importing a panel project adds
its processor for you — see **Loading an XPanel** below.

## Loading an XPanel

A panel project already says which joins exist, where each control sits
and what it does. That is the whole configuration, so loading one does
the lot rather than leaving you to type it in again.

**You need a compiled panel: `.c3p` or `.vtz`.** A `.vtp` is the VT Pro-e
source and cannot be read — compile it first. The add-on will say so if
you try.

1. Open the add-on and press **Load panel**.
2. Choose the file, and give it a **name**. This becomes both the
   processor's name and the Home Assistant device's name.
3. Fill in **Processor IP**, **IPID** and **Port**. The IPID is the one
   the panel is configured for, which you can read in VT Pro-e under the
   project's Ethernet settings.
4. Press **Load and assign**.

The add-on then, in one go:

- reads the object tree, with each control's position and joins;
- adds the processor and connects to it immediately — no restart;
- creates every join the panel uses, before any traffic, because CIP
  only reports joins that are off their default and waiting would leave
  the panel half-configured until somebody pressed every button on it;
- names each one and picks its platform — a fader becomes a `number`, a
  panel button a `button` because it is momentary, text a `sensor`;
- exposes them and groups them into one device.

You should see something like *14 controls · 800×600 · processor added ·
9 joins configured*.

### Getting the card

One step is left, and it cannot be automated: the add-on knows the join
numbers but has no idea what those joins became in Home Assistant. Only
the entity registry knows that, so the integration finishes the job.

1. **Developer tools → Actions**.
2. Run `crestron_cip.generate_panel_card`.
3. Copy the `yaml` from the response.
4. On a dashboard: **Add card → Manual**, and paste.

The card is `custom:crestron-panel-card` from the
[HA-rellis-erigon-Cards](https://github.com/rellis-erigon/HA-rellis-erigon-Cards)
repository, which must be installed and added as a dashboard resource.
Unlike the other cards there, the layout is not from the bundled
catalogue — it travels inside the card configuration, because a panel
belongs to one site.

### What gets drawn

| Panel object | Becomes |
|--------------|---------|
| Fader with an analog feedback join | a fader you can click to position |
| Button with a digital press join | a button, momentary, with a lamp on the same join for feedback |
| Text with an indirect text or serial join | live text |
| A button's caption | text laid over the button, bound to its serial — the caption arrives at runtime, so the object name is not one |

Joins at or above 17000 are Crestron's reserved range. They drive the
panel itself rather than the program, so they are not made into
controls.

### Limits worth knowing

- **One panel at a time.** Importing a second replaces the first.
- **Subpage reference lists render once.** A panel that repeats one strip
  per zone at runtime is drawn as the project defines it, not repeated.
  For that shape use **Build mixer** instead, which reads the repeating
  join layout and builds a console from it.
- **Re-importing the same panel is safe** — it updates in place. A panel
  name already pointing at a different address is refused rather than
  silently moved, because moving it would take its joins with it.
- The assignment survives a restart: it is stored with the panel and
  re-applied at startup.

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

## Many joins at once

Eighty joins is too many to set one at a time.

- Tick the box on any row, or the one in the header to take **everything
  currently shown** — it respects your filter, because ticking a hidden
  row is a surprise.
- A bar appears: **Expose**, **Hide**, or set a **Group**.

## Devices

CIP has no notion of a device. A processor exposes numbered joins and
nothing else, so without grouping every join on a processor lands in one
Home Assistant device — a sixteen-zone system becomes one device holding
eighty entities.

Set a **Group** on a join and it becomes its own device, with the
processor as its parent.

**Build device groups** does it for you where the layout repeats. A
Crestron subpage reference list numbers item N at
`start + (N-1) × stride`, and the first serial in each block is that
item's label. That is enough to derive and name the devices without
anyone typing. Joins outside the pattern are left alone rather than
swept into a guess.

## Building a mixer

**Build mixer** turns grouped zones into one console card.

It opens a panel rather than guessing, because no two programs lay a
zone strip out the same way:

1. Tick the properties a channel carries — volume, mute, source,
   balance, the three EQ bands.
2. Give each a **signal type** and an **offset** from the zone's first
   join.
3. Set **start**, **stride** and **strips per row**, then **Build**.

Sixteen strips in one row is 1800px wide, which a dashboard column
shrinks until nothing can be read, so the card wraps — eight a row by
default.

The result reports what it left out, and keeps two different problems
apart: zones with *nothing bound* need their joins wiring, zones *over
the card's limit* need a second card.

As with the panel, the add-on emits join keys and the integration turns
them into entity ids: run `crestron_cip.generate_mixer_card` and paste
the YAML.

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

## Processor load and memory

CIP carries joins and nothing else, so load and memory come from the
processor's text console over SSH. Add credentials to a processor to enable
it:

```yaml
processors:
  - name: Main Hall
    host: 192.0.2.10
    ipid: 16
    console_username: admin
    console_password: secret
health_interval: 300
```

Without credentials the bridge simply reports no figures; nothing else
changes.

You get CPU load, memory used and free, uptime and firmware version, as
diagnostic entities on each processor's device.

**The interval is deliberately in minutes.** Each reading is a full SSH
login, and these processors do not enjoy having sessions opened at them in
quick succession — during development a run of rapid logins was followed by
a processor dropping off the network entirely. The bridge reads one
processor at a time, ten seconds apart, and the default gap is five minutes.
Do not shorten it without a reason.

One quirk worth knowing: **the first `cpuload` of any console session always
reports around 100%**, because it is measuring the session starting up. The
bridge asks twice and discards the first answer. A processor idling at 16%
will otherwise look pinned.

## Re-scanning joins

**Re-scan joins** asks every processor to dump its joins again, without
reconnecting or restarting anything. The registration stays up.

This matters more here than on other systems. A processor only reports joins
whose value is not the default, so what the bridge holds is whatever it
happened to be sent. After someone has used the room — or a program has been
reloaded — asking again is how new joins turn up.

Also available as the `crestron_cip.rescan` service, optionally limited to
one processor.
