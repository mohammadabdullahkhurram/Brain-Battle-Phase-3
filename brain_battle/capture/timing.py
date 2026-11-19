"""
timing.py — how well the devices keep time, and the buzzer calibration
──────────────────────────────────────────────────────────────────────
Goal: **under 10 ms between all devices after correction**
(docs/decisions/adr-0024-timing-check-and-buzzer-calibration.md).

Every camera frame and every headband packet is stamped on arrival by the one
capture clock (``capture/clock.py``). Each device delivers its data a little
late — cameras over USB, headbands over Bluetooth — and that delay is
different per device. This module has three parts:

**1. Live timing per device** (``RateMonitor``). Every arrival is fed in; once
a second ``snapshot()`` says the actual rate, the jitter (how unevenly the
arrivals are spaced, ms), and what was lost (camera frames dropped, headband
packets missing). Shown in the "Test timing" dialog and on the Recording
screen, and written once a second to ``timing_quality.csv``.

**2. De-jittered headband timestamps** (``LinearClockFit``). A headband sends
12 EEG samples per Bluetooth packet; packets arrive unevenly. The fit is a
straight line through (sample number, arrival time) for every packet so far,
so each sample gets the time the line gives — the arrival jitter is gone, the
device's constant delay stays (that is what the buzzer test measures).

**3. Buzzer calibration.** Five beeps ~1.5 s apart; at each, both boxers tap
their headband once in view of the cameras. The tap is found in each
headband's EEG (a sharp artifact at 256 Hz, confirmed by the accelerometer)
and in each camera (frame-difference energy, with a sub-frame estimate). A
device's **correction** is the median, over the taps, of how much later than
the other devices it reported that tap (``calibrate``): the beep's own audio
delay and the boxers' reaction time are the same for every device watching
one tap, so they cancel; what is left is how much later one device reports
the same instant than the others. Corrections are saved in the
manifest (``timing_calibration``) and applied when recording.xdf,
eeg_coregistered.csv and Review & Label's alignment are built. Raw files are
never changed.

Pure logic + numpy; no Qt.
"""
from __future__ import annotations

import collections
import datetime as _dt
import math
import threading
from dataclasses import dataclass
from typing import Optional

import numpy as np

GOAL_MS = 10.0                 # the professor's target, between all devices
N_BEEPS = 5
BEEP_INTERVAL_SEC = 1.5
TAP_WINDOW = (0.0, 0.9)        # look for a tap this long after each beep (s)


# ═══════════════════════════════════════════════════ 1. live timing per device

def _steady_frame_times(t, lo: float, hi: float):
    """Frame stamps in [lo, hi] replaced by the camera's steady frame clock.

    A camera captures every frame period T on its own crystal; the stamps add
    a delivery delay that varies (USB, a busy computer) and frames can be lost.
    Each stamp is locked to the lattice phi + n·T (n = its frame number,
    rounded) — so a frame that arrived late misplaces only itself — and phi
    and T are refitted robustly (least squares, then the outliers dropped,
    intercept by median) a few times. Stamps outside the window are left as
    they are."""
    t = np.asarray(t, dtype=float).copy()
    sel = np.nonzero((t >= lo) & (t <= hi))[0]
    if len(sel) < 8:
        return t
    ts = t[sel]
    d = np.diff(ts)
    per = float(np.median(d)) if len(d) else 0.0
    if per <= 0:
        return t
    phi = float(ts[0])
    size = 16
    while True:
        m = min(size, len(ts))
        sub = ts[:m]
        for _ in range(3):
            n = np.round((sub - phi) / per)
            res = sub - (phi + per * n)
            med = float(np.median(res))
            mad = max(1.4826 * float(np.median(np.abs(res - med))), 1e-4)
            keep = np.abs(res - med) <= 3 * mad
            if keep.sum() >= 6 and np.ptp(n[keep]) > 0:
                per, _a = np.polyfit(n[keep], sub[keep], 1)
                per = float(per)
                phi = float(np.median(sub[keep] - per * n[keep]))
            else:
                phi += med
        if m == len(ts):
            break
        size *= 2
    n = np.round((ts - phi) / per)
    t[sel] = phi + per * n
    return t


