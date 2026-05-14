"""
session_storage.py
──────────────────
Owns the on-disk layout for a capture session.

Per the Brain Battle spec, every session is saved inside:

    Brain Battle <timestamp>/
        baseline/
            Side Cams/
            POV Cams/
            Muse data/
        Round 1/
            Side Cams/
            POV Cams/
            Muse data/
        Round 2/
            ...
        session_manifest.json

The manifest captures the session config plus each phase's start/end
wall-clock and monotonic timestamps — this is what the post-session sync
engine uses to split SD-card footage into per-round segments later.

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


def _phase_folder_name(phase: Phase, round_num: int) -> str:
    if phase == Phase.BASELINE:
        return "baseline"
    if phase == Phase.ROUND:
        return f"Round {round_num}"
    if phase == Phase.BREAK:
        # Breaks do not get their own recordings under the spec, but we
        # still note them in the manifest; no folder needed.
        return ""
    return ""


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

    # ---- folder resolution ---------------------------------------------------

    def phase_dir(self, phase: Phase, round_num: int) -> Optional[Path]:
        """
        Returns the existing folder for the given phase, or None for BREAK
        phases (breaks don't get a folder under the spec).
        """
        name = _phase_folder_name(phase, round_num)
        if not name:
            return None
        return self.root / name

    def side_cams_dir(self, phase: Phase, round_num: int) -> Optional[Path]:
        pd = self.phase_dir(phase, round_num)
        return pd / "Side Cams" if pd else None

    def pov_cams_dir(self, phase: Phase, round_num: int) -> Optional[Path]:
        pd = self.phase_dir(phase, round_num)
        return pd / "POV Cams" if pd else None

    def muse_dir(self, phase: Phase, round_num: int) -> Optional[Path]:
        pd = self.phase_dir(phase, round_num)
        return pd / "Muse data" if pd else None

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
            "version":        "Phase 2",
            "started_at":     self.started_at.isoformat(timespec="seconds"),
            "started_unix":   self.started_at.timestamp(),
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
    Build the full Brain Battle <timestamp>/ tree up-front.

    We create every phase folder eagerly (even before that phase runs) so
    that: (a) disk-full / permission failures surface immediately, and
    (b) the operator can watch files appear in the right place in real time.
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
