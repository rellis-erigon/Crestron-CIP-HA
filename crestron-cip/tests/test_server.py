"""Tests for the add-on's HTTP API.

These drive the aiohttp app through a real test client so the routes, JSON
shapes and error statuses are exercised the way the ingress UI and the
integration will hit them.
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from join_store import JoinStore  # noqa: E402
from server import Hub, build_app  # noqa: E402


class FakeConnection:
    """Stands in for CipConnection; records what would go on the wire."""

    def __init__(self, host: str, ipid: int, name: str) -> None:
        self.host, self.ipid, self.name = host, ipid, name
        self.connected = True
        self.registered = True
        self.discovery_complete = True
        self.connected_since = 1_700_000_000.0
        self.last_error = ""
        self.joins: dict = {}
        self.writes: list[tuple] = []

    def snapshot(self) -> dict:
        return {
            "host": self.host, "name": self.name, "ipid": self.ipid,
            "connected": self.connected, "registered": self.registered,
            "discovery_complete": self.discovery_complete,
            "connected_since": self.connected_since,
            "last_error": self.last_error, "join_count": 0, "joins": [],
        }

    async def set_digital(self, join, state):
        self.writes.append(("d", join, state))

    async def pulse_digital(self, join):
        self.writes.append(("pulse", join, None))

    async def set_analog(self, join, value):
        self.writes.append(("a", join, value))

    async def set_serial(self, join, value):
        self.writes.append(("s", join, value))

    async def close(self):
        self.registered = False


@pytest.fixture
def hub(tmp_path):
    store = JoinStore(tmp_path / "joins.json")
    hub = Hub(store)
    conn = FakeConnection("192.0.2.10", 0x10, "Processor")
    hub.connections["Processor"] = conn
    hub._on_join("Processor", _join("d", 7, True))
    hub._on_join("Processor", _join("a", 1, 32768))
    hub._on_join("Processor", _join("s", 1, "Main Hall"))
    return hub


def _join(signal, number, value):
    """A duck-typed stand-in for cip_client.Join."""
    class _Signal:
        def __init__(self, v): self.value = v

    class _Join:
        pass

    join = _Join()
    join.signal = _Signal(signal)
    join.number = number
    join.value = value
    return join


@pytest.fixture
async def client(hub, aiohttp_client):
    return await aiohttp_client(build_app(hub))


async def test_status_counts_without_dumping_joins(client, hub):
    body = await (await client.get("/api/status")).json()
    assert body["total_joins"] == 3
    assert body["exposed_joins"] == 0
    proc = body["processors"][0]
    assert proc["name"] == "Processor"
    assert proc["stored_joins"] == 3
    # The status view must stay small even on a panel with hundreds of joins.
    assert proc["joins"] is None


async def test_list_joins_returns_config(client):
    body = await (await client.get("/api/joins")).json()
    assert body["total"] == 3
    keys = [j["key"] for j in body["joins"]]
    assert keys == ["a1", "d7", "s1"]
    assert body["joins"][0]["config"]["enabled"] is False


async def test_list_joins_filters(client):
    only_digital = await (await client.get("/api/joins?signal=d")).json()
    assert [j["key"] for j in only_digital["joins"]] == ["d7"]

    by_value = await (await client.get("/api/joins?search=main hall")).json()
    assert [j["key"] for j in by_value["joins"]] == ["s1"]

    unknown = await (await client.get("/api/joins?processor=nope")).json()
    assert unknown["total"] == 0


async def test_configure_persists_and_exposes(client, hub):
    response = await client.post("/api/joins/configure", json={
        "processor": "Processor", "key": "s1",
        "name": "Room name", "kind": "text", "enabled": True,
    })
    assert response.status == 200
    body = await response.json()
    assert body["join"]["config"]["name"] == "Room name"

    assert hub.store.path.exists()
    saved = json.loads(hub.store.path.read_text())
    assert saved["joins"]["Processor/s1"]["config"]["name"] == "Room name"

    status = await (await client.get("/api/status")).json()
    assert status["exposed_joins"] == 1


async def test_configure_rejects_a_kind_the_signal_cannot_be(client):
    response = await client.post("/api/joins/configure", json={
        "processor": "Processor", "key": "a1", "kind": "switch",
    })
    assert response.status == 400


async def test_configure_unknown_join_is_404(client):
    response = await client.post("/api/joins/configure", json={
        "processor": "Processor", "key": "d999", "name": "x",
    })
    assert response.status == 404


async def test_configure_requires_processor_and_key(client):
    response = await client.post("/api/joins/configure", json={"key": "d7"})
    assert response.status == 400


async def test_set_writes_to_the_connection(client, hub):
    conn = hub.connections["Processor"]
    assert (await client.post("/api/joins/set", json={
        "processor": "Processor", "key": "d7", "value": True})).status == 200
    assert (await client.post("/api/joins/set", json={
        "processor": "Processor", "key": "d7", "pulse": True})).status == 200
    assert (await client.post("/api/joins/set", json={
        "processor": "Processor", "key": "a1", "value": 100})).status == 200
    assert (await client.post("/api/joins/set", json={
        "processor": "Processor", "key": "s1", "value": "hi"})).status == 200
    assert conn.writes == [
        ("d", 7, True), ("pulse", 7, None), ("a", 1, 100), ("s", 1, "hi"),
    ]


async def test_set_rejects_a_malformed_key(client):
    response = await client.post("/api/joins/set", json={
        "processor": "Processor", "key": "x9", "value": 1})
    assert response.status == 400


async def test_set_on_a_disconnected_processor_is_unavailable(client, hub):
    hub.connections["Processor"].registered = False
    response = await client.post("/api/joins/set", json={
        "processor": "Processor", "key": "d7", "value": True})
    assert response.status == 503


async def test_integration_feed_only_carries_exposed_joins(client, hub):
    hub.store.configure("Processor", "d7", kind="switch", enabled=True, name="Mute")
    body = await (await client.get("/api/integration/joins")).json()
    assert len(body["joins"]) == 1
    entry = body["joins"][0]
    assert entry["kind"] == "switch"
    assert entry["name"] == "Mute"
    assert entry["available"] is True
    assert body["processors"]["Processor"]["registered"] is True


async def test_integration_feed_names_unnamed_joins(client, hub):
    hub.store.configure("Processor", "a1", enabled=True)
    body = await (await client.get("/api/integration/joins")).json()
    assert body["joins"][0]["name"] == "Processor a1"


async def test_integration_feed_reports_a_dropped_processor(client, hub):
    hub.store.configure("Processor", "d7", enabled=True)
    hub.connections["Processor"].registered = False
    body = await (await client.get("/api/integration/joins")).json()
    assert body["joins"][0]["available"] is False


async def test_kinds_are_constrained_per_signal(client):
    body = await (await client.get("/api/kinds")).json()
    assert "switch" in body["by_signal"]["d"]
    assert "switch" not in body["by_signal"]["a"]
    assert set(body["by_signal"]) == {"d", "a", "s"}


async def test_events_streams_join_updates(client, hub):
    response = await client.get("/api/events")
    assert response.status == 200
    assert response.headers["Content-Type"].startswith("text/event-stream")

    # The subscriber is registered during prepare, so give the handler a turn
    # before publishing, otherwise the event goes nowhere.
    await asyncio.sleep(0.05)
    hub._on_join("Processor", _join("d", 12, True))

    line = await asyncio.wait_for(response.content.readline(), timeout=5)
    while line.startswith(b":") or line == b"\n":
        line = await asyncio.wait_for(response.content.readline(), timeout=5)
    event = json.loads(line.decode().removeprefix("data: "))
    assert event["key"] == "d12"
    assert event["value"] is True
    assert event["type"] == "join"
    response.close()


async def test_a_stalled_subscriber_is_dropped_not_blocking(hub):
    queue = hub.subscribe()
    for index in range(600):
        hub._on_join("Processor", _join("d", index + 1, True))
    # The queue is bounded; a browser that stopped reading gets unsubscribed
    # rather than stalling the hub.
    assert queue not in hub._subscribers


async def test_index_falls_back_when_the_ui_is_missing(client, monkeypatch):
    import server
    monkeypatch.setattr(server, "STATIC_DIR", Path("/nonexistent"))
    response = await client.get("/")
    assert response.status == 404


async def test_a_double_slash_still_serves_the_panel(client):
    """Ingress composed the panel URL with a trailing double slash.

    The add-on 404'd on it and the panel came up blank in Home Assistant, so
    any non-API path now serves the page.
    """
    for path in ("/", "//", "/anything"):
        response = await client.get(path)
        assert response.status == 200, path
        assert "<!DOCTYPE html>" in await response.text()


async def test_an_unknown_api_path_is_still_404(client):
    assert (await client.get("/api/nope")).status == 404
