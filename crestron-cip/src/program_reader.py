"""Reading a compiled Crestron control program.

The panel project says what a screen looks like and which joins it binds.
It does not say what the joins *are*, which room the panel belongs to, or
what sits behind the controls. The program running on the processor says
all three, and a copy of it is something anybody can pull out of Toolbox.

What is actually in a compiled program, worked out from a real one:

    Program01.zip
      NAME.bin      the compiled program, with UTF-16 strings surviving
      NAME.dsc      the device table: IP-ID -> model
      NAME.cloud    the same devices with the comments a human wrote
      NAME.dip      the IP table: IP-ID -> address
      NAME.spp      symbol tree with module instance names
      NAME.rte      runtime data, no strings
      NAME.cdm      index tables, no strings

The `.bin` is compiled, so **per-join signal names are not in it** — they
are discarded along with the rest of the symbol detail. What survives is
the header, every string constant the program can print, and the name of
each module instance. That last one is the valuable part: an audio DSP
driven by named control leaves the control names in the binary, and those
are what the panel's faders and mutes actually move.

Three things this deliberately will not do.

**It does not return a module's parameters.** A connection module's
arguments are its address, username and password, and a real program has
them in clear. They are counted and flagged, never read back, so they
cannot reach a card, a log, or a stored file by accident.

**It does not guess at join meanings.** The compiled form does not carry
them. Where a name is wanted and absent, that is reported rather than
invented — the source `.smw` is where it would come from.

**It does not fail on an unfamiliar archive.** Crestron has shipped many
compilers; anything unparseable is skipped and said so, because a device
table alone is worth having.
"""

from __future__ import annotations

import configparser
import io
import logging
import re
import zipfile
from typing import Any

logger = logging.getLogger("crestron-cip.program_reader")


class ProgramReadError(Exception):
    """The upload is not a Crestron program."""


# Strings in the binary are UTF-16LE and not length-prefixed in any way
# worth trusting, so they are recovered by scanning. Three characters is
# the shortest thing worth having and keeps the noise down.
_UTF16_RUN = re.compile(rb"(?:[\x20-\x7e]\x00){3,}")

# The header is written as "Key:  value" lines, one string each.
_HEADER = re.compile(r"^([A-Za-z][A-Za-z0-9 ]{2,20}):\s+(.*)$")

# A symbol's position in the program tree: S-2.3.2.1.1, sometimes with a
# ":S-1" suffix naming a pin. Everything between two of these belongs to
# the symbol named by the first.
_SYMBOL = re.compile(r"^S-\d+(?:\.\d+)*(?::S-[\d.]+)?$")

# Slot and IP-ID markers from the device tree.
_SLOT = re.compile(r"^(?:Slot-\d+|IP-ID-[0-9A-Fa-f]+(?:\.\d+)?)$")

# A module that opens a connection carries credentials in its parameters.
# Matched loosely on purpose: the cost of treating an innocent module as
# sensitive is that its parameters are not reported, which costs nothing.
_SENSITIVE_MODULE = re.compile(
    r"core|client|login|auth|tcp|ssh|telnet|http|socket|cloud|api",
    re.I,
)

# A module that names one control on another system. The instance name is
# the control's name, and it is the single most useful string in the file.
_NAMED_CONTROL = re.compile(r"named[_ ]?control", re.I)


def _strings(data: bytes) -> list[str]:
    return [m.decode("utf-16-le") for m in _UTF16_RUN.findall(data)]


def _clean(text: str) -> str:
    """Drop the stray byte that often trails a string in the binary.

    Strings are packed against the next record, so a run frequently picks
    up one extra printable character — "Source\"" for "Source", or
    "Startup" written "StartupT". Only a trailing non-alphanumeric is
    removed, which is safe; a trailing letter is left alone because
    "ShutdownT" cannot be told from a name that really ends in T.
    """
    text = text.strip()
    while text and text[-1] in '"\'`,;:<>|^~\\&*(){}[]+=@#$%':
        text = text[:-1]
    return text.strip()


def _system(strings: list[str]) -> dict[str, str]:
    """The header block: what this program is and what it runs on."""
    out: dict[str, str] = {}
    for raw in strings[:60]:
        match = _HEADER.match(raw.strip())
        if not match:
            continue
        key = match.group(1).strip().lower().replace(" ", "_")
        value = _clean(match.group(2))
        if value:
            out.setdefault(key, value)
    return out


