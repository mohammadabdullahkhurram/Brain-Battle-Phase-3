"""
export_module.py  —  Brain Battle export pipeline

Two exports
───────────

1. VideoExportWorker  (QThread)
   Merges up to 4 synced video files into a single 2x2 quad-grid MP4
   using ffmpeg filter_complex. Each video is trimmed to its sync offset
   and scaled to a common resolution before compositing.

2. LabelsExportWorker  (QThread)
   Writes all placed labels to a CSV with columns:
       timestamp_sec, timestamp_str, label, color

3. FullSessionExport
   Combines both into a single zip:
   - merged_video.mp4
   - labels.csv
   - sync_offsets.csv
   - eeg/eeg_01.csv, eeg/eeg_02.csv
   - README.txt

Changes from original
─────────────────────
- _detect_hw_encoder() probes for GPU encoders and selects the best one:
    Windows: h264_nvenc (NVIDIA) > h264_amf (AMD) > h264_qsv (Intel)
    macOS:   h264_videotoolbox
    Fallback: libx264 fast crf=23
- VideoExportWorker uses hardware encoder when available (3-8x faster)
- labels_config atomic write (temp+rename) moved here as a utility used
  by LabelsExporter — labels_config.py also updated separately
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
    QInputDialog, QMessageBox, QWidget,
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
            self.error.emit(
                "ffmpeg not found. Install with: brew install ffmpeg (macOS) "
                "or https://ffmpeg.org/download.html (Windows)"
            )
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

class LabelsExporter:
    """
    Synchronous (non-threaded) labels CSV writer.
    Called from the main thread — fast enough not to need a worker.
    """

    @staticmethod
    def export(labels: list[dict], output_path: str):
        """
        labels: list of dicts with keys: timestamp_sec, timestamp_str, label, color
        """
        with open(output_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=["timestamp_sec", "timestamp_str", "label", "color"],
            )
            writer.writeheader()
            writer.writerows(labels)


# --- 3. Full session zip export worker ---------------------------------------

class FullSessionExportWorker(QThread):
    """
    Packages everything into a named zip:
      merged_video.mp4   — quad-grid merged video (optional, skipped if no ffmpeg)
      labels.csv         — all placed labels with timestamps
      sync_offsets.csv   — source offsets from sync engine
      eeg/eeg_01.csv     — EEG recording files
      eeg/eeg_02.csv
      videos/            — original video files (copied)
      README.txt
    """

    progress = pyqtSignal(int, str)
    finished = pyqtSignal(str)
    error    = pyqtSignal(str)

    def __init__(
        self,
        zip_path:        str,
        zip_name:        str,
        video_paths:     list[str],
        offset_secs:     list[float],
        eeg_paths:       list[str],
        labels:          list[dict],
        sync_offsets_rows: list[dict],
        global_duration: float,
        merge_video:     bool = True,
        parent=None,
    ):
        super().__init__(parent)
        self.zip_path           = zip_path
        self.zip_name           = zip_name
        self.video_paths        = video_paths
        self.offset_secs        = offset_secs
        self.eeg_paths          = eeg_paths
        self.labels             = labels
        self.sync_offsets_rows  = sync_offsets_rows
        self.global_duration    = global_duration
        self.merge_video        = merge_video

    def run(self):
        try:
            self._run()
        except Exception as exc:
            self.error.emit(str(exc))

    def _run(self):
        self.progress.emit(2, "Preparing export...")

        with tempfile.TemporaryDirectory() as tmp:
            files_to_zip: list[tuple[str, str]] = []

            # Merged video
            merged_path = None
            if self.merge_video and self.video_paths and _find_ffmpeg():
                merged_path = os.path.join(tmp, "merged_video.mp4")
                self.progress.emit(5, f"Merging video ({_HW_ENCODER}) — this may take a while...")

                done = {"ok": False, "err": ""}

                worker = VideoExportWorker(
                    video_paths=self.video_paths,
                    offset_secs=self.offset_secs,
                    output_path=merged_path,
                    global_duration=self.global_duration,
                )

                def on_prog(pct, msg):
                    self.progress.emit(5 + int(pct * 0.80), msg)

                def on_done(path):
                    done["ok"] = True

                def on_err(msg):
                    done["err"] = msg

                worker.progress.connect(on_prog)
                worker.finished.connect(on_done)
                worker.error.connect(on_err)
                worker.start()
                worker.wait()

                if done["ok"] and os.path.exists(merged_path):
                    files_to_zip.append((merged_path, "merged_video.mp4"))
                else:
                    self.progress.emit(85, f"Video merge skipped: {done['err'][:60]}")
            else:
                self.progress.emit(85, "Skipping video merge...")

            # Labels CSV
            self.progress.emit(86, "Writing labels CSV...")
            labels_path = os.path.join(tmp, "labels.csv")
            LabelsExporter.export(self.labels, labels_path)
            files_to_zip.append((labels_path, "labels.csv"))

            # Sync offsets CSV
            self.progress.emit(88, "Writing sync offsets...")
            offsets_path = os.path.join(tmp, "sync_offsets.csv")
            if self.sync_offsets_rows:
                with open(offsets_path, "w", newline="") as f:
                    writer = csv.DictWriter(
                        f,
                        fieldnames=["source", "offset_sec", "method",
                                    "confidence", "is_reference"],
                    )
                    writer.writeheader()
                    writer.writerows(self.sync_offsets_rows)
                files_to_zip.append((offsets_path, "sync_offsets.csv"))

            # EEG files
            self.progress.emit(90, "Adding EEG files...")
            for i, eeg_path in enumerate(self.eeg_paths):
                if eeg_path and os.path.exists(eeg_path):
                    files_to_zip.append((eeg_path, f"eeg/eeg_{i+1:02d}.csv"))

            # Original videos
            self.progress.emit(92, "Adding original videos...")
            for i, vpath in enumerate(self.video_paths):
                if vpath and os.path.exists(vpath):
                    ext = os.path.splitext(vpath)[1]
                    files_to_zip.append((vpath, f"videos/video_{i+1:02d}{ext}"))

            # README
            self.progress.emit(95, "Writing README...")
            created = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            readme_lines = [
                "Brain Battle — Session Export",
                f"Name:            {self.zip_name}",
                f"Created:         {created}",
                f"Global duration: {self.global_duration:.3f}s",
                f"Videos:          {len([v for v in self.video_paths if v])}",
                f"EEG files:       {len([e for e in self.eeg_paths if e])}",
                f"Labels placed:   {len(self.labels)}",
                f"Video encoder:   {_HW_ENCODER}",
                "",
                "Contents:",
                "  merged_video.mp4   — quad-grid synced video",
                "  labels.csv         — event labels with timestamps",
                "  sync_offsets.csv   — per-source sync offsets",
                "  eeg/               — raw EEG CSV recordings",
                "  videos/            — original camera files",
            ]
            readme_path = os.path.join(tmp, "README.txt")
            with open(readme_path, "w") as f:
                f.write("\n".join(readme_lines))
            files_to_zip.append((readme_path, "README.txt"))

            # Write zip
            self.progress.emit(97, "Writing zip file...")
            with zipfile.ZipFile(self.zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
                for disk_path, arcname in files_to_zip:
                    zf.write(disk_path, arcname)

        self.progress.emit(100, "Export complete.")
        self.finished.emit(self.zip_path)


# --- 4. Export progress dialog -----------------------------------------------

class ExportProgressDialog(QDialog):
    """
    Modal dialog that shows export progress.
    Runs FullSessionExportWorker and displays progress bar + status.
    """

    def __init__(self, worker: FullSessionExportWorker, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Exporting...")
        self.setMinimumWidth(420)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #0E0E12; }
            QLabel  { color: #ECEDEE; font-family: 'Inter'; font-size: 12px; }
            QProgressBar {
                background: #0E0E12; border: 1px solid #2A2A30; height: 6px;
                text-align: center; color: transparent;
            }
            QProgressBar::chunk { background: #637DFF; }
            QPushButton {
                background: #1C1C21; border: 1px solid #2A2A30; color: #ECEDEE;
                font-family: 'Inter'; font-size: 11px; padding: 7px 20px;
            }
        """)

        self._worker = worker
        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        self._status = QLabel("Preparing...")
        layout.addWidget(self._status)

        self._bar = QProgressBar()
        self._bar.setRange(0, 100)
        self._bar.setValue(0)
        layout.addWidget(self._bar)

        self._close_btn = QPushButton("CLOSE")
        self._close_btn.setEnabled(False)
        self._close_btn.clicked.connect(self.accept)
        layout.addWidget(self._close_btn)

        worker.progress.connect(self._on_progress)
        worker.finished.connect(self._on_finished)
        worker.error.connect(self._on_error)
        worker.start()

    def _on_progress(self, pct: int, msg: str):
        self._bar.setValue(pct)
        self._status.setText(msg)

    def _on_finished(self, path: str):
        self._bar.setValue(100)
        self._status.setText(f"Done  v\n{path}")
        self._status.setStyleSheet(
            "color: #22c55e; font-family: 'Inter'; font-size: 12px;"
        )
        self._close_btn.setEnabled(True)

    def _on_error(self, msg: str):
        # 800 chars, not 120 — the message now carries the tail of ffmpeg's
        # stderr, and truncating it would hide the actual failure reason.
        self._status.setWordWrap(True)
        self._status.setText(f"Error: {msg[:800]}")
        self._status.setStyleSheet(
            "color: #ef4444; font-family: 'Inter'; font-size: 12px;"
        )
        self._close_btn.setEnabled(True)
