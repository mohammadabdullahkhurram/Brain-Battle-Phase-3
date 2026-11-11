"""
contact_quality.py
──────────────────
A simple, documented heuristic for per-sensor EEG contact quality, shown in
plain language on the connect / recording / review screens (DESIGN vocabulary:
TP9 = left ear, AF7 = left forehead, AF8 = right forehead, TP10 = right ear).

*** HEURISTIC, pending real data. *** Nobody has inspected raw Muse-S EEG from
a real sparring round yet (AGENTS.md "Project situation"), so these thresholds
are first-guess rules over a short window, not validated against ground truth:

  • flat     — std below FLAT_UV: the electrode isn't picking anything up
               (not touching skin / disconnected).
  • clipped  — too many samples pinned near the ±CLIP_UV rail: railed amplifier,
               usually a bad/by-hair contact.
  • noisy    — std above NOISY_UV: motion / loose electrode swamping the signal.
  • otherwise "good".

Returns UI-ready strings: "Signal good", or "Weak contact: left ear, right
forehead" naming the sensors to re-seat. Revisit the thresholds once real
sparring EEG exists.
"""
from __future__ import annotations

import numpy as np

# Muse channel code → plain-language sensor location (DESIGN vocabulary).
CHANNELS = ["TP9", "AF7", "AF8", "TP10"]
SENSOR_NAME = {
    "TP9":  "left ear",
    "AF7":  "left forehead",
    "AF8":  "right forehead",
    "TP10": "right ear",
}

# Heuristic thresholds (microvolts). First-guess values — see module docstring.
FLAT_UV = 0.5       # std below this → nothing coming through
NOISY_UV = 120.0    # std above this → motion / loose electrode
CLIP_UV = 800.0     # |sample| at/above this counts as railed
CLIP_FRACTION = 0.05  # >5% railed samples → clipped


def channel_status(samples) -> str:
    """One of 'good' | 'flat' | 'clipped' | 'noisy' for a short window of one
    channel's samples (microvolts)."""
    x = np.asarray(samples, dtype=float)
    x = x[np.isfinite(x)]
    if x.size < 2:
        return "flat"
    # Clipping is judged on the raw amplitude; flat/noisy on the AC (de-meaned)
    # signal so a large DC offset alone doesn't read as noise.
    if np.mean(np.abs(x) >= CLIP_UV) > CLIP_FRACTION:
        return "clipped"
    std = float(np.std(x - np.mean(x)))
    if std < FLAT_UV:
        return "flat"
    if std > NOISY_UV:
        return "noisy"
    return "good"


def summarise(statuses: dict) -> str:
    """Turn {channel: status} into the DESIGN contact-quality line."""
    weak = [SENSOR_NAME.get(ch, ch) for ch in CHANNELS
            if statuses.get(ch, "good") != "good"]
    if not weak:
        return "Signal good"
    return "Weak contact: " + ", ".join(weak)


def assess_window(df, channels=None) -> tuple[str, dict]:
    """Assess a DataFrame window (columns = channel codes). Returns
    (summary_line, {channel: status})."""
    channels = channels or CHANNELS
    statuses = {}
    for ch in channels:
        if hasattr(df, "columns") and ch in df.columns:
            statuses[ch] = channel_status(df[ch].to_numpy())
        else:
            statuses[ch] = "flat"
    return summarise(statuses), statuses


def assess_at(df, t_center: float, half: float = 1.0,
              t_col: str = "t_session_seconds") -> tuple[str, dict]:
    """Assess the ±half-second window around a session time."""
    if df is None or not hasattr(df, "columns") or t_col not in df.columns:
        return "Signal good", {ch: "good" for ch in CHANNELS}
    t = df[t_col].to_numpy(dtype=float)
    mask = (t >= t_center - half) & (t <= t_center + half)
    if not mask.any():
        # before/after the recording — nearest half-window
        idx = int(np.argmin(np.abs(t - t_center)))
        lo = max(0, idx - int(half * 256)); hi = idx + int(half * 256)
        window = df.iloc[lo:hi]
    else:
        window = df.iloc[np.flatnonzero(mask)]
    return assess_window(window)


__all__ = ["CHANNELS", "SENSOR_NAME", "channel_status", "summarise",
           "assess_window", "assess_at",
           "FLAT_UV", "NOISY_UV", "CLIP_UV", "CLIP_FRACTION"]
