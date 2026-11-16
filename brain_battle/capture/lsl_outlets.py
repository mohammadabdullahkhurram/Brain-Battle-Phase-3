"""
lsl_outlets.py — live Lab Streaming Layer (LSL) outlets
───────────────────────────────────────────────────────
Publishes what the app is capturing as standard LSL streams on the local
network, so the usual Muse tool-chain works alongside Brain Battle:

    headband → (this app) → LSL streams → LabRecorder → .xdf → EEGLAB / Python

Any LSL program on the same computer or network — LabRecorder, an LSL viewer
such as the BrainVision LSL Viewer, a student's own script — sees:

    BrainBattle_EEG_A / _B        type EEG          5 ch · 256 Hz · microvolts
    BrainBattle_ACC_A / _B        type ACC          3 ch · 52 Hz  · g
    BrainBattle_GYRO_A / _B       type GYRO         3 ch · 52 Hz  · deg/s
    BrainBattle_PPG_A / _B        type PPG          3 ch · 64 Hz
    BrainBattle_Markers           type Markers      rounds, breaks, marked moments
    BrainBattle_Video_<camera>    type VideoFrames  one sample per recorded frame
                                                    (value = frame number in the MP4)

Every sample is pushed with an explicit timestamp converted from the one
capture clock (``capture/clock.py``) to LSL time, so a LabRecorder file made
from these streams lines up with the app's own ``recording.xdf``.

This is a convenience layer and is **fail-soft**: the app's own recording
never depends on it. If pylsl is missing or an outlet cannot be opened, the
problem is logged once and every ``push_*`` becomes a no-op. Qt-free. Stream
names come from ``data/signals.py`` (shared with ``recording.xdf``).

**Outlets are opened once per run of the app and then reused** (ADR-0022).
One outlet holds 13 open descriptors (sockets and pipes), and the LSL library
does not give all of them back when an outlet is destroyed — 3 stay open for
good. Opening a fresh set on every visit to New recording therefore used up the
operating system's open-file limit ("Too many open files") after a few
recordings. So the outlets live in a module-level store: ``close()`` only hands
them back, and the next publisher picks the same ones up. A closed publisher's
streams stay listed on the network, silent, until the app quits.
"""
from __future__ import annotations

import logging
import threading

import numpy as np

import brain_battle.capture.clock as clock
from brain_battle.data.signals import (
    EEG_CHANNELS, EEG_UNIT, SAMPLE_RATE, SENSORS, MARKER_STREAM,
    eeg_stream_name, sensor_stream_name, video_stream_name)

log = logging.getLogger(__name__)

# Every outlet this process has opened: name → (what it was opened with, outlet).
# Never emptied while the app runs — see the module docstring.
_OPEN: dict = {}
_OPEN_LOCK = threading.Lock()


def open_outlet_count() -> int:
    """How many LSL outlets this process holds (they are kept until it exits)."""
    return len(_OPEN)


