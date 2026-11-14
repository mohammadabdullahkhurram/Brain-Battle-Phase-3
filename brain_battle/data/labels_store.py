"""
labels_store.py
───────────────
Labels on disk and in memory. Pure logic (no Qt), so Review & Label drives it
and the tests exercise it headlessly.

**One file per labeller.** More than one person may label the same recording
(to check how well they agree), so each labeller's labels live in their own
file inside the recording folder:

    <recording>/labels/<labeller>.json

    {"schema_version": 3, "time_axis": "t_session_seconds",
     "labeller": "Maya K",
     "labels": [ {id, label, who, kind, t_start, t_end, color,
                  created_by, created_at}, ... ],
     "named_moments": [12.4, ...]}

  who   : "A" | "B" | "Both"
  kind  : "point" (a Moment, t_end is null) | "duration" (Lasts a while)
  times : numbers on the t_session_seconds axis (the one capture clock)
  named_moments : the "Mark this moment" marks this labeller has already named

Two people therefore never write to the same file, even when the recordings
folder is shared between computers. The same person labelling on two computers
at once is handled too: a save first merges in anything that appeared in the
file since it was last read (``LabelsStore.save``).

**Older recordings.** A single ``labels.json`` (schema 2, or the even older
schema 1) is still read everywhere; opening the recording in Review & Label
moves it to ``labels/<name>.json`` (``migrate_legacy``) and keeps the original
as ``labels.json.v2.bak``. Its labels belong to the name stored in their
``created_by`` field, or to "Earlier labels" if there is none.
(docs/decisions/adr-0019-one-labels-file-per-labeller.md)
"""
from __future__ import annotations

import collections
import datetime as _dt
import json
import os
import re
import uuid
from pathlib import Path
from typing import Optional

import brain_battle.data.safe_io as safe_io

