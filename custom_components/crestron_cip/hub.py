"""Connection hub: wraps CIPClient and bridges feedback to HA dispatcher signals."""
from __future__ import annotations

import asyncio
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.dispatcher import async_dispatcher_send
from homeassistant.util import dt as dt_util

from .client import CIPClient
from .xpanel_mux import XPanelMux
from .const import SIGNAL_CONN, SIGNAL_NEW, SIGNAL_VALUE

_LOGGER = logging.getLogger(__name__)


class PanelUplink:
    """A registration at another of the processor's IPIDs, for panels only.

    A room's panel sits at an IPID of its own, and the processor tells each
    IPID only what its program drives at that IPID, so a panel at IPID 04
    cannot be served from the entry's own registration at 03. Its feedback
    makes no entities; it is only for the panels served from it.
    """

    def __init__(self, hub: CrestronHub, ipid: int) -> None:
        self.hub = hub
        self.ipid = ipid
        self._lost = asyncio.Event()
        self.client = CIPClient(
            hub.host, ipid, port=hub.client.port, use_ssl=hub.client.use_ssl,
            on_disconnect=self._on_disconnect)
        self.mux = XPanelMux(self.client.send_data, lambda: self.client.connect_response)
        self.client.data_listeners.append(self.mux.from_processor)
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = self.hub.entry.async_create_background_task(
            self.hub.hass, self._run(),
            f"crestron_cip panels {self.hub.host} IPID {self.ipid:02X}")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.mux.processor_lost()
        await self.client.close()

    @callback
    def _on_disconnect(self) -> None:
        self.mux.processor_lost()
        self._lost.set()

    async def _run(self) -> None:
        delay = 5
        while True:
            self._lost.clear()
            try:
                await self.client.connect()
            except Exception as exc:  # noqa: BLE001
                _LOGGER.debug("Panel IPID %02X on %s: %s", self.ipid, self.hub.host, exc)
                await self.client.close()
                await asyncio.sleep(delay)
                delay = min(delay * 2, 60)
                continue
            _LOGGER.info("Registered for panels at %s IPID %02X", self.hub.host, self.ipid)
            delay = 5
            await self._lost.wait()
            if not self.hub.hass.is_stopping:
                _LOGGER.warning("Lost panel IPID %02X on %s; reconnecting",
                                self.ipid, self.hub.host)
            await self.client.close()
            await asyncio.sleep(delay)


