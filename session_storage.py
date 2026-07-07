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


__all__ = ["SessionLayout", "PhaseRecord", "create_session"]