SCHEMA_VERSION = 3
LEGACY_SCHEMA_VERSION = 2          # the single-file labels.json
TIME_AXIS = "t_session_seconds"
JSON_NAME = "labels.json"          # legacy single file (still read; migrated on open)
LABELS_DIR = "labels"              # one <labeller>.json per labeller
LEGACY_BACKUP = "labels.json.v2.bak"
EARLIER_LABELS = "Earlier labels"  # labeller name for legacy labels with no author

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
    """One labeller's placed labels, with an undo/redo history. Each mutating
    call records an inverse op; undo/redo replay them. The caller persists via
    ``save()`` and logs its own history events.

    ``labeller`` is whose labels these are; ``path`` is their file (set by
    ``open_for_labeller``), so ``save()`` needs no argument."""

    def __init__(self, labels=None, labeller: str = "", path=None, named_moments=None,
                 migrated_from: Optional[str] = None):
        self._labels: list[dict] = [dict(x) for x in (labels or [])]
        self._undo: list[tuple] = []   # (op, label_dict)
        self._redo: list[tuple] = []
        self.labeller = labeller or ""
        self.path = None if path is None else str(path)
        self.migrated_from = migrated_from
        self._named_moments: list[float] = [round(float(t), 3) for t in (named_moments or [])]
        self._removed_ids: set = set()         # deleted here since the file was read
        self._disk_stamp = _stamp(self.path)   # what the file looked like when read

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

    # ---- marked moments this labeller has named ----
    @property
    def named_moments(self) -> list[float]:
        return list(self._named_moments)

    def moment_named(self, t) -> bool:
        return any(abs(float(t) - x) < 1e-3 for x in self._named_moments)

    def mark_moment_named(self, t) -> None:
        if not self.moment_named(t):
            self._named_moments.append(round(float(t), 3))

    # ---- mutations (each clears the redo stack) ----
    def add(self, label: dict) -> dict:
        self._labels.append(label)
        self._removed_ids.discard(label.get("id"))
        self._undo.append(("add", label))
        self._redo.clear()
        return label

    def delete(self, label_id) -> dict | None:
        lab = self.by_id(label_id)
        if lab is None:
            return None
        self._labels.remove(lab)
        self._removed_ids.add(lab.get("id"))
        self._undo.append(("delete", lab))
        self._redo.clear()
        return lab

    # ---- undo / redo ----
    def _take(self, lab):
        if lab in self._labels:
            self._labels.remove(lab)
        self._removed_ids.add(lab.get("id"))

    def _put(self, lab):
        self._labels.append(lab)
        self._removed_ids.discard(lab.get("id"))

    def undo(self) -> tuple | None:
        if not self._undo:
            return None
        op, lab = self._undo.pop()
        self._take(lab) if op == "add" else self._put(lab)
        self._redo.append((op, lab))
        return (op, lab)

    def redo(self) -> tuple | None:
        if not self._redo:
            return None
        op, lab = self._redo.pop()
        self._put(lab) if op == "add" else self._take(lab)
        self._undo.append((op, lab))
        return (op, lab)

    # ---- persistence ----
    def to_json(self) -> dict:
        data = {"schema_version": SCHEMA_VERSION, "time_axis": TIME_AXIS,
                "labeller": self.labeller,
                "labels": [dict(x) for x in self._labels],
                "named_moments": sorted(self._named_moments)}
        if self.migrated_from:
            data["migrated_from"] = self.migrated_from
        return data

    def merge_from_disk(self) -> int:
        """If the file changed since it was read (the same labeller working on
        another computer), take in the labels and named moments that appeared
        there — except ones deleted here. Returns how many labels came in."""
        if not self.path or _stamp(self.path) == self._disk_stamp:
            return 0
        data = safe_io.read_json(self.path)
        if not isinstance(data, dict):
            return 0
        mine = {x.get("id") for x in self._labels}
        added = 0
        for lab in data.get("labels") or []:
            if isinstance(lab, dict) and lab.get("id") not in mine \
                    and lab.get("id") not in self._removed_ids and "t_start" in lab:
                self._labels.append(dict(lab))
                added += 1
        for t in data.get("named_moments") or []:
            try:
                self.mark_moment_named(float(t))
            except (TypeError, ValueError):
                pass
        return added

    def save(self, path=None) -> None:
        """Write this labeller's file atomically. Raises OSError if the folder
        cannot be written (e.g. a shared drive that has gone away)."""
        if path is not None:
            self.path = str(path)
        if not self.path:
            raise OSError("this labels store has no file")
        # Only the labels/ folder itself may be created — never its parents. If
        # the recording folder is not there (a shared drive that went away),
        # this must fail, not quietly make an empty look-alike folder.
        Path(self.path).parent.mkdir(exist_ok=True)
        self.merge_from_disk()
        safe_io.atomic_write_json(self.path, self.to_json())
        self._disk_stamp = _stamp(self.path)


def _stamp(path):
    """(mtime, size) of a file, or None if it is not there."""
    if not path:
        return None
    try:
        st = os.stat(path)
        return (st.st_mtime_ns, st.st_size)
    except OSError:
        return None


