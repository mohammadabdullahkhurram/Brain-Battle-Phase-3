"""
session_storage.py
──────────────────
Owns the on-disk layout of a recording (one continuous take per source for the
whole session; rounds and breaks are timestamps, not folders):

    2026-10-27 14-05 Sam vs Alex/
        continuous/
            Side Cams/*.mp4 + *.timestamps.csv   one video per camera
            POV Cams/*.mp4  + *.timestamps.csv
            Muse data/*.csv                      one EEG file per headband
            Muse sensors/*.csv                   movement / pulse (optional)
        phase_markers.csv        one row per phase transition (appended live)
        session_manifest.json    names, plan, clock origin, moments
        recording.xdf            everything above on one clock (session_xdf.py)

This module also finds the files of an existing recording (``eeg_files``,
``camera_files``, ``read_phase_markers``), classifies recordings for the
Sessions screen, and does the disk-space estimate.

Pure filesystem logic: no Qt, no subprocess, no audio — easy to unit-test.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from brain_battle.capture.session_controller import SessionConfig, Phase
from brain_battle.data.signals import EEG_DIR_NAME, SENSOR_DIR_NAME, SENSORS
from brain_battle.version import __version__


# ─── Directory naming ─────────────────────────────────────────────────────────

# Illegal on Windows (and '/' on macOS): the above + ASCII control chars.
_ILLEGAL_FS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WIN_RESERVED = {"CON", "PRN", "AUX", "NUL",
                 *(f"COM{i}" for i in range(1, 10)),
                 *(f"LPT{i}" for i in range(1, 10))}
_NAME_MAXLEN = 40   # per boxer name, to keep folder names sane


def sanitize_name(name: str, maxlen: int = _NAME_MAXLEN) -> str:
    """Make a boxer name safe to put in a folder name on macOS AND Windows:
    drop filesystem-illegal chars and control chars, collapse whitespace, trim
    trailing dots/spaces (Windows), avoid reserved device names, cap length.
    Emoji and ordinary Unicode are kept (valid on both filesystems)."""
    if not name:
        return ""
    s = _ILLEGAL_FS.sub(" ", str(name))
    s = re.sub(r"\s+", " ", s).strip()
    s = s.strip(". ")                       # Windows: no trailing dot/space
    if s.upper() in _WIN_RESERVED:
        s = f"_{s}"
    return s[:maxlen].strip()


def _session_folder_name(started_at: _dt.datetime,
                         boxer_a_name: Optional[str] = None,
                         boxer_b_name: Optional[str] = None) -> str:
    """'2026-04-17 14-32 Sam vs Alex' (colon-free so it works on Windows), or
    just '2026-04-17 14-32' when no names are given. One or both names
    sanitized."""
    ts = started_at.strftime("%Y-%m-%d %H-%M")
    a = sanitize_name(boxer_a_name or "")
    b = sanitize_name(boxer_b_name or "")
    if a and b:
        return f"{ts} {a} vs {b}"
    if a:
        return f"{ts} {a}"
    if b:
        return f"{ts} {b}"
    return ts


def _unique_dir(base_dir: Path, name: str) -> Path:
    """`base_dir/name`, or `name (2)`, `name (3)`… if it already exists. Never
    renames or reuses an existing folder."""
    cand = base_dir / name
    if not cand.exists():
        return cand
    i = 2
    while (base_dir / f"{name} ({i})").exists():
        i += 1
    return base_dir / f"{name} ({i})"


def _norm_boxer(b) -> dict:
    """Normalise a boxer arg (name str, or {name, headband} dict, or None) to
    {'name': str|None, 'headband': str|None}."""
    if b is None:
        return {"name": None, "headband": None}
    if isinstance(b, dict):
        return {"name": (b.get("name") or None), "headband": (b.get("headband") or None)}
    return {"name": (str(b) or None), "headband": None}


# ─── Data ─────────────────────────────────────────────────────────────────────

@dataclass
class PhaseRecord:
    """One entry in the session manifest — written per phase."""
    phase:            str             # "baseline" | "round" | "break"
    label:            str             # "BASELINE" | "ROUND 1" | "BREAK 1"
    round_num:        int             # 0 for baseline
    folder:           str             # relative path inside session root, "" for breaks
    planned_duration: int             # seconds (as configured)
    start_unix:       float           # wall-clock seconds since epoch
    start_monotonic:  float           # time.monotonic() snapshot
    end_unix:         Optional[float] = None
    end_monotonic:    Optional[float] = None


@dataclass
class SessionLayout:
    """Concrete filesystem layout for one live session."""
    root:          Path
    started_at:    _dt.datetime
    config:        SessionConfig
    phases:        list[PhaseRecord] = field(default_factory=list)
    # Capture-time clock bridge (set once at recording start). This pair,
    # captured at the same instant, is what maps the monotonic domain
    # (phase markers, camera frame sidecars — all t_session_seconds) to the
    # Unix domain (EEG CSV timestamps). t_session = unix_ts - capture_t0_unix.
    # Fixes the "three assumed zeros" of ARCHITECTURE_AND_EVALUATION.md §1.6/§2.4.
    capture_t0_monotonic: Optional[float] = None
    capture_t0_unix:      Optional[float] = None
    capture_t0_lsl:       Optional[float] = None   # same instant on the LSL clock
    # Measured per-source first-sample time (Unix), so each source's startup
    # latency is recorded, not assumed. Keyed by source id (e.g. "EEG_01").
    source_starts:        dict = field(default_factory=dict)
    # Boxers: {"A": {"name", "headband"}, "B": {...}} — names shown everywhere
    # in the UI, headband = the Muse-S device each boxer wears.
    boxers:               dict = field(default_factory=lambda: {
                              "A": {"name": None, "headband": None},
                              "B": {"name": None, "headband": None}})
    # True only for sessions written by tools/generate_demo_session.py, so the
    # Sessions screen can badge demo data and nobody mistakes it for a real
    # recording.
    demo:                 bool = False
    # "Mark this moment" during recording — each {t_session_seconds, unix_time}.
    # Surfaced in Review & Label as unnamed labels the user names later.
    moments:              list = field(default_factory=list)
    # Clean early stop (operator pressed Stop and save) — NOT an interruption.
    # Records where the recording was stopped so Sessions can show
    # "Stopped at 9:41 of 12:00".
    stopped_early:        bool = False
    stopped_t_session:    Optional[float] = None

    # ---- folder resolution ---------------------------------------------------

    def manifest_path(self) -> Path:
        return self.root / "session_manifest.json"

    def markers_path(self) -> Path:
        """CSV of phase transition markers for the continuous recording.
        Columns: t_session_seconds, phase_id, label, round_num, unix_time."""
        return self.root / "phase_markers.csv"

    def continuous_dir(self) -> Path:
        """Folder where the single continuous recording per camera lives."""
        return self.root / "continuous"

    # ---- continuous-recording helpers ---------------------------------------

    def append_marker(self, t_session_sec: float, phase_id: str,
                      label: str, round_num: int) -> None:
        """Append a phase-marker row to phase_markers.csv. Creates the
        file with a header on first call. Used by the continuous-recording
        pipeline to mark where in the single session video each phase
        starts/ends."""
        import csv as _csv
        path = self.markers_path()
        new_file = not path.exists()
        with path.open("a", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            if new_file:
                w.writerow(["t_session_seconds", "phase_id", "label",
                           "round_num", "unix_time"])
            w.writerow([f"{t_session_sec:.3f}", phase_id, label,
                       round_num, f"{time.time():.3f}"])

    def add_moment(self, t_session_sec: float) -> dict:
        """Record a 'Mark this moment' at a session time. Stored in the manifest
        (and surfaced in Review & Label as an unnamed label). Returns the entry."""
        entry = {"t_session_seconds": round(float(t_session_sec), 3),
                 "unix_time": round(time.time(), 3)}
        self.moments.append(entry)
        self._write_manifest()
        return entry

    def mark_stopped_early(self, t_session_sec: float) -> None:
        """Flag that the operator stopped the recording early (cleanly). This is
        NOT an interruption — a SESSION_END marker is still written."""
        self.stopped_early = True
        self.stopped_t_session = round(float(t_session_sec), 3)
        self._write_manifest()

    # ---- capture-time clock bridge ------------------------------------------

    def set_capture_origin(self, t0_monotonic: float, t0_unix: float,
                           t0_lsl: Optional[float] = None) -> None:
        """Record the shared capture-time origin captured at recording start
        (see ``capture/clock.session_origin``). This is the authoritative
        bridge that places markers, camera frames, and EEG samples on ONE
        axis; ``t0_lsl`` is the same instant on the LSL clock, used for the
        timestamps inside recording.xdf."""
        self.capture_t0_monotonic = float(t0_monotonic)
        self.capture_t0_unix = float(t0_unix)
        self.capture_t0_lsl = None if t0_lsl is None else float(t0_lsl)
        self._write_manifest()

    def record_source_start(self, source_id: str, first_unix: float) -> None:
        """Record a source's measured first-sample Unix time. With the
        capture origin set, its session-axis offset is first_unix -
        capture_t0_unix — the measured startup latency for that source."""
        entry = {"first_unix": float(first_unix)}
        if self.capture_t0_unix is not None:
            entry["first_t_session"] = float(first_unix) - self.capture_t0_unix
        self.source_starts[source_id] = entry
        self._write_manifest()

    # ---- manifest bookkeeping -----------------------------------------------

    def add_phase(self, rec: PhaseRecord) -> None:
        self.phases.append(rec)
        self._write_manifest()

    def close_phase(self, phase: Phase, round_num: int) -> None:
        """Stamp end times on the most recent matching phase."""
        for rec in reversed(self.phases):
            if rec.phase == phase.value and rec.round_num == round_num and rec.end_unix is None:
                rec.end_unix      = time.time()
                rec.end_monotonic = time.monotonic()
                break
        self._write_manifest()

    def _write_manifest(self) -> None:
        data = {
            "app":            "Brain Battle",
            "version":        __version__,
            "started_at":     self.started_at.isoformat(timespec="seconds"),
            "started_unix":   self.started_at.timestamp(),
            "boxers":         self.boxers,
            "demo":           self.demo,
            "moments":        self.moments,
            "stopped_early":  self.stopped_early,
            "stopped_t_session": self.stopped_t_session,
            "capture_clock":  {
                "t0_monotonic": self.capture_t0_monotonic,
                "t0_unix":      self.capture_t0_unix,
                "t0_lsl":       self.capture_t0_lsl,
            },
            "source_starts":  self.source_starts,
            "config":         asdict(self.config),
            "phases":         [asdict(p) for p in self.phases],
        }
        self.manifest_path().write_text(
            json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8"
        )


# ─── Public factory ───────────────────────────────────────────────────────────

def create_session(base_dir: str | os.PathLike,
                   config: SessionConfig,
                   started_at: Optional[_dt.datetime] = None,
                   boxer_a=None, boxer_b=None, demo: bool = False) -> SessionLayout:
    """
    Build the '<date time> <A> vs <B>/' tree for a v2.x continuous session:
    the root, continuous/ (+ Muse data/), and an initial manifest — created
    eagerly so disk-full / permission failures surface immediately. Camera
    subfolders are created lazily by the recording code (see NOTE below).

    `boxer_a`/`boxer_b` are each a name string or a {"name", "headband"} dict;
    names drive the folder name and are stored in the manifest. On a folder-name
    collision (same minute + names) a " (2)", " (3)"… suffix is appended; an
    existing folder is never renamed or reused.
    """
    started_at = started_at or _dt.datetime.now()
    a, b = _norm_boxer(boxer_a), _norm_boxer(boxer_b)
    base_name = _session_folder_name(started_at, a["name"], b["name"])
    root = _unique_dir(Path(base_dir), base_name)
    root.mkdir(parents=True, exist_ok=False)  # fail loud on a genuine race

    layout = SessionLayout(root=root, started_at=started_at, config=config,
                           boxers={"A": a, "B": b}, demo=bool(demo))

    # Continuous recording folder — one MP4 per camera + one CSV per Muse-S
    # for the entire session, from baseline through final round end.
    # Subfolders are created lazily by the recording code only when a slot
    # actually records, so absent folders mean "nothing connected for that
    # source" rather than "feature broken".
    (root / "continuous").mkdir(parents=True, exist_ok=True)
    (root / "continuous" / EEG_DIR_NAME).mkdir(parents=True, exist_ok=True)

    # Write an initial (empty-phases) manifest so it exists from t=0.
    layout._write_manifest()
    return layout


# ─── Crash recovery ─────────────────────────────────────────────────────────────

def _read_manifest(session_root: Path) -> Optional[dict]:
    p = session_root / "session_manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _update_manifest(session_root, change) -> bool:
    """Read the manifest, let ``change(manifest)`` edit it (return False to
    abandon), and write it back atomically (temp file + rename). Returns True
    if it was written."""
    root = Path(session_root)
    m = _read_manifest(root)
    if m is None or change(m) is False:
        return False
    tmp = root / "session_manifest.json.tmp"
    try:
        tmp.write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, root / "session_manifest.json")
        return True
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False


def _has_session_end(session_root: Path) -> bool:
    """True if phase_markers.csv contains a SESSION_END row — the explicit
    'this recording finished cleanly' marker written by _on_session_finished."""
    p = session_root / "phase_markers.csv"
    if not p.is_file():
        return False
    try:
        return "SESSION_END" in p.read_text(encoding="utf-8")
    except OSError:
        return False


def is_interrupted_session(session_root: str | os.PathLike) -> bool:
    """A session that recorded at least one phase but never finalised: no
    SESSION_END marker was written. (A clean finish always writes one.)
    Sessions already flagged `interrupted: true` in the manifest return
    False here so the recovery prompt doesn't nag about them again."""
    root = Path(session_root)
    m = _read_manifest(root)
    if m is None:
        return False
    if m.get("interrupted") is True:
        return False
    if not m.get("phases"):          # nothing was ever recorded
        return False
    return not _has_session_end(root)


