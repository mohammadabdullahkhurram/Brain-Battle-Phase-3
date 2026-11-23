"""
label_vocab.py
──────────────
The shared label list for the whole team, stored as `label_list.json` in the
recordings folder (so a shared folder shares one vocabulary, per DESIGN §7).
Seeded from the DESIGN.md default list on first use. A list a team edited is
never overwritten; a list that is still exactly an OLD default (``OLD_DEFAULTS``)
is replaced by the current default once, and that is written into the file's
own ``history`` (and the log). One-off descriptions are not label-list entries: they are custom
notes (labels_store, ``type: "note"``) and stay with their recording.

Each entry: {"key": 1-9, "name": str, "color": "#RRGGBB", "kind": "point" |
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

# DESIGN "Label list defaults" (the professor's list) — keys 1–8. Clinch and
# Referee break "last a while" (duration); the rest are single moments (point).
# Only used to seed a recordings folder that has no label_list.json yet: an
# existing team list is never overwritten.
DEFAULT_LABELS: list[dict] = [
    {"key": 1, "name": "Punch initiated", "color": "#2155D6", "kind": POINT},
    {"key": 2, "name": "Punch landed",  "color": "#0F7A62", "kind": POINT},
    {"key": 3, "name": "Punch missed",  "color": "#7A3FC4", "kind": POINT},
    {"key": 4, "name": "Punch blocked", "color": "#465063", "kind": POINT},
    {"key": 5, "name": "Punch dodged",  "color": "#A8570A", "kind": POINT},
    {"key": 6, "name": "Clinch",        "color": "#B5246B", "kind": DURATION},
    {"key": 7, "name": "Knockdown",     "color": "#B42318", "kind": POINT},
    {"key": 8, "name": "Referee break", "color": "#161A22", "kind": DURATION},
]

# Earlier built-in defaults, by name in order. A team list that is still
# exactly one of these was never edited, so it is replaced by DEFAULT_LABELS
# once (owner, 2026-11-23). The second is the short-lived 3.1-dev list.
OLD_DEFAULTS: list[list[str]] = [
    ["Jab", "Cross", "Hook", "Uppercut", "Block", "Clinch", "Knockdown", "Referee stop"],
    ["Punch thrown", "Punch landed", "Punch missed", "Punch blocked", "Punch dodged",
     "Clinch", "Knockdown", "Referee break"],
]
EVENT_DEFAULT_REPLACED = "default_list_replaced"

# UI words for the two kinds (DESIGN vocabulary — never show "point"/"duration").
KIND_LABEL = {POINT: "Moment", DURATION: "Lasts a while"}

# Colour palette the Settings label editor auto-picks from for [+ Add label]
# (DESIGN.md "Design tokens" — the default labels' colours plus the remaining
# named accents, so a new label never collides with an in-use colour until the
# palette itself is exhausted).
PALETTE: list[str] = [
    "#2155D6", "#0F7A62", "#7A3FC4", "#465063", "#A8570A", "#B5246B",
    "#B42318", "#161A22", "#0E7490", "#C2410C", "#15803D", "#2B3AA8",
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
    order IS the number-key mapping (DESIGN §6 "press 1-9 to place")."""
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


def _read(p: Path) -> dict:
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return {"labels": data}
    return data if isinstance(data, dict) else {}


def history(recordings_dir) -> list:
    """What happened to the list itself (e.g. the old default replaced)."""
    try:
        h = _read(Path(path(recordings_dir))).get("history")
    except (OSError, ValueError):
        return []
    return list(h) if isinstance(h, list) else []


def _replace_old_default(recordings_dir, labels: list[dict], data: dict) -> list[dict]:
    """A list that is still exactly an old built-in default → the current
    default, once (the file's ``history`` says so; never again after that)."""
    names = [e["name"] for e in labels]
    done = any(isinstance(h, dict) and h.get("event") == EVENT_DEFAULT_REPLACED
               for h in (data.get("history") or []))
    if done or names not in OLD_DEFAULTS:
        return labels
    import logging
    from brain_battle.data import session_history
    user, source = session_history.current_user()
    try:
        from brain_battle.support.platform_utils import computer_name
        computer = computer_name()
    except Exception:
        computer = ""
    try:
        from brain_battle.version import __version__ as version
    except Exception:
        version = ""
    new = [dict(x) for x in DEFAULT_LABELS]
    event = {"time": session_history._now_iso(), "user": user, "user_source": source,
             "computer": computer, "app_version": version, "event": EVENT_DEFAULT_REPLACED,
             "details": {"from": names, "to": [e["name"] for e in new]}}
    try:
        save(recordings_dir, new, add_history=event)
    except OSError:
        return labels                    # can't write: keep showing what is there
    logging.getLogger(__name__).info(
        "label list in %s was the old default %s — replaced by the current default",
        recordings_dir, names)
    return new


def load(recordings_dir, seed_if_missing: bool = True) -> list[dict]:
    """Return the team label list for this recordings folder, seeding the file
    from the defaults the first time it is used (and replacing an untouched
    old default once)."""
    p = Path(path(recordings_dir))
    if p.is_file():
        try:
            data = _read(p)
            labels = _normalise(data.get("labels"))
            if labels:
                return _replace_old_default(recordings_dir, labels, data)
        except (OSError, ValueError):
            pass  # unreadable → fall through to defaults
    labels = [dict(x) for x in DEFAULT_LABELS]
    if seed_if_missing:
        try:
            save(recordings_dir, labels)
        except OSError:
            pass
    return labels


def save(recordings_dir, labels, add_history: dict | None = None) -> None:
    """Write the team label list atomically, holding the folder lock so two
    computers editing it at once cannot interleave. The file's ``history`` is
    kept (and ``add_history`` appended to it)."""
    os.makedirs(str(recordings_dir), exist_ok=True)
    p = Path(path(recordings_dir))
    import brain_battle.data.safe_io as safe_io
    with safe_io.FolderLock(p):
        try:
            hist = list(_read(p).get("history") or []) if p.is_file() else []
        except (OSError, ValueError):
            hist = []
        if add_history:
            hist.append(add_history)
        data = {"schema_version": 1, "labels": _normalise(labels)}
        if hist:
            data["history"] = hist
        safe_io.atomic_write_json(p, data)


__all__ = ["DEFAULT_LABELS", "OLD_DEFAULTS", "KIND_LABEL", "POINT", "DURATION", "FILENAME",
           "EVENT_DEFAULT_REPLACED", "path", "load", "save", "history"]
