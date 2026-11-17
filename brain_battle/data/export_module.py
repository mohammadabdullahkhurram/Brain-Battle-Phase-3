"""
export_module.py — "Export for analysis"
────────────────────────────────────────
One button in Review & Label → a small dialog → one zip holding everything an
analyst needs:

    recording.xdf         every signal + markers + labels + video-frame times
                          on one clock (opens in EEGLAB, MNE-Python, pyxdf)
    labels.csv            every labeller's placed labels (a ``labeller`` column)
    events.csv            phase markers + labels on one timeline
    agreement.csv         how far each pair of labellers agree (2+ labellers)
    eeg_coregistered.csv  both boxers' EEG on one time grid (for pandas / R)
    eeg/                  the raw per-headband EEG CSVs
    sensors/              headband movement / pulse CSVs (if recorded)
    videos/               camera MP4s + their per-frame .timestamps.csv
    session_manifest.json, phase_markers.csv, moments.csv, timing_quality.csv,
    contact_quality.csv, history.jsonl, README.txt
    combined_2x2.mp4      optional (off by default; slow to make)

Fighters appear only as their codes and colours (ADR-0023), so there are no
names to hide. The 4 camera videos can be left out (``include_videos``) for a
small export.

Also here: the H.264 encoder probe shared with the camera recorder
(``_HW_ENCODER`` — a hardware encoder is only used if a test encode actually
works), ``VideoExportWorker`` (the optional 2×2 combined video), and
``build_coregistered_eeg``.
"""

from __future__ import annotations

import csv
import logging
import os
import subprocess
import sys
import tempfile
import zipfile
import datetime
import shutil
from typing import Optional

from PyQt6.QtCore import QThread, pyqtSignal

log = logging.getLogger(__name__)

from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QProgressBar, QFileDialog,
)


# --- Hardware encoder detection ----------------------------------------------

def _encoder_args(encoder: str) -> list[str]:
    """Return encoder-specific quality/preset flags.

    MUST be defined above _detect_hw_encoder's import-time invocation —
    _validate_encoder builds its probe command with these flags, and a
    forward reference here raises NameError inside the probe's broad
    except, silently downgrading every run to libx264 (real bug, found
    2026-07-06)."""
    if encoder == "libx264":
        return ["-preset", "fast", "-crf", "23"]
    elif encoder == "h264_nvenc":
        return ["-preset", "p4", "-cq", "23"]
    elif encoder == "h264_videotoolbox":
        return ["-q:v", "65"]
    elif encoder in ("h264_amf", "h264_qsv"):
        return ["-quality", "balanced"]
    return ["-preset", "fast", "-crf", "23"]


def _candidate_encoders() -> list[str]:
    """Hardware H.264 encoder candidates for this platform, best first."""
    if sys.platform == "win32":
        return ["h264_nvenc", "h264_amf", "h264_qsv"]
    if sys.platform == "darwin":
        return ["h264_videotoolbox"]
    return ["h264_nvenc", "h264_qsv"]  # Linux


def _validate_encoder(ff: str, encoder: str) -> bool:
    """
    Prove the encoder actually WORKS, not just that ffmpeg was compiled with
    it: encode 8 tiny black frames to the null muxer with the same quality
    flags a real export would use. An nvenc-enabled ffmpeg build on a machine
    with no NVIDIA GPU lists h264_nvenc but fails here at device init.
    """
    try:
        r = subprocess.run(
            [ff, "-hide_banner", "-v", "error",
             "-f", "lavfi", "-i", "color=black:s=192x108:r=8:d=1",
             "-frames:v", "8", "-c:v", encoder]
            + _encoder_args(encoder)
            + ["-f", "null", "-"],
            capture_output=True, timeout=20,
        )
        return r.returncode == 0
    except Exception as e:
        # Swallowed-probe-error class (the videotoolbox NameError bug lived
        # here for four commits) — always leave a trace.
        log.warning("encoder probe for %s raised: %r", encoder, e)
        return False