def _session_dirs(base: Path):
    """Yield immediate subfolders that ARE sessions — identified by the
    presence of session_manifest.json, NOT by any folder-name prefix (folder
    names now carry boxer names). A folder with a present-but-unreadable
    manifest still counts (so the UI can surface it)."""
    if not base.is_dir():
        return
    for child in sorted(base.iterdir()):
        if child.is_dir() and (child / "session_manifest.json").is_file():
            yield child


def scan_interrupted_sessions(base_dir: str | os.PathLike) -> list["SessionLayoutInfo"]:
    """Find capture sessions under base_dir that look interrupted (crashed
    mid-recording) and have not yet been flagged. Returns a list of
    SessionLayoutInfo sorted newest-first by started_at."""
    base = Path(base_dir)
    out: list[SessionLayoutInfo] = []
    if not base.is_dir():
        return out
    for child in _session_dirs(base):
        if is_interrupted_session(child):
            m = _read_manifest(child) or {}
            out.append(SessionLayoutInfo(
                root=str(child),
                started_at=m.get("started_at", ""),
                started_unix=float(m.get("started_unix") or 0.0),
            ))
    out.sort(key=lambda s: s.started_unix, reverse=True)
    return out


def mark_session_interrupted(session_root: str | os.PathLike) -> bool:
    """Flag a session's manifest `interrupted: true` so it is reported once.
    Does NOT fabricate a SESSION_END marker — the recording genuinely never
    finished. Returns True on success."""
    return _update_manifest(session_root, lambda m: m.update(interrupted=True))


