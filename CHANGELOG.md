# Changelog

The add-on and the Home Assistant integration are released as a matched
pair and share a version number. CI fails the build if they drift.

## 0.12.3 — 2026-10-09

- **Fix**: Four features on the add-on's page had never worked: panel
  import, the mixer card builder, bulk configure and autogroup. The
  request helper prepends `api/` to the path, and those four call sites
  also wrote it themselves, producing `.../api//api/panel/upload`. That
  matches no route, so the request fell through to the catch-all GET that
  serves the page and came back **405 Method Not Allowed** — naming
  neither the path nor the feature.

  The helper now strips a stray prefix, the call sites are spelled one
  way, and `tests/test_ui_routes.py` reads the page and the server and
  insists every request the page makes reaches a route that exists for
  that method. Nothing failed at build time before and no test touched
  it; the only way to find it was to click the button and know what 405
  meant.

## 0.12.2 — 2026-10-09

- **Fix**: Loading a panel through the add-on's own page failed with
  "content too large". A compiled project is 18-26 MB, nearly all of it
  Flash runtime and theme artwork, and Home Assistant's ingress proxy caps
  a request body well below that — so the upload was rejected before the
  add-on ever saw it. Raising that cap is not the add-on's to raise.

  The reader opens exactly two files out of the archive, so the page now
  extracts those in the browser and uploads a small zip holding just them:
  26 MB becomes 35 KB, and the add-on reads it byte-for-byte identically
  because it is still a zip with the same two entries. Verified on all
  four project types to hand (.zip, .vtz, .c3p) — every one produces the
  same faceplate as the full archive. A .vtp, or anything that cannot be
  unpacked, is sent whole exactly as before, so nothing that used to work
  stops working.

## 0.12.1 — 2026-10-09

- **Feature**: The import screen now says what it found and what it had to
  work out. A panel has several screens; it shows which one it drew, with
  the others offered in a picker that re-reads the file already chosen. It
  names the states that screen has, the subpages it matched by position
  because the project file does not say which reference places them, and
  any subpage nothing places — a reference list builds those one per zone
  while the program runs. An import that guessed is worth having; one that
  guessed silently is not, because what was assumed is the one thing
  nobody can check afterwards.

## 0.12.0 — 2026-10-09

- **Feature**: An imported panel now produces a card that looks like the
  panel. The reader already knew which joins a panel binds; it did not
  know the layout, so a card was a list of controls rather than the
  screen somebody already knows how to use. The same project file carries
  every control's position, size, colour and the words it shows, and a
  faceplate is generated from it. Each state of the panel's main screen —
  the overlapping subpages a visibility join switches between — becomes a
  page of the card.

  Nothing is invented. A control whose purpose cannot be read off the
  file is drawn in the right place and does nothing. A button wired only
  to a reserved join flips a page on the panel itself and can never be
  driven from Home Assistant, so it is not offered as something that can
  be. Where the file genuinely does not say — two same-sized popups that
  cannot be told apart — the guess is reported rather than hidden.

- **Fix**: Four reader faults, each of which produced confident wrong
  output rather than an error. Every subpage in a real export is called
  "Subpage", so nine of them collapsed into one key and stacked every
  popup on the main screen. The duplicate-control dedupe spanned pages,
  which deleted shared controls from every page but the first and left a
  whole page empty. Subpage references are tagged `<Subpage>` rather than
  `<Child>`, so the layout was missed entirely. Labels are HTML with the
  serial join marked inside them, so stripping the tags lost the join and
  dropping the markers lost the sentence of a multi-state button.

- **Fix**: Panel size is read from `XPanel.ini`. No real panel carries it
  on the project element, and a card cannot be laid out without it.

- **Change**: Join roles are readable — `button_101`, `level_211` —
  because the role is what the card editor shows whoever is binding
  entities to it. The terse `d101` form is still understood, so panels
  imported before this keep working.

- **Known gap**: A Subpage Reference List instantiates its subpage once
  per zone and the project file records the list, not the instances. Such
  a subpage is offered as a screen in its own right, which is what it is,
  rather than placed by guesswork.

## 0.2.0 — 2026-09-24

- **Feature**: Force a re-scan of joins, from the panel or via
  `crestron_cip.rescan`. A processor only reports joins whose value is not
  the default, so what the bridge holds is whatever it happened to be sent.
  After someone uses a room, or a program is reloaded, asking again is how
  new joins turn up. The registration stays up; no reconnect is needed.

## 0.1.6 — 2026-09-23

- **Fix**: Retry a registration timeout instead of giving up permanently. A
  timeout was treated the same as an IPID the program does not define —
  fatal, connection task exits, nothing retries. When the add-on restarted
  and the processors were still holding the previous session, registration
  timed out on all three and every connection task exited for good. A
  refusal now retries every fifteen minutes rather than never, because
  designs get redeployed.

## 0.1.5 — 2026-09-23

- **Feature**: Processor load and memory, read from the text console over
  SSH and exposed as diagnostic entities: CPU, memory used and free, uptime,
  firmware. Optional — without console credentials nothing changes.
  Polling is five minutes, one processor at a time, ten seconds apart.
- **Note**: The first `cpuload` of any console session reports around 100%
  because it measures the session starting up. The bridge asks twice and
  discards the first; otherwise a processor idling at 16% reads as pinned.

## 0.1.4 — 2026-09-23

- **Fix**: Send a disconnect frame before dropping a connection, so a
  restart does not look to the processor like a yanked cable. The frame is
  inferred from the packet framing rather than captured from a real panel,
  so it is best-effort and never raises.
- **Docs**: How to tell a stopped program from a rejected registration.
  `ECONNREFUSED` and `ff ff 02` both look like "it will not talk to me" and
  mean entirely different things.

## 0.1.3 — 2026-09-23

- **Fix**: Widen the gap between reconnection attempts. A flat ten-second
  retry knocked on a booting processor's door six times a minute; the delay
  now doubles to a two-minute ceiling and resets on success.

## 0.1.2 — 2026-09-23

- **Fix**: The ingress panel returned 404. `ingress_entry` was set to `/`
  and Supervisor appends that to a token path that already ends in a slash,
  so the add-on was asked for `//`. Any non-API path now serves the page.

## 0.1.1 — 2026-09-23

- **Fix**: Report the real version. `BUILD_VERSION` did not survive the
  Supervisor build and the add-on logged `vunknown`; `config.yaml` now
  travels with the image as the fallback.

## 0.1.0 — 2026-09-23

- Initial release. Speaks CIP — the protocol a Crestron touchpanel uses —
  so nothing is needed on the processor beyond a panel definition at the
  IPID you configure.
- Ingress panel built around live join watching, because a processor only
  reports joins that are not at their default: mapping a system means
  pressing something and seeing what moves.
- Six entity platforms. A digital join can be a binary sensor, a switch or
  a button — a panel button is a pulse, and exposing those as switches
  leaves the program latched on.
- What you decide about a join is kept apart from what discovery reports,
  and survives restarts and program reloads.