def _detect_hw_encoder() -> str:
    """
    Pick the best USABLE H.264 encoder.

    Priority:
      Windows: h264_nvenc > h264_amf > h264_qsv > libx264
      macOS:   h264_videotoolbox > libx264
      Linux:   h264_nvenc > h264_qsv > libx264

    A candidate must both appear in `ffmpeg -encoders` (compiled in) AND
    pass a throwaway test encode (_validate_encoder — hardware present and
    initialisable). Result is cached once at import time via _HW_ENCODER.
    """
    ff = shutil.which("ffmpeg")
    if not ff:
        return "libx264"
    try:
        result = subprocess.run(
            [ff, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=8,
        )
        listed = result.stdout
    except Exception as e:
        log.warning("ffmpeg -encoders listing failed (%r) — falling back to libx264", e)
        return "libx264"
    for enc in _candidate_encoders():
        if enc not in listed:
            continue
        if _validate_encoder(ff, enc):
            return enc
        log.warning("%s is listed by ffmpeg but failed a test encode "
                    "(no usable hardware?) — falling back", enc)
    return "libx264"


# Detect once at import time; shared by all workers in this process
_HW_ENCODER = _detect_hw_encoder()
log.info("H.264 encoder: %s", _HW_ENCODER)


# --- Helpers -----------------------------------------------------------------

def _find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def _fmt_ts(seconds: float) -> str:
    """Convert float seconds to HH:MM:SS.mmm string for ffmpeg -ss."""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:06.3f}"


# --- 1. Video export worker --------------------------------------------------

class VideoExportWorker(QThread):
    """
    Merges up to 4 synced videos into a quad-grid MP4 using ffmpeg.

    Layout (2x2):
        [video 1] [video 2]
        [video 3] [video 4]

    Each video is:
      - Trimmed from its sync offset (so all start at global t=0)
      - Scaled to CELL_W x CELL_H  (default 960x540, giving 1920x1080 output)
      - Padded with black if shorter than the reference

    Uses hardware encoding when available (_HW_ENCODER).
    """

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    CELL_W = 960
    CELL_H = 540

    def __init__(
        self,
        video_paths:  list[str],
        offset_secs:  list[float],
        output_path:  str,
        global_duration: float = 0.0,
        parent=None,
    ):
        super().__init__(parent)
        self.video_paths     = video_paths
        self.offset_secs     = offset_secs
        self.output_path     = output_path
        self.global_duration = global_duration

    def run(self):
        ffmpeg = _find_ffmpeg()
        if not ffmpeg:
            from brain_battle.support.platform_utils import ffmpeg_install_hint
            self.error.emit("The video tool ffmpeg was not found. " + ffmpeg_install_hint())
            return
        try:
            self._export(ffmpeg)
        except Exception as exc:
            self.error.emit(str(exc))

    def _export(self, ffmpeg: str):
        n = len(self.video_paths)
        self.progress.emit(5, f"Preparing ffmpeg filter graph (encoder: {_HW_ENCODER})...")

        # Build input args
        input_args = []
        for i, (path, offset) in enumerate(zip(self.video_paths, self.offset_secs)):
            if offset > 0:
                input_args += ["-ss", _fmt_ts(offset)]
            if self.global_duration > 0:
                input_args += ["-t", f"{self.global_duration:.3f}"]
            input_args += ["-i", path]

        # Fill missing slots with lavfi black
        null_inputs = []
        for i in range(n, 4):
            dur = self.global_duration if self.global_duration > 0 else 3600
            null_inputs += [
                "-f", "lavfi",
                "-t", f"{dur:.3f}",
                "-i", f"color=black:s={self.CELL_W}x{self.CELL_H}:r=30",
            ]

        self.progress.emit(15, "Building filter graph...")

        # Scale each input to CELL_W x CELL_H, then xstack 2x2
        filter_parts = []
        scale_labels = []
        for i in range(4):
            lbl = f"v{i}"
            filter_parts.append(
                f"[{i}:v]scale={self.CELL_W}:{self.CELL_H}:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={self.CELL_W}:{self.CELL_H}:(ow-iw)/2:(oh-ih)/2,"
                f"setsar=1[{lbl}]"
            )
            scale_labels.append(f"[{lbl}]")

        stack_inputs = "".join(scale_labels)
        filter_parts.append(
            f"{stack_inputs}xstack=inputs=4:"
            f"layout=0_0|w0_0|0_h0|w0_h0[out]"
        )

        filter_complex = ";".join(filter_parts)

        self.progress.emit(25, f"Running ffmpeg ({_HW_ENCODER}) — this may take a while...")

        cmd = (
            [ffmpeg, "-y"]
            + input_args
            + null_inputs
            + [
                "-filter_complex", filter_complex,
                "-map", "[out]",
                "-c:v", _HW_ENCODER,
            ]
            + _encoder_args(_HW_ENCODER)
            + [
                "-movflags", "+faststart",
                self.output_path,
            ]
        )

        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        # Parse ffmpeg progress from stderr, keeping a tail of recent lines.
        # The readline() loop drains stderr to EOF, so a later
        # proc.stderr.read() would return empty — the tail deque is the ONLY
        # copy of ffmpeg's error output if the encode fails.
        import collections
        stderr_tail: "collections.deque[str]" = collections.deque(maxlen=40)
        total_dur = self.global_duration or 1.0
        while True:
            line = proc.stderr.readline()
            if not line:
                break
            line = line.decode(errors="replace")
            # ffmpeg separates in-place stats updates with \r within one
            # \n-terminated chunk — split so the tail stays line-shaped.
            for part in line.split("\r"):
                if part.strip():
                    stderr_tail.append(part.rstrip())
            if "time=" in line:
                try:
                    t_str = line.split("time=")[1].split()[0]
                    parts = t_str.split(":")
                    if len(parts) == 3:
                        elapsed = (
                            float(parts[0]) * 3600
                            + float(parts[1]) * 60
                            + float(parts[2])
                        )
                        pct = min(95, int(25 + (elapsed / total_dur) * 70))
                        self.progress.emit(pct, f"Encoding... {t_str}")
                except Exception:
                    pass

        proc.wait()

        if proc.returncode != 0:
            err = "\n".join(stderr_tail)[-1000:]
            if not err:
                err = f"(ffmpeg produced no stderr output, exit code {proc.returncode})"
            self.error.emit(f"ffmpeg failed (exit {proc.returncode}):\n{err}")
            return

        self.progress.emit(100, "Video export complete.")
        self.finished.emit(self.output_path)