@dataclass
class TimingSnapshot:
    device: str
    rate: float = 0.0          # frames / samples per second actually delivered
    jitter_ms: float = 0.0     # spread of arrival intervals (std, ms)
    dropped: int = 0           # frames dropped / packets missing in the last second
    dropped_total: int = 0     # … since the monitor started
    unit: str = "fps"          # "fps" (cameras) or "Hz" (headbands)
    packet_ms: float = 0.0     # mean packet interval (headbands)


class RateMonitor:
    """Thread-safe arrival log for one device. ``add(t, n)`` from the capture
    thread (``n`` = frames or samples in that arrival); ``snapshot()`` from
    the UI thread, about once a second."""

    def __init__(self, device: str, nominal_interval: float, unit: str = "fps",
                 samples_per_arrival: int = 1, window: float = 1.0):
        self.device = device
        self.nominal = float(nominal_interval)      # expected seconds between arrivals
        self.unit = unit
        self.per_arrival = int(samples_per_arrival)
        self.window = float(window)
        self._lock = threading.Lock()
        self._arrivals: collections.deque = collections.deque()   # (t, n)
        self._dropped_events: collections.deque = collections.deque()  # (t, n)
        self._dropped_total = 0
        self._last_t: Optional[float] = None

    def add(self, t: float, n: int = 1) -> None:
        with self._lock:
            if self._last_t is not None and self.nominal > 0:
                gap = t - self._last_t
                missing = int(round(gap / self.nominal)) - 1
                if missing >= 1 and gap > 1.5 * self.nominal:
                    self._dropped_events.append((t, missing))
                    self._dropped_total += missing
            self._last_t = t
            self._arrivals.append((t, n))
            self._trim(t)

    def add_dropped(self, t: float, n: int = 1) -> None:
        """Losses the arrival gaps cannot see (a camera frame captured but not
        handed to the encoder because its queue was full)."""
        with self._lock:
            self._dropped_events.append((t, n))
            self._dropped_total += n
            self._trim(t)

    def _trim(self, now: float) -> None:
        horizon = now - max(self.window, 5.0)
        while self._arrivals and self._arrivals[0][0] < horizon:
            self._arrivals.popleft()
        while self._dropped_events and self._dropped_events[0][0] < now - self.window:
            self._dropped_events.popleft()

    def snapshot(self, now: Optional[float] = None) -> TimingSnapshot:
        with self._lock:
            arr = list(self._arrivals)
            drops = list(self._dropped_events)
            total = self._dropped_total
        snap = TimingSnapshot(self.device, unit=self.unit, dropped_total=total)
        if not arr:
            return snap
        now = arr[-1][0] if now is None else now
        recent = [(t, n) for t, n in arr if t >= now - self.window]
        # frames / samples per second between the first and the last arrival
        # of the window (a headband's 12-sample packets would otherwise make
        # the count jump between 252 and 264 a second)
        if len(recent) >= 2 and recent[-1][0] > recent[0][0]:
            snap.rate = float(sum(n for _t, n in recent[1:])) / (recent[-1][0] - recent[0][0])
        else:
            snap.rate = float(sum(n for _t, n in recent)) / self.window if recent else 0.0
        ts = np.array([t for t, _n in arr if t >= now - max(self.window, 2.0)])
        if len(ts) >= 3:
            d = np.diff(ts)
            # gaps where something was lost are counted as "dropped", not jitter
            if self.nominal > 0:
                d = d[d <= 1.5 * self.nominal] if (d <= 1.5 * self.nominal).sum() >= 2 else d
            snap.jitter_ms = float(np.std(d) * 1000.0)
            snap.packet_ms = float(np.mean(d) * 1000.0)
        snap.dropped = int(sum(n for t, n in drops if t >= now - self.window))
        return snap


# ═══════════════════════════════════════════════ 2. de-jittered headband time

