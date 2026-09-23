"""Keeps Home Assistant in step with the Crestron CIP add-on.

Two channels, deliberately:

  - a Server-Sent Events stream, which carries join changes as they happen,
    because AV feedback that arrives a poll interval late is useless — a
    mute button that lights up three seconds after it was pressed reads as
    a broken system
  - a slow reconcile poll, which picks up joins newly exposed, renamed or
    unexposed in the add-on UI, and covers anything the stream missed while
    it was reconnecting
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .const import CONF_ADDON_URL, RECONCILE_INTERVAL

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class JoinData:
    """One exposed join, as the add-on describes it."""

    processor: str
    key: str
    signal: str
    number: int
    kind: str
    name: str
    value: Any = None
    unit: str = ""
    device_class: str = ""
    scale: float = 1.0
    available: bool = True

    @property
    def store_key(self) -> str:
        return f"{self.processor}/{self.key}"

    @classmethod
    def from_json(cls, data: dict) -> "JoinData":
        return cls(
            processor=data["processor"],
            key=data["key"],
            signal=data["signal"],
            number=int(data["number"]),
            kind=data.get("kind") or "sensor",
            name=data.get("name") or f"{data['processor']} {data['key']}",
            value=data.get("value"),
            unit=data.get("unit") or "",
            device_class=data.get("device_class") or "",
            scale=float(data.get("scale") or 1.0),
            available=bool(data.get("available", True)),
        )


class CrestronCoordinator(DataUpdateCoordinator[dict[str, JoinData]]):
    """Holds the exposed joins and their current values."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="Crestron CIP",
            update_interval=timedelta(seconds=RECONCILE_INTERVAL),
        )
        self.entry = entry
        self.url = entry.data[CONF_ADDON_URL].rstrip("/")
        self.processors: dict[str, dict] = {}
        self._session = async_get_clientsession(hass)
        self._stream_task: asyncio.Task | None = None
        self._known_keys: set[str] = set()
        self._new_key_callbacks: list = []

    # -- reconcile -------------------------------------------------------

    async def _async_update_data(self) -> dict[str, JoinData]:
        try:
            async with self._session.get(
                f"{self.url}/api/integration/joins",
                timeout=aiohttp.ClientTimeout(total=30),
            ) as response:
                response.raise_for_status()
                payload = await response.json()
        except (aiohttp.ClientError, TimeoutError, ValueError) as err:
            raise UpdateFailed(f"Could not reach the add-on: {err}") from err

        self.processors = payload.get("processors", {})
        joins = {}
        for entry in payload.get("joins", []):
            try:
                join = JoinData.from_json(entry)
            except (KeyError, TypeError, ValueError):
                _LOGGER.debug("Skipping malformed join %s", entry)
                continue
            joins[join.store_key] = join

        # The stream is the fast path, but it only starts once there is
        # something to listen to.
        self._ensure_stream()
        return joins

    # -- live stream -----------------------------------------------------

    def _ensure_stream(self) -> None:
        if self._stream_task is None or self._stream_task.done():
            self._stream_task = self.entry.async_create_background_task(
                self.hass, self._run_stream(), "crestron_cip_events",
            )

    async def _run_stream(self) -> None:
        backoff = 1
        while True:
            try:
                await self._consume_stream()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - the stream must never die
                _LOGGER.debug("Event stream dropped (%s); retrying in %ss", err, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _consume_stream(self) -> None:
        # No total timeout: the stream is meant to stay open indefinitely,
        # and the add-on sends a keepalive comment every 20 seconds.
        timeout = aiohttp.ClientTimeout(total=None, sock_read=90)
        async with self._session.get(
            f"{self.url}/api/events", timeout=timeout,
        ) as response:
            response.raise_for_status()
            _LOGGER.debug("Listening for join changes")
            async for raw in response.content:
                line = raw.decode(errors="replace").strip()
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line[5:].strip())
                except json.JSONDecodeError:
                    continue
                self._apply_event(event)

    def _apply_event(self, event: dict) -> None:
        if event.get("type") != "join" or not self.data:
            return
        key = f"{event.get('processor')}/{event.get('key')}"
        current = self.data.get(key)
        if current is None:
            # A join that is not exposed, or one exposed since the last
            # reconcile. Either way the poll will pick it up.
            return
        if current.value == event.get("value"):
            return
        updated = dict(self.data)
        updated[key] = replace(current, value=event.get("value"))
        self.async_set_updated_data(updated)

    # -- dynamic entities ------------------------------------------------

    def track_new_joins(self, callback) -> None:
        """Register a platform's "add what is new" callback.

        Joins are exposed from the add-on UI while Home Assistant is running,
        so platforms cannot just enumerate once at setup.
        """
        self._new_key_callbacks.append(callback)

    def new_joins(self, kind: str, existing: set[str]) -> list[JoinData]:
        if not self.data:
            return []
        return [
            join for key, join in self.data.items()
            if join.kind == kind and key not in existing
        ]

    # -- writing ---------------------------------------------------------

    async def async_set(self, join: JoinData, **payload: Any) -> None:
        """Write a join back to the processor."""
        body = {"processor": join.processor, "key": join.key, **payload}
        try:
            async with self._session.post(
                f"{self.url}/api/joins/set", json=body,
                timeout=aiohttp.ClientTimeout(total=15),
            ) as response:
                if response.status >= 400:
                    raise UpdateFailed(
                        f"{join.key}: {response.reason or response.status}"
                    )
        except (aiohttp.ClientError, TimeoutError) as err:
            raise UpdateFailed(f"Could not write {join.key}: {err}") from err

        # A processor echoes the change back over the stream, but only when
        # it actually took. Nothing is assumed here.