def dismiss_interrupted(session_root) -> bool:
    """Flag `interrupted_dismissed: true` so the Sessions interrupted banner
    never shows this recording again. Returns True on success."""
    return _update_manifest(session_root, lambda m: m.update(interrupted_dismissed=True))


def read_moments(session_root) -> list:
    """The 'Mark this moment' entries recorded during capture (each a dict with
    t_session_seconds), newest-last. [] if none / unreadable."""
    m = _read_manifest(Path(session_root))
    if not m:
        return []
    out = []
    for e in (m.get("moments") or []):
        if isinstance(e, dict) and "t_session_seconds" in e:
            try:
                out.append({"t_session_seconds": float(e["t_session_seconds"])})
            except (TypeError, ValueError):
                continue
    return out


def consume_moment(session_root, t_session_seconds) -> bool:
    """Remove the moment at (≈) `t_session_seconds` from the manifest once the
    user has named it in Review. Returns True if one was removed."""
    target = round(float(t_session_seconds), 3)

    def change(m):
        kept, removed = [], False
        for e in (m.get("moments") or []):
            try:
                t = round(float(e.get("t_session_seconds")), 3)
            except (TypeError, ValueError, AttributeError):
                kept.append(e)
                continue
            if not removed and abs(t - target) < 1e-3:
                removed = True
            else:
                kept.append(e)
        m["moments"] = kept
        return removed

    return _update_manifest(session_root, change)