class LinearClockFit:
    """Straight line through (sample index, arrival time) for every packet so
    far: ``time(i) = a + b·i``. The slope ``b`` (the headband's real sample
    period, as seen by this computer's clock) and the intercept ``a`` come
    from the last ~90 s of packets: a least-squares line, then packets far off
    it (more than 3 robust standard deviations — a busy computer, a Bluetooth
    retry) are dropped and the line is fitted again; the intercept is the
    MEDIAN of (arrival − b·i), so late packets do not pull every sample
    later. The window follows slow drift between the headband's crystal and
    this computer's clock.
    Numerically safe over hours (sums are kept relative to the first point).
    One per headband, fed only from that headband's Bluetooth callback."""

    MIN_POINTS = 8          # before this, arrival-based times are used as they are
    KEEP = 2048             # packets used for the robust intercept (~90 s)
    REFRESH = 16            # recompute the intercept every this many packets
    SETTLE = 200            # packets (~10 s) before re-estimates are slewed
    MAX_STEP = 0.00025      # after that, the line moves at most 0.25 ms per re-estimate

    def __init__(self, nominal_rate: float):
        self.rate = float(nominal_rate)
        self._i0 = None
        self._t0 = None
        self._n = 0
        self._sx = self._sy = self._sxx = self._sxy = 0.0
        self._recent = collections.deque(maxlen=self.KEEP)    # (x, y) relative
        self._cache = None
        self._applied = None
        self._since = 0

    def add(self, index: float, t_arrival: float) -> None:
        if self._i0 is None:
            self._i0, self._t0 = float(index), float(t_arrival)
        x = float(index) - self._i0
        y = float(t_arrival) - self._t0
        self._n += 1
        self._sx += x; self._sy += y; self._sxx += x * x; self._sxy += x * y
        self._recent.append((x, y))
        self._since += 1
        if self._cache is None or self._since >= self.REFRESH:
            self._cache = None

    @property
    def ready(self) -> bool:
        return self._n >= self.MIN_POINTS

    def _slope(self) -> float:
        n = self._n
        den = n * self._sxx - self._sx ** 2
        if n < 2 or den <= 0:
            return 1.0 / self.rate
        return (n * self._sxy - self._sx * self._sy) / den

    def params(self) -> tuple[float, float]:
        """(time at index 0, in arrival seconds; seconds per sample)."""
        if self._n < 2:
            return (self._t0 or 0.0) - (self._i0 or 0.0) / self.rate, 1.0 / self.rate
        if self._cache is None:
            r = np.asarray(self._recent, dtype=np.float64)
            x, y = r[:, 0], r[:, 1]
            if len(r) >= 16 and np.ptp(x) > 0:
                b, a = np.polyfit(x, y, 1)
                res = y - (a + b * x)
                med = np.median(res)
                mad = max(1.4826 * np.median(np.abs(res - med)), 0.001)
                keep = np.abs(res - med) <= 3 * mad
                if keep.sum() >= 8:
                    b, a = np.polyfit(x[keep], y[keep], 1)
                    x, y = x[keep], y[keep]
            else:
                b = self._slope()
            a = float(np.median(y - b * x))
            new = (self._t0 + a - b * self._i0, float(b))
            # Once settled (~10 s of packets), the line may only move a little
            # at a time: a re-estimate must never make the sample times jump
            # (that would look like missing or doubled samples).
            if self._applied is not None and self._n > self.SETTLE:
                pa, pb = self._applied
                x_now = float(self._recent[-1][0]) + self._i0
                t_old = pa + pb * x_now
                t_new = new[0] + new[1] * x_now
                step = max(-self.MAX_STEP, min(self.MAX_STEP, t_new - t_old))
                new = (t_old + step - new[1] * x_now, new[1])
            self._applied = new
            self._cache = new
            self._since = 0
        return self._cache

    def times(self, indices) -> np.ndarray:
        a, b = self.params()
        return a + b * np.asarray(indices, dtype=np.float64)