class LslPublisher:
    """Owns the app's LSL outlets. Thread-safe: EEG is pushed from the UI
    thread, video frames from each camera's writer thread."""

    def __init__(self, enabled: bool = True):
        self._outlets: dict = {}
        self._lock = threading.Lock()
        self._pylsl = None
        if enabled:
            try:
                import pylsl
                self._pylsl = pylsl
            except Exception as e:
                log.warning("LSL streams are off: pylsl could not be loaded (%s)", e)

    @property
    def active(self) -> bool:
        return self._pylsl is not None

    def stream_names(self) -> list:
        return sorted(self._outlets)

    # ── outlet creation ──
    def _outlet(self, name, type_, channels, rate, fmt, units, ch_type, extra=None):
        """Get the outlet called ``name``: this publisher's, else the one the
        process already has open, else a new one."""
        out = self._outlets.get(name)
        if out is not None or self._pylsl is None:
            return out
        signature = (type_, tuple(channels), rate, fmt, units, ch_type,
                     tuple(sorted((k, str(v)) for k, v in (extra or {}).items())))
        with self._lock, _OPEN_LOCK:
            out = self._outlets.get(name)
            if out is not None:
                return out
            held = _OPEN.get(name)
            if held is not None and held[0] == signature:
                self._outlets[name] = held[1]
                return held[1]
            # Not open yet — or open with a different description (another
            # headband is on this boxer now), which has to be replaced.
            lsl = self._pylsl
            try:
                info = lsl.StreamInfo(name, type_, len(channels), rate, fmt,
                                      f"brainbattle-{name}")
                desc = info.desc()
                desc.append_child_value("manufacturer", "Brain Battle")
                for k, v in (extra or {}).items():
                    desc.append_child_value(k, str(v))
                chans = desc.append_child("channels")
                for c in channels:
                    ch = chans.append_child("channel")
                    ch.append_child_value("label", c)
                    if units:
                        ch.append_child_value("unit", units)
                    ch.append_child_value("type", ch_type)
                out = lsl.StreamOutlet(info)
            except Exception as e:
                log.warning("could not open LSL stream %s: %s — LSL streams are off", name, e)
                self._pylsl = None
                return None
            _OPEN[name] = (signature, out)
            self._outlets[name] = out
            return out

    def open_markers(self) -> None:
        if self._pylsl is not None:
            self._outlet(MARKER_STREAM, "Markers", ["marker"],
                         self._pylsl.IRREGULAR_RATE, self._pylsl.cf_string, "", "Marker")

    def open_video(self, stem: str) -> None:
        if self._pylsl is not None:
            self._outlet(video_stream_name(stem), "VideoFrames", ["frame_index"],
                         self._pylsl.IRREGULAR_RATE, self._pylsl.cf_int32, "", "FrameIndex",
                         {"video_file": f"session_{stem}.mp4"})

    # ── pushing (all timestamps are converted to LSL time here) ──
    def push_eeg(self, which: str, data: np.ndarray, ts_unix: np.ndarray,
                 device: str = "") -> None:
        """``data`` is (N, 5) microvolts, ``ts_unix`` (N,) capture-clock Unix times."""
        if self._pylsl is None:
            return
        out = self._outlet(eeg_stream_name(which), "EEG", EEG_CHANNELS, SAMPLE_RATE,
                           self._pylsl.cf_float32, EEG_UNIT, "EEG",
                           {"boxer": which.upper(), "headband": device})
        self._push_chunk(out, data, [clock.unix_to_lsl(t) for t in ts_unix])

    def push_sensor(self, kind: str, which: str, data: np.ndarray,
                    ts_unix: np.ndarray, device: str = "") -> None:
        if self._pylsl is None or kind not in SENSORS:
            return
        rate, channels, unit, ch_type = SENSORS[kind]
        out = self._outlet(sensor_stream_name(kind, which), kind, channels, rate,
                           self._pylsl.cf_float32, unit, ch_type,
                           {"boxer": which.upper(), "headband": device})
        self._push_chunk(out, data, [clock.unix_to_lsl(t) for t in ts_unix])

    def push_marker(self, text: str, t_monotonic: float) -> None:
        self.open_markers()
        self._push_sample(self._outlets.get(MARKER_STREAM), [str(text)],
                          clock.mono_to_lsl(t_monotonic))

    def push_frame(self, stem: str, frame_index: int, t_monotonic: float) -> None:
        self._push_sample(self._outlets.get(video_stream_name(stem)),
                          [int(frame_index)], clock.mono_to_lsl(t_monotonic))

    def _push_chunk(self, out, data, ts) -> None:
        if out is None or len(ts) == 0:
            return
        try:
            out.push_chunk(np.ascontiguousarray(data, dtype=np.float32), ts)
        except Exception as e:
            log.debug("LSL push failed: %r", e)

    def _push_sample(self, out, sample, ts) -> None:
        if out is None:
            return
        try:
            out.push_sample(sample, ts)
        except Exception as e:
            log.debug("LSL push failed: %r", e)

    def close(self) -> None:
        """Stop publishing. The outlets themselves stay open for the next
        publisher (see the module docstring), so the streams stay listed but
        carry no data."""
        with self._lock:
            self._outlets.clear()
            self._pylsl = None


__all__ = ["LslPublisher", "open_outlet_count"]
