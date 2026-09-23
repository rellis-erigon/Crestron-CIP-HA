"""Persistence for discovered joins and what the user made of them.

Discovery tells us a join exists and what it currently holds. It cannot tell
us that d14 is the mute button for Function Room 2 — that is knowledge the
user adds, and it must survive rediscovery, restarts and program reloads.

So two things are kept apart:

  - what the processor reports, refreshed on every connection
  - what the user decided, which is never overwritten by discovery

A join is identified by processor and join key ("a7", "d14", "s3"), because
join numbers are only meaningful per panel.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("crestron-cip.store")

STORE_FILE = Path("/config/crestron-cip/joins.json")

# Entity kinds a join can be exposed as. "ignore" keeps a join out of Home
# Assistant without forgetting it, which matters when a panel reports
# hundreds of joins and only a handful are worth exposing.
KIND_IGNORE = "ignore"
KIND_SENSOR = "sensor"
KIND_BINARY_SENSOR = "binary_sensor"
KIND_SWITCH = "switch"
KIND_BUTTON = "button"
KIND_NUMBER = "number"
KIND_TEXT = "text"

VALID_KINDS = (
    KIND_IGNORE, KIND_SENSOR, KIND_BINARY_SENSOR, KIND_SWITCH,
    KIND_BUTTON, KIND_NUMBER, KIND_TEXT,
)

# What each signal type can sensibly become.
KINDS_FOR_SIGNAL = {
    "d": (KIND_IGNORE, KIND_BINARY_SENSOR, KIND_SWITCH, KIND_BUTTON),
    "a": (KIND_IGNORE, KIND_SENSOR, KIND_NUMBER),
    "s": (KIND_IGNORE, KIND_SENSOR, KIND_TEXT),
}

DEFAULT_KIND = {
    "d": KIND_BINARY_SENSOR,
    "a": KIND_SENSOR,
    "s": KIND_SENSOR,
}


@dataclass
class JoinConfig:
    """What the user decided about one join."""

    name: str = ""
    kind: str = ""
    enabled: bool = False
    unit: str = ""
    device_class: str = ""
    # Analog joins are 0-65535 on the wire but often mean 0-100%, so a scale
    # is kept rather than baking the conversion into the entity.
    scale: float = 1.0
    notes: str = ""

    def resolved_kind(self, signal: str) -> str:
        return self.kind or DEFAULT_KIND.get(signal, KIND_SENSOR)


@dataclass
class StoredJoin:
    """A join as last seen, plus whatever the user configured."""

    processor: str
    key: str
    signal: str
    number: int
    value: Any = None
    updates: int = 0
    first_seen: float = field(default_factory=time.time)
    last_seen: float = field(default_factory=time.time)
    config: JoinConfig = field(default_factory=JoinConfig)

    @property
    def store_key(self) -> str:
        return f"{self.processor}/{self.key}"

    def to_dict(self) -> dict:
        data = asdict(self)
        data["kind"] = self.config.resolved_kind(self.signal)
        return data


class JoinStore:
    """Discovered joins plus user configuration, persisted atomically."""

    def __init__(self, path: Path = STORE_FILE) -> None:
        self.path = path
        self.joins: dict[str, StoredJoin] = {}
        self._dirty = False

    # -- persistence -----------------------------------------------------

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text())
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, OSError) as err:
            logger.warning("Could not read %s: %s", self.path, err)
            return

        for key, entry in (raw.get("joins") or {}).items():
            cfg = entry.get("config") or {}
            try:
                self.joins[key] = StoredJoin(
                    processor=entry["processor"],
                    key=entry["key"],
                    signal=entry["signal"],
                    number=int(entry["number"]),
                    value=entry.get("value"),
                    updates=int(entry.get("updates", 0)),
                    first_seen=float(entry.get("first_seen", time.time())),
                    last_seen=float(entry.get("last_seen", time.time())),
                    config=JoinConfig(
                        name=cfg.get("name", ""),
                        kind=cfg.get("kind", ""),
                        enabled=bool(cfg.get("enabled", False)),
                        unit=cfg.get("unit", ""),
                        device_class=cfg.get("device_class", ""),
                        scale=float(cfg.get("scale", 1.0) or 1.0),
                        notes=cfg.get("notes", ""),
                    ),
                )
            except (KeyError, TypeError, ValueError) as err:
                logger.warning("Skipping malformed join %s: %s", key, err)
        logger.info("Loaded %d joins from %s", len(self.joins), self.path)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "_comment": (
                "Discovered Crestron joins and how they are exposed. The "
                "'config' of each join is yours; everything else is "
                "refreshed from the processor on each connection."
            ),
            "joins": {k: asdict(v) for k, v in sorted(self.joins.items())},
        }
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=1, sort_keys=False))
        # Rename is atomic, so a crash cannot leave a half-written file.
        tmp.replace(self.path)
        self._dirty = False

    def save_if_dirty(self) -> bool:
        if not self._dirty:
            return False
        self.save()
        return True

    # -- discovery -------------------------------------------------------

    def observe(self, processor: str, signal: str, number: int, value: Any) -> StoredJoin:
        """Record what a processor reported, leaving user config untouched."""
        key = f"{processor}/{signal}{number}"
        join = self.joins.get(key)
        now = time.time()
        if join is None:
            join = StoredJoin(
                processor=processor, key=f"{signal}{number}",
                signal=signal, number=number, first_seen=now,
            )
            self.joins[key] = join
            self._dirty = True
            logger.debug("New join %s", key)
        if join.value != value:
            self._dirty = True
        join.value = value
        join.updates += 1
        join.last_seen = now
        return join

    # -- configuration ---------------------------------------------------

    def configure(self, processor: str, join_key: str, **changes) -> StoredJoin:
        """Apply user changes to one join."""
        key = f"{processor}/{join_key}"
        join = self.joins.get(key)
        if join is None:
            raise KeyError(f"unknown join {key}")

        if "kind" in changes and changes["kind"]:
            kind = changes["kind"]
            if kind not in VALID_KINDS:
                raise ValueError(f"unknown kind {kind!r}")
            allowed = KINDS_FOR_SIGNAL.get(join.signal, VALID_KINDS)
            if kind not in allowed:
                raise ValueError(
                    f"a {join.signal!r} join cannot be a {kind}; "
                    f"choose one of {', '.join(allowed)}"
                )
            join.config.kind = kind

        for field_name in ("name", "unit", "device_class", "notes"):
            if field_name in changes:
                setattr(join.config, field_name, str(changes[field_name]).strip())
        if "enabled" in changes:
            join.config.enabled = bool(changes["enabled"])
        if "scale" in changes and changes["scale"] not in (None, ""):
            scale = float(changes["scale"])
            if scale == 0:
                raise ValueError("scale cannot be zero")
            join.config.scale = scale

        self._dirty = True
        return join

    # -- queries ---------------------------------------------------------

    def for_processor(self, processor: str) -> list[StoredJoin]:
        return sorted(
            (j for j in self.joins.values() if j.processor == processor),
            key=lambda j: (j.signal, j.number),
        )

    def exposed(self) -> list[StoredJoin]:
        """Joins the user enabled and did not mark as ignored."""
        return [
            j for j in self.joins.values()
            if j.config.enabled
            and j.config.resolved_kind(j.signal) != KIND_IGNORE
        ]

    def prune(self, processor: str, seen_keys: set[str]) -> int:
        """Forget joins a processor no longer reports.

        Configured joins are kept: a program reload can drop a join
        temporarily, and silently discarding the user's naming would be
        worse than carrying a stale row.
        """
        gone = [
            key for key, join in self.joins.items()
            if join.processor == processor
            and join.key not in seen_keys
            and not join.config.enabled
            and not join.config.name
        ]
        for key in gone:
            del self.joins[key]
        if gone:
            self._dirty = True
            logger.info("Pruned %d joins no longer reported by %s",
                        len(gone), processor)
        return len(gone)