class PacketTimer:
    """Per headband: logs each Bluetooth packet's arrival, works out the
    sample index of every sample (counting samples lost in missing packets
    from the gaps in the timestamps the Muse library hands over), keeps the
    ``LinearClockFit`` and feeds a ``RateMonitor``. ``stamp(arrival, ts)``
    returns the de-jittered times for one chunk."""

    def __init__(self, device: str, rate: float = 256.0, chunk: int = 12):
        self.rate = float(rate)
        self.chunk = int(chunk)
        self.fit = LinearClockFit(rate)
        self.monitor = RateMonitor(device, chunk / rate, unit="Hz", samples_per_arrival=chunk)
        self._next_index = 0
        self._prev_last_ts: Optional[float] = None
        self._last_out: Optional[float] = None
        self.packets = 0
        self.missing_samples = 0

    def stamp(self, arrival_unix: float, ts_in) -> np.ndarray:
        ts_in = np.asarray(ts_in, dtype=np.float64)
        n = len(ts_in)
        if n == 0:
            return ts_in
        if self._prev_last_ts is not None:
            gap = int(round((ts_in[0] - self._prev_last_ts) * self.rate)) - 1
            if gap >= self.chunk // 2:               # whole packets went missing
                self._next_index += gap
                self.missing_samples += gap
        self._prev_last_ts = float(ts_in[-1])
        idx = self._next_index + np.arange(n)
        self._next_index += n
        self.packets += 1
        # the packet "arrives" when its last sample has been sent
        self.fit.add(idx[-1], arrival_unix)
        self.monitor.add(arrival_unix, n)
        if not self.fit.ready:
            out = arrival_unix - (n - 1 - np.arange(n)) / self.rate
        else:
            out = self.fit.times(idx)
        # Times never go backwards: while the line is still settling (the first
        # seconds after the headband connects) a sample may come out at or
        # before the previous one; only those samples are nudged just past it
        # (by 0.1 ms each), so nothing is carried into later samples.
        if self._last_out is not None and out[0] <= self._last_out:
            out = np.maximum(out, self._last_out + 1e-4 * np.arange(1, n + 1))
        self._last_out = float(out[-1])
        return out


class FrameClock:
    """Per camera: the time of every frame on the camera's own steady clock,
    instead of the moment this computer happened to read it (ADR-0025).

    A camera captures a frame every period T on its own crystal. With a
    1-frame driver buffer the reader always gets the NEWEST frame, so a frame
    is read between 0 and one period after it became available — later when
    the computer was busy. Stamping the read time puts some frames up to a
    period (17 ms at 60 fps) late.

    A small tracker follows the camera's ON-TIME frame clock (the earliest a
    frame is ever read) instead:

    * **Frame number.** ``steps = floor((read time − previous stamp) / T +
      EARLY)``. Two reads less than 1.5 periods apart are consecutive frames
      (nothing can have been skipped): one step — or none, when ``steps`` is
      0, which means the previous frame was counted one too far after a
      pause, so one mistake never shifts later frames. After a longer pause:
      ``max(1, steps)``; more than one step is frames the camera lost.
    * **Stamp.** predicted = previous stamp + steps·T. A frame read EARLIER
      than predicted moves the clock a fifth of the way towards it (the
      on-time clock is earlier than thought); a frame read later moves it
      only a hundredth (being late is normal, the clock is not). The period T
      follows these corrections, so a camera that declares 30 fps but runs at
      29.5 is tracked. T starts as the median of the first frame intervals.

    Everything is local, so nothing can drift far: no global line that a
    wrong frame count could bend. Measured on a simulated busy reader:
    stamps within 0.5–2.7 ms of the true frame time (99 %), against up to a
    period for read times; a constant ~1.3 ms early (the on-time edge rather
    than the typical read), which the buzzer test calibrates out with the rest
    of the camera's delay. ``reset()`` after the camera was reopened (a new
    frame clock); stamps never go backwards."""

    EARLY = 0.25
    TOWARDS_EARLY = 0.2      # share of the gap to an early frame taken at once
    TOWARDS_LATE = 0.01      # share of the gap to a late frame
    PERIOD_GAIN = 0.02       # how much the period follows each correction
    WARMUP = 60              # frame intervals used for the first period

    def __init__(self, fps: float):
        self.fps = float(fps) if fps and fps > 0 else 30.0
        self.nominal = 1.0 / self.fps
        self.lost = 0
        self._last_out: Optional[float] = None
        self.reset()

    def reset(self) -> None:
        self.period = self.nominal
        self._warm: list = []
        self._stamp: Optional[float] = None
        self._arrival: Optional[float] = None

    def stamp(self, arrival: float) -> float:
        arrival = float(arrival)
        if self._stamp is None:
            out = arrival
        else:
            gap = arrival - self._arrival
            if len(self._warm) < self.WARMUP and gap < 1.5 * self.nominal:
                self._warm.append(gap)
                if len(self._warm) >= 10:
                    self.period = float(np.median(self._warm))
            T = self.period
            steps = int(math.floor((arrival - self._stamp) / T + self.EARLY))
            k = (1 if steps >= 1 else 0) if gap < 1.5 * T else max(1, steps)
            self.lost += max(0, k - 1)
            predicted = self._stamp + k * T
            r = arrival - predicted
            c = self.TOWARDS_EARLY * r if r < 0 else self.TOWARDS_LATE * min(r, 0.5 * T)
            out = predicted + c
            if k >= 1:
                self.period = min(2.0 * self.nominal, max(0.5 * self.nominal,
                                                         T + self.PERIOD_GAIN * c / k))
        self._stamp = out
        self._arrival = arrival
        if self._last_out is not None and out <= self._last_out:
            out = self._last_out + 1e-4
        self._last_out = out
        return out