# --- 2. Labels CSV export ----------------------------------------------------

# --- Co-registered EEG (both boxers on one shared axis) ----------------------

_EEG_CHANNELS = ["TP9", "AF7", "AF8", "TP10", "AUX"]


def build_coregistered_eeg(eeg_dfs, phase_markers, sr: int = 256):
    """Merge both boxers' EEG onto ONE shared `t_session_seconds` axis so the
    between-brain comparison is a direct row lookup instead of a manual
    cross-reference of two CSVs on different clocks.

    Inputs
      eeg_dfs       — [Boxer A's DataFrame, Boxer B's] (either may be None),
                      each carrying a
                      `t_session_seconds` column (from the v3 capture-time
                      clock) + the 5 Muse channels. Bands lacking that column
                      are skipped (v3 always writes it).
      phase_markers — list of (t_session_seconds, phase_id, label) on the SAME
                      axis as the EEG (raw session time, NOT the review-tab's
                      axis_shifted display values).
      sr            — grid rate (Hz).

    Output: a wide DataFrame — one uniform-grid row per time point spanning the
    union of the bands, columns:
      t_session_seconds, <chan>_01…, <chan>_02…, phase_id, phase_label, marker
    Each band's channels are linearly interpolated onto the grid within its own
    range and NaN outside it (honest "no sample here"). `phase_id`/`phase_label`
    are the active phase (forward-filled from markers); `marker` is non-empty
    only on the grid row at each phase boundary.

    NOTE (UNVERIFIED): each band's position on this shared axis comes from the
    Step-2 EEG capture-time stamping, whose clock source (muselsl wall clock +
    BLE latency) is NOT confirmed without a real Muse band. The RELATIVE
    between-brain skew here is therefore measured-but-hardware-unverified.
    Returns None if no band carries `t_session_seconds`.
    """
    import numpy as np
    import pandas as pd

    # Keep each band's POSITION in the input list: the first is always _01
    # (Boxer A), the second _02 (Boxer B), even if one of them is missing.
    numbered = [(i, df) for i, df in enumerate(eeg_dfs, start=1)
                if df is not None and len(df)
                and "t_session_seconds" in getattr(df, "columns", [])]
    bands = [df for _i, df in numbered]
    if not bands:
        return None

    t_min = min(float(df["t_session_seconds"].iloc[0]) for df in bands)
    t_max = max(float(df["t_session_seconds"].iloc[-1]) for df in bands)
    n = max(1, int(round((t_max - t_min) * sr)) + 1)
    grid = t_min + np.arange(n) / sr

    out = {"t_session_seconds": np.round(grid, 6)}
    for bi, df in numbered:
        ts = df["t_session_seconds"].to_numpy(dtype=float)
        for ch in _EEG_CHANNELS:
            if ch in df.columns:
                y = df[ch].to_numpy(dtype=float)
                # interp within [ts[0], ts[-1]]; NaN outside this band's range
                out[f"{ch}_{bi:02d}"] = np.interp(grid, ts, y,
                                                  left=np.nan, right=np.nan)

    phase_id    = np.array([""] * n, dtype=object)
    phase_label = np.array([""] * n, dtype=object)
    marker      = np.array([""] * n, dtype=object)
    import brain_battle.data.session_storage as session_storage
    for mt, pid, lab in sorted((tuple(m[:3]) for m in phase_markers or []), key=lambda m: m[0]):
        idx = int(np.searchsorted(grid, mt))     # first grid row >= marker time
        if 0 <= idx < n:
            # two markers on one row (ROUND_1_END + BREAK_1_START) → "A;B"
            marker[idx] = f"{marker[idx]};{lab}" if marker[idx] else lab
            if session_storage.is_phase_start(lab):
                phase_id[idx:]    = pid           # active from here onward
                phase_label[idx:] = lab
    out["phase_id"], out["phase_label"], out["marker"] = phase_id, phase_label, marker
    return pd.DataFrame(out)



