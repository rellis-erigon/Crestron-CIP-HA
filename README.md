# Crestron CIP for Home Assistant

Connects Home Assistant to a Crestron control processor over **CIP**, the
protocol Crestron touch panels and XPanels use. Home Assistant registers as a
panel at an IPID, receives the processor's feedback joins as entities, and can
press, set and send joins back.

One custom integration, standard library only (asyncio and ssl). No add-on.

> **Upgrading from 0.14.x?** Up to 0.14.3 this repository was an add-on plus
> a thin integration. 0.15.0 replaces both with this single integration, and
> the configuration does not carry over: remove the old Crestron CIP entries
> and the **Crestron CIP Bridge** add-on, then add the integration again.
> The old code is on the [`addon-0.14.3`](../../tree/addon-0.14.3) branch.

## Install

1. Install through HACS as a custom repository, or copy
   `custom_components/crestron_cip/` into your Home Assistant
   `custom_components/` folder.
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → Crestron CIP.**
4. Enter:
   - **Host**: the processor's address.
   - **IPID**: the panel slot to register at, as hex (`0x03`) or decimal.
     The program must define a panel at that IPID.
   - **TLS**: only if the processor uses secure CIP.
   - **Port**: leave blank for the default (41794 plain, 41796 TLS).
   - **Project file** (optional): the path to a `.c3p` or a panel `.zip`.
     Joins are then named after the panel's own labels, and each press join
     gets a button.

Add one entry per processor.

## What you get

| Entity | From |
|--------|------|
| `binary_sensor` … connection | One per processor |
| `binary_sensor` | Each digital feedback join |
| `sensor` | Each serial (text) feedback join |
| `number` | Each analog join. **Disabled by default**: changing one writes it to the processor |
| `button` | Each press join in the project file |

**A processor only reports joins that are not at their default value.**
Anything at 0, off or empty is never sent, so a program with hundreds of
joins may produce a dozen entities at first. Entities appear as joins
change. Operate the system and watch them arrive.

SmartObject lists (source lists, zone lists) are decoded into their serial
joins.

## Actions

| Action | Does |
|--------|------|
| `crestron_cip.press` | Pulse a digital join, as a button press |
| `crestron_cip.set_digital` | Hold a digital join high or low |
| `crestron_cip.set_analog` | Set an analog join, 0–65535 |
| `crestron_cip.set_serial` | Send text to a serial join |
| `crestron_cip.request_update` | Ask the processor to send every join again |

Each takes an optional `entry_id`. Without one, the action goes to every
processor.

## Before you write to a live system

Sending joins uses the same framing as the feedback that has been verified
on real processors, but **it has not yet been tested against live
hardware**. A wrong join number is a room doing something unexpected. Test
writes with someone present, against an IPID nothing else is using.

Sharing an IPID with a real panel works on some processors and not others.
On one site a DMPS took a second connection on a panel's IPID without
trouble, while a CP4 stopped serving the panel already there. If a panel
goes quiet after you add the integration, give the integration its own
IPID in the program.

## Connection behaviour

- A processor that drops the connection, or stops answering heartbeats for
  45 seconds, is marked unavailable and reconnected, backing off from 5 to
  60 seconds.
- An IPID that the program does not define is refused by the processor
  (`ff ff 02`). Setup reports it as a failed connection rather than
  appearing to connect.

## Development

```
python3 tests/test_protocol.py
python3 tests/test_integration.py
python3 tests/test_connection.py
python3 scripts/check-no-site-detail.py
```

The tests need only Python 3.11+. They load the protocol and client without
Home Assistant, and run the client against an in-process mock processor.

`scripts/check-no-site-detail.py` fails on real addresses and site names. CI
runs it, along with the tests and hassfest.