# ═══════════════════════════════════════════════════ 3. buzzer calibration

def _robust_noise(x) -> float:
    x = np.asarray(x, dtype=float)
    if len(x) == 0:
        return 0.0
    return float(np.median(np.abs(x - np.median(x))) * 1.4826) or float(np.std(x)) or 1e-9


def detect_eeg_tap(t, data, t_from: float, t_to: float,
                   acc_t=None, acc=None, k: float = 8.0) -> Optional[float]:
    """Onset time of the sharp tap artifact in a headband's EEG between
    ``t_from`` and ``t_to`` (same clock as ``t``), or None.

    ``data`` is (N, channels). The artifact is the largest, fastest deflection
    on the skin channels; its onset is where the summed absolute deviation
    from the pre-tap level first crosses ``k`` robust standard deviations,
    refined to sub-sample precision by back-extrapolating the rising edge
    (the samples between 15 % and 85 % of its peak) to the baseline. A tap
    moves the electrodes for several milliseconds, so the edge spans more
    than one 3.9 ms sample. With ``acc`` given
    (N×3 accelerometer), the tap must also show as a jolt within ±0.15 s."""
    t = np.asarray(t, dtype=float)
    x = np.asarray(data, dtype=float)
    if x.ndim == 1:
        x = x[:, None]
    x = x[:, :min(4, x.shape[1])]              # TP9 AF7 AF8 TP10 (not AUX)
    pre = (t >= t_from - 0.5) & (t < t_from)
    win = (t >= t_from) & (t <= t_to)
    if pre.sum() < 16 or win.sum() < 8:
        return None
    base = np.median(x[pre], axis=0)
    env_pre = np.abs(x[pre] - base).sum(axis=1)
    env = np.abs(x[win] - base).sum(axis=1)
    tw = t[win]
    level = np.median(env_pre)
    noise = _robust_noise(env_pre)
    thr = level + k * noise
    above = np.nonzero(env > thr)[0]
    if len(above) == 0:
        return None
    i = int(above[0])
    # rising edge: from the crossing to the local peak just after it
    j_end = min(len(env), i + 8)
    pk = i + int(np.argmax(env[i:j_end]))
    peak = env[pk] - level
    if peak <= 0:
        return None
    seg_t, seg_v = [], []
    for j in range(max(0, i - 3), pk + 1):
        frac = (env[j] - level) / peak
        if 0.15 <= frac <= 0.85:
            seg_t.append(tw[j]); seg_v.append(env[j] - level)
    if len(seg_t) >= 2:
        b, a = np.polyfit(seg_t, seg_v, 1)
        onset = -a / b if b > 0 else tw[i]
    else:
        # linear interpolation of the threshold crossing, then step back by
        # the fraction of the rise the threshold sits at
        j = max(1, i)
        v0, v1 = env[j - 1] - level, env[j] - level
        f = (thr - level - v0) / (v1 - v0) if v1 != v0 else 0.0
        onset = tw[j - 1] + f * (tw[j] - tw[j - 1]) - (thr - level) / peak * (tw[pk] - tw[j - 1])
    onset = float(min(max(onset, tw[max(0, i - 3)]), tw[pk]))
    if acc is not None and acc_t is not None and len(acc_t):
        at = np.asarray(acc_t, dtype=float)
        a = np.asarray(acc, dtype=float)
        mag = np.linalg.norm(a, axis=1) if a.ndim == 2 else np.abs(a)
        pre_a = (at >= t_from - 0.5) & (at < t_from)
        near = (at >= onset - 0.15) & (at <= onset + 0.15)
        if pre_a.sum() >= 4 and near.sum() >= 1:
            jolt = np.abs(mag[near] - np.median(mag[pre_a])).max()
            if jolt < 4.0 * _robust_noise(mag[pre_a]):
                return None                    # EEG spike without a jolt: not the tap
    return onset