# ─── Finding a recording's files ────────────────────────────────────────────────

def read_phase_markers(session_root) -> list[tuple]:
    """phase_markers.csv as time-sorted ``(t_session_seconds, phase_id, label)``
    rows. [] if the file is missing or unreadable; bad rows are skipped."""
    import csv as _csv
    p = Path(session_root) / "phase_markers.csv"
    out = []
    try:
        with open(p, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                try:
                    out.append((float(row["t_session_seconds"]),
                                row.get("phase_id", ""), row.get("label", "")))
                except (KeyError, TypeError, ValueError):
                    continue
    except OSError:
        return []
    out.sort(key=lambda r: r[0])
    return out


def read_csv_tolerant(path):
    """pandas.read_csv that ignores a last line with no line ending — the row
    a crash interrupted mid-write, which may hold a half-written number."""
    import io
    import pandas as pd      # imported here: the rest of this module needs no pandas
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        if size == 0:
            return pd.DataFrame()
        f.seek(size - 1)
        complete = f.read(1) == b"\n"
        if complete:
            return pd.read_csv(path, on_bad_lines="skip")
        f.seek(0)
        raw = f.read()
    return pd.read_csv(io.BytesIO(raw[:raw.rfind(b"\n") + 1]), on_bad_lines="skip")


# Marker tokens → the words the UI uses.

def friendly_phase(label: str) -> str:
    """Marker token → full DESIGN vocabulary name."""
    u = (label or "").upper()
    if u.startswith("BASELINE"):
        return "Rest before round 1"
    if u.startswith("ROUND"):
        m = re.search(r"(\d+)", u)
        return f"Round {m.group(1)}" if m else "Round"
    if u.startswith("BREAK"):
        return "Break"
    if u.startswith("SESSION_END") or u == "DONE":
        return "Finished"
    return label or ""


def short_phase(label: str) -> str:
    """Shortest sensible form for a narrow timeline band."""
    u = (label or "").upper()
    if u.startswith("BASELINE"):
        return "Rest"
    if u.startswith("ROUND"):
        return friendly_phase(label)       # "Round N" is already short
    if u.startswith("BREAK"):
        return "Break"
    if u.startswith("SESSION_END") or u == "DONE":
        return "End"
    return friendly_phase(label)


def _band_file_token(headband: str) -> str:
    """'MuseS-7538' → 'Muse S 7538', the form used in the EEG file names."""
    return (headband or "").replace("MuseS-", "Muse S ").replace(
        "Muse-S-", "Muse S ").replace("-", " ")


def eeg_files(session_root) -> dict:
    """``{"A": path, "B": path}`` — which EEG CSV belongs to which boxer.

    Decided by the headband names stored in THIS recording's manifest (so it
    stays right even if the headband assignment in Settings changes later).
    Recordings whose manifest names no headbands fall back to file-name order
    (first file = A)."""
    root = Path(session_root)
    muse = root / "continuous" / EEG_DIR_NAME
    files = sorted(p for p in muse.glob("*.csv")) if muse.is_dir() else []
    boxers = (_read_manifest(root) or {}).get("boxers") or {}
    out, used = {}, set()
    for who in ("A", "B"):
        token = _band_file_token((boxers.get(who) or {}).get("headband") or "")
        match = next((p for p in files if token and token in p.name and p not in used), None)
        if match is not None:
            out[who] = str(match)
            used.add(match)
    rest = [p for p in files if p not in used]
    for who in ("A", "B"):
        if who not in out and rest:
            out[who] = str(rest.pop(0))
    return out


def sensor_files(session_root) -> dict:
    """``{("A", "ACC"): path, …}`` for the optional movement / pulse CSVs,
    matched to boxers through their EEG file's name."""
    root = Path(session_root)
    out = {}
    for who, eeg in eeg_files(root).items():
        stem = Path(eeg).stem
        for kind in SENSORS:
            p = root / "continuous" / SENSOR_DIR_NAME / f"{stem}_{kind}.csv"
            if p.is_file():
                out[(who, kind)] = str(p)
    return out


def camera_files(session_root) -> list[tuple]:
    """``[(stem, mp4_path, sidecar_path), …]`` for every recorded camera, e.g.
    stem ``SIDE_01``; side cameras first, then the boxer views."""
    cont = Path(session_root) / "continuous"
    out = []
    for sub in ("Side Cams", "POV Cams"):
        d = cont / sub
        if d.is_dir():
            for mp4 in sorted(d.glob("*.mp4")):
                out.append((mp4.stem.replace("session_", ""), str(mp4),
                            str(mp4.with_suffix(".timestamps.csv"))))
    return out


@dataclass
class SessionLayoutInfo:
    """Lightweight descriptor for a session folder found on disk (used by
    crash recovery — no live SessionLayout needed)."""
    root:         str
    started_at:   str      # ISO string from the manifest, for display
    started_unix: float


# ─── Sessions listing + status (for the Sessions screen) ──────────────────────

STATUS_INTERRUPTED     = "interrupted"
STATUS_EXPORTED        = "exported"
STATUS_LABELLED        = "labelled"
STATUS_NEEDS_LABELLING = "needs_labelling"
STATUS_UNREADABLE      = "unreadable"


def _labels_count(root: Path) -> int:
    p = root / "labels.json"
    if not p.is_file():
        return 0
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        labs = data.get("labels", []) if isinstance(data, dict) else []
        return len([x for x in labs if isinstance(x, dict)])
    except (OSError, ValueError):
        return 0


def _history_has_export(root: Path) -> bool:
    p = root / "history.jsonl"
    if not p.is_file():
        return False
    try:
        for ln in p.read_text(encoding="utf-8").splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                obj = json.loads(ln)
            except ValueError:
                continue
            if isinstance(obj, dict) and obj.get("event") == "export_created":
                return True
    except OSError:
        pass
    return False


def _last_marker_tsession(root: Path):
    p = root / "phase_markers.csv"
    if not p.is_file():
        return None
    import csv as _csv
    last = None
    try:
        with open(p, encoding="utf-8") as f:
            for row in _csv.DictReader(f):
                try:
                    last = float(row.get("t_session_seconds", ""))
                except (TypeError, ValueError):
                    continue
    except OSError:
        return None
    return last


@dataclass
class SessionInfo:
    root:          str
    name:          str              # folder basename
    started_at:    str              # ISO from manifest ("" if unreadable)
    started_unix:  float
    boxer_a:       Optional[str]
    boxer_b:       Optional[str]
    rounds:        int
    planned_sec:   int
    stopped_sec:   Optional[float]  # where an interrupted recording stopped
    status:        str
    labels_count:  int
    interrupted:   bool
    dismissed:     bool
    readable:      bool
    demo:          bool = False


def session_info(root) -> SessionInfo:
    """Classify one session folder for the Sessions screen. Never raises; an
    unreadable manifest yields a 'unreadable' row rather than being hidden."""
    root = Path(root)
    name = root.name
    m = _read_manifest(root)
    if m is None:
        return SessionInfo(str(root), name, "", 0.0, None, None, 0, 0, None,
                           STATUS_UNREADABLE, 0, False, False, False)
    boxers = m.get("boxers") or {}
    a = (boxers.get("A") or {}).get("name")
    b = (boxers.get("B") or {}).get("name")
    cfg = m.get("config") or {}
    rounds = int(cfg.get("num_rounds") or 0)
    planned = int((cfg.get("baseline_sec", 0) or 0)
                  + rounds * (cfg.get("round_sec", 0) or 0)
                  + max(0, rounds - 1) * (cfg.get("break_sec", 0) or 0))
    interrupted = m.get("interrupted") is True
    dismissed = m.get("interrupted_dismissed") is True
    labels = _labels_count(root)
    if interrupted:
        status = STATUS_INTERRUPTED
    elif _history_has_export(root):
        status = STATUS_EXPORTED
    elif labels >= 1:
        status = STATUS_LABELLED
    else:
        status = STATUS_NEEDS_LABELLING
    if interrupted:
        stopped = _last_marker_tsession(root)
    elif m.get("stopped_early") is True:
        # Cleanly stopped early — show "Stopped at m:ss of m:ss", not interrupted.
        stopped = m.get("stopped_t_session")
        if stopped is None:
            stopped = _last_marker_tsession(root)
    else:
        stopped = None
    return SessionInfo(str(root), name, m.get("started_at", "") or "",
                       float(m.get("started_unix") or 0.0), a, b, rounds,
                       planned, stopped, status, labels, interrupted, dismissed, True,
                       demo=m.get("demo") is True)


def list_sessions(base_dir) -> list[SessionInfo]:
    """All sessions under base_dir (discovered by manifest presence), newest
    first. Reads small files only — safe to call off the UI thread."""
    out = [session_info(d) for d in _session_dirs(Path(base_dir))]
    out.sort(key=lambda s: (s.started_unix, s.name), reverse=True)
    return out


def flag_new_interrupted(base_dir) -> int:
    """Flag any not-yet-flagged interrupted sessions (crashed, no SESSION_END)
    so their status + the banner are correct. Logs one recording_interrupted
    history event each. Returns how many were flagged."""
    n = 0
    for info in scan_interrupted_sessions(base_dir):   # unflagged interrupted
        if mark_session_interrupted(info.root):
            n += 1
            try:
                import brain_battle.data.session_history as session_history
                session_history.log_event(
                    info.root, session_history.EVENT_RECORDING_INTERRUPTED)
            except Exception:
                pass
            # The crash happened before recording.xdf was made — build it now
            # from what was saved (never raises).
            import brain_battle.data.session_xdf as session_xdf
            session_xdf.ensure_session_xdf(info.root)
    return n


def folder_size_bytes(root) -> int:
    """Total size of all files under a session folder (for the footer). The
    walk is slow-ish on big sessions — call it off the UI thread."""
    total = 0
    try:
        for dp, _dn, fn in os.walk(root):
            for f in fn:
                try:
                    total += os.path.getsize(os.path.join(dp, f))
                except OSError:
                    continue
    except OSError:
        pass
    return total


# ─── Disk-space estimate ────────────────────────────────────────────────────────

# Per-source write rates for the pre-flight size estimate. The video rate is
# derived from the recorder's actual target bitrate (Settings' video-quality
# mbps, see `video_bytes_per_sec_for_mbps` below — Standard = 5 Mbps per
# camera, capped at -maxrate 1.2x) rather than measured from low-motion demo
# footage — so it holds for real boxing footage, which H.264 rate control
# drives toward that target regardless of scene complexity. The EEG rate was
# measured from the 256 Hz CSV (same structure for real + demo).
# VIDEO_BYTES_PER_SEC below is the Standard-quality default used when a
# caller doesn't pass an explicit per-quality rate.
VIDEO_BYTES_PER_SEC = 700_000   # ~5-6 Mbps H.264 target, bytes/s, with headroom
EEG_BYTES_PER_SEC   = 25_000    # ~22.5 KB/s/band measured + headroom

# Peak-rate headroom above the nominal bitrate: the recorder's -maxrate/
# -bufsize caps (usb_camera.py) let a burst of motion run above the nominal
# target, so the estimate budgets +12% — this is exactly what produces
# VIDEO_BYTES_PER_SEC above from the default 5 Mbps (5_000_000/8*1.12 = 700_000).
VIDEO_RATE_HEADROOM = 1.12


def video_bytes_per_sec_for_mbps(mbps: float) -> int:
    """Bytes/sec estimate for one camera at a given nominal H.264 bitrate
    (Settings' video-quality mbps), including the recorder's peak headroom —
    the single place the Settings copy, the disk estimate, and the recorder's
    own rate-control flags all derive from, so they can't drift apart."""
    return int(float(mbps) * 1_000_000 / 8 * VIDEO_RATE_HEADROOM)


def session_total_seconds(config: SessionConfig) -> int:
    """Planned wall-clock length of a session: baseline + rounds + the breaks
    between them (no break after the last round)."""
    return (config.baseline_sec
            + config.num_rounds * config.round_sec
            + max(0, config.num_rounds - 1) * config.break_sec)


def estimate_session_bytes(config: SessionConfig, n_cameras: int, n_eeg: int,
                           video_bytes_per_sec: int = VIDEO_BYTES_PER_SEC) -> int:
    """Estimated on-disk size of a session given its configured duration, the
    number of recording cameras / EEG bands, and the per-camera video rate
    (pass `video_bytes_per_sec_for_mbps(settings.video_mbps())` for the
    currently-chosen video quality; defaults to the Standard rate)."""
    secs = session_total_seconds(config)
    return int(secs * (n_cameras * video_bytes_per_sec + n_eeg * EEG_BYTES_PER_SEC))


def disk_space_verdict(config: SessionConfig, n_cameras: int, n_eeg: int,
                       free_bytes: int, video_bytes_per_sec: int = VIDEO_BYTES_PER_SEC) -> str:
    """Pure decision for the pre-flight disk check: 'blocked' when free space is
    below the estimate, 'low' when below 2x it, else 'ok'. Shared by the setup
    screen and the record flow so the UI copy and the gate never diverge."""
    est = estimate_session_bytes(config, n_cameras, n_eeg, video_bytes_per_sec)
    if free_bytes < est:
        return "blocked"
    if free_bytes < 2 * est:
        return "low"
    return "ok"


def human_bytes(n: float) -> str:
    """Human-readable byte size, e.g. 264_000_000 -> '264 MB'."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0


__all__ = [
    "SessionLayout", "PhaseRecord", "SessionLayoutInfo", "SessionInfo",
    "create_session", "sanitize_name",
    "is_interrupted_session", "scan_interrupted_sessions", "flag_new_interrupted",
    "mark_session_interrupted", "dismiss_interrupted",
    "read_moments", "consume_moment",
    "read_phase_markers", "read_csv_tolerant", "friendly_phase", "short_phase", "eeg_files", "sensor_files", "camera_files",
    "session_info", "list_sessions", "folder_size_bytes",
    "session_total_seconds", "estimate_session_bytes", "disk_space_verdict",
    "human_bytes", "VIDEO_BYTES_PER_SEC", "EEG_BYTES_PER_SEC",
    "video_bytes_per_sec_for_mbps", "VIDEO_RATE_HEADROOM",
    "STATUS_INTERRUPTED", "STATUS_EXPORTED", "STATUS_LABELLED",
    "STATUS_NEEDS_LABELLING", "STATUS_UNREADABLE",
]
