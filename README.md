# Crestron CIP for Home Assistant

Brings Crestron control systems into Home Assistant by speaking CIP, the
protocol a Crestron touchpanel uses, so no SIMPL module or symbol is needed on
the processor beyond an XPanel definition.

It ships in two halves, released as a matched pair:

- **`crestron-cip/`** — the add-on. Holds the connection to each processor,
  discovers joins, and provides the panel where you decide what each one is.
- **`custom_components/crestron_cip/`** — the integration. A thin client that
  turns the joins you exposed into entities.

## Why two halves

The processor connection must stay open to see joins change, and the mapping
work — pressing a button and watching which join moves — wants a real UI. The
add-on owns both. The integration then has one job: create entities and write
values back, which keeps it small enough to be reviewable.

## Installing

1. **Settings → Add-ons → Add-on store → ⋮ → Repositories**, add
   `https://github.com/rellis-erigon/Crestron-CIP-HA`.
2. Install **Crestron CIP Bridge** and configure your processors.
3. Copy `custom_components/crestron_cip/` into your Home Assistant
   `custom_components/` folder, or install this repository through HACS.
4. **Settings → Devices & services → Add integration → Crestron CIP.** The
   add-on is found through Supervisor; you should not have to type a URL.

See [the add-on documentation](crestron-cip/DOCS.md) for how to find and map
joins.

## What CIP gives you

The bridge registers as an XPanel and receives the same join feedback a panel
would: digital, analog and serial. That means it sees what the program
publishes, and can drive anything the panel could drive.

It does not read the SIMPL program, so it cannot tell you what `d12` is for.
Nothing can — but a *panel project* can, which is what the XPanel import
is for.

## Loading an XPanel

A compiled panel project — `.c3p` or `.vtz` — already says which joins
exist, where each control sits and what it does. Load one and the add-on
adds the processor, creates and names every join, exposes them, groups
them into a device, and gives you the panel back as a Home Assistant
card.

```
Load panel → file, name, processor IP, IPID, port → Load and assign
Developer tools → Actions → crestron_cip.generate_panel_card
Dashboard → Add card → Manual → paste
```

Full walkthrough, including what each panel object becomes and what the
import cannot do, is in [the add-on documentation](crestron-cip/DOCS.md).

The card itself is `custom:crestron-panel-card` from
[HA-rellis-erigon-Cards](https://github.com/rellis-erigon/HA-rellis-erigon-Cards).

## Requirements

A panel defined in each processor's **compiled program** at the IPID you
configure. An IP table entry alone will not do — the processor answers and
then rejects the registration, because the program has no device at that ID.

You do not necessarily need a *new* one. Two connections on the same IPID
have been measured holding simultaneously on a DMPS, both registered and
both receiving feedback, so the bridge can often sit alongside an
existing panel rather than replacing it.

**This is processor-dependent — confirm it on your own system.** A CP4
here behaves differently: with its panel connected, a second session on
the same IPID is accepted at TCP level and then never answered, which
looks exactly like a timeout:

```
Connected to 192.0.2.10:41794 as IPID 0x03
No registration result within 20s
```

If you see that while a panel is running, the IPID is taken. Define a
spare XPanel IPID in the program for the bridge.

## Development

```bash
pip install pytest pytest-asyncio pytest-aiohttp aiohttp
pytest crestron-cip/tests -q
```

The add-on and the integration carry the same version number, and CI fails
the build if they drift.
