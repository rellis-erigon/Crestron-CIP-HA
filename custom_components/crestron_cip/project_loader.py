"""Load a Crestron project (.zip panel dump, .c3p, or Environment.xml) into a join map.

Cross-platform, stdlib only. Extracts the project's Environment.xml and walks the
SmartGraphics object tree, grouping every non-empty *Join* property by its owning
object, with page context and a cleaned text label. Also reads <CNXConnection>.
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree as ET

from .join_map import JoinEntry, JoinMap

_TAG_HTML = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")


def _clean(text: str | None) -> str:
    if not text:
        return ""
    s = _TAG_HTML.sub("", text)
    for a, b in (("&lt;", "<"), ("&gt;", ">"), ("&amp;", "&"), ("&#39;", "'"), ("&quot;", '"')):
        s = s.replace(a, b)
    return _WS.sub(" ", s).strip()


def _role(kind: str) -> str:
    k = kind.lower()
    if "digitalpressjoin" in k:
        return "press"
    if "visibility" in k or "enable" in k or "hasfocus" in k:
        return "fb:show/enable"
    if "analogfeedback" in k or "backgroundanalog" in k or "analogmode" in k:
        return "fb:analog"
    if "indirecttext" in k or k.endswith("serialjoin") or "dynamicgraphicsserial" in k:
        return "fb:serial"
    if any(x in k for x in ("startjoinnumber", "joinincrement", "joinoffset",
                            "numreservedjoins", "controljoin", "analognumberofitems")):
        return "list-range"
    if k == "joinnumber":
        return "page/object"
    return "other"


def _read_environment_xml(path: str | Path) -> bytes:
    p = Path(path)
    if p.suffix.lower() in (".zip", ".c3p"):
        with zipfile.ZipFile(p) as z:
            names = z.namelist()
            # prefer swf/Environment.xml; else any Environment.xml
            cand = next((n for n in names if n.lower().endswith("swf/environment.xml")), None) \
                or next((n for n in names if n.lower().endswith("environment.xml")), None)
            if not cand:
                raise ValueError(f"No Environment.xml inside {p.name}")
            return z.read(cand)
    return p.read_bytes()


def load_project(path: str | Path) -> JoinMap:
    raw = _read_environment_xml(path)
    root = ET.parse(io.BytesIO(raw)).getroot()

    # parent + nearest-object + page lookups
    parents: dict[ET.Element, ET.Element] = {}
    for parent in root.iter():
        for child in parent:
            parents[child] = parent

    def is_object(el: ET.Element) -> bool:
        return el.find("ControlName") is not None

    def owner(el: ET.Element) -> ET.Element | None:
        n = parents.get(el)
        while n is not None:
            if is_object(n):
                return n
            n = parents.get(n)
        return None

    def page_of(el: ET.Element) -> str:
        n: ET.Element | None = el
        while n is not None:
            if n.tag == "Page" and n.get("Name"):
                return n.get("Name", "")
            n = parents.get(n)
        return ""

    def label_of(obj: ET.Element) -> str:
        for tag in ("Text", "Label", "InitialText", "NonAnalogText", "ObjectName"):
            for e in obj.iter(tag):
                # only labels belonging to THIS object, not a nested child object
                if owner(e) is obj or parents.get(e) is obj or (parents.get(e) is not None and parents.get(parents[e]) is obj):
                    c = _clean(e.text)
                    if c and len(c) <= 60:
                        return c
        return ""

    # connection
    conn: dict[str, str] = {}
    cnx = root.find(".//CNXConnection")
    if cnx is not None:
        for k in ("Host", "IPID", "Port", "EnableSSL", "ProgramInstanceId"):
            e = cnx.find(k)
            conn[k] = (e.text or "").strip() if e is not None else ""

    entries: list[JoinEntry] = []
    for el in root.iter():
        if "Join" not in el.tag:
            continue
        val = (el.text or "").strip()
        if not re.fullmatch(r"\d+", val) or int(val) == 0:
            continue
        own = owner(el)
        if own is None:
            continue
        cn = own.find("ControlName")
        entries.append(JoinEntry(
            join=int(val),
            kind=el.tag,
            role=_role(el.tag),
            object_type=(cn.text or "") if cn is not None else "",
            label=label_of(own),
            page=page_of(own),
            uid=own.get("uid", ""),
        ))

    return JoinMap(sorted(entries, key=lambda e: (e.join, e.kind)), connection=conn)