def _modules(strings: list[str]) -> tuple[list[dict[str, Any]], set[str]]:
    """Every module instance, and whether any held something secret.

    A module appears as its library file followed by its parameters, and
    the run ends at the next symbol marker. For a named-control module the
    first parameter is the control name. For anything that opens a
    connection the parameters are its credentials, and only their number
    is reported.
    """
    modules: list[dict[str, Any]] = []
    withheld: set[str] = set()
    for index, raw in enumerate(strings):
        name = _clean(raw)
        if not name.lower().endswith(".dll"):
            continue
        params: list[str] = []
        for following in strings[index + 1:]:
            text = _clean(following)
            if _SYMBOL.match(text) or _SLOT.match(text) or text.lower().endswith(".dll"):
                break
            params.append(text)

        sensitive = bool(_SENSITIVE_MODULE.search(name)) and not _NAMED_CONTROL.search(name)
        entry: dict[str, Any] = {
            "library": name,
            "kind": "named_control" if _NAMED_CONTROL.search(name) else "module",
            "parameter_count": len(params),
        }
        if entry["kind"] == "named_control" and params:
            entry["control"] = params[0]
        elif sensitive and params:
            # Counted, never carried. A connection module's parameters are
            # an address and a login, and a program has them in clear.
            # They are remembered only so they can be kept out of
            # everything else the reader returns — a password is also a
            # string constant, and would otherwise come back as a label.
            withheld.update(params)
            entry["withheld"] = True
        elif params:
            entry["parameters"] = params
        modules.append(entry)
    return modules, withheld


def _devices_from_dsc(text: str) -> list[dict[str, Any]]:
    """The device table.

    Each line is `Slot : 7.IP-ID : 03;8;Touchpanels(Enet);TSW-770;;TSW-770;-12`
    — the IP-ID, an internal index, a category, and the model twice.
    """
    devices = []
    for line in text.splitlines():
        match = re.search(r"IP-ID\s*:\s*([0-9A-Fa-f]+)\s*;(.*)$", line.strip())
        if not match:
            continue
        # index ; category ; model ; ... ; model ; code
        fields = [f.strip() for f in match.group(2).split(";")]
        category = fields[1] if len(fields) > 1 else ""
        model = next((f for f in fields[2:] if f and f != category), "")
        ipid = int(match.group(1), 16)
        devices.append({
            "ipid": ipid,
            # Crestron writes an IP-ID in hex everywhere a person sees
            # one — on the panel, in Toolbox, on the label. Showing 17
            # for what the installer calls 11 invites a wrong entry.
            "ipid_hex": f"{ipid:02X}",
            "model": model,
            "category": category,
        })
    return devices


def _devices_from_cloud(text: str) -> dict[int, dict[str, str]]:
    """Device comments, which are the only human names in the archive.

    The `.cloud` file lists the same devices as the `.dsc` with the
    comment the programmer typed — "Funct Rm 1 TP". That comment is what
    tells you which room a panel belongs to, and nothing else in the
    archive does.
    """
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read_string(text)
    except configparser.Error as err:
        logger.debug("cloud section will not parse: %s", err)
        return {}
    out: dict[int, dict[str, str]] = {}
    for section in parser.sections():
        items = {k.lower(): v for k, v in parser.items(section)}
        count = int(items.get("numdevices", "0") or 0)
        for n in range(1, count + 1):
            index = items.get(f"deviceindex{n}")
            if index is None:
                continue
            out[int(index)] = {
                "model": items.get(f"devicename{n}", ""),
                "comment": items.get(f"devicecomment{n}", ""),
            }
    return out


def _ip_table(text: str) -> list[dict[str, Any]]:
    parser = configparser.ConfigParser(strict=False)
    try:
        parser.read_string(text)
    except configparser.Error:
        return []
    rows = []
    for section in parser.sections():
        items = {k.lower(): v for k, v in parser.items(section)}
        for key, value in items.items():
            match = re.fullmatch(r"id(\d+)", key)
            if not match:
                continue
            address = items.get(f"addr{match.group(1)}", "")
            rows.append({"ipid": int(value, 16), "address": address})
    return rows


