"""
simulation.py  —  DEVELOPER-ONLY simulated cameras + headbands
──────────────────────────────────────────────────────────────
Hardware is currently broken (see AGENTS.md "Project situation"), so this
module lets the guided recording flow be built and tested end to end without
real cameras or Muse-S bands. It is gated entirely behind the
``BRAIN_BATTLE_SIMULATE`` environment variable: with it unset, nothing here is
reachable and the UI shows no trace of simulation.

**This module is NOT shipped** (see docs/ROADMAP.md Phase 6 — excluded from the
packaged app alongside tools/ and tests/).

Everything goes through the REAL capture paths, so the on-disk result is
byte-for-byte what real hardware would produce:
  • Cameras → a cv2.VideoCapture-compatible ``SimCapture`` fed to the real
    ``USBCameraThread`` (→ ffmpeg fragmented H.264 + per-frame sidecars),
    rate-limited to real time.
  • Headbands → ``SimMuseReader`` (a subclass of the real
    ``eeg_module._MuseDirectReader``) that synthesises 12-sample chunks at
    256 Hz and feeds them through the reader's real ``_on_eeg`` /
    ``start_recording`` / ``stop_recording`` path, plus synthetic movement and
    pulse sensors, battery telemetry, and a deliberately weak sensor on Boxer B
    so the contact-quality warnings can be seen. Because it is the real reader,
    the live LSL streams and ``recording.xdf`` come out of simulation too.

Timing (for the timing check and the buzzer test, ADR-0024): every simulated
device delivers its data **late by a known delay plus random jitter**, exactly
as USB and Bluetooth do, so the live timing numbers and the buzzer calibration
can be checked against known answers:
  • cameras: a frame's picture shows the instant ``stamp − delay − jitter``;
  • headbands: a 12-sample packet reaches the reader ``delay + jitter`` after
    its last sample was taken.
Defaults are realistic (``DEFAULT_DELAYS_MS``, jitter ±2 ms for cameras, ±4 ms
for headbands); BRAIN_BATTLE_SIMULATE_DELAYS="SIDE_01=52,EEG_A=31,…" and
BRAIN_BATTLE_SIMULATE_JITTER_MS="CAM=2,EEG=4" override them.
``schedule_taps(times)`` makes both boxers "tap their headband" at those
capture-clock instants: a bright patch appears on every camera (rendered as a
real camera integrates light over the frame, so part of the change lands in
the frame the tap started in) and a sharp artifact + accelerometer jolt
appears in both headbands.

Failure injection (also env-gated, for the failure-path screens/tests):
  • BRAIN_BATTLE_SIMULATE_MISSING_CAMERA=1  → only 3 cameras enumerate.
  • BRAIN_BATTLE_SIMULATE_DROP_HEADBAND=<s> → Boxer B's band disconnects <s>
    seconds into recording (default 8 when set to "1").
  • BRAIN_BATTLE_SIMULATE_MAX_FPS=30        → cameras cannot do 60 fps.
  • BRAIN_BATTLE_SIMULATE_STALL_CAMERA=<stem>:<after s>:<for s> → that camera
    stops delivering frames for a while (the watchdog test).
"""
from __future__ import annotations

import os
import random
import threading
import time

import numpy as np

# Weak sensor shown on Boxer B's band (DESIGN: TP10 = right ear).
_WEAK_SENSOR = "TP10"
_WEAK_BOXER = "B"


# ─── gating ───────────────────────────────────────────────────────────────────

