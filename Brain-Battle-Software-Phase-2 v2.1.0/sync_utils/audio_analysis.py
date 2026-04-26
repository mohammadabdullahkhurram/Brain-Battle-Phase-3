"""
sync_utils/audio_analysis.py  —  Brain Battle audio cross-correlation helpers

Changes from original
─────────────────────
- Downsample to XCORR_SR (4000 Hz) for xcorr — ~11x faster FFT, 0.25 ms resolution
- Switch from mode="same" to mode="full" so cameras that start >N/2 samples
  apart are still found correctly
- Add +/-MAX_OFFSET_S search window to avoid wrap-around false peaks
- Add bandpass pre-filter (80-1800 Hz) and half-wave envelope smoothing to
  improve peak SNR when audio has continuous crowd noise
- compute_delays() / find_all_delays() kept as public aliases used by
  MainEditorSyncWorker in brain_battle_main.py
"""

from __future__ import annotations

from typing import List

import numpy as np
from scipy import signal
import os
import subprocess
import ffmpeg as _ffmpeg


# --- Config ------------------------------------------------------------------

EXTRACT_SR   = 44100    # sample rate for raw extraction (kept for reference)
XCORR_SR     = 4000     # downsample target for cross-correlation
MAX_OFFSET_S = 30.0     # maximum expected camera start-time offset (seconds)


# --- Helpers -----------------------------------------------------------------

def get_file_name(path: str) -> str:
    file_name = os.path.basename(path)
    file = os.path.splitext(file_name)
    return file[0]


def _probe_duration(video_path: str) -> float:
    """Return video duration in seconds via ffprobe."""
    try:
        probe = _ffmpeg.probe(video_path)
        return float(probe["format"].get("duration", 0.0))
    except Exception:
        return 0.0


def find_longest_vid(video_paths: List[str]) -> int:
    return_val = 0
    max_duration = -1.0
    for i, path in enumerate(video_paths):
        d = _probe_duration(path)
        if d > max_duration:
            max_duration = d
            return_val = i
    return return_val


# --- Audio extraction --------------------------------------------------------

def process_audio(video_path: str, target_sr: int = XCORR_SR):
    """
    Extract mono audio via ffmpeg pipe, resampled to target_sr with a
    bandpass pre-filter (80-1800 Hz).

    The bandpass removes 50/60 Hz hum and high-frequency camera noise
    while keeping punch transients and corner bells that anchor xcorr.

    Returns (array: float32, sample_rate: int).
    """
    cmd = [
        "ffmpeg", "-i", video_path,
        "-vn",
        "-ac", "1",
        "-ar", str(target_sr),
        "-af", "highpass=f=80,lowpass=f=1800",
        "-f", "f32le",
        "-"
    ]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    audio = np.frombuffer(result.stdout, dtype=np.float32).copy()
    print(f"Audio extracted: {get_file_name(video_path)} "
          f"(sr={target_sr}, samples={len(audio)})")
    return audio, target_sr


# --- Envelope helper ---------------------------------------------------------

def _envelope(x: np.ndarray, sr: int = XCORR_SR) -> np.ndarray:
    """
    Half-wave rectify and smooth with a 50 ms window.
    Converts waveform to amplitude envelope — sharpens the xcorr peak
    when audio has continuous background noise (crowd cheering).
    """
    window = max(1, int(sr * 0.05))
    return np.convolve(np.maximum(x, 0.0),
                       np.ones(window, dtype=np.float32) / window,
                       mode="same")


def _norm(x: np.ndarray) -> np.ndarray:
    """Normalise to unit variance; no-op if signal is silent."""
    s = float(x.std())
    return x / s if s > 1e-9 else x


# --- Cross-correlation delay -------------------------------------------------

def _xcorr_delay(ref: np.ndarray, other: np.ndarray,
                 sr: int, max_offset_s: float = MAX_OFFSET_S) -> float:
    """
    Compute time delay of `other` relative to `ref` via FFT cross-correlation.

    Uses mode="full" so the peak can be anywhere in [-len, +len] lags.
    Restricts search to +/-max_offset_s to avoid wrap-around noise.

    Returns delay in seconds.  Positive = other starts LATER than ref.
    """
    a = _envelope(_norm(ref.astype(np.float64)))
    b = _envelope(_norm(other.astype(np.float64)))

    corr = signal.correlate(a, b, mode="full", method="fft")
    lags = signal.correlation_lags(len(a), len(b), mode="full")

    # Restrict to +/-max_offset_s window
    max_lag = int(max_offset_s * sr)
    mask = np.abs(lags) <= max_lag
    if not np.any(mask):
        return 0.0

    search_corr = np.abs(corr[mask])
    search_lags = lags[mask]
    peak_idx = int(np.argmax(search_corr))
    return float(search_lags[peak_idx]) / sr


# --- Public API --------------------------------------------------------------

def find_all_delays(video_paths: List[str],
                    max_offset_s: float = MAX_OFFSET_S) -> List[float]:
    """
    Compute per-video delay relative to the longest video.

    Returns list of floats (seconds). Positive = that clip starts later
    than the reference. Reference clip always has delay 0.0.
    """
    if len(video_paths) < 2:
        raise ValueError("At least two video paths are required for autosync.")

    delays = [0.0] * len(video_paths)
    longest_index = find_longest_vid(video_paths)
    results = [process_audio(p) for p in video_paths]
    audios = [r[0] for r in results]
    srs    = [r[1] for r in results]

    ref = audios[longest_index]
    sr  = srs[longest_index]

    for i in range(len(video_paths)):
        if i == longest_index:
            continue
        delays[i] = _xcorr_delay(ref, audios[i], sr, max_offset_s)
        print(f"  clip {i} ({get_file_name(video_paths[i])}): "
              f"delay={delays[i]:.4f}s")

    return delays


def find_all_delays_with_pivot(video_paths: List[str],
                               pivot_index: int,
                               max_offset_s: float = MAX_OFFSET_S) -> List[float]:
    """
    Same as find_all_delays but uses a caller-specified pivot index.
    """
    if len(video_paths) < 2:
        raise ValueError("At least two video paths are required for autosync.")

    delays = [0.0] * len(video_paths)
    results = [process_audio(p) for p in video_paths]
    audios = [r[0] for r in results]
    srs    = [r[1] for r in results]

    ref = audios[pivot_index]
    sr  = srs[pivot_index]

    for i in range(len(video_paths)):
        if i == pivot_index:
            continue
        delays[i] = _xcorr_delay(ref, audios[i], sr, max_offset_s)

    return delays


def find_all_durations(video_paths: List[str]) -> List[float]:
    return [_probe_duration(p) for p in video_paths]


def compute_delays(video_paths: List[str]) -> List[float]:
    """
    Public alias used by MainEditorSyncWorker in brain_battle_main.py.
    Returns raw delays (not normalised); normalisation is done in the worker.
    """
    return find_all_delays(video_paths)


def autosync(video_paths: List[str]) -> List[float]:
    """Legacy entry point — compute and return delays."""
    if not all(os.path.exists(p) for p in video_paths):
        raise FileNotFoundError("One or more video paths are invalid.")
    delays = find_all_delays(video_paths)
    print(f"Delays: {delays}")
    return delays