# =====================================================================
#  v2 "Export for analysis" (merged Review & Label screen, DESIGN §6)
# =====================================================================
#
# One button → a small dialog → a zip with the analysis-ready bundle:
#   labels.csv            — every labeller's placed labels (labeller column)
#   events.csv            — phase markers + labels on one timeline
#   agreement.csv         — pairwise labeller agreement (2+ labellers)
#   eeg_coregistered.csv  — both boxers' EEG on one t_session axis
#   eeg/                  — raw per-band EEG CSVs
#   videos/               — camera MP4s + their .timestamps.csv sidecars
#   session_manifest.json, phase_markers.csv, history.jsonl, README.txt
#   combined_2x2.mp4      — optional (off by default; slow)

_LABELS_V2_COLUMNS = ["id", "labeller", "type", "label", "who", "kind", "t_start", "t_end",
                      "text", "color", "created_by", "created_at"]
_EVENTS_COLUMNS = ["t_start", "t_end", "source", "who", "label", "labeller"]
_AGREEMENT_COLUMNS = ["labeller_a", "labeller_b", "tolerance_seconds", "same_event",
                      "only_a", "only_b", "percent_agreement", "mean_time_difference_seconds"]


def _write_labels_csv_v2(labels, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_LABELS_V2_COLUMNS)
        w.writeheader()
        for lab in labels:
            row = {k: ("" if lab.get(k) is None else lab.get(k)) for k in _LABELS_V2_COLUMNS}
            row["type"] = lab.get("type") or "label"
            w.writerow(row)


def _write_events_csv(labels, phase_markers, path):
    import brain_battle.data.labels_store as labels_store
    rows = labels_store.events_timeline(labels, phase_markers)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=_EVENTS_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def _by_labeller(labels) -> dict:
    out: dict = {}
    for lab in labels or []:
        out.setdefault(str(lab.get("labeller") or lab.get("created_by") or "labels"), []).append(lab)
    return dict(sorted(out.items(), key=lambda kv: kv[0].lower()))


def _agreements(labels):
    import brain_battle.data.label_agreement as label_agreement
    return label_agreement.pairwise(_by_labeller(labels))


def _write_agreement_csv(agreements, path):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_AGREEMENT_COLUMNS)
        for ag in agreements:
            w.writerow([ag.name_a, ag.name_b, ag.tolerance, len(ag.matched), len(ag.only_a),
                        len(ag.only_b), ag.percent, f"{ag.mean_time_difference:.3f}"])


def default_export_name(session_root) -> str:
    """The zip's file name: the recording folder's name
    ('2026-11-17 14-05 K7Q2 vs M3XA.zip')."""
    return os.path.basename(os.path.abspath(session_root)) + ".zip"