def _truthy(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def enabled() -> bool:
    return _truthy(os.environ.get("BRAIN_BATTLE_SIMULATE"))


def missing_camera() -> bool:
    return enabled() and _truthy(os.environ.get("BRAIN_BATTLE_SIMULATE_MISSING_CAMERA"))


# ─── simulated timing (delays, jitter, taps) ─────────────────────────────────

DEFAULT_DELAYS_MS = {"SIDE_01": 52.0, "SIDE_02": 49.0, "POV_01": 63.0, "POV_02": 57.0,
                     "EEG_A": 31.0, "EEG_B": 36.0}
DEFAULT_JITTER_MS = {"CAM": 2.0, "EEG": 4.0}
TAP_PATCH_SEC = 0.25          # how long the "hand" stays in view after a tap
_taps_lock = threading.Lock()
_taps: list = []              # capture-clock (monotonic) instants of scheduled taps
# What each simulated device ACTUALLY delivered: (when, delay in s) — the
# injected delay + jitter + however late the computer let the thread run.
# The timing test compares the buzzer calibration with this ground truth.
_delivered: dict = {}
_stall_first_open: dict = {}     # stem → when the stalling camera was first opened


def note_delivered(device: str, when: float, delay: float) -> None:
    import collections
    with _taps_lock:
        _delivered.setdefault(device, collections.deque(maxlen=4000)).append((when, delay))


def camera_key(device_index: int) -> str:
    """Key of a simulated camera in the delivered-delay log (by DEVICE — which
    slot a device sits in is the operator's choice and is remembered)."""
    return f"CAM_{int(device_index)}"


def delivered_delay(device: str, t0: float, t1: float):
    """Median delay (s) the device really delivered between t0 and t1."""
    with _taps_lock:
        vals = [d for w, d in _delivered.get(device, ()) if t0 <= w <= t1]
    return float(np.median(vals)) if vals else None


def _parse_pairs(raw: str) -> dict:
    out = {}
    for part in (raw or "").split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                out[k.strip().upper()] = float(v)
            except ValueError:
                pass
    return out


def delay_ms(device: str) -> float:
    """The simulated device's constant delay (ms)."""
    over = _parse_pairs(os.environ.get("BRAIN_BATTLE_SIMULATE_DELAYS", ""))
    return float(over.get(device.upper(), DEFAULT_DELAYS_MS.get(device.upper(), 40.0)))


def jitter_ms(kind: str) -> float:
    """Half-width (ms) of the uniform jitter for 'CAM' or 'EEG'."""
    over = _parse_pairs(os.environ.get("BRAIN_BATTLE_SIMULATE_JITTER_MS", ""))
    return float(over.get(kind, DEFAULT_JITTER_MS.get(kind, 0.0)))


def max_fps() -> float | None:
    v = os.environ.get("BRAIN_BATTLE_SIMULATE_MAX_FPS")
    try:
        return float(v) if v else None
    except ValueError:
        return None


def stall_camera():
    """(stem, after_s, for_s) for the camera that should stall, or None."""
    v = os.environ.get("BRAIN_BATTLE_SIMULATE_STALL_CAMERA", "")
    try:
        stem, after, dur = v.split(":")
        return stem.strip().upper(), float(after), float(dur)
    except ValueError:
        return None


def schedule_taps(times_monotonic) -> None:
    """Both boxers tap their headband at these capture-clock instants."""
    with _taps_lock:
        _taps.extend(float(t) for t in times_monotonic)
        _taps.sort()


def clear_taps() -> None:
    with _taps_lock:
        _taps.clear()


def taps_between(t0: float, t1: float) -> list:
    with _taps_lock:
        return [t for t in _taps if t0 - TAP_PATCH_SEC - 1.0 <= t <= t1]


def drop_headband_after() -> float | None:
    """Seconds into recording that Boxer B's band should drop, or None."""
    if not enabled():
        return None
    v = os.environ.get("BRAIN_BATTLE_SIMULATE_DROP_HEADBAND")
    if not v:
        return None
    if _truthy(v):
        return 8.0
    try:
        return float(v)
    except ValueError:
        return None


# ─── simulated cameras ────────────────────────────────────────────────────────

def camera_devices():
    """Enumerate simulated capture devices as real CameraDevice records so the
    picker / auto-assign treats them like hardware. One fewer when the
    missing-camera failure is on."""
    from brain_battle.capture.usb_camera import CameraDevice
    names = ["Simulated Side camera 1", "Simulated Side camera 2",
             "Simulated Boxer A view", "Simulated Boxer B view"]
    if missing_camera():
        names = names[:3]
    return [CameraDevice(index=200 + i, name=n, backend=0)
            for i, n in enumerate(names)]


class SimCapture:
    """cv2.VideoCapture-compatible synthetic source. ``read()`` blocks until the
    next frame is due, pacing capture to real time exactly as a physical camera
    would, so the real USBCameraThread timing/sidecar logic is exercised.

    The picture of each frame shows the instant ``now − delay − jitter``
    (``delay_ms(stem)``), so the frame stamp the camera thread takes on return
    is late by a known amount; scheduled taps appear as a bright patch."""

    def __init__(self, index: int, w: int = 960, h: int = 540, fps: float = 30.0,
                 stem: str = ""):
        import brain_battle.capture.clock as clock
        self._clock = clock
        self._index = int(index)
        self._stem = stem.upper()
        self._w, self._h = int(w), int(h)
        self._fps = float(fps) or 30.0
        self._open = True
        self._t0 = clock.monotonic()
        self._n = 0
        self._label = f"SIM CAM {self._index}"
        self._delay = delay_ms(self._stem) / 1000.0 if self._stem else 0.0
        self._jit = jitter_ms("CAM") / 1000.0
        self._rng = random.Random(self._index)
        self._stall = stall_camera()
        self._made = time.monotonic()
        # Pre-render a static background so each read() is cheap.
        self._bg = None
        self._make_bg()

    # cv2.VideoCapture surface used by USBCameraThread.run() ───────────────────
    def _make_bg(self):
        import cv2
        self._bg = np.full((self._h, self._w, 3), 22, dtype=np.uint8)
        cv2.putText(self._bg, self._label, (28, 70), cv2.FONT_HERSHEY_SIMPLEX,
                    1.1, (235, 235, 235), 2, cv2.LINE_AA)

    def isOpened(self):  # noqa: N802
        return self._open

    def set(self, prop, val):  # noqa: N802
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            self._w = int(val) or self._w
            self._make_bg()
        elif prop == cv2.CAP_PROP_FRAME_HEIGHT:
            self._h = int(val) or self._h
            self._make_bg()
        elif prop == cv2.CAP_PROP_FPS:
            want = float(val) or self._fps
            cap = max_fps()
            self._fps = min(want, cap) if cap else want
            self._t0 = self._clock.monotonic(); self._n = 0
        return True

    def get(self, prop):  # noqa: N802
        import cv2
        if prop == cv2.CAP_PROP_FRAME_WIDTH:
            return float(self._w)
        if prop == cv2.CAP_PROP_FRAME_HEIGHT:
            return float(self._h)
        if prop == cv2.CAP_PROP_FPS:
            return float(self._fps)
        return 0.0

    def _stalled(self) -> bool:
        """The injected stall happens once per run: <after> seconds after this
        camera was first opened, for <for> seconds — a reopened camera does
        not stall again."""
        if not self._stall or self._stall[0] != self._stem:
            return False
        first = _stall_first_open.setdefault(self._stem, self._made)
        since = time.monotonic() - first
        return self._stall[1] <= since < self._stall[1] + self._stall[2]

    def read(self, image=None):  # noqa: N802
        if not self._open:
            return False, None
        if self._stalled():
            time.sleep(0.05)
            self._t0 = self._clock.monotonic(); self._n = 0
            return False, None
        # A real camera captures on its own steady clock; only DELIVERY is
        # late (USB, decoding) and uneven. Frame n shows the instant
        # t0 + n/fps and is handed over delay + jitter after it. With a
        # 1-frame driver buffer (CAP_PROP_BUFFERSIZE=1) a reader that falls
        # behind gets the NEWEST frame — the ones in between are lost.
        late_by = self._clock.monotonic() - (self._t0 + self._n / self._fps + self._delay)
        if late_by > 1.0 / self._fps:
            self._n += int(late_by * self._fps)
        shown = self._t0 + self._n / self._fps
        ready = shown + self._delay + self._rng.uniform(-self._jit, self._jit)
        frame = self._render(self._n, image)
        self._draw_tap(frame, shown)
        wait = ready - self._clock.monotonic()
        if wait > 0:
            time.sleep(min(wait, 0.2))
        now = self._clock.monotonic()
        note_delivered(camera_key(self._index), now, now - shown)
        self._n += 1
        return True, frame

    def release(self):  # noqa: N802
        self._open = False

    # ── frame rendering (cheap; a moving bar + burned-in clock + tap patch) ──
    def _render(self, n, out=None):
        """Draw frame n — into ``out`` when given (like cv2's read(image))."""
        import cv2
        if out is not None and out.shape == self._bg.shape:
            np.copyto(out, self._bg)
            img = out
        else:
            img = self._bg.copy()
        t = n / self._fps
        span = max(1, self._w - 40)
        pos = (n * 6) % (2 * span)
        bar_x = int(pos if pos < span else 2 * span - pos)     # bounces, never jumps
        img[:, bar_x:bar_x + 40] = (60, 70, 160)
        cv2.putText(img, f"{t:6.2f}s  f{n}", (28, 130), cv2.FONT_HERSHEY_SIMPLEX,
                    0.9, (180, 190, 210), 2, cv2.LINE_AA)
        return img

    def _draw_tap(self, img, shown):
        # A tap: the hand is in view for TAP_PATCH_SEC. The camera integrates
        # light over one frame period, so a frame shows the fraction of its
        # exposure that came after the tap started.
        period = 1.0 / self._fps
        level = 0.0
        for tau in taps_between(shown - TAP_PATCH_SEC - period, shown):
            on = min(max((shown - tau) / period, 0.0), 1.0)
            off = min(max((shown - tau - TAP_PATCH_SEC) / period, 0.0), 1.0)
            level = max(level, on - off)
        if level > 0:
            y0, x0 = self._h // 3, self._w // 3
            patch = img[y0:y0 + self._h // 3, x0:x0 + self._w // 3]
            patch[:] = (patch * (1 - level) + 235 * level).astype(np.uint8)


_SIM_STEMS = {200: "SIDE_01", 201: "SIDE_02", 202: "POV_01", 203: "POV_02"}


def open_capture(device, w=960, h=540, fps=30.0):
    """cv2.VideoCapture replacement for a simulated CameraDevice."""
    idx = getattr(device, "index", 0)
    return SimCapture(idx, w=w, h=h, fps=fps, stem=_SIM_STEMS.get(idx, ""))


# ─── simulated headbands ──────────────────────────────────────────────────────

def muse_devices():
    """Two simulated Muse-S bands whose names match the default headband
    mapping, so they auto-assign to Boxer A / Boxer B."""
    import brain_battle.data.app_settings as app_settings
    s = app_settings.load()
    out = []
    for who in ("A", "B"):
        dev = s.headband_for(who) or f"MuseS-000{who}"
        out.append({"name": dev, "address": f"sim-{who}", "boxer": who})
    return out


_SIM_READER_CLS = None


def _sim_reader_cls():
    """Define SimMuseReader lazily (subclassing the real _MuseDirectReader), so
    importing this module doesn't pull in Qt/eeg_module until a simulated reader
    is actually created."""
    global _SIM_READER_CLS
    if _SIM_READER_CLS is not None:
        return _SIM_READER_CLS

    import brain_battle.capture.clock as clock
    from brain_battle.capture.eeg_module import _MuseDirectReader, SENSORS

    class SimMuseReader(_MuseDirectReader):
        """Generates synthetic headband data and feeds it through the real
        reader's callbacks (``_on_eeg`` / ``_on_imu`` / ``_on_sensor`` /
        ``_on_telemetry``), so recording, flushing, the Qt signals and the LSL
        streams are all the real code. Boxer B's right-ear sensor is kept weak
        so the contact-quality warning is visible; optionally drops
        mid-recording."""

        def __init__(self, address, name="", boxer="", **kw):
            super().__init__(address=address, name=name, **kw)
            self._boxer = (boxer or "").upper()
            self._rec_start_mono = None
            self._battery_level = 82 if self._boxer == "A" else 76

        def start_recording(self, path, origin_unix=None, append=False):
            self._rec_start_mono = time.monotonic()
            return super().start_recording(path, origin_unix=origin_unix, append=append)

        def stop(self):
            self._running = False

        def run(self):
            """Samples are taken on a true clock; a packet reaches the reader
            (``_on_eeg`` with its arrival time) ``delay + jitter`` after its
            last sample — so the reader's packet log and straight-line fit see
            what a real Bluetooth link would give them."""
            self._running = True
            self._first_chunk_seen = False
            rng = np.random.default_rng(abs(hash(self.name)) % (2**32))
            jr = random.Random(abs(hash(self.name)) % (2**32))
            ph = rng.uniform(0, 2 * np.pi)
            dev = f"EEG_{self._boxer}" if self._boxer else "EEG_A"
            dly = delay_ms(dev) / 1000.0
            jit = jitter_ms("EEG") / 1000.0
            t_start = clock.monotonic()                 # true time of sample 0
            drop_after = drop_headband_after()
            # (kind, rate, chunk, samples sent so far, jitter of the pending packet)
            streams = [["EEG", 256, 12, 0, jr.uniform(-jit, jit)]]
            if self.motion:
                streams += [["ACC", SENSORS["ACC"][0], 3, 0, 0.0],
                            ["GYRO", SENSORS["GYRO"][0], 3, 0, 0.0]]
            if self.ppg:
                streams += [["PPG", SENSORS["PPG"][0], 6, 0, 0.0]]
            last_batt = 0.0
            self._on_telemetry(clock.unix_now(), self._battery_level)
            while self._running:
                now = clock.monotonic()
                busy = False
                for st in streams:
                    kind, sr, chunk, sent, jj = st
                    t_last = t_start + (sent + chunk - 1) / sr      # true time, last sample
                    if now < t_last + dly + jj:
                        continue
                    busy = True
                    t_true = t_start + (sent + np.arange(chunk)) / sr
                    ts_true = np.array([clock.mono_to_unix(x) for x in t_true])
                    try:
                        if kind == "EEG":
                            data = self._make_chunk(sent, chunk, sr, rng, ph)
                            self._add_taps(data, t_true)
                            arr = clock.unix_now()
                            self._on_eeg(data, ts_true, arrival=arr)
                            note_delivered(dev, clock.monotonic(), arr - float(ts_true[-1]))
                        else:
                            data = self._make_sensor(kind, sent, chunk, sr, rng)
                            if kind == "ACC":
                                self._add_jolts(data, t_true)
                            self._on_sensor(kind, data, ts_true + dly)
                    except Exception:
                        pass
                    st[3] = sent + chunk
                    st[4] = jr.uniform(-jit, jit) if kind == "EEG" else 0.0
                if not busy:
                    time.sleep(0.002)
                if now - last_batt > 3.0:
                    last_batt = now
                    self._battery_level = max(1, self._battery_level - 1)
                    self._on_telemetry(clock.unix_now(), self._battery_level)
                if (drop_after is not None and self._boxer == _WEAK_BOXER
                        and self._rec_start_mono is not None
                        and time.monotonic() - self._rec_start_mono > drop_after):
                    self._running = False
                    self.error.emit("The headband turned off or moved out of range.")
                    return

        @staticmethod
        def _add_taps(data, t_true):
            """A tap moves the electrodes: an 8 ms rise, then a 40 ms decay,
            on the four skin channels."""
            for tau in taps_between(float(t_true[0]) - 0.3, float(t_true[-1])):
                d = t_true - tau
                m = d > 0
                if m.any():
                    art = 300.0 * np.minimum(d[m] / 0.008, 1.0) * np.exp(
                        -np.maximum(d[m] - 0.008, 0.0) / 0.04)
                    data[:4, m] += art.astype(np.float32)

        @staticmethod
        def _add_jolts(data, t_true):
            for tau in taps_between(float(t_true[0]) - 0.1, float(t_true[-1])):
                d = t_true - tau
                m = (d >= 0) & (d < 0.06)
                data[:, m] += np.float32(1.5)      # a tap on the head: a jolt of ~1.5 g

        def _make_chunk(self, start, n, sr, rng, ph):
            t = (start + np.arange(n)) / sr
            data = np.zeros((5, n), dtype=np.float32)
            for ci, ch in enumerate(("TP9", "AF7", "AF8", "TP10")):
                sig = rng.normal(0.0, 8.0, n) + 3.0 * np.sin(2 * np.pi * 10.0 * t + ph + ci)
                if self._boxer == _WEAK_BOXER and ch == _WEAK_SENSOR:
                    sig = rng.normal(0.0, 0.1, n)   # near-flat → "weak contact"
                data[ci] = sig
            data[4] = rng.normal(0.0, 4.0, n)       # AUX
            return data

        def _make_sensor(self, kind, start, n, sr, rng):
            t = (start + np.arange(n)) / sr
            sway = np.sin(2 * np.pi * 1.5 * t)       # a boxer bouncing at 1.5 Hz
            if kind == "ACC":                        # g: gravity on Z + sway
                base = np.array([[0.02], [0.05], [0.98]])
                return (base + 0.15 * sway + rng.normal(0, 0.01, (3, n))).astype(np.float32)
            if kind == "GYRO":                       # degrees / second
                return (25.0 * sway + rng.normal(0, 2.0, (3, n))).astype(np.float32)
            pulse = np.sin(2 * np.pi * 1.8 * t)      # ~108 bpm
            return (60000 + 400 * pulse + rng.normal(0, 30, (3, n))).astype(np.float32)

    _SIM_READER_CLS = SimMuseReader
    return _SIM_READER_CLS


def make_muse_reader(address: str, name: str, boxer: str = "", **kw):
    """A simulated reader standing in for _MuseDirectReader over fake Bluetooth."""
    return _sim_reader_cls()(address=address, name=name, boxer=boxer, **kw)


__all__ = [
    "enabled", "missing_camera", "drop_headband_after", "max_fps", "stall_camera",
    "delay_ms", "jitter_ms", "schedule_taps", "clear_taps", "DEFAULT_DELAYS_MS",
    "delivered_delay", "camera_key",
    "camera_devices", "SimCapture", "open_capture",
    "muse_devices", "make_muse_reader",
]