def _looks_like_a_label(text: str) -> bool:
    """Whether a string constant is something a person would read.

    The binary is mostly symbol paths and slot names. What is wanted is
    the text the program sends to a panel — room names, source names,
    status messages — and those are the strings with a letter, some
    length, and none of the shape of an identifier.
    """
    if len(text) < 3 or len(text) > 120:
        return False
    if _SYMBOL.match(text) or _SLOT.match(text):
        return False
    if text.lower().endswith((".dll", ".smw", ".dat")):
        return False
    if not any(c.isalpha() for c in text):
        return False
    # Paths, versions and bare numbers.
    if re.fullmatch(r"[\d.]+", text) or "\\" in text or "://" in text:
        return False
    if _HEADER.match(text):
        return False
    return True


def read_program(data: bytes) -> dict[str, Any]:
    """Everything a compiled program will say about itself."""
    parts: dict[str, bytes] = {}
    if data[:2] == b"PK":
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                for name in zf.namelist():
                    if name.endswith("/"):
                        continue
                    suffix = name.rsplit(".", 1)[-1].lower() if "." in name else ""
                    parts.setdefault(suffix, zf.read(name))
        except zipfile.BadZipFile as err:
            raise ProgramReadError(f"the archive will not open: {err}") from err
    else:
        # A bare .bin is a reasonable thing to be handed.
        parts["bin"] = data

    if "bin" not in parts and "dsc" not in parts:
        raise ProgramReadError(
            "no Crestron program in that file — expected the .zip Toolbox "
            "saves, or the .bin inside it"
        )

    warnings: list[str] = []
    strings = _strings(parts.get("bin", b"")) if "bin" in parts else []
    system = _system(strings)
    modules, withheld = _modules(strings)
    secrets = bool(withheld)

    devices = _devices_from_dsc(parts["dsc"].decode("latin-1")) if "dsc" in parts else []
    comments = _devices_from_cloud(parts["cloud"].decode("latin-1")) if "cloud" in parts else {}
    addresses = {r["ipid"]: r["address"]
                 for r in (_ip_table(parts["dip"].decode("latin-1")) if "dip" in parts else [])}

    # The .cloud is keyed by an internal index rather than by IP-ID, and
    # the .dsc carries both, so the two are joined on the order they
    # appear rather than on the index directly.
    for position, device in enumerate(devices):
        detail = comments.get(position) or {}
        if not detail and len(comments) == len(devices):
            detail = list(comments.values())[position]
        device["comment"] = detail.get("comment", "")
        # The .cloud names the model properly where the .dsc only has a
        # category — "TSW-770" against "Touchpanels(Enet)".
        if detail.get("model"):
            device["model"] = detail["model"]
        device["address"] = addresses.get(device["ipid"], "")

    if not devices:
        warnings.append(
            "no device table in that archive, so panels cannot be matched "
            "to an IP-ID automatically"
        )
    if "bin" not in parts:
        warnings.append("no compiled program, so no control names were read")
    if secrets:
        warnings.append(
            "this program contains a login in clear text. It was not read "
            "and is not stored, but keep the archive out of shared folders"
        )

    labels = []
    seen = set()
    for raw in strings:
        text = _clean(raw)
        if text in withheld or raw in withheld:
            continue
        if _looks_like_a_label(text) and text not in seen:
            seen.add(text)
            labels.append(text)

    return {
        "system": system,
        "devices": devices,
        "ip_table": [{"ipid": k, "address": v} for k, v in sorted(addresses.items())],
        "modules": modules,
        "named_controls": [m["control"] for m in modules if m.get("control")],
        "labels": labels,
        "contains_credentials": secrets,
        "warnings": warnings,
    }


def summarise(program: dict[str, Any]) -> dict[str, Any]:
    """What to show someone who has just uploaded one."""
    return {
        "name": program["system"].get("system_name", ""),
        "rack": program["system"].get("target_rack", ""),
        "compiled": program["system"].get("compiled_on", ""),
        "devices": len(program["devices"]),
        "panels": [d for d in program["devices"] if _is_panel(d)],
        "named_controls": len(program["named_controls"]),
        "warnings": program["warnings"],
    }


# Models whose name says they are a touch panel. A panel is what a card
# is generated from, so these are the devices worth offering.
_PANEL_MODEL = re.compile(r"^(TS|TSW|TST|TSD|XPANEL|TPMC|DGE|DM-TX)", re.I)


def _is_panel(device: dict[str, Any]) -> bool:
    if _PANEL_MODEL.match(device.get("model", "")):
        return True
    return "touchpanel" in (device.get("category", "") or "").lower()