class SessionExportWorker(QThread):
    """Builds the analysis bundle zip from a session folder. Everything is read
    straight from the recording on disk, so the zip is self-describing
    (manifest + history + sidecars included).

    ``labels`` is a flat list of labels, each carrying ``labeller``; ``None``
    means "every labeller's labels in the folder". ``include_videos=False``
    leaves the camera MP4s out (their frame-time CSVs are always included)."""
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(self, session_root, labels, phase_markers, zip_path,
                 merge_video=False, include_videos=True, parent=None):
        super().__init__(parent)
        self.root = session_root
        self.labels = labels
        self.phase_markers = phase_markers
        self.zip_path = zip_path
        self.merge_video = merge_video
        self.include_videos = bool(include_videos)

    def run(self):
        try:
            self._run()
        except OSError as exc:
            self.error.emit(f"The export could not be written ({exc.strerror or exc}). Check that "
                            f"the place you are saving to is connected and has free space.")
        except Exception as exc:
            self.error.emit(str(exc))

    def _run(self):
        import json
        import brain_battle.data.labels_store as labels_store
        import brain_battle.data.session_storage as session_storage
        root = self.root
        self.progress.emit(3, "Preparing export…")
        if self.labels is None:
            self.labels = labels_store.all_labels(root)
        if self.phase_markers is None:
            self.phase_markers = session_storage.read_phase_markers(root)
        manifest = session_storage._read_manifest(root) or {}
        with tempfile.TemporaryDirectory() as tmp:
            files: list[tuple[str, str]] = []

            lp = os.path.join(tmp, "labels.csv")
            _write_labels_csv_v2(self.labels, lp); files.append((lp, "labels.csv"))
            ep = os.path.join(tmp, "events.csv")
            _write_events_csv(self.labels, self.phase_markers, ep); files.append((ep, "events.csv"))
            agreements = _agreements(self.labels)
            if agreements:
                ap = os.path.join(tmp, "agreement.csv")
                _write_agreement_csv(agreements, ap); files.append((ap, "agreement.csv"))

            # everything on one clock, in the standard LSL file format —
            # rebuilt now so it carries the labels placed so far.
            self.progress.emit(8, "Building recording.xdf…")
            try:
                import brain_battle.data.session_xdf as session_xdf
                tmp_xdf = os.path.join(tmp, "recording.xdf")
                try:
                    xp = session_xdf.build_session_xdf(root, labels=self.labels)
                except OSError:     # recording folder not writable: build in temp
                    xp = session_xdf.build_session_xdf(root, labels=self.labels,
                                                       out_path=tmp_xdf)
                if xp:
                    files.append((xp, "recording.xdf"))
            except Exception as e:
                log.warning("recording.xdf build failed: %r", e)

            # raw EEG (Boxer A's file first) + the optional movement / pulse files
            self.progress.emit(15, "Adding brain recordings…")
            eeg_by_boxer = session_storage.eeg_files(root)
            eeg_paths = [eeg_by_boxer.get(who) for who in ("A", "B")]   # None = no file
            for p in filter(None, eeg_paths):
                files.append((p, f"eeg/{os.path.basename(p)}"))
            for p in sorted(session_storage.sensor_files(root).values()):
                files.append((p, f"sensors/{os.path.basename(p)}"))

            # co-registered EEG (with the buzzer test's corrections applied)
            self.progress.emit(25, "Putting both boxers' brain activity on one timeline…")
            try:
                import brain_battle.capture.timing as timing
                corr = timing.corrections(manifest)
                dfs = [session_storage.read_csv_tolerant(p).dropna() if p else None
                       for p in eeg_paths]
                for who, df in zip(("A", "B"), dfs):
                    shift = corr.get(timing.band_device(who), 0.0)
                    if df is not None and shift and "t_session_seconds" in df.columns:
                        df["t_session_seconds"] = df["t_session_seconds"] - shift
                coreg = build_coregistered_eeg(dfs, self.phase_markers)
                if coreg is not None:
                    cp = os.path.join(tmp, "eeg_coregistered.csv")
                    coreg.to_csv(cp, index=False)
                    files.append((cp, "eeg_coregistered.csv"))
            except Exception as e:
                log.warning("co-registered EEG build failed: %r", e)

            # videos + sidecars
            self.progress.emit(40, "Adding camera files…")
            video_paths = []
            cont = os.path.join(root, "continuous")
            for sub in ("Side Cams", "POV Cams"):
                d = os.path.join(cont, sub)
                if not os.path.isdir(d):
                    continue
                for fn in sorted(os.listdir(d)):
                    p = os.path.join(d, fn)
                    if fn.endswith(".mp4"):
                        video_paths.append(p)
                        if self.include_videos:
                            files.append((p, f"videos/{fn}"))
                    elif fn.endswith(".timestamps.csv"):
                        files.append((p, f"videos/{fn}"))

            # optional combined 2x2 video
            if self.merge_video and video_paths and _find_ffmpeg():
                self.progress.emit(45, "Making the combined video of all four cameras…")
                merged = os.path.join(tmp, "combined_2x2.mp4")
                starts = []
                for vp in video_paths:
                    side = os.path.splitext(vp)[0] + ".timestamps.csv"
                    first = 0.0
                    try:
                        with open(side, encoding="utf-8") as f:
                            r = csv.DictReader(f)
                            row = next(r, None)
                            if row:
                                first = float(row["t_session_seconds"])
                    except Exception:
                        pass
                    starts.append(first)
                base = min(starts) if starts else 0.0
                offsets = [s - base for s in starts]
                done = {"ok": False}
                vw = VideoExportWorker(video_paths=video_paths, offset_secs=offsets,
                                       output_path=merged, global_duration=0.0)
                vw.progress.connect(lambda pct, msg: self.progress.emit(45 + int(pct * 0.35), msg))
                vw.finished.connect(lambda _p: done.__setitem__("ok", True))
                vw.error.connect(lambda m: log.warning("combined video failed: %s", m))
                vw.start(); vw.wait()
                if done["ok"] and os.path.exists(merged):
                    files.append((merged, "combined_2x2.mp4"))

            # manifest, markers, history
            self.progress.emit(85, "Adding the recording details and history…")
            mp = os.path.join(root, "session_manifest.json")
            if os.path.isfile(mp):
                files.append((mp, "session_manifest.json"))
            # every other recording CSV: the system markers, the marked moments
            # (+ notes), the timing report and the sensor-contact log
            for fn in ("phase_markers.csv", "moments.csv", "timing_quality.csv",
                       "contact_quality.csv"):
                fp = os.path.join(root, fn)
                if os.path.isfile(fp):
                    files.append((fp, fn))
            hp = os.path.join(root, "history.jsonl")
            if os.path.isfile(hp):
                files.append((hp, "history.jsonl"))

            # README
            self.progress.emit(90, "Writing the README…")
            rp = os.path.join(tmp, "README.txt")
            with open(rp, "w", encoding="utf-8") as f:
                f.write(_export_readme(root, self.labels, manifest, agreements))
            files.append((rp, "README.txt"))

            # zip — written beside its final name, then swapped in, so a
            # half-written zip never sits under the real name
            self.progress.emit(95, "Writing the zip file…")
            part = self.zip_path + ".part"
            try:
                with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
                    for disk, arc in files:
                        zf.write(disk, arc)
                from brain_battle.data.safe_io import replace_with_retry
                replace_with_retry(part, self.zip_path)
            finally:
                if os.path.exists(part):
                    try:
                        os.unlink(part)
                    except OSError:
                        pass

        try:
            import brain_battle.data.session_history as session_history
            session_history.log_event(root, session_history.EVENT_EXPORT_CREATED,
                                      {"name": os.path.basename(self.zip_path),
                                       "videos": self.include_videos,
                                       "labellers": sorted(_by_labeller(self.labels))})
        except Exception as e:
            log.warning("could not log export_created: %r", e)

        self.progress.emit(100, "Export complete.")
        self.finished.emit(self.zip_path)


