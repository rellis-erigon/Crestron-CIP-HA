# Crestron CIP for Home Assistant

Brings a Crestron control processor's touchpanel joins into Home Assistant,
discovering them automatically rather than asking you to type in join
numbers.

## How it works

A processor exposes a touchpanel's joins over CIP on port 41794. Registering
with an IPID the SIMPL program defines as an XPanel, then asking for an
update, makes the processor dump every join it drives for that panel —
digitals, analogs and serials, with their current values — and then signal
end-of-query. That dump is the discovery: the panel describes itself.

Two parts, versioned together:

- **Add-on** — owns the CIP connections, the discovered join store, and the
  management UI served through ingress.
- **Integration** — a thin client over the add-on's REST API that creates
  Home Assistant entities. No protocol code.

## Before you start

Add an **XPanel (Smart Graphics)** device to each SIMPL program at an IPID
that is not already in use, wired to the same joins as the existing
touchpanel. Copying the existing panel symbol is the usual way.

Registering with an IPID a real panel is using takes that panel's place and
knocks it offline, which is why this needs an IPID of its own. An IPID the
program does not define is refused harmlessly — `probe_ipid()` uses exactly
that to check an IPID without disturbing anything.

Find the IPIDs already in use from the processor console:

```
iptable
```

## Status

Early. The CIP client and its discovery path are built and tested; the join
store, UI, REST API and integration are not yet written.
