"""Loader for the join-map CSVs extracted from Crestron projects.

CSV columns: Join, JoinKind, Role, ObjectType, Label, Page, Uid
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class JoinEntry:
    join: int
    kind: str          # e.g. DigitalPressJoin, AnalogFeedbackJoin, IndirectTextJoin
    role: str          # press / fb:* / serial text / digital / list-range / ...
    object_type: str
    label: str
    page: str
    uid: str

    @property
    def signal(self) -> str:
        """Coarse signal class for picking a transport: digital / analog / serial."""
        k = self.kind.lower()
        if "analog" in k:
            return "analog"
        if "serial" in k or "indirecttext" in k:
            return "serial"
        return "digital"

    @property
    def is_output(self) -> bool:
        """True = panel writes it (press); False = feedback the panel reads."""
        return "press" in self.role


@dataclass
class JoinMap:
    entries: list[JoinEntry] = field(default_factory=list)
    connection: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_csv(cls, path: str | Path) -> "JoinMap":
        entries: list[JoinEntry] = []
        with open(path, newline="", encoding="utf-8-sig") as fh:
            for row in csv.DictReader(fh):
                try:
                    join = int(row["Join"])
                except (KeyError, ValueError):
                    continue
                entries.append(JoinEntry(
                    join=join,
                    kind=row.get("JoinKind", ""),
                    role=row.get("Role", ""),
                    object_type=row.get("ObjectType", ""),
                    label=row.get("Label", ""),
                    page=row.get("Page", ""),
                    uid=row.get("Uid", ""),
                ))
        return cls(entries)

    # -- queries ------------------------------------------------------------
    def presses(self) -> list[JoinEntry]:
        return [e for e in self.entries if e.is_output]

    def feedback(self) -> list[JoinEntry]:
        return [e for e in self.entries if not e.is_output and e.role != "list-range"]

    def by_signal(self, signal: str) -> list[JoinEntry]:
        return [e for e in self.entries if e.signal == signal]

    def labelled(self) -> list[JoinEntry]:
        """Entries that carry a human-readable label — the best entity candidates."""
        return [e for e in self.entries if e.label and not e.label.startswith(("{", "<", "http"))]

    def __len__(self) -> int:
        return len(self.entries)