class CrestronHub:
    """Owns one CIP session for a config entry."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, host: str, ipid: int,
                 port: int | None, use_ssl: bool, name: str, project_file: str | None = None) -> None:
        self.hass = hass
        self.entry = entry
        self.name = name
        self.host = host
        self.ipid = ipid
        self._stopping = False
        self._reconnect_task: asyncio.Task | None = None
        # (kind, join, smart object); the object is 0 for an ordinary join.
        self._seen: set[tuple[str, int, int]] = set()
        # digital join -> when it last went high. Kept here rather than on
        # the entity because the entity for a join first seen in a pulse
        # is created after the pulse has been released.
        self.last_on: dict[tuple[int, int], str] = {}
        self._on: dict[tuple[int, int], bool] = {}
        self.joinmap = None
        self._labels: dict[tuple[str, int], str] = {}
        if project_file:
            try:
                from .project_loader import load_project
                self.joinmap = load_project(project_file)
                for e in self.joinmap.entries:
                    if e.label and (e.signal, e.join) not in self._labels:
                        self._labels[(e.signal, e.join)] = e.label
            except Exception as exc:  # noqa: BLE001
                _LOGGER.warning("Could not load project file %s: %s", project_file, exc)
        self.client = CIPClient(
            host, ipid, port=port, use_ssl=use_ssl,
            on_feedback=self._on_feedback,
            on_connect=lambda: self._conn(True),
            on_disconnect=self._on_disconnect,
        )
        # Browser XPanels, served over this one connection.
        self.mux = XPanelMux(self.client.send_data, lambda: self.client.connect_response)
        self.client.data_listeners.append(self.mux.from_processor)
        # The room panels' other IPIDs, each a registration of its own.
        self.uplinks: dict[int, PanelUplink] = {}

    @property
    def unique_id(self) -> str:
        return self.entry.entry_id

    @property
    def available(self) -> bool:
        return self.client.connected

    async def async_start(self) -> None:
        await self.client.connect()

    def link_for(self, ipid: int) -> tuple[CIPClient, XPanelMux] | None:
        """The connection and mux a panel at this IPID is served from."""
        if ipid == self.ipid:
            return self.client, self.mux
        uplink = self.uplinks.get(ipid)
        return (uplink.client, uplink.mux) if uplink else None

    async def async_sync_panels(self, ipids: set[int]) -> None:
        """Register at every IPID a panel is at, and at none other."""
        wanted = {int(i) for i in ipids} - {self.ipid}
        for ipid in set(self.uplinks) - wanted:
            await self.uplinks.pop(ipid).stop()
        for ipid in wanted - set(self.uplinks):
            uplink = self.uplinks[ipid] = PanelUplink(self, ipid)
            uplink.start()

    async def async_stop(self) -> None:
        self._stopping = True
        if self._reconnect_task is not None:
            self._reconnect_task.cancel()
        await self.async_sync_panels(set())
        self.mux.processor_lost()
        await self.client.close()

    @callback
    def _on_disconnect(self) -> None:
        self._conn(False)
        self.mux.processor_lost()
        # Home Assistant shutting down closes every socket; that is not a
        # lost processor, and warning about it buried the real ones.
        if self._stopping or self._reconnect_task is not None or self.hass.is_stopping:
            return
        _LOGGER.warning("Lost the connection to %s; reconnecting", self.host)
        self._reconnect_task = self.entry.async_create_background_task(
            self.hass, self._reconnect(), f"crestron_cip reconnect {self.host}")

    async def _reconnect(self) -> None:
        delay = 5
        try:
            while not self._stopping:
                await asyncio.sleep(delay)
                await self.client.close()
                try:
                    await self.client.connect()
                except Exception as exc:  # noqa: BLE001
                    _LOGGER.debug("Reconnect to %s failed: %s", self.host, exc)
                    delay = min(delay * 2, 60)
                    continue
                _LOGGER.info("Reconnected to %s", self.host)
                return
        finally:
            self._reconnect_task = None

    # -- bridge client callbacks to HA dispatcher (runs in HA's loop) -------
    @callback
    def _conn(self, up: bool) -> None:
        async_dispatcher_send(self.hass, SIGNAL_CONN.format(entry=self.entry.entry_id), up)

    @callback
    def _on_feedback(self, kind: str, join: int, value: object, obj: int = 0) -> None:
        key = (kind, join, obj)
        if kind == "digital":
            if value and not self._on.get((obj, join), False):
                self.last_on[(obj, join)] = dt_util.utcnow().isoformat()
            self._on[(obj, join)] = bool(value)
        if key not in self._seen:
            self._seen.add(key)
            async_dispatcher_send(
                self.hass, SIGNAL_NEW.format(entry=self.entry.entry_id), kind, join, obj)
        async_dispatcher_send(
            self.hass,
            SIGNAL_VALUE.format(entry=self.entry.entry_id, kind=kind, obj=obj, join=join),
            value,
        )

    def known(self, kind: str) -> list[tuple[int, int]]:
        """(join, smart object) for every join of this kind seen so far."""
        return sorted((j, o) for (k, j, o) in self._seen if k == kind)

    def name_for(self, signal: str, join: int, obj: int = 0) -> str:
        """Friendly entity name: project label if known, else a generic one."""
        if obj:
            return f"{self.name} object {obj} {signal} {join}"
        label = self._labels.get((signal, join))
        base = label or f"{signal} {join}"
        return f"{self.name} {base}"

    def press_joins(self) -> list[tuple[int, str]]:
        """(join, name) for every mapped DigitalPressJoin — candidates for buttons."""
        if not self.joinmap:
            return []
        seen: set[int] = set()
        out: list[tuple[int, str]] = []
        for e in self.joinmap.entries:
            if e.is_output and e.join not in seen:
                seen.add(e.join)
                out.append((e.join, e.label or f"press {e.join}"))
        return sorted(out)