def load(path) -> tuple[LabelsStore, bool]:
    """Load ONE labels file (a labeller's file, or a legacy labels.json) into a store. Returns (store, migrated). A v1 file is
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
    if ver in (SCHEMA_VERSION, LEGACY_SCHEMA_VERSION):
        return LabelsStore(data.get("labels") or [], labeller=data.get("labeller") or "",
                           path=p, named_moments=data.get("named_moments"),
                           migrated_from=data.get("migrated_from")), False
    # v1 (or an older/unversioned file that still has a labels list) → migrate.
    v2 = migrate_v1_to_v2(data if isinstance(data, dict) else {})
    bak = p.with_suffix(".json.v1.bak")
    try:
        if not bak.exists():
            bak.write_text(p.read_text(encoding="utf-8"), encoding="utf-8")
    except OSError:
        pass
    store = LabelsStore(v2, path=p)
    try:
        store.save()
    except OSError:
        pass
    return store, True


# ─── one file per labeller ────────────────────────────────────────────────────

_SLUG_BAD = re.compile(r"[^a-z0-9]+")


def labeller_slug(name: str) -> str:
    """File-name-safe form of a labeller's name: 'Maya K.' → 'maya-k'. Names
    that differ only in capitals, spacing or punctuation are the same labeller.
    Non-Latin names keep their letters ('Zoë' → 'zoë')."""
    text = (name or "").strip().lower()
    out = []
    for ch in text:
        out.append(ch if ch.isalnum() else "-")
    slug = re.sub(r"-+", "-", "".join(out)).strip("-")[:40].strip("-")
    return slug or "labeller"


def labels_dir(session_root) -> str:
    return os.path.join(str(session_root), LABELS_DIR)


def labeller_path(session_root, name: str) -> str:
    return os.path.join(labels_dir(session_root), labeller_slug(name) + ".json")


def _legacy_owner(labels) -> str:
    """Whose labels a legacy labels.json holds: the most common ``created_by``,
    or 'Earlier labels' if none was recorded."""
    names = [str(x.get("created_by") or "").strip() for x in labels]
    names = [n for n in names if n and n != "migrated"]
    return collections.Counter(names).most_common(1)[0][0] if names else EARLIER_LABELS


def _read_legacy(session_root):
    """(labeller name, labels) from a not-yet-migrated labels.json, or None.
    Read-only: used by listings so a scan never writes into a recording."""
    p = os.path.join(str(session_root), JSON_NAME)
    data = safe_io.read_json(p) if os.path.isfile(p) else None
    if not isinstance(data, dict):
        return None
    if data.get("schema_version") in (LEGACY_SCHEMA_VERSION, SCHEMA_VERSION):
        labels = [x for x in (data.get("labels") or []) if isinstance(x, dict) and "t_start" in x]
    else:
        labels = migrate_v1_to_v2(data)
    return (_legacy_owner(labels), labels) if labels else None


def migrate_legacy(session_root) -> Optional[str]:
    """Move a single-file ``labels.json`` into ``labels/<name>.json`` and keep
    the original as ``labels.json.v2.bak``. Returns the labeller name the labels
    went to, or None if there was nothing to migrate. Safe if two computers do
    it at once: both write the same content, and the rename happens once."""
    root = str(session_root)
    legacy = os.path.join(root, JSON_NAME)
    if not os.path.isfile(legacy):
        return None
    store, _was_v1 = load(legacy)          # also upgrades a schema-1 file
    labels = store.labels
    owner = None
    try:
        if labels:
            owner = _legacy_owner(labels)
            dest = labeller_path(root, owner)
            existing = safe_io.read_json(dest)
            have = {x.get("id") for x in (existing or {}).get("labels") or []} \
                if isinstance(existing, dict) else set()
            merged = list((existing or {}).get("labels") or []) if isinstance(existing, dict) else []
            merged += [x for x in labels if x.get("id") not in have]
            LabelsStore(merged, labeller=(existing or {}).get("labeller") or owner if
                        isinstance(existing, dict) else owner, path=dest,
                        named_moments=(existing or {}).get("named_moments") if
                        isinstance(existing, dict) else None,
                        migrated_from=JSON_NAME).save()
        safe_io.replace_with_retry(legacy, os.path.join(root, LEGACY_BACKUP))
    except OSError:
        return None                         # read-only folder: keep reading the legacy file
    return owner


def read_all(session_root) -> dict:
    """``{labeller name: [label, …]}`` for every labeller of a recording, in
    name order. Every label carries a ``labeller`` key. A not-yet-migrated
    ``labels.json`` is included under its owner's name. Read-only."""
    out: dict = {}
    d = labels_dir(session_root)
    try:
        names = sorted(fn for fn in os.listdir(d) if fn.endswith(".json") and not fn.startswith("."))
    except OSError:
        names = []
    for fn in names:
        data = safe_io.read_json(os.path.join(d, fn))
        if not isinstance(data, dict):
            continue
        who = str(data.get("labeller") or os.path.splitext(fn)[0])
        labs = [dict(x, labeller=who) for x in (data.get("labels") or [])
                if isinstance(x, dict) and "t_start" in x]
        out.setdefault(who, []).extend(labs)
    legacy = _read_legacy(session_root)
    if legacy:
        who, labs = legacy
        have = {x.get("id") for x in out.get(who, [])}
        out.setdefault(who, []).extend(dict(x, labeller=who) for x in labs
                                       if x.get("id") not in have)
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def all_labels(session_root) -> list[dict]:
    """Every labeller's labels in one time-sorted list (each with ``labeller``)."""
    rows = [lab for labs in read_all(session_root).values() for lab in labs]
    rows.sort(key=lambda x: (float(x.get("t_start", 0.0)), x.get("labeller", "")))
    return rows


