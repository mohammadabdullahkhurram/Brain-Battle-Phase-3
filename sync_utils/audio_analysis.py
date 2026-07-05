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


def has_audio_stream(video_path: str) -> bool:
    """
    True if the file contains at least one audio stream (via ffprobe).

    In-app captures are written by cv2.VideoWriter and never have audio, so
    callers use this to decide between audio-xcorr sync (external footage)
    and simultaneous-start alignment (in-app continuous sessions).
    Unreadable/missing files count as no-audio.
    """
    try:
        probe = _ffmpeg.probe(video_path)
        return any(s.get("codec_type") == "audio"
                   for s in probe.get("streams", []))
    except Exception:
        return False


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

def _peak_confidence(search_corr: np.ndarray, peak_idx: int, sr: int,
                     guard_s: float = 0.25) -> float:
    """
    Confidence in [0, 1] for a correlation peak: baseline-corrected
    peak-to-second-peak prominence.

        conf = 1 - (second_peak - baseline) / (peak - baseline)

    where `second_peak` is the highest correlation value outside a
    +/-guard_s window around the peak (excluding the peak's own shoulder —
    the 50 ms envelope smoothing gives the true peak ~100 ms of width) and
    `baseline` is the median of the search window. The baseline correction
    matters because envelopes are non-negative, so raw correlations ride a
    large DC floor that would otherwise mask the peak's real prominence.

    A shared signal produces one peak towering over the corrected floor
    (conf near 1); unrelated signals produce many comparable fluctuations,
    so the runner-up nearly matches the peak (conf near 0). Deliberately a
    RATIO, not raw peak height — raw height scales with signal energy and
    is not comparable across clips.
    """
    peak = float(search_corr[peak_idx])
    baseline = float(np.median(search_corr))
    denom = peak - baseline
    if denom <= 0.0:                      # flat correlation — no information
        return 0.0
    guard = max(1, int(guard_s * sr))
    outside = np.ones(len(search_corr), dtype=bool)
    outside[max(0, peak_idx - guard):peak_idx + guard + 1] = False
    if not outside.any():                 # window smaller than the guard
        return 1.0
    second = float(np.max(search_corr[outside]))
    conf = 1.0 - max(0.0, second - baseline) / denom
    return float(min(max(conf, 0.0), 1.0))


def _xcorr_delay(ref: np.ndarray, other: np.ndarray,
                 sr: int, max_offset_s: float = MAX_OFFSET_S) -> tuple[float, float]:
    """
    Compute time delay of `other` relative to `ref` via FFT cross-correlation.

    Uses mode="full" so the peak can be anywhere in [-len, +len] lags.
    Restricts search to +/-max_offset_s to avoid wrap-around noise.

    Returns (delay_seconds, confidence).
    Delay: positive = other starts LATER than ref.
    Confidence: [0, 1] peak prominence (see _peak_confidence); 0.0 when no
    delay could be measured at all.
    """
    # A clip with no audio stream extracts to an empty buffer; correlating
    # it is meaningless (and np.convolve/scipy.correlate raise on empty
    # arrays), so treat it as "no measurable offset".
    if len(ref) == 0 or len(other) == 0:
        return 0.0, 0.0

    a = _envelope(_norm(ref.astype(np.float64)))
    b = _envelope(_norm(other.astype(np.float64)))

    corr = signal.correlate(a, b, mode="full", method="fft")
    lags = signal.correlation_lags(len(a), len(b), mode="full")

    # Restrict to +/-max_offset_s window
    max_lag = int(max_offset_s * sr)
    mask = np.abs(lags) <= max_lag
    if not np.any(mask):
        return 0.0, 0.0

    search_corr = np.abs(corr[mask])
    search_lags = lags[mask]
    peak_idx = int(np.argmax(search_corr))
    delay = float(search_lags[peak_idx]) / sr
    if not np.isfinite(delay):
        return 0.0, 0.0
    return delay, _peak_confidence(search_corr, peak_idx, sr)


# --- Public API --------------------------------------------------------------

def find_all_delays(video_paths: List[str],
                    max_offset_s: float = MAX_OFFSET_S,
                    pivot_index: int | None = None,
                    ) -> tuple[List[float], List[float]]:
    """
    Compute per-video delay relative to the longest video (or to
    `pivot_index` if given).

    Returns (delays, confidences), one entry per input path:
      delays      — seconds; positive = that clip starts later than the
                    reference. Reference clip always has delay 0.0.
      confidences — [0, 1] peak-prominence per pairing (_peak_confidence).
                    The reference's offset is 0 by construction, not
                    measured, so its confidence is the max of its pairings —
                    the anchor is only as corroborated as its best match.
    """
    if len(video_paths) < 2:
        raise ValueError("At least two video paths are required for autosync.")

    delays = [0.0] * len(video_paths)
    confidences = [0.0] * len(video_paths)
    ref_index = pivot_index if pivot_index is not None else find_longest_vid(video_paths)
    results = [process_audio(p) for p in video_paths]
    audios = [r[0] for r in results]
    srs    = [r[1] for r in results]

    ref = audios[ref_index]
    sr  = srs[ref_index]

    for i in range(len(video_paths)):
        if i == ref_index:
            continue
        delays[i], confidences[i] = _xcorr_delay(ref, audios[i], sr, max_offset_s)
        print(f"  clip {i} ({get_file_name(video_paths[i])}): "
              f"delay={delays[i]:.4f}s  confidence={confidences[i]:.3f}")

    confidences[ref_index] = max(
        c for i, c in enumerate(confidences) if i != ref_index)
    return delays, confidences


def find_all_delays_with_pivot(video_paths: List[str],
                               pivot_index: int,
                               max_offset_s: float = MAX_OFFSET_S,
                               ) -> tuple[List[float], List[float]]:
    """
    Same as find_all_delays but uses a caller-specified pivot index.
    Returns (delays, confidences).
    """
    return find_all_delays(video_paths, max_offset_s, pivot_index=pivot_index)


def find_all_durations(video_paths: List[str]) -> List[float]:
    return [_probe_duration(p) for p in video_paths]


def compute_delays_with_confidence(
        video_paths: List[str]) -> tuple[List[float], List[float]]:
    """
    Used by MainEditorSyncWorker in brain_battle_main.py.
    Returns (raw delays, confidences); normalisation is done in the worker
    and does not affect confidences.
    """
    return find_all_delays(video_paths)


def compute_delays(video_paths: List[str]) -> List[float]:
    """Back-compat alias — delays only, confidences discarded."""
    return find_all_delays(video_paths)[0]


def autosync(video_paths: List[str]) -> List[float]:
    """Legacy entry point — compute and return delays."""
    if not all(os.path.exists(p) for p in video_paths):
        raise FileNotFoundError("One or more video paths are invalid.")
    delays, _confs = find_all_delays(video_paths)
    print(f"Delays: {delays}")
    return delays
