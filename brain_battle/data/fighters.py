"""
fighters.py — fighter codes and fighter colours
───────────────────────────────────────────────
Fighters are never stored by name. Each one gets, before the match:

  • a **code** — exactly 4 characters, capital letters A–Z and digits 0–9
    (``K7Q2``). The two fighters' codes must differ. "New code" makes one up.
    The list that links codes to people is kept outside the app, as the
    team's data-protection plan requires.
  • a **colour** — what they wear (Red, Blue, Black, …), so a labeller can
    tell them apart on video. The two colours must differ.

Codes and colours go into the manifest, the folder name
(``2026-11-17 14-05 K7Q2 vs M3XA``), ``recording.xdf`` and every export.
(docs/decisions/adr-0023-fighter-codes-and-colours.md, which supersedes
ADR-0021's names + hiding.)

The colour list is shared by the team: ``fighter_colours.json`` in the
recordings folder, next to ``label_list.json``. Red, Blue and Black are always
there; more can be added in Settings.

Recordings made before codes existed stored ``{"name": …}`` per boxer; they
still open — :func:`display` shows the stored name (or "Boxer A").

Pure logic + small file I/O, no Qt.
"""
from __future__ import annotations

import json
import os
import random
import re
from pathlib import Path
from typing import Optional

CODE_LEN = 4
CODE_RE = re.compile(r"^[A-Z0-9]{4}$")
# "New code" avoids characters that are easy to misread (0/O, 1/I); typed
# codes may use any of A–Z and 0–9.
_GEN_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

COLOURS_FILENAME = "fighter_colours.json"
PRESET_COLOURS: list[dict] = [
    {"name": "Red",   "color": "#D3241B"},
    {"name": "Blue",  "color": "#1B45C4"},
    {"name": "Black", "color": "#111111"},
]
DEFAULT_COLOUR = {"A": "Red", "B": "Blue"}
_HEX_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FALLBACK_HEX = "#556070"


# ─── codes ────────────────────────────────────────────────────────────────────

def normalise_code(text) -> str:
    """What a code field shows while typing: capitals, only A–Z/0–9, at most 4."""
    return re.sub(r"[^A-Z0-9]", "", str(text or "").upper())[:CODE_LEN]


def code_ok(code) -> bool:
    return bool(CODE_RE.match(str(code or "")))


def new_code(avoid=(), rng: Optional[random.Random] = None) -> str:
    """A fresh random code, different from every code in ``avoid``."""
    rng = rng or random.SystemRandom()
    avoid = {str(c).upper() for c in avoid if c}
    while True:
        c = "".join(rng.choice(_GEN_ALPHABET) for _ in range(CODE_LEN))
        if c not in avoid:
            return c


def codes_problem(a, b) -> Optional[str]:
    """Plain-language reason the two codes can't be used, or None."""
    if not code_ok(a):
        return "Boxer A needs a 4-character code"
    if not code_ok(b):
        return "Boxer B needs a 4-character code"
    if a == b:
        return "The two codes must be different"
    return None


def colours_problem(colour_a, colour_b) -> Optional[str]:
    if not colour_a or not colour_b:
        return "Pick a colour for each fighter"
    if str(colour_a).lower() == str(colour_b).lower():
        return "Pick a different colour for each fighter"
    return None


# ─── the shared colour list ───────────────────────────────────────────────────

def colours_path(recordings_dir) -> str:
    return os.path.join(str(recordings_dir), COLOURS_FILENAME)


def _normalise_colours(raw) -> list[dict]:
    out, seen = [dict(c) for c in PRESET_COLOURS], {c["name"].lower() for c in PRESET_COLOURS}
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name = " ".join(str(item.get("name", "")).split())[:24]
        color = str(item.get("color", ""))
        if not name or not _HEX_RE.match(color) or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "color": color.upper()})
    return out