def detect_camera_tap(frame_t, energy, t_from: float, t_to: float,
                      k: float = 8.0) -> Optional[float]:
    """Onset time of a tap seen by a camera, from its per-frame
    frame-difference energy (mean |frame − previous frame|, ``energy[i]``
    belongs to the frame stamped ``frame_t[i]``), or None.

    A camera integrates light over the frame: if the tap starts a fraction
    ``f`` of the way back from the end of frame k's exposure, frame k shows
    fraction f of the change and frame k+1 the rest, so
    ``f = e[k] / (e[k] + e[k+1])`` (after removing the noise level) and the
    onset is ``t[k] − f · (t[k] − t[k−1])``. That gives a sub-frame estimate
    from two frames.

    The frame stamps carry USB / thread jitter, but a camera's own frame clock
    is steady, so the stamps around the tap are first replaced by a straight
    line through them (frame number vs stamp, lost frames counted)."""
    t = np.asarray(frame_t, dtype=float)
    e = np.asarray(energy, dtype=float)
    t = _steady_frame_times(t, t_from - 1.0, t_to + 0.5)
    pre = (t >= t_from - 0.5) & (t < t_from)
    idx = np.nonzero((t >= t_from) & (t <= t_to))[0]
    if pre.sum() < 4 or len(idx) < 3:
        return None
    level = float(np.median(e[pre]))
    noise = _robust_noise(e[pre])
    # at least one grey level of change on the small picture, so a camera
    # with an almost noise-free background does not trigger on nothing
    thr = level + max(k * noise, 1.0)
    hits = [i for i in idx if e[i] > thr]
    if not hits:
        return None
    k0 = hits[0]
    if k0 < 1 or k0 + 1 >= len(e):
        return float(t[k0])
    d0 = max(0.0, e[k0] - level)
    d1 = max(0.0, e[k0 + 1] - level)
    f = d0 / (d0 + d1) if (d0 + d1) > 0 else 1.0
    period = t[k0] - t[k0 - 1]
    return float(t[k0] - f * period)


