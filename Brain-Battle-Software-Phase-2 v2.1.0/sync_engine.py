"""
sync_engine.py  —  Brain Battle synchronisation engine

Responsibilities
────────────────
1. Video-to-video sync
   For each of the 4 video files, extract mono audio and compute the
   time offset relative to the reference video (the longest one) using
   FFT cross-correlation.

2. EEG-to-video sync
   Each Muse-S CSV carries Unix timestamps.  The paired video file has a
   wall-clock creation_time in its metadata (written by the camera at
   record-start).  The offset is simply:

       eeg_offset = video_creation_time_unix - eeg_first_timestamps_unix

3. Result object
   SyncResult holds every offset plus the aligned "session start" so
   callers can convert any source-local timestamps to a global timeline
   position:

       global_t = source_local_t - source_offset

Changes from original
─────────────────────
- _xcorr_offset() switches to mode="full" with +/-30s search window so
  cameras that start >N/2 samples apart are found correctly
- Add half-wave envelope smoothing before xcorr for better peak SNR
- Downsample to 4000 Hz before correlating (~11x faster, 0.25 ms resolution)
- Remove dead code (_set_high_z_position, _clip_nsview_layer were never here
  but xcorr mode was the same issue as audio_analysis.py)
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from typing import Optional

import subprocess
import tempfile
import os
import sys

import ffmpeg
import numpy as np
import pandas as pd
from scipy import signal

from PyQt6.QtCore import QThread, pyqtSignal


# --- Config ------------------------------------------------------------------

_XCORR_SR     = 4000    # downsample target for cross-correlation
_MAX_OFFSET_S = 30.0    # maximum expected camera start-time offset (seconds)


# --- Data classes ------------------------------------------------------------

@dataclass
class VideoInfo:
    path: str
    duration: float          # seconds
    sample_rate: int         # audio sample rate (after resampling)
    audio: np.ndarray        # mono float32
    creation_time: Optional[float] = None   # unix epoch, from metadata


@dataclass
class EEGInfo:
    path: str
    df: pd.DataFrame
    first_timestamps: float   # unix epoch (first row of 'timestamps' column)
    channels: list[str]       # e.g. ['TP9','AF7','TP10','AF8']


@dataclass
class SourceOffset:
    """Offset for a single source relative to the global timeline start."""
    source_id: str
    path: str
    offset_sec: float        # seek this many seconds into the source to reach t=0
    is_reference: bool = False
    method: str = ""         # 'reference' | 'xcorr' | 'creation_time'
    confidence: float = 1.0  # 0-1, based on xcorr peak ratio


@dataclass
class SyncResult:
    """
    Full synchronisation result for one session.

    global_duration  — length of the shortest aligned source (safe play window)
    video_offsets    — list[SourceOffset], one per video
    eeg_offsets      — list[SourceOffset], one per EEG CSV
    warnings         — non-fatal issues encountered
    """
    global_duration: float
    video_offsets: list[SourceOffset] = field(default_factory=list)
    eeg_offsets:   list[SourceOffset] = field(default_factory=list)
    warnings:      list[str]          = field(default_factory=list)

    def all_offsets(self) -> list[SourceOffset]:
        return self.video_offsets + self.eeg_offsets

    def summary(self) -> str:
        lines = [f"Global duration: {self.global_duration:.3f}s", ""]
        for o in self.all_offsets():
            tag = "[REF]" if o.is_reference else "     "
            conf = f"  conf={o.confidence:.2f}" if o.method == "xcorr" else ""
            lines.append(
                f"{tag} {o.source_id:<18} offset={o.offset_sec:+.4f}s  "
                f"({o.method}){conf}"
            )
        if self.warnings:
            lines.append("")
            lines.extend(f"  WARN: {w}" for w in self.warnings)
        return "\n".join(lines)


# --- Core audio helpers ------------------------------------------------------

def _load_video(path: str) -> VideoInfo:
    """
    Load a video using ffmpeg-python only (no moviepy dependency).

    Audio is extracted as mono float32 resampled to _XCORR_SR Hz with a
    bandpass pre-filter (80-1800 Hz) for better xcorr peak SNR.
    Metadata (duration, creation_time) is read via ffmpeg.probe().
    """
    # Probe metadata
    probe = ffmpeg.probe(path)

    # Duration
    duration: float = float(probe["format"].get("duration", 0.0))
    if duration == 0.0:
        for s in probe.get("streams", []):
            if s.get("codec_type") == "video" and "duration" in s:
                duration = float(s["duration"])
                break

    # creation_time
    creation_unix: Optional[float] = None
    candidates = [s.get("tags", {}) for s in probe.get("streams", [])]
    candidates.append(probe.get("format", {}).get("tags", {}))
    for tags in candidates:
        ct = tags.get("creation_time")
        if ct:
            fmt = "%Y-%m-%dT%H:%M:%S.%f%z" if "." in ct else "%Y-%m-%dT%H:%M:%S%z"
            try:
                creation_unix = dt.datetime.strptime(ct, fmt).timestamp()
                break
            except ValueError:
                continue

    # Audio extraction via ffmpeg pipe with bandpass + resample
    has_audio = any(
        s.get("codec_type") == "audio" for s in probe.get("streams", [])
    )

    if has_audio:
        try:
            cmd = [
                "ffmpeg", "-i", path,
                "-vn", "-ac", "1",
                "-ar", str(_XCORR_SR),
                "-af", "highpass=f=80,lowpass=f=1800",
                "-f", "f32le", "-"
            ]
            result = subprocess.run(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
            mono = np.frombuffer(result.stdout, dtype=np.float32).copy()
        except Exception:
            mono = np.zeros(int(duration * _XCORR_SR), dtype=np.float32)
    else:
        mono = np.zeros(int(duration * _XCORR_SR), dtype=np.float32)

    return VideoInfo(
        path=path,
        duration=duration,
        sample_rate=_XCORR_SR,
        audio=mono,
        creation_time=creation_unix,
    )


def _load_eeg(path: str) -> EEGInfo:
    """Load a muselsl CSV.  Expected columns: timestamps, TP9, AF7, TP10, AF8."""
    df = pd.read_csv(path)

    if "timestamps" not in df.columns:
        raise ValueError(f"EEG CSV '{path}' has no 'timestamps' column.")

    # Handle firmware variants that prefix channels (e.g. EEG_TP9 -> TP9)
    rename = {}
    for col in df.columns:
        for ch in ["TP9", "AF7", "TP10", "AF8"]:
            if col.upper().endswith(ch) and col != ch:
                rename[col] = ch
    if rename:
        df = df.rename(columns=rename)

    eeg_channels = [c for c in ["TP9", "AF7", "TP10", "AF8"] if c in df.columns]
    if not eeg_channels:
        raise ValueError(f"EEG CSV '{path}' contains none of the expected EEG channels.")

    return EEGInfo(
        path=path,
        df=df,
        first_timestamps=float(df["timestamps"].iloc[0]),
        channels=eeg_channels,
    )


def _envelope(x: np.ndarray, sr: int = _XCORR_SR) -> np.ndarray:
    """Half-wave rectify + 50 ms smooth — improves peak SNR on noisy audio."""
    window = max(1, int(sr * 0.05))
    return np.convolve(np.maximum(x, 0.0),
                       np.ones(window, dtype=np.float32) / window,
                       mode="same")


def _xcorr_offset(
    ref_audio: np.ndarray,
    other_audio: np.ndarray,
    ref_sr: int,
    other_sr: int,
) -> tuple[float, float]:
    """
    Compute the time offset (seconds) of other_audio relative to ref_audio
    using FFT cross-correlation.

    Changes from original:
    - Uses mode="full" — searches the full lag range, not just ±N/2
    - Restricts to ±_MAX_OFFSET_S search window to avoid wrap-around peaks
    - Applies envelope smoothing before correlation

    Returns (offset_seconds, confidence).
    Positive offset means other_audio starts LATER than ref_audio.
    """
    # Resample other to ref sr if needed
    if ref_sr != other_sr:
        from scipy.signal import resample_poly
        from math import gcd
        g = gcd(ref_sr, other_sr)
        other_audio = resample_poly(other_audio, ref_sr // g, other_sr // g)

    def _norm(x):
        s = x.std()
        return x / s if s > 1e-9 else x

    a = _envelope(_norm(ref_audio.astype(np.float64)))
    b = _envelope(_norm(other_audio.astype(np.float64)))

    corr = signal.correlate(a, b, mode="full", method="fft")
    lags = signal.correlation_lags(len(a), len(b), mode="full")

    # Restrict search to +/-_MAX_OFFSET_S
    max_lag = int(_MAX_OFFSET_S * ref_sr)
    mask = np.abs(lags) <= max_lag
    if not np.any(mask):
        return 0.0, 0.0

    search_corr = corr[mask]
    search_lags = lags[mask]

    peak_idx = int(np.argmax(np.abs(search_corr)))
    offset_sec = float(search_lags[peak_idx]) / ref_sr

    # Confidence: peak-to-mean ratio (clipped to [0, 1])
    peak_val = float(np.abs(search_corr[peak_idx]))
    mean_val = float(np.abs(search_corr).mean())
    confidence = float(np.clip((peak_val / mean_val - 1.0) / 50.0, 0.0, 1.0))

    return offset_sec, confidence


# --- Main sync function ------------------------------------------------------

def compute_sync(
    video_paths: list[str],
    eeg_paths: list[str],
    eeg_video_pairs: Optional[list[tuple[int, int]]] = None,
    progress_cb=None,
) -> SyncResult:
    """
    Compute full synchronisation for a session.

    Parameters
    ──────────
    video_paths      — list of 1-4 video file paths
    eeg_paths        — list of 0-2 EEG CSV file paths
    eeg_video_pairs  — optional list of (eeg_index, video_index) pairs
    progress_cb      — optional callable(int, str) receiving (percent, message)
    """
    warnings: list[str] = []

    def _progress(pct: int, msg: str):
        if progress_cb:
            progress_cb(pct, msg)

    # 1. Load videos
    _progress(0, "Loading video files...")
    video_infos: list[VideoInfo] = []
    for i, path in enumerate(video_paths):
        _progress(int(5 + i * 15), f"Loading video {i+1}/{len(video_paths)}...")
        video_infos.append(_load_video(path))

    # 2. Choose reference video (longest)
    ref_idx = int(np.argmax([v.duration for v in video_infos]))
    ref = video_infos[ref_idx]
    _progress(20, f"Reference video: #{ref_idx+1} ({ref.duration:.1f}s)")

    # 3. Compute video-to-video offsets via xcorr
    video_offsets: list[SourceOffset] = []
    for i, vi in enumerate(video_infos):
        source_id = f"VIDEO_{i+1:02d}"
        if i == ref_idx:
            video_offsets.append(SourceOffset(
                source_id=source_id,
                path=vi.path,
                offset_sec=0.0,
                is_reference=True,
                method="reference",
                confidence=1.0,
            ))
            continue

        pct = 20 + int((i / len(video_infos)) * 50)
        _progress(pct, f"Cross-correlating video {i+1} vs reference...")

        off, conf = _xcorr_offset(ref.audio, vi.audio, ref.sample_rate, vi.sample_rate)

        if conf < 0.15:
            warnings.append(
                f"VIDEO_{i+1:02d}: low xcorr confidence ({conf:.2f}). "
                "Consider checking audio levels."
            )

        video_offsets.append(SourceOffset(
            source_id=source_id,
            path=vi.path,
            offset_sec=off,
            is_reference=False,
            method="xcorr",
            confidence=conf,
        ))

    # 4. Load EEG files
    _progress(70, "Loading EEG files...")
    eeg_infos: list[EEGInfo] = []
    for i, path in enumerate(eeg_paths):
        _progress(70 + i * 5, f"Loading EEG {i+1}/{len(eeg_paths)}...")
        eeg_infos.append(_load_eeg(path))

    # 5. Compute EEG offsets via creation_time delta
    _progress(80, "Aligning EEG timestamps to video...")

    if eeg_video_pairs is None:
        eeg_video_pairs = [(i, i) for i in range(len(eeg_infos))]

    eeg_offsets: list[SourceOffset] = []
    for eeg_i, (eeg_idx, vid_idx) in enumerate(eeg_video_pairs):
        eeg = eeg_infos[eeg_idx]
        vi  = video_infos[vid_idx]
        source_id = f"EEG_{eeg_idx+1:02d}"

        if vi.creation_time is None:
            warnings.append(
                f"EEG_{eeg_idx+1:02d}: paired video has no creation_time metadata. "
                "EEG offset set to 0 - manual alignment may be needed."
            )
            eeg_offsets.append(SourceOffset(
                source_id=source_id,
                path=eeg.path,
                offset_sec=0.0,
                method="fallback",
                confidence=0.0,
            ))
            continue

        delta = vi.creation_time - eeg.first_timestamps
        vid_offset = video_offsets[vid_idx].offset_sec
        eeg_global_offset = delta - vid_offset

        eeg_offsets.append(SourceOffset(
            source_id=source_id,
            path=eeg.path,
            offset_sec=eeg_global_offset,
            method="creation_time",
            confidence=0.95,
        ))

    # 6. Compute global duration
    _progress(90, "Computing global duration...")
    durations: list[float] = []
    for vi, off in zip(video_infos, video_offsets):
        durations.append(vi.duration - abs(off.offset_sec))
    global_duration = min(durations) if durations else 0.0

    _progress(100, "Sync complete.")

    return SyncResult(
        global_duration=global_duration,
        video_offsets=video_offsets,
        eeg_offsets=eeg_offsets,
        warnings=warnings,
    )


def get_eeg_window(
    eeg_info: EEGInfo,
    eeg_offset: SourceOffset,
    global_t: float,
    window_sec: float = 10.0,
) -> pd.DataFrame:
    """Return EEG rows visible at a given global timeline position."""
    df = eeg_info.df.copy()
    df["global_t"] = (df["timestamps"] - eeg_info.first_timestamps) - eeg_offset.offset_sec
    mask = (df["global_t"] >= global_t) & (df["global_t"] < global_t + window_sec)
    return df[mask].reset_index(drop=True)


# --- QThread worker ----------------------------------------------------------

class SyncWorker(QThread):
    """
    Runs compute_sync() off the main thread.

    Signals
    ───────
    progress(int, str)     — percent complete + status message
    finished(SyncResult)   — emitted when sync succeeds
    error(str)             — emitted on exception
    """
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(object)   # SyncResult
    error    = pyqtSignal(str)

    def __init__(
        self,
        video_paths: list[str],
        eeg_paths: list[str],
        eeg_video_pairs: Optional[list[tuple[int, int]]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.video_paths    = video_paths
        self.eeg_paths      = eeg_paths
        self.eeg_video_pairs = eeg_video_pairs

    def run(self):
        try:
            result = compute_sync(
                video_paths=self.video_paths,
                eeg_paths=self.eeg_paths,
                eeg_video_pairs=self.eeg_video_pairs,
                progress_cb=lambda pct, msg: self.progress.emit(pct, msg),
            )
            self.finished.emit(result)
        except Exception as exc:
            self.error.emit(str(exc))