def _export_readme(root, labels, manifest=None, agreements=None):
    import brain_battle.data.fighters as fighters
    import brain_battle.capture.timing as timing
    timing_summary = timing.summary_text((manifest or {}).get("timing_calibration"))
    created = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(labels, int):              # older callers passed a count
        n_labels, per = labels, {}
    else:
        n_labels, per = len(labels or []), _by_labeller(labels)
    folder = os.path.basename(os.path.abspath(root))

    def fighter(w):
        i = fighters.info(manifest or {}, w)
        shown = fighters.display(manifest or {}, w)
        return f"{shown} ({i['colour'].lower()})" if i["colour"] else shown

    lines = [
        "Brain Battle — analysis export",
        f"Recording:  {folder}",
        f"Created:    {created}",
        f"Boxer A:    {fighter('A')}",
        f"Boxer B:    {fighter('B')}",
        f"Labels:     {n_labels}",
    ]
    for who, labs in per.items():
        lines.append(f"  {who}: {len(labs)}")
    lines += ["", "Fighters are stored only as their 4-character codes and colours."]
    if agreements:
        lines += ["", "Agreement between labellers (same label, same boxer, start times",
                  f"within {agreements[0].tolerance:g} s; details in agreement.csv):"]
        lines += [f"  {ag.sentence(a_is_you=False)}" for ag in agreements]
    lines += [
        "",
        "Contents:",
        "  recording.xdf         every signal, the round markers, the labels and",
        "                        every video frame's time on ONE clock, in the",
        "                        standard Lab Streaming Layer format. Opens in",
        "                        EEGLAB (load_xdf), MNE-Python and pyxdf. Each",
        "                        labeller's labels are their own marker stream,",
        "                        BrainBattle_Labels_<labeller>.",
        "  labels.csv            placed labels (id, labeller, label, who, kind,",
        "                        t_start, t_end, color, created_by, created_at).",
        "                        Times are seconds on the recording's shared clock.",
        "  events.csv            phase markers + labels on one timeline (t_start,",
        "                        t_end, source[phase|label], who, label, labeller).",
        "  agreement.csv         one row per pair of labellers (only when two or",
        "                        more people labelled this recording).",
        "  eeg_coregistered.csv  both boxers' EEG on one t_session_seconds grid.",
        "  eeg/                  raw per-band EEG recordings.",
        "  sensors/              headband movement / pulse recordings (if any).",
        "  videos/               camera MP4s + their .timestamps.csv sidecars",
        "                        (one row per frame: frame time on the shared clock).",
        "  phase_markers.csv     the automatic system markers (recording start,",
        "                        baseline, each round and break, recording end).",
        "  moments.csv           moments marked while recording, with their notes.",
        "  timing_quality.csv    once a second per device: rate, jitter, dropped",
        "                        frames / packets, and the buzzer-test correction.",
        "  contact_quality.csv   once a second per headband: sensor contact.",
        "  session_manifest.json (codes, plan, clock, timing_calibration), history.jsonl",
        "  combined_2x2.mp4      the four cameras in one frame (only if requested).",
        "",
        "Timing:",
        f"  {timing_summary}",
        "  recording.xdf and eeg_coregistered.csv have the buzzer-test corrections",
        "  applied (timing_correction_ms in each XDF stream); the raw CSVs and",
        "  .timestamps.csv files are exactly as recorded.",
        "  NOT YET CHECKED ON REAL HARDWARE: the corrections have only been",
        "  verified with simulated devices.",
    ]
    return "\n".join(lines)