def load_colours(recordings_dir) -> list[dict]:
    """The team's fighter colours: the three presets first, then any added in
    Settings. Never raises; a missing or unreadable file means presets only."""
    p = Path(colours_path(recordings_dir))
    if p.is_file():
        try:
            import brain_battle.data.safe_io as safe_io
            data = json.loads(safe_io.read_text(p))
            return _normalise_colours(data.get("colours") if isinstance(data, dict) else data)
        except (OSError, ValueError):
            pass
    return [dict(c) for c in PRESET_COLOURS]


def save_colours(recordings_dir, colours) -> None:
    """Write the shared colour list atomically under the folder lock (two
    computers editing it at once cannot interleave)."""
    import brain_battle.data.safe_io as safe_io
    p = Path(colours_path(recordings_dir))
    data = {"schema_version": 1, "colours": _normalise_colours(colours)}
    with safe_io.FolderLock(p):
        safe_io.atomic_write_json(p, data)


def is_preset(name) -> bool:
    return str(name or "").lower() in {c["name"].lower() for c in PRESET_COLOURS}


def add_colour(colours, name, color) -> tuple[list[dict], Optional[str]]:
    """(new list, problem). Problem is a plain sentence, or None when added."""
    name = " ".join(str(name or "").split())[:24]
    if not name:
        return list(colours), "Give the colour a name"
    if not _HEX_RE.match(str(color or "")):
        return list(colours), "Pick a colour"
    if any(c["name"].lower() == name.lower() for c in colours):
        return list(colours), "That colour name already exists"
    name = name[0].upper() + name[1:]
    return list(colours) + [{"name": name, "color": str(color).upper()}], None


def remove_colour(colours, name) -> list[dict]:
    """Remove an added colour (the presets always stay)."""
    if is_preset(name):
        return list(colours)
    return [c for c in colours if c["name"].lower() != str(name).lower()]


def colour_hex(name, colours=None) -> str:
    for c in (colours or PRESET_COLOURS):
        if c["name"].lower() == str(name or "").lower():
            return c["color"]
    return _FALLBACK_HEX


# ─── the manifest's "boxers" entry ────────────────────────────────────────────

def boxer_entry(code: str, colour: str, colour_hex_value: str,
                headband: Optional[str] = None) -> dict:
    """One fighter as stored in ``session_manifest.json``'s ``boxers``."""
    return {"code": code, "colour": colour, "colour_hex": colour_hex_value,
            "headband": headband}


def info(manifest_or_boxers, which: str) -> dict:
    """``{"code", "colour", "colour_hex", "name", "headband"}`` for one side,
    from a manifest (or its ``boxers`` dict). Old recordings have ``name``
    and no code; new ones have a code and no name."""
    m = manifest_or_boxers or {}
    boxers = m.get("boxers", m) if isinstance(m, dict) else {}
    b = (boxers or {}).get(which) or {}
    if not isinstance(b, dict):
        b = {}
    colour = b.get("colour") or None
    hx = b.get("colour_hex") or (colour_hex(colour) if colour else None)
    return {"code": b.get("code") or None, "colour": colour, "colour_hex": hx,
            "name": b.get("name") or None, "headband": b.get("headband") or None}


def display(manifest_or_boxers, which: str) -> str:
    """What a screen prints for a fighter: the code; for an old recording the
    stored name; else 'Boxer A' / 'Boxer B'."""
    i = info(manifest_or_boxers, which)
    return i["code"] or i["name"] or f"Boxer {which}"


def versus(manifest_or_boxers) -> str:
    return f"{display(manifest_or_boxers, 'A')} vs {display(manifest_or_boxers, 'B')}"


__all__ = [
    "CODE_LEN", "CODE_RE", "normalise_code", "code_ok", "new_code",
    "codes_problem", "colours_problem",
    "COLOURS_FILENAME", "PRESET_COLOURS", "DEFAULT_COLOUR",
    "colours_path", "load_colours", "save_colours", "is_preset",
    "add_colour", "remove_colour", "colour_hex",
    "boxer_entry", "info", "display", "versus",
]
