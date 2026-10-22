"""
session_storage.py
──────────────────
Owns the on-disk layout for a capture session (v2.x continuous-recording
model — one take per camera for the whole session, phases as timestamps):

    Brain Battle <timestamp>/
        continuous/
            Side Cams/*.mp4      # one MP4 per camera, entire session
            POV Cams/*.mp4       # (subfolders created lazily by the
            Muse data/*.csv      #  recording code when a slot records)
        phase_markers.csv        # t_session_seconds, phase_id, label,
                                 # round_num, unix_time — one row per
                                 # phase transition (appended live)
        session_manifest.json

Recording never stops between baseline → rounds → breaks; downstream tools
derive phase boundaries from phase_markers.csv rather than from folders.
The manifest additionally records the session config and each phase's
start/end wall-clock + monotonic timestamps for analytics.

(The v1.x per-phase model — baseline/, Round 1/, … each with their own
recordings — is gone; its folder-resolution API was removed with it.)

This module is pure filesystem logic: no Qt, no subprocess, no audio.
That keeps it easy to unit-test.
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

from session_controller import SessionConfig, Phase
from version import __version__


# ─── Directory naming ─────────────────────────────────────────────────────────

_SAFE_CHARS = re.compile(r'[<>:"/\\|?*]')


def _safe(name: str) -> str:
    """Strip characters that are illegal on Windows filesystems."""
    return _SAFE_CHARS.sub('-', name).strip()


def _session_folder_name(started_at: _dt.datetime) -> str:
    """
    'Brain Battle 2026-04-17 14-32-05' — colon-free so it works on Windows.
    """
    ts = started_at.strftime("%Y-%m-%d %H-%M-%S")
    return f"Brain Battle {ts}"


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
    # Measured per-source first-sample time (Unix), so each source's startup
    # latency is recorded, not assumed. Keyed by source id (e.g. "EEG_01").
    source_starts:        dict = field(default_factory=dict)

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

    # ---- capture-time clock bridge ------------------------------------------

    def set_capture_origin(self, t0_monotonic: float, t0_unix: float) -> None:
        """Record the shared capture-time origin captured at recording start.
        This is the authoritative bridge review/label read to place markers,
        camera frames, and EEG samples on ONE axis."""
        self.capture_t0_monotonic = float(t0_monotonic)
        self.capture_t0_unix = float(t0_unix)
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
            "capture_clock":  {
                "t0_monotonic": self.capture_t0_monotonic,
                "t0_unix":      self.capture_t0_unix,
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
                   started_at: Optional[_dt.datetime] = None) -> SessionLayout:
    """
    Build the Brain Battle <timestamp>/ tree for a v2.x continuous session:
    the root, continuous/ (+ Muse data/), and an initial manifest — created
    eagerly so disk-full / permission failures surface immediately. Camera
    subfolders are created lazily by the recording code (see NOTE below).
    """
    started_at = started_at or _dt.datetime.now()
    root = Path(base_dir) / _session_folder_name(started_at)
    root.mkdir(parents=True, exist_ok=False)  # fail loud on duplicate timestamps

    layout = SessionLayout(root=root, started_at=started_at, config=config)

    # Continuous recording folder — one MP4 per camera + one CSV per Muse-S
    # for the entire session, from baseline through final round end.
    # Subfolders are created lazily by the recording code only when a slot
    # actually records, so absent folders mean "nothing connected for that
    # source" rather than "feature broken".
    (root / "continuous").mkdir(parents=True, exist_ok=True)
    (root / "continuous" / "Muse data").mkdir(parents=True, exist_ok=True)

    # NOTE: per-phase folders (baseline/, Round 1/, …) used to be created
    # here for the v1.x per-phase-recording model. They are NOT created in
    # v2.x — recording is continuous and phases are derived from
    # phase_markers.csv. Downstream tools should walk continuous/ and read
    # the markers file instead of looking for separate round folders.

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


def scan_interrupted_sessions(base_dir: str | os.PathLike) -> list["SessionLayoutInfo"]:
    """Find capture sessions under base_dir that look interrupted (crashed
    mid-recording) and have not yet been flagged. Returns a list of
    SessionLayoutInfo sorted newest-first by started_at."""
    base = Path(base_dir)
    out: list[SessionLayoutInfo] = []
    if not base.is_dir():
        return out
    for child in base.iterdir():
        if not child.is_dir() or not child.name.startswith("Brain Battle "):
            continue
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
    """Flag a session's manifest `interrupted: true` (atomic temp+rename) so
    the crash-recovery prompt won't ask about it again. Does NOT fabricate a
    SESSION_END marker — the recording genuinely never finished. Returns True
    on success."""
    root = Path(session_root)
    m = _read_manifest(root)
    if m is None:
        return False
    m["interrupted"] = True
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


@dataclass
class SessionLayoutInfo:
    """Lightweight descriptor for a session folder found on disk (used by
    crash recovery — no live SessionLayout needed)."""
    root:         str
    started_at:   str      # ISO string from the manifest, for display
    started_unix: float


# ─── Disk-space estimate ────────────────────────────────────────────────────────

# Per-source write rates for the pre-flight size estimate. The video rate is
# now derived from the recorder's actual target bitrate (usb_camera.REC_BITRATE
# = 5 Mbps per camera, capped at -maxrate 6 Mbps) rather than measured from
# low-motion demo footage — so it holds for real boxing footage, which H.264
# rate control drives toward that target regardless of scene complexity. The
# EEG rate was measured from the 256 Hz CSV (same structure for real + demo).
VIDEO_BYTES_PER_SEC = 700_000   # ~5-6 Mbps H.264 target, bytes/s, with headroom
EEG_BYTES_PER_SEC   = 25_000    # ~22.5 KB/s/band measured + headroom


def session_total_seconds(config: SessionConfig) -> int:
    """Planned wall-clock length of a session: baseline + rounds + the breaks
    between them (no break after the last round)."""
    return (config.baseline_sec
            + config.num_rounds * config.round_sec
            + max(0, config.num_rounds - 1) * config.break_sec)


def estimate_session_bytes(config: SessionConfig, n_cameras: int, n_eeg: int) -> int:
    """Estimated on-disk size of a session given its configured duration and
    the number of recording cameras / EEG bands."""
    secs = session_total_seconds(config)
    return int(secs * (n_cameras * VIDEO_BYTES_PER_SEC + n_eeg * EEG_BYTES_PER_SEC))


def human_bytes(n: float) -> str:
    """Human-readable byte size, e.g. 264_000_000 -> '264 MB'."""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1024.0 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024.0


__all__ = [
    "SessionLayout", "PhaseRecord", "SessionLayoutInfo", "create_session",
    "is_interrupted_session", "scan_interrupted_sessions",
    "mark_session_interrupted", "session_total_seconds", "estimate_session_bytes",
    "human_bytes", "VIDEO_BYTES_PER_SEC", "EEG_BYTES_PER_SEC",
]
