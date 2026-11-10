"""
labels_store.py
───────────────
labels.json schema v2 + an in-memory store with undo/redo. Pure logic (no Qt),
so the merged Review & Label screen drives it and the tests exercise it
headlessly.

labels.json v2:
    {"schema_version": 2, "time_axis": "t_session_seconds",
     "labels": [ {id, label, who, kind, t_start, t_end, color,
                  created_by, created_at}, ... ]}

  who   : "A" | "B" | "Both"
  kind  : "point" (a Moment, t_end is null) | "duration" (Lasts a while)
  times : numbers on the t_session_seconds axis (the one capture clock)

v1 labels.json ({"schema_version": 1, "time_axis": "labelling_playhead_offset0",
labels:[{t_seconds, label, color, boxer, end_t_seconds}]}) is migrated
automatically. v1's playhead axis shares its origin with t_session_seconds
(session start); any residual is sub-frame and unrecorded, so v1 t_seconds map
straight to v2 t_start. The original is kept as labels.json.v1.bak.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import uuid
from pathlib import Path

SCHEMA_VERSION = 2
TIME_AXIS = "t_session_seconds"
JSON_NAME = "labels.json"

POINT = "point"
DURATION = "duration"
WHO_VALUES = ("A", "B", "Both")


def _now_iso() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def new_label(label: str, who: str, kind: str, t_start: float,
              t_end: float | None = None, color: str = "#3D4FD6",
              created_by: str = "", created_at: str | None = None,
              id: str | None = None) -> dict:
    if who not in WHO_VALUES:
        who = "Both"
    if kind not in (POINT, DURATION):
        kind = POINT
    if kind == POINT:
        t_end = None
    return {
        "id": id or uuid.uuid4().hex,
        "label": str(label),
        "who": who,
        "kind": kind,
        "t_start": round(float(t_start), 3),
        "t_end": None if t_end is None else round(float(t_end), 3),
        "color": str(color or "#3D4FD6"),
        "created_by": created_by or "",
        "created_at": created_at or _now_iso(),
    }


def migrate_v1_to_v2(v1: dict, created_by: str = "migrated") -> list[dict]:
    """Turn a v1 labels dict into a list of v2 label dicts."""
    out = []
    for e in (v1.get("labels") or []):
        if not isinstance(e, dict):
            continue
        try:
            t0 = float(e.get("t_seconds", 0.0))
        except (TypeError, ValueError):
            continue
        end = e.get("end_t_seconds")
        try:
            t1 = None if end is None else float(end)
        except (TypeError, ValueError):
            t1 = None
        who = e.get("boxer")
        who = who if who in WHO_VALUES else "Both"
        kind = DURATION if t1 is not None else POINT
        out.append(new_label(
            label=e.get("label", "Label"), who=who, kind=kind,
            t_start=t0, t_end=t1, color=e.get("color", "#3D4FD6"),
            created_by=created_by))
    return out


class LabelsStore:
    """Holds the placed labels with an undo/redo history. Each mutating call
    records an inverse op; undo/redo replay them. Caller persists via save()
    and logs its own history events."""

    def __init__(self, labels=None):
        self._labels: list[dict] = [dict(x) for x in (labels or [])]
        self._undo: list[tuple] = []   # (op, label_dict)
        self._redo: list[tuple] = []

    # ---- queries ----
    @property
    def labels(self) -> list[dict]:
        return list(self._labels)

    def __len__(self):
        return len(self._labels)

    def by_id(self, label_id):
        return next((x for x in self._labels if x["id"] == label_id), None)

    def can_undo(self) -> bool:
        return bool(self._undo)

    def can_redo(self) -> bool:
        return bool(self._redo)

    # ---- mutations (each clears the redo stack) ----
    def add(self, label: dict) -> dict:
        self._labels.append(label)
        self._undo.append(("add", label))
        self._redo.clear()
        return label

    def delete(self, label_id) -> dict | None:
        lab = self.by_id(label_id)
        if lab is None:
            return None
        self._labels.remove(lab)
        self._undo.append(("delete", lab))
        self._redo.clear()
        return lab

    # ---- undo / redo ----
    def undo(self) -> tuple | None:
        if not self._undo:
            return None
        op, lab = self._undo.pop()
        if op == "add":
            if lab in self._labels:
                self._labels.remove(lab)
        else:  # delete → put it back
            self._labels.append(lab)
        self._redo.append((op, lab))
        return (op, lab)

    def redo(self) -> tuple | None:
        if not self._redo:
            return None
        op, lab = self._redo.pop()
        if op == "add":
            self._labels.append(lab)
        else:  # delete
            if lab in self._labels:
                self._labels.remove(lab)
        self._undo.append((op, lab))
        return (op, lab)

    # ---- persistence ----
    def to_json(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, "time_axis": TIME_AXIS,
                "labels": [dict(x) for x in self._labels]}

    def save(self, path) -> None:
        p = Path(path)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(p)


def load(path) -> tuple[LabelsStore, bool]:
    """Load labels.json into a store. Returns (store, migrated). A v1 file is
    migrated to v2 in place (original kept as labels.json.v1.bak) and migrated
    is True; a missing file yields an empty store."""
    p = Path(path)
    if not p.is_file():
        return LabelsStore(), False
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return LabelsStore(), False
    ver = data.get("schema_version") if isinstance(data, dict) else None
    if ver == SCHEMA_VERSION:
        return LabelsStore(data.get("labels") or []), False
    # v1 (or an older/unversioned file that still has a labels list) → migrate.
    v2 = migrate_v1_to_v2(data if isinstance(data, dict) else {})
    bak = p.with_suffix(".json.v1.bak")
    try:
        if not bak.exists():
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    store = LabelsStore(v2)
    try:
        store.save(p)
    except OSError:
        pass
    return store, True


def events_timeline(labels, phase_markers) -> list[dict]:
    """Phase markers + labels on ONE timeline for export's events.csv.
    Columns: t_start, t_end, source ('phase'|'label'), who, label. Sorted by
    t_start, then phases before labels at the same instant."""
    rows = []
    for m in (phase_markers or []):
        # accept (t, pid, label) or (t, pid, label, round)
        t = float(m[0]); lab = m[2] if len(m) > 2 else m[1]
        rows.append({"t_start": round(t, 3), "t_end": "", "source": "phase",
                     "who": "", "label": lab})
    for lab in (labels or []):
        rows.append({
            "t_start": round(float(lab["t_start"]), 3),
            "t_end": "" if lab.get("t_end") is None else round(float(lab["t_end"]), 3),
            "source": "label",
            "who": lab.get("who", ""),
            "label": lab.get("label", ""),
        })
    rows.sort(key=lambda r: (r["t_start"], 0 if r["source"] == "phase" else 1))
    return rows


__all__ = ["LabelsStore", "load", "new_label", "migrate_v1_to_v2",
           "events_timeline", "SCHEMA_VERSION", "TIME_AXIS", "JSON_NAME",
           "POINT", "DURATION", "WHO_VALUES"]