def labeller_counts(session_root) -> dict:
    """``{labeller name: number of labels}`` (labellers with a file but no
    labels yet are included with 0)."""
    return {who: len(labs) for who, labs in read_all(session_root).items()}


def same_labeller(a: str, b: str) -> bool:
    return labeller_slug(a) == labeller_slug(b)


def open_for_labeller(session_root, name: str) -> LabelsStore:
    """The store for one labeller of a recording, bound to their file (created
    on the first save). Migrates a legacy labels.json first."""
    migrate_legacy(session_root)
    path = labeller_path(session_root, name)
    data = safe_io.read_json(path)
    if isinstance(data, dict):
        return LabelsStore(data.get("labels") or [], labeller=data.get("labeller") or name,
                           path=path, named_moments=data.get("named_moments"),
                           migrated_from=data.get("migrated_from"))
    return LabelsStore([], labeller=name, path=path)


def legacy_sets(session_root) -> list[str]:
    """Names of label sets that came from an old single-file labels.json and
    have not been claimed by a labeller yet."""
    out = []
    d = labels_dir(session_root)
    try:
        files = sorted(os.listdir(d))
    except OSError:
        files = []
    for fn in files:
        data = safe_io.read_json(os.path.join(d, fn)) if fn.endswith(".json") else None
        if isinstance(data, dict) and data.get("migrated_from"):
            out.append(str(data.get("labeller") or os.path.splitext(fn)[0]))
    return out


def claim_legacy(session_root, legacy_name: str, store: LabelsStore) -> int:
    """"These are mine": move the labels of a migrated legacy set into
    ``store`` (the current labeller) and remove the legacy file. Returns how
    many labels moved. Only sets that came from an old labels.json can be
    claimed — never another labeller's own work."""
    src = labeller_path(session_root, legacy_name)
    data = safe_io.read_json(src)
    if not isinstance(data, dict) or not data.get("migrated_from"):
        return 0
    if os.path.abspath(src) == os.path.abspath(store.path or ""):
        store.migrated_from = None          # it already is this labeller's file
        store.save()
        return len(store)
    have = {x.get("id") for x in store.labels}
    moved = 0
    for lab in data.get("labels") or []:
        if isinstance(lab, dict) and "t_start" in lab and lab.get("id") not in have:
            store._labels.append(dict(lab))
            moved += 1
    for t in data.get("named_moments") or []:
        store.mark_moment_named(t)
    store.save()
    try:
        os.unlink(src)
    except OSError:
        pass
    return moved


def events_timeline(labels, phase_markers) -> list[dict]:
    """Phase markers + labels on ONE timeline for export's events.csv.
    Columns: t_start, t_end, source ('phase'|'label'), who, label, labeller.
    Sorted by t_start, then phases before labels at the same instant."""
    rows = []
    for m in (phase_markers or []):
        # accept (t, pid, label) or (t, pid, label, round)
        t = float(m[0]); lab = m[2] if len(m) > 2 else m[1]
        rows.append({"t_start": round(t, 3), "t_end": "", "source": "phase",
                     "who": "", "label": lab, "labeller": ""})
    for lab in (labels or []):
        rows.append({
            "t_start": round(float(lab["t_start"]), 3),
            "t_end": "" if lab.get("t_end") is None else round(float(lab["t_end"]), 3),
            "source": "label",
            "who": lab.get("who", ""),
            "label": lab.get("label", ""),
            "labeller": lab.get("labeller", ""),
        })
    rows.sort(key=lambda r: (r["t_start"], 0 if r["source"] == "phase" else 1))
    return rows


__all__ = ["LabelsStore", "load", "new_label", "migrate_v1_to_v2",
           "events_timeline", "SCHEMA_VERSION", "LEGACY_SCHEMA_VERSION", "TIME_AXIS",
           "JSON_NAME", "LABELS_DIR", "LEGACY_BACKUP", "EARLIER_LABELS",
           "POINT", "DURATION", "WHO_VALUES",
           "labeller_slug", "labels_dir", "labeller_path", "migrate_legacy", "read_all",
           "all_labels", "labeller_counts", "same_labeller", "open_for_labeller",
           "legacy_sets", "claim_legacy"]
