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
| `sensor` | Each analog join (a level, 0–65535), read-only |
| `button` | Each press join in the project file |

**A processor only reports joins that are not at their default value.**
Anything at 0, off or empty is never sent, so a program with hundreds of
joins may produce a dozen entities at first. Entities appear as joins
change. Operate the system and watch them arrive.

SmartObject lists (source lists, zone lists) are decoded into their serial
joins.

Every join entity carries a `join` attribute naming its bus and number:
`d101`, `a211`, `s1`.

## Using it as the panel

`crestron-panel-card` (in the HA-rellis-erigon-Cards collection) draws a
panel from its own project file. Point it at a processor's device and it
binds every control by its `join` attribute. Tapping a control then sends
the join the panel itself would: the panel's volume up presses the
program's volume-up join and lets the program ramp, rather than writing a
level.

Import the panel's own project once:

```yaml
action: crestron_cip.import_panel
data:
  path: /share/Room-TP.zip          # .c3p or the panel's .zip
  name: Room
```

It writes every screen and the panel's images under
`www/crestron-panels/Room/`, and returns the `panel_url`. Then:

```yaml
type: custom:crestron-panel-card
device: Room Processor           # the processor's device name
panel_url: /local/crestron-panels/Room/panel.json
default_screen: 2.0-Start        # optional: until the processor flips a page
scale: 2                         # optional
```

The card shows the screen the processor flipped to last. A processor
pulses a page's join, so until it flips one in front of Home Assistant the
card shows `default_screen`, or the busiest screen, with tabs to choose.
Buttons are held for as long as they are pressed, so the program's volume
ramps run as they do on the wall.

## Running the panel's own XPanel

The panel's `.zip` carries Crestron's compiled browser XPanel
(`container.swf`, the SmartGraphics engine and its theme). The
**Crestron XPanel card** runs it under [Ruffle](https://ruffle.rs), so the
pages, graphics and logic are the panel's own rather than a redrawing.

**The card is built in.** The integration serves the card and Ruffle
itself (`/crestron_cip/frontend/`, see `frontend/README.md`) and loads the
card on every Home Assistant page. There is no dashboard resource to add
and nothing else to install, and it works on a site with no internet.

### Adding a room's panel

A processor usually serves several touch panels, one per room, each at its
own IPID. Give it each one:

1. **Settings → Devices & services → Crestron CIP → (the processor) →
   Configure → Add or replace a room's panel.**
2. Fill in:
   - **Room**: what the room is called. The panel's device is named after it.
   - **IPID**: the IPID that room's panel has in the processor's IP table,
     in hex as the table writes it (`03`, `04`, `0A`).
   - **Panel file**: pick it from the drop-down, which lists the `.zip` and
     `.c3p` files already on Home Assistant (the copies kept from earlier
     uploads first, then `/share` and `/media`). Or type a path, or upload
     the file below.
3. **Submit.** The panel is imported, made ready for a browser, and a copy
   of the file is kept under `crestron_cip/` in the config folder.

Each room's panel becomes a **device of its own** under its processor
(model `XPanel IPID 0x04`, for example). Giving a room that already has a
panel a new file replaces it. To remove one, use **Configure → Remove a
room's panel**, or delete its device.

### Adding the card

**Dashboard → Edit → Add card → Crestron XPanel Card**, and pick the room's
panel device. In YAML:

```yaml
type: custom:crestron-xpanel-card
device: Room 1 panel        # the room panel's device (name or id)
scale: 0.75                 # a fraction of the size that fits the window
background: true            # load it as soon as Home Assistant opens
```

| Option | Default | Does |
|--------|---------|------|
| `device` | — | The room panel's device. A processor's own device shows its first panel |
| `entry_id`, `panel` | — | Instead of `device`: the processor's config entry and the panel's id |
| `scale` | `1` | 0.1–1, a fraction of the size that fits the window |
| `fit_window` | `true` | Fit both the window's width and height; `false` fills the card's width only |
| `background` | `false` | Start the panel when Home Assistant opens in a browser, on any dashboard, so it is already loaded when its view is shown |
| `title` | — | A heading above the panel |
| `xpanel_url` | the room's panel | A `container.swf` to run instead of the imported one |
| `debug` | `false` | Copy Ruffle's console into Home Assistant's log, for diagnosing a panel that will not start |

### How it runs

The XPanel speaks CIP over TCP, which a browser cannot open. Ruffle turns
its socket into a websocket to `/api/crestron_cip/xpanel/<entry>`, and the
integration serves it **from its own connection**: it answers the panel as
the processor does, replays what the processor has reported, then passes
presses up and changes down.

- **Any number of browsers and users.** However many have a room's panel
  open, the processor sees one registration at that room's IPID. Every
  copy shows the same page, text and button states, and a press in one
  shows in all. A panel opened later lands on the page the processor last
  chose.
- **A room at another IPID** gets a registration of its own at that IPID,
  used only for that room's panels (no entities). A processor can send one
  IPID its joins as DATA and another as EXTENDED_DATA; each panel is given
  them as its own IPID was.
- **Across views.** A panel keeps running while you move between views,
  so coming back does not reload it.
- **In a hidden window.** A browser stops running the panel in a window or
  tab that is not showing. The relay pings the websocket rather than
  waiting on the panel's own heartbeats, so a hidden panel is not closed;
  it carries on when shown.
- **Across restarts.** Each processor has a panel key of its own, kept in
  its config entry, so an open panel reconnects by itself after Home
  Assistant restarts.

Each browser still runs its own copy: the XPanel is a program that runs in
the browser, and loads in a few seconds. `background: true` does that when
Home Assistant opens rather than when the view is first shown.

Phones and tablets are the exception. Each panel is a player with its own
copy of the engine, and Safari on iOS closes a tab that holds more than one
or two. On a phone or tablet (or any browser reporting under 4 GB of
memory) no panel starts in the background, and opening a panel closes the
ones no card is showing; the one on screen loads as its view opens.

`import_panel` makes the changes a Crestron XPanel needs to run in Ruffle:
UTF-8 `Environment.xml`, web paths, lowercase file names, Ruffle's missing
`SecureSocket`, and a versioned URL so a browser never runs a stale copy.

### When a panel will not start

| Symptom | Cause |
|---------|-------|
| `Environment.zip … 404` in the log with `debug: true` | Normal: the engine looks for a zipped copy first, then loads `Environment.xml` |
| "This processor has no panel yet" | Add one under Configure, then pick the room's device in the card |
| Panel stays on "Loading Interface" | The processor is not connected (the panel retries by itself), or the panel is at an IPID the program does not define |
| The real wall panel stops responding after adding a room | That processor will not share an IPID with a live panel. Remove the room's panel, or give Home Assistant a spare IPID in the program |
| "Configuration error" in place of the card on an iPad or iPhone | Home Assistant 2026.10 gives browsers before iOS 18.1 its legacy build, which loaded no card before 0.23.2. Update, then reload the page |
| Safari on an iPhone keeps reloading the page | Before 0.23.2 every background panel started on the phone too, more than Safari allows a tab. Update, then reload |
| A panel reloads after a long time in the background | The browser unloaded the tab to save memory. In Chrome, add Home Assistant to *Always keep these sites active* |

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
