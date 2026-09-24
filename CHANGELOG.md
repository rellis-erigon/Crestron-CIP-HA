# Changelog

The add-on and the Home Assistant integration are released as a matched
pair and share a version number. CI fails the build if they drift.

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