def calibrate(beep_times, events: dict, min_taps: int = 3) -> dict:
    """Turn detected tap times into the calibration saved in the manifest.

    ``beep_times``: the instants each beep was played (capture clock, s).
    ``events``: ``{device: [tap time or None per beep]}`` on the same clock.

    For each beep, the **consensus** is the median tap time over the devices
    that found it; a device's per-tap value is its tap time minus that
    consensus. That cancels everything all devices share for one tap — the
    beep's audio delay and the boxers' reaction time, which also varies from
    tap to tap. A device's correction is the median of its per-tap values,
    re-centred so the corrections' median is 0 (the data stays where the
    devices, on average, put it). ``offset_ms`` (median tap − beep) is kept
    for reference. A device needs ``min_taps`` taps found to be corrected.
    Returns the ``timing_calibration`` dict (see file-formats.md)."""
    beeps = [float(b) for b in beep_times]
    devs = list(events)
    taps = {d: [None if v is None else float(v) for v in (list(events[d]) + [None] * len(beeps))[:len(beeps)]]
            for d in devs}
    consensus = []
    for i in range(len(beeps)):
        found = [taps[d][i] for d in devs if taps[d][i] is not None]
        consensus.append(float(np.median(found)) if len(found) >= 2 else None)
    rel, raw = {}, {}
    for d in devs:
        rel[d] = [(taps[d][i] - consensus[i]) * 1000.0 for i in range(len(beeps))
                  if taps[d][i] is not None and consensus[i] is not None]
        raw[d] = [(taps[d][i] - beeps[i]) * 1000.0 for i in range(len(beeps))
                  if taps[d][i] is not None]
    med = {d: float(np.median(rel[d])) for d in devs if len(rel[d]) >= min_taps}
    centre = float(np.median(list(med.values()))) if med else 0.0
    out_devices = {}
    for d in devs:
        ok = d in med
        spread = float(np.median(np.abs(np.array(rel[d]) - med[d]))) if ok else None
        out_devices[d] = {
            "offset_ms": round(float(np.median(raw[d])), 2) if raw[d] else None,
            "correction_ms": round(med[d] - centre, 2) if ok else None,
            "delay_ms": None,
            "taps_found": len(raw[d]),
            "spread_ms": None if spread is None else round(spread, 2),
            "per_tap_ms": [round(v, 2) for v in raw[d]],
        }
    corr = [v["correction_ms"] for v in out_devices.values() if v["correction_ms"] is not None]
    fastest = min(corr) if corr else 0.0
    for v in out_devices.values():
        if v["correction_ms"] is not None:
            v["delay_ms"] = round(v["correction_ms"] - fastest, 2)
    # what is left after correction: how far each device's taps still wander
    # around the consensus; the two worst devices together bound the largest
    # difference between any two devices
    spreads = sorted((v["spread_ms"] for v in out_devices.values() if v["spread_ms"] is not None),
                     reverse=True)
    largest = (spreads[0] + (spreads[1] if len(spreads) > 1 else 0.0)) if spreads else None
    return {
        "method": "buzzer",
        "measured_at": _dt.datetime.now().isoformat(timespec="seconds"),
        "beeps": len(beeps),
        "beep_times": [round(b, 4) for b in beeps],
        "reference": "median of devices",
        "devices": out_devices,
        "complete": bool(out_devices) and all(v["correction_ms"] is not None
                                              for v in out_devices.values()),
        "largest_difference_ms": None if largest is None else round(largest, 2),
    }


def summary_text(cal: Optional[dict]) -> str:
    """One plain sentence for the Saved screen / dialog."""
    if not cal or not cal.get("devices"):
        return "Timing not measured"
    worst = cal.get("largest_difference_ms")
    missing = [d for d, v in cal["devices"].items() if v.get("correction_ms") is None]
    if missing:
        return (f"Buzzer test incomplete: no taps found for {', '.join(missing)}. "
                f"Those devices are not corrected.")
    if worst is None:
        return "Timing not measured"
    if worst < GOAL_MS:
        return (f"Largest difference between devices after correction: {worst:.0f} ms "
                f"(under the {GOAL_MS:.0f} ms goal).")
    return (f"Largest difference between devices after correction: {worst:.0f} ms "
            f"(over the {GOAL_MS:.0f} ms goal). The taps did not line up well — run the "
            f"buzzer test again, with one clear tap at each beep; if it stays over, record "
            f"the cameras at 60 fps (Settings › Advanced) and close other programs.")


# ═══════════════════════════════════ applying the calibration (XDF, export, Review)

def camera_device(stem: str) -> str:
    """Device key of a camera in the calibration ('SIDE_01', 'POV_02')."""
    return str(stem)


def band_device(which: str) -> str:
    """Device key of a headband ('EEG_A', 'EEG_B')."""
    return f"EEG_{str(which).upper()}"


def corrections(manifest_or_cal) -> dict:
    """``{device: seconds to SUBTRACT from that device's times}`` from a
    manifest's ``timing_calibration`` (or the calibration itself). Devices the
    buzzer test did not measure get no entry (= 0)."""
    m = manifest_or_cal or {}
    cal = m.get("timing_calibration", m) if isinstance(m, dict) else {}
    out = {}
    for dev, v in ((cal or {}).get("devices") or {}).items():
        c = (v or {}).get("correction_ms")
        if c is not None and math.isfinite(float(c)):
            out[dev] = float(c) / 1000.0
    return out


def correction_for(manifest, device: str) -> float:
    return corrections(manifest).get(device, 0.0)


__all__ = [
    "GOAL_MS", "N_BEEPS", "BEEP_INTERVAL_SEC", "TAP_WINDOW",
    "TimingSnapshot", "RateMonitor", "LinearClockFit", "PacketTimer", "FrameClock",
    "detect_eeg_tap", "detect_camera_tap", "calibrate", "summary_text",
    "camera_device", "band_device", "corrections", "correction_for",
]