class ExportOptionsDialog(QDialog):
    """Export for analysis (DESIGN "Export"): what is always included, two
    video choices (the 4 camera videos; also one combined video), where to
    save. Runs the export with a progress bar and ends with "Show in Finder /
    Explorer"."""

    ALWAYS = (
        "recording.xdf: everything on one clock, for EEGLAB, MNE or Python",
        "The same data as separate CSV files: both brain recordings, head movement, "
        "automatic markers, moments and notes, everyone\u2019s labels, and every "
        "camera\u2019s frame times",
        "A timing report (timing_quality.csv): frame rates, delays, jitter, dropped "
        "frames and packets, and the buzzer-test corrections for every device",
        "How much the labellers agree, the session details and the history",
    )

    def __init__(self, session_root, labels=None, phase_markers=None, parent=None):
        super().__init__(parent)
        import brain_battle.ui.design_tokens as dt
        import brain_battle.data.fighters as fighters
        import brain_battle.data.session_storage as session_storage
        from brain_battle.support.platform_utils import show_in_file_manager_label
        from PyQt6.QtWidgets import QCheckBox
        self._root = session_root
        self._labels = labels
        self._phases = phase_markers
        self._worker = None
        self.setWindowTitle("Export for analysis")
        self.setModal(True)
        self.setMinimumWidth(560)
        self.setStyleSheet(f"QDialog {{ background: {dt.surface()}; }}")

        manifest = session_storage._read_manifest(session_root) or {}
        self._dir = os.path.dirname(os.path.abspath(session_root))
        self._dest = os.path.join(self._dir, default_export_name(session_root))

        v = QVBoxLayout(self); v.setContentsMargins(28, 24, 28, 22); v.setSpacing(14)
        title = QLabel("Export for analysis")
        title.setStyleSheet(dt.css_text(dt.SIZE_XL, 700))
        v.addWidget(title)
        self._name_lbl = QLabel(os.path.basename(self._dest))
        self._name_lbl.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary(), mono=True))
        v.addWidget(self._name_lbl)

        head = QLabel("Always included")
        head.setStyleSheet(dt.css_text(dt.SIZE_SM, 700))
        v.addWidget(head)
        for text in self.ALWAYS:
            row = QLabel(f"<span style='color:{dt.success_color()};font-weight:700'>\u2713</span> "
                         f"{text}")
            row.setWordWrap(True)
            row.setStyleSheet(dt.css_text(dt.SIZE_SM, 400))
            v.addWidget(row)

        vh = QLabel("Videos")
        vh.setStyleSheet(dt.css_text(dt.SIZE_SM, 700))
        v.addWidget(vh)
        self._videos_cb = QCheckBox("The 4 camera videos")
        self._videos_cb.setChecked(True)
        self._videos_cb.setStyleSheet(dt.qss_checkbox())
        v.addWidget(self._videos_cb)
        self._merge_cb = QCheckBox("Also one combined video with all 4 cameras")
        self._merge_cb.setChecked(False)
        self._merge_cb.setStyleSheet(dt.qss_checkbox())
        v.addWidget(self._merge_cb)

        a, b = fighters.display(manifest, "A"), fighters.display(manifest, "B")
        legacy = fighters.info(manifest, "A")["name"] or fighters.info(manifest, "B")["name"]
        note = QLabel(f"Fighters appear only as their codes ({a}, {b}) and colours. No names are "
                      f"stored, so there is nothing to hide." if not legacy else
                      "This recording was made before fighter codes; it keeps the names it "
                      "was recorded with.")
        note.setWordWrap(True)
        note.setObjectName("exportCodesNote")
        note.setStyleSheet(f"#exportCodesNote {{ background: {dt.success_bg()}; "
                           f"color: {dt.success_color()}; border-radius: 12px; "
                           f"padding: 10px 12px; }}" + "")
        v.addWidget(note)

        dest_row = QHBoxLayout(); dest_row.setSpacing(10)
        save_lbl = QLabel("Save to")
        save_lbl.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.text_secondary()))
        self._dest_lbl = QLabel(self._dest)
        self._dest_lbl.setWordWrap(True)
        self._dest_lbl.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        change = QPushButton("Change\u2026"); change.setMinimumHeight(36)
        change.setStyleSheet(dt.qss_secondary_button(compact=True)); change.clicked.connect(self._pick_dest)
        dest_row.addWidget(save_lbl); dest_row.addWidget(self._dest_lbl, 1); dest_row.addWidget(change)
        v.addLayout(dest_row)

        self._status = QLabel("")
        self._status.setWordWrap(True)
        self._status.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.text_tertiary()))
        v.addWidget(self._status)
        self._bar = QProgressBar(); self._bar.setRange(0, 100); self._bar.setValue(0)
        self._bar.setFixedHeight(6); self._bar.setTextVisible(False); self._bar.setVisible(False)
        v.addWidget(self._bar)

        btns = QHBoxLayout(); btns.setSpacing(10)
        self._reveal = QPushButton(show_in_file_manager_label()); self._reveal.setMinimumHeight(44)
        self._reveal.setStyleSheet(dt.qss_secondary_button()); self._reveal.setVisible(False)
        self._reveal.clicked.connect(self._reveal_export)
        btns.addWidget(self._reveal); btns.addStretch()
        self._cancel = QPushButton("Cancel"); self._cancel.setMinimumHeight(44)
        self._cancel.setStyleSheet(dt.qss_secondary_button()); self._cancel.clicked.connect(self.reject)
        self._go = QPushButton("Export"); self._go.setMinimumHeight(44)
        self._go.setStyleSheet(dt.qss_primary_button()); self._go.clicked.connect(self._start)
        btns.addWidget(self._cancel); btns.addWidget(self._go)
        v.addLayout(btns)

    def _pick_dest(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save export", self._dest, "Zip (*.zip)")
        if path:
            self._dest = path if path.endswith(".zip") else path + ".zip"
            self._dest_lbl.setText(self._dest)
            self._name_lbl.setText(os.path.basename(self._dest))

    def _start(self):
        self._go.setEnabled(False)
        self._merge_cb.setEnabled(False); self._videos_cb.setEnabled(False)
        self._bar.setVisible(True)
        self._worker = SessionExportWorker(self._root, self._labels, self._phases,
                                           self._dest, merge_video=self._merge_cb.isChecked(),
                                           include_videos=self._videos_cb.isChecked())
        self._worker.progress.connect(lambda pct, msg: (self._bar.setValue(pct), self._status.setText(msg)))
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.start()

    def _on_done(self, path):
        import brain_battle.ui.design_tokens as dt
        self._status.setText(f"✓ Exported to {path}")
        self._status.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.success_color()))
        self._bar.setVisible(False)
        self._go.setVisible(False); self._reveal.setVisible(True)
        self._cancel.setText("Done")

    def _on_error(self, msg):
        import brain_battle.ui.design_tokens as dt
        self._status.setText(f"The export did not finish. {msg[:400]}")
        self._status.setStyleSheet(dt.css_text(dt.SIZE_SM, 600, dt.error_color()))
        self._bar.setVisible(False)
        self._go.setEnabled(True)
        self._merge_cb.setEnabled(True); self._videos_cb.setEnabled(True)

    def _reveal_export(self):
        from brain_battle.support.platform_utils import reveal_in_file_manager
        reveal_in_file_manager(self._dest)
