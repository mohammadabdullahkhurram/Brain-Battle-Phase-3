"""
label_vocab.py
──────────────
The shared label list for the whole team, stored as `label_list.json` in the
recordings folder (so a shared folder shares one vocabulary, per DESIGN §7).
Seeded from the DESIGN.md default palette on first use.

Each entry: {"key": 1-8, "name": str, "color": "#RRGGBB", "kind": "point" |
"duration"}. "point" = a Moment; "duration" = Lasts a while (press to start,
press again to end). Removing a label from this list does NOT touch labels
already placed — those keep their own stored name and colour (see
labels_store).
"""
from __future__ import annotations

import json
import os
from pathlib import Path

FILENAME = "label_list.json"

POINT = "point"
DURATION = "duration"

# DESIGN §"Label palette defaults" — keys 1–8, Clinch + Referee stop "last a
# while" (duration), the rest are Moments (point).
DEFAULT_LABELS: list[dict] = [
    {"key": 1, "name": "Jab",         "color": "#3D4FD6", "kind": POINT},
    {"key": 2, "name": "Cross",       "color": "#7C3AED", "kind": POINT},
    {"key": 3, "name": "Hook",        "color": "#0F766E", "kind": POINT},
    {"key": 4, "name": "Uppercut",    "color": "#B45309", "kind": POINT},
    {"key": 5, "name": "Block",       "color": "#4B5563", "kind": POINT},
    {"key": 6, "name": "Clinch",      "color": "#BE185D", "kind": DURATION},
    {"key": 7, "name": "Knockdown",   "color": "#B91C1C", "kind": POINT},
    {"key": 8, "name": "Referee stop","color": "#15171C", "kind": DURATION},
]

# UI words for the two kinds (DESIGN vocabulary — never show "point"/"duration").
KIND_LABEL = {POINT: "Moment", DURATION: "Lasts a while"}

# Colour palette the Settings label editor auto-picks from for [+ Add label]
# (DESIGN.md "Design tokens" — the default labels' colours plus the remaining
# named accents, so a new label never collides with an in-use colour until the
# palette itself is exhausted).
PALETTE: list[str] = [
    "#3D4FD6", "#7C3AED", "#0F766E", "#B45309", "#4B5563", "#BE185D",
    "#B91C1C", "#15171C", "#0E7490", "#C2410C", "#15803D", "#2B3AA8",
]


def next_color(existing: list[dict] | list[str]) -> str:
    """First PALETTE colour not already used by `existing` (labels or plain
    colour strings); cycles through the palette once all are taken."""
    used = {(e.get("color") if isinstance(e, dict) else e or "").upper()
            for e in existing}
    for c in PALETTE:
        if c.upper() not in used:
            return c
    return PALETTE[len(list(existing)) % len(PALETTE)]


def renumber(labels: list[dict]) -> list[dict]:
    """Reassign `key` = position + 1 (1-9) to match display order — the
    order IS the number-key mapping (DESIGN §6 "press 1-8 to place")."""
    for i, item in enumerate(labels):
        item["key"] = i + 1
    return labels


def path(recordings_dir) -> str:
    return os.path.join(str(recordings_dir), FILENAME)


def _normalise(raw) -> list[dict]:
    out = []
    for i, item in enumerate(raw or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        kind = item.get("kind")
        if kind not in (POINT, DURATION):
            kind = POINT
        try:
            key = int(item.get("key"))
        except (TypeError, ValueError):
            key = i + 1
        out.append({"key": key, "name": name,
                    "color": str(item.get("color") or "#3D4FD6"), "kind": kind})
    return out


def load(recordings_dir, seed_if_missing: bool = True) -> list[dict]:
    """Return the team label list for this recordings folder, seeding the file
    from the defaults the first time it is used."""
    p = Path(path(recordings_dir))
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            labels = _normalise(data.get("labels") if isinstance(data, dict) else data)
            if labels:
                return labels
        except (OSError, ValueError):
            pass  # unreadable → fall through to defaults
    labels = [dict(x) for x in DEFAULT_LABELS]
    if seed_if_missing:
        try:
            save(recordings_dir, labels)
        except OSError:
            pass
    return labels


def save(recordings_dir, labels) -> None:
    """Atomically write the team label list (temp + rename)."""
    os.makedirs(str(recordings_dir), exist_ok=True)
    p = Path(path(recordings_dir))
    data = {"schema_version": 1, "labels": _normalise(labels)}
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


__all__ = ["DEFAULT_LABELS", "KIND_LABEL", "POINT", "DURATION", "FILENAME",
           "path", "load", "save"]
