# Built-in frontend

Served by the integration at `/crestron_cip/frontend/` and loaded on every
Home Assistant page, so the Crestron XPanel card needs no resource of its own.

- `crestron-xpanel-card.js`: built from `src/cards/panel/crestron-xpanel-card.ts`
  in [HA-rellis-erigon-Cards](https://github.com/rellis-erigon/HA-rellis-erigon-Cards)
  (`rollup -c`, then copy `dist/crestron-xpanel-card.js` here).
- `ruffle/`: [Ruffle](https://ruffle.rs) 0.7.1, self-hosted build, unchanged.
  Dual-licensed MIT / Apache-2.0; see `ruffle/LICENSE_MIT` and
  `ruffle/LICENSE_APACHE`.
