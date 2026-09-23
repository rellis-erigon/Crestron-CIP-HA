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
Nothing can. That is what the live join watcher is for.

## Requirements

A panel defined in each processor's **compiled program** at the IPID you
configure. An IP table entry alone will not do — the processor answers and
then rejects the registration, because the program has no device at that ID.

You do not necessarily need a *new* one. Two connections on the same IPID
have been measured holding simultaneously, both registered and both
receiving feedback, so the bridge can usually sit alongside an existing
panel rather than replacing it. Confirm it on your own system before
relying on it.

## Development

```bash
pip install pytest pytest-asyncio pytest-aiohttp aiohttp
pytest crestron-cip/tests -q
```

The add-on and the integration carry the same version number, and CI fails
the build if they drift.
