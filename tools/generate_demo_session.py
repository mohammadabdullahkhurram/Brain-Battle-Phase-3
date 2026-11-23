"""
generate_demo_session.py
─────────────────────────
Generates a realistic fake Brain Battle recording folder so the rest of the
app can be built and tested without hardware (see AGENTS.md "Project
situation" — real cameras / Muse-S bands are currently unavailable).

It REUSES the real writers, so the demo can never silently drift from the
format the app actually reads and writes:
  - `session_storage.create_session` / `SessionLayout` — the manifest and
    phase_markers.csv writers the live app uses.
  - `session_controller._build_timeline` — the real phase plan (rest → rounds →
    breaks, no break after the last round).
  - `usb_camera.FrameEncoder` — the real crash-safe fragmented-H.264 encoder +
    row-per-frame sidecar, driven synchronously with synthetic frames.
  - `eeg_module._MuseDirectReader` — the real per-chunk CSV streaming path
    (`_on_eeg` / `_on_sensor` / `start_recording` / `stop_recording`), fed
    synthetic chunks instead of Bluetooth callbacks.
  - `session_xdf.build_session_xdf` — the real recording.xdf builder.

What is synthetic: the video pixels (burned-in text + a white flash at every
phase marker), the EEG / movement signals (plausible noise, a little alpha,
blinks, more movement during rounds, a sharp spike at every phase marker), and
the timeline itself (stamped on one made-up but consistent axis rather than in
real time).

It also writes what the app writes around them: ``contact_quality.csv`` (a
weak-sensor stretch when asked), ``timing_quality.csv``, ``moments.csv``, the
history (as "Recording" and the demo labellers, on "LAB-PC-1" — never this
computer's login or name), each labeller's labels file, a session log and the
buzzer-test ``timing_calibration``. Fighters are codes and colours only.

Usage:
    python3 tools/generate_demo_session.py --out /tmp/demo
    python3 tools/generate_demo_session.py --out /tmp/demo --rounds 3 --seed 7
    python3 tools/generate_demo_session.py --out /tmp/demo --interrupted
    python3 tools/generate_demo_session.py --out /tmp/demo --stopped-early 60
    python3 tools/generate_demo_session.py --out /tmp/library --library

To open a demo library in the app with simulated devices, use
``python3 tools/demo.py``.

Deterministic for a given --seed. Default config (15 s lead-in, baseline 20 s,
2 rounds of 60 s with a 20 s break = 175 s) generates in about a minute.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import cv2

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from brain_battle.capture.session_controller import (  # noqa: E402
    SessionConfig, _build_timeline, planned_markers)
from brain_battle.data.session_storage import create_session, PhaseRecord  # noqa: E402
from brain_battle.capture.usb_camera import FrameEncoder, preflight_recording  # noqa: E402

# Camera slots (index, label, kind). The labels drive the on-disk filenames
# (session_SIDE_01.mp4 etc.) and the drift-camera selection; they match
# camera_source's _SLOT_STEM so the demo's layout equals a real recording's.
_SLOT_SPECS = [
    (1, "SIDE 01", "side"), (2, "SIDE 02", "side"),
    (3, "POV 01", "pov"),   (4, "POV 02", "pov"),
]
import brain_battle.capture.clock as capture_clock  # noqa: E402
import brain_battle.data.app_settings as app_settings  # noqa: E402
import brain_battle.data.session_xdf as session_xdf  # noqa: E402
from brain_battle.capture.eeg_module import _MuseDirectReader, muse_friendly as _muse_friendly  # noqa: E402
from brain_battle.data.app_settings import default_capture_root as _default_capture_root  # noqa: E402
from brain_battle.data.signals import SENSORS  # noqa: E402

# PyQt requires a live Python reference to the QApplication singleton or it
# can be garbage-collected even though QApplication.instance() still reports
# one existing at the C++ level — the next QWidget construction anywhere in
# the process then aborts with "Must construct a QApplication before a
# QWidget". A function-local reference isn't enough here since callers (e.g.
# tests/test_demo_session.py) construct widgets in a LATER call after
# generate_demo_session() has already returned — so the reference has to
# outlive the function. Same pattern as the `app = ... # noqa: F841` idiom
# in tests/test_smoke.py and tests/test_capture_clock.py, just held at
# module scope instead of a function's local scope.
_qapp_ref = None


# ─── Constants ────────────────────────────────────────────────────────────────

WIDTH, HEIGHT = 960, 540
NOMINAL_FPS = 30.0          # declared container fps for every camera
DRIFT_FPS = 29.5            # TRUE capture rate of the one drifting camera
DRIFT_CAMERA_LABEL = "POV 01"   # fixed so test_demo_session.py can assert on it by name
SR_EEG = 256
FLASH_FRAMES = 3            # consecutive frames of full-white flash per marker
_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


# ─── Phase timeline ────────────────────────────────────────────────────────────

def _phase_plan(cfg: SessionConfig, interrupted: bool, stop_at: float | None = None):
    """Walk the REAL _build_timeline() / planned_markers() output and compute,
    on one synthetic axis, every phase's (start, end) elapsed time, the system
    markers a live recording writes (RECORDING_START, BASELINE_START/END,
    ROUND_n_START/END, BREAK_n_START/END, RECORDING_END), and — for
    --interrupted or a stopped-early recording — where it ends.

    Returns (phase_records, markers, total_duration) where:
      phase_records: list of dict(phase=Phase, label, round_num, start, end_or_None)
      markers:       list of (t_session_sec, phase_id, label, round_num)
      total_duration: the (possibly truncated) session length in seconds
    """
    phase_records = []
    elapsed = 0.0
    for info in _build_timeline(cfg):
        phase_records.append({
            "phase": info.phase, "label": info.label, "round_num": info.round_num,
            "start": elapsed, "end": elapsed + info.duration,
        })
        elapsed += info.duration
    total_duration = elapsed
    markers = [(t, pid, text, rn) for t, text, pid, rn in planned_markers(cfg)]

    if stop_at is not None and 0 < stop_at < total_duration:
        # Stopped early (cleanly, Stop now): the phase in progress gets its
        # _END and RECORDING_END at the stop instant, as the controller writes.
        phase_records = [r for r in phase_records if r["start"] < stop_at]
        phase_records[-1]["end"] = stop_at
        later = [m for m in markers if m[0] > stop_at]
        cur_end = next((m for m in later if m[2].endswith("_END") and m[2] != "RECORDING_END"), None)
        rec_end = next(m for m in markers if m[2] == "RECORDING_END")
        markers = [m for m in markers if m[0] <= stop_at]
        if cur_end is not None:
            markers.append((stop_at, cur_end[1], cur_end[2], cur_end[3]))
        markers.append((round(stop_at + 0.001, 3), rec_end[1], rec_end[2], rec_end[3]))
        return phase_records, markers, stop_at

    if not interrupted:
        return phase_records, markers, total_duration

    # --interrupted: cut 40% into the FINAL phase — no RECORDING_END, no _END
    # for the phase in progress, the last phase record stays open (end=None).
    last = phase_records[-1]
    cutoff = last["start"] + 0.4 * (last["end"] - last["start"])
    last["end"] = None  # the open/interrupted phase
    markers = [m for m in markers if m[0] <= cutoff]
    return phase_records, markers, cutoff


# ─── Video (per camera) ────────────────────────────────────────────────────────

def _flash_frame_set(marker_times, start_offset, fps, n_frames):
    flashes = set()
    for mt in marker_times:
        rel = mt - start_offset
        if rel < 0:
            continue
        i0 = int(math.ceil(rel * fps))
        for i in range(i0, i0 + FLASH_FRAMES):
            if 0 <= i < n_frames:
                flashes.add(i)
    return flashes


def _render_frame(camera_label: str, frame_idx: int, t_session: float, flash: bool):
    """A plain picture with one small line of text in the TOP-RIGHT corner —
    the app draws the camera's name at top left and its state / timecode along
    the bottom, so nothing here sits under them."""
    img = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    if flash:
        img[:] = (255, 255, 255)
        color = (0, 0, 0)
    else:
        img[:] = (24, 24, 24)
        color = (170, 180, 200)
        x = int((frame_idx * 4) % (WIDTH - 40))
        img[:, x:x + 40] = (60, 70, 160)          # a slow moving bar: playback is visible
    text = f"{camera_label}  {t_session:.2f}s  f{frame_idx}"
    scale = 0.5
    (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
    cv2.putText(img, text, (WIDTH - tw - 16, 16 + th), cv2.FONT_HERSHEY_SIMPLEX, scale,
                color, 1, cv2.LINE_AA)
    return img


def _render_camera_worker(task: dict) -> dict:
    """Runs in its own process (see generate_demo_session's ProcessPoolExecutor
    below) — the four cameras are fully independent, and H.264 encoding is the
    dominant cost, so parallelizing across cameras keeps the default config
    fast. Everything random was already drawn deterministically in the parent
    process before dispatch (`start_offset` arrives as a plain float); nothing
    here uses randomness, so process scheduling order doesn't affect frame
    counts / sidecars.

    Uses the SAME FrameEncoder the live recorder uses (crash-safe fragmented
    H.264, row-per-encoded-frame sidecar), so the demo can't drift from the
    real on-disk format. The container is encoded at NOMINAL_FPS while the
    sidecar carries the true (possibly drifted) capture times — exactly what a
    real camera whose reported fps differs from its true rate produces.
    """
    label = task["label"]
    start_offset = task["start_offset"]
    capture_fps = task["capture_fps"]
    t0_monotonic = task["t0_monotonic"]
    t0_unix = task["t0_unix"]
    total_duration = task["total_duration"]
    marker_times = task["marker_times"]
    encoder = task["encoder"]
    mp4_path = Path(task["mp4_path"])
    mp4_path.parent.mkdir(parents=True, exist_ok=True)

    n_frames = max(0, round((total_duration - start_offset) * capture_fps))

    enc = FrameEncoder(mp4_path, WIDTH, HEIGHT, NOMINAL_FPS,
                       clock=(t0_monotonic, t0_unix), encoder=encoder)
    if not enc.start():
        raise RuntimeError(f"demo: could not start encoder for {label}")

    flashes = _flash_frame_set(marker_times, start_offset, capture_fps, n_frames)
    for i in range(n_frames):
        t_session = start_offset + i / capture_fps
        t_monotonic = t0_monotonic + t_session
        frame = _render_frame(label, i, t_session, flash=(i in flashes))
        if not enc.write_frame(frame, t_monotonic):
            raise RuntimeError(f"demo: encoder died writing {label} frame {i}")
    enc.close()

    return {
        "label": label, "path": str(mp4_path), "kind": task["kind"],
        "start_offset_sec": start_offset, "capture_fps": capture_fps,
        "declared_fps": NOMINAL_FPS, "n_frames": n_frames,
        "is_drift": task["is_drift"],
    }


# ─── EEG (per boxer band) ──────────────────────────────────────────────────────

def _synth_eeg_signal(rng, n_samples, sr, phase_windows, marker_times, start_offset,
                      weak=None):
    """Plausible fake 4-channel EEG + AUX. Not real physiology — a demo
    fixture: background noise, a bit of alpha, occasional frontal blink
    artifacts, 3x motion-artifact noise during ROUND phases, and a sharp
    spike in every channel at each phase-marker instant (the same instants
    the video flashes on, so eyes-on-glass can check both at once)."""
    channels = {ch: np.zeros(n_samples, dtype=np.float64) for ch in ("TP9", "AF7", "AF8", "TP10")}
    aux = np.zeros(n_samples, dtype=np.float64)
    if n_samples <= 0:
        return channels, aux

    t_local = np.arange(n_samples) / sr
    t_session = start_offset + t_local

    base_uv = 8.0
    for ch in channels:
        noise = rng.normal(0.0, base_uv, n_samples)
        alpha = 3.0 * np.sin(2 * np.pi * 10.0 * t_local + rng.uniform(0, 2 * np.pi))
        channels[ch] = noise + alpha
    aux[:] = rng.normal(0.0, base_uv * 0.5, n_samples)

    motion_scale = np.ones(n_samples)
    for p_start, p_end, kind in phase_windows:
        if kind != "round":
            continue
        motion_scale[(t_session >= p_start) & (t_session < p_end)] = 3.0
    for ch in channels:
        channels[ch] = channels[ch] * motion_scale

    # Occasional blink artifacts on the frontal channels.
    blink_t = start_offset + 2.0
    end_t = t_session[-1]
    while blink_t < end_t:
        local_idx = int(round((blink_t - start_offset) * sr))
        width = int(0.25 * sr)
        hi = min(n_samples, local_idx + width)
        if 0 <= local_idx < hi:
            w = np.arange(hi - local_idx)
            bump = 100.0 * np.sin(np.pi * w / max(1, len(w)))
            channels["AF7"][local_idx:hi] += bump
            channels["AF8"][local_idx:hi] += bump
        blink_t += rng.uniform(3.5, 6.5)

    # A poorly fitting sensor: much noisier for a stretch ("Worth checking").
    if weak is not None:
        ch, w0, w1 = weak
        sel = (t_session >= w0) & (t_session < w1)
        channels[ch][sel] += rng.normal(0.0, 70.0, int(sel.sum()))

    # Sharp spike in BOTH bands at every phase-marker instant.
    width = int(0.08 * sr)
    for mt in marker_times:
        local_idx = int(round((mt - start_offset) * sr))
        lo, hi = max(0, local_idx), min(n_samples, local_idx + width)
        if hi > lo:
            for ch in channels:
                channels[ch][lo:hi] += 180.0

    return channels, aux


def _generate_eeg_band(muse_dir, device_name, boxer_letter, rng, t0_unix,
                       total_duration, phase_windows, marker_times, weak=None):
    reader = _MuseDirectReader(address=f"demo-{boxer_letter}", name=device_name,
                               motion=True)
    fn = f"session_{_muse_friendly(device_name)}.csv"
    path = muse_dir / _ILLEGAL.sub("-", fn)

    start_offset = float(rng.uniform(0.2, 1.0))
    reader.start_recording(str(path), origin_unix=t0_unix)

    n_samples = max(0, round((total_duration - start_offset) * SR_EEG))
    channels, aux = _synth_eeg_signal(
        rng, n_samples, SR_EEG, phase_windows, marker_times, start_offset, weak=weak)

    chunk = 12
    for start in range(0, n_samples, chunk):
        end = min(start + chunk, n_samples)
        data = np.zeros((5, end - start), dtype=np.float32)
        data[0] = channels["TP9"][start:end]
        data[1] = channels["AF7"][start:end]
        data[2] = channels["AF8"][start:end]
        data[3] = channels["TP10"][start:end]
        data[4] = aux[start:end]
        ts = t0_unix + start_offset + np.arange(start, end) / SR_EEG
        reader._on_eeg(data, ts.astype(np.float64), arrival=float(ts[-1]))

    # Movement sensors: gravity + a bounce that is 4x bigger during rounds.
    for kind in ("ACC", "GYRO"):
        rate = SENSORS[kind][0]
        n = max(0, round((total_duration - start_offset) * rate))
        t_s = start_offset + np.arange(n) / rate
        moving = np.ones(n)
        for p_start, p_end, phase_kind in phase_windows:
            if phase_kind == "round":
                moving[(t_s >= p_start) & (t_s < p_end)] = 4.0
        bounce = np.sin(2 * np.pi * 1.5 * t_s) * moving
        if kind == "ACC":
            sig = np.array([[0.02], [0.05], [0.98]]) + 0.05 * bounce + rng.normal(0, 0.01, (3, n))
        else:
            sig = 10.0 * bounce + rng.normal(0, 2.0, (3, n))
        for start in range(0, n, 3):
            end = min(start + 3, n)
            reader._on_sensor(kind, sig[:, start:end].astype(np.float32),
                              t0_unix + t_s[start:end])

    written = reader.stop_recording()
    return {
        "device_name": device_name, "boxer": boxer_letter, "path": written,
        "start_offset_sec": start_offset, "n_samples": n_samples,
        "first_unix": reader._last_first_unix,
    }


# ─── Orchestration ─────────────────────────────────────────────────────────────

def _write_demo_log(layout) -> None:
    """The recording's ``session_<date>_<time>.log``, as the app leaves one —
    a few plain lines (no paths: nothing of this computer goes in a demo)."""
    started = layout.started_at
    name = f"session_{started:%Y%m%d_%H%M%S}.log"
    lines = [f"{started:%H:%M:%S} INFO    brain_battle.demo: demo recording made by "
             f"tools/generate_demo_session.py — simulated, no hardware",
             f"{started:%H:%M:%S} INFO    brain_battle.demo: 4 cameras, 2 headbands "
             f"({', '.join(app_settings._DEFAULT_HEADBANDS.values())})"]
    with open(os.path.join(layout.root, name), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


DEMO_OPERATOR = "Maya K"          # who "ran" the demo recordings (history)
DEMO_COMPUTER = "LAB-PC-1"         # the computer named in demo history lines


def generate_demo_session(out: str | None = None, rounds: int = 2, round_sec: int = 60,
                          break_sec: int = 20, baseline_sec: int = 20, seed: int = 0,
                          interrupted: bool = False,
                          boxer_a: str | None = "K7Q2", boxer_b: str | None = "M3XA",
                          colour_a: str = "Red", colour_b: str = "Blue",
                          legacy_names: tuple | None = None,
                          moments=None, calibration: dict | None = None,
                          started_at=None, stop_at: float | None = None,
                          labels: dict | None = None, weak: tuple | None = None,
                          lead_in_sec: int | None = None,
                          operator: str = DEMO_OPERATOR) -> dict:
    """Build one complete demo session folder. Returns a summary dict
    (session root, per-camera + per-band generation parameters, markers,
    total duration, interrupted flag) — used both by main()'s printed
    summary and by tests/test_demo_session.py's assertions.

    ``boxer_a`` / ``boxer_b`` are fighter codes (``None`` leaves them out);
    ``legacy_names=("Sam", "Alex")`` instead writes the manifest the way
    recordings made before fighter codes did (``{"name", "headband"}``) — only
    for tests of old recordings; the library never does. ``moments`` is a list
    of ``(t_session_seconds, note)`` "Mark this moment" presses (note may be
    ""). ``calibration`` is ``{device: correction_ms}`` — stored as the
    manifest's ``timing_calibration`` as if the buzzer test had been run.
    ``stop_at`` stops the recording early (cleanly) at that session time.
    ``labels`` is ``{labeller: [(label, who, t, t_end_or_None) | ("note", who,
    t, text)]}``, written to each labeller's own file. ``weak`` is ``(boxer,
    channel, start, end)``: that sensor is noisy and logged as poor in
    ``contact_quality.csv`` for that stretch. ``lead_in_sec`` defaults to the
    app's current lead-in (15 s; tests shorten it)."""
    global _qapp_ref
    _qapp_ref = QApplication.instance() or QApplication(sys.argv)

    import brain_battle.capture.session_controller as _sc
    saved_lead = _sc.LEAD_IN_SEC
    if lead_in_sec is not None:
        _sc.LEAD_IN_SEC = int(lead_in_sec)
    try:
        return _generate(out, rounds, round_sec, break_sec, baseline_sec, seed, interrupted,
                         boxer_a, boxer_b, colour_a, colour_b, legacy_names, moments,
                         calibration, started_at, stop_at, labels, weak, operator)
    finally:
        _sc.LEAD_IN_SEC = saved_lead


def _generate(out, rounds, round_sec, break_sec, baseline_sec, seed, interrupted,
              boxer_a, boxer_b, colour_a, colour_b, legacy_names, moments,
              calibration, started_at, stop_at, labels, weak, operator) -> dict:
    cfg = SessionConfig(num_rounds=rounds, round_sec=round_sec,
                        break_sec=break_sec, baseline_sec=baseline_sec)
    err = cfg.validate()
    if err:
        raise ValueError(err)

    import brain_battle.data.session_history as _sh
    import brain_battle.data.session_storage as _ss

    def hist(root, event, details=None, user=None):
        _sh.log_event(root, event, details, user=user or "Recording", computer=DEMO_COMPUTER)

    base_dir = out or _default_capture_root()
    os.makedirs(base_dir, exist_ok=True)
    # REAL writer: folder (named by the codes) + continuous/ + initial manifest
    headbands = dict(app_settings._DEFAULT_HEADBANDS)   # never this computer's settings
    import brain_battle.data.fighters as fighters
    colours = fighters.load_colours(base_dir)
    if legacy_names:
        boxer_a, boxer_b = legacy_names
        entries = ({"name": legacy_names[0], "headband": headbands["A"]},
                   {"name": legacy_names[1], "headband": headbands["B"]})
    else:
        entries = tuple(
            fighters.boxer_entry(code, colour, fighters.colour_hex(colour, colours), headbands[w])
            if code else {"code": None, "colour": None, "colour_hex": None,
                          "headband": headbands[w]}
            for w, code, colour in (("A", boxer_a, colour_a), ("B", boxer_b, colour_b)))
    layout = create_session(base_dir, cfg, started_at=started_at, demo=True,
                            boxer_a=entries[0], boxer_b=entries[1])
    _write_demo_log(layout)                              # every recording has its log

    origin = capture_clock.session_origin()            # the REAL clock origin
    t0_monotonic, t0_unix = origin["t0_monotonic"], origin["t0_unix"]
    layout.set_capture_origin(**origin)                # REAL writer

    phase_records, markers, total_duration = _phase_plan(cfg, interrupted, stop_at)
    stopped = stop_at is not None and abs(total_duration - stop_at) < 1e-9
    hist(layout.root, _sh.EVENT_RECORDING_STARTED,
         {"boxer_a": boxer_a, "boxer_b": boxer_b, "rounds": rounds})

    for rec in phase_records:
        layout.add_phase(PhaseRecord(               # REAL writer
            phase=rec["phase"].value, label=rec["label"], round_num=rec["round_num"],
            folder="continuous", planned_duration=int(round(rec["end"] - rec["start"]))
                if rec["end"] is not None else int(round(total_duration - rec["start"])),
            start_unix=t0_unix + rec["start"], start_monotonic=t0_monotonic + rec["start"],
            end_unix=(t0_unix + rec["end"]) if rec["end"] is not None else None,
            end_monotonic=(t0_monotonic + rec["end"]) if rec["end"] is not None else None,
        ))

    for (t_sec, phase_id, label, round_num) in markers:
        layout.append_marker(t_sec, phase_id, label, round_num)   # REAL writer
    _ss.write_moments_csv(layout.root, [])                         # every recording has one
    for t_sec, note in (moments or []):
        if t_sec <= total_duration:
            layout.add_moment(t_sec, note=note)                    # REAL writer
            hist(layout.root, _sh.EVENT_MOMENT_MARKED, {"t_session_seconds": t_sec})
    if calibration:
        import brain_battle.capture.timing as _timing
        beeps = [1.0 + 1.5 * i for i in range(_timing.N_BEEPS)]
        events = {dev: [b + 0.2 + c / 1000.0 for b in beeps] for dev, c in calibration.items()}
        cal = _timing.calibrate(beeps, events)
        layout.set_timing_calibration(cal)                         # REAL format
        hist(layout.root, _sh.EVENT_TIMING_CALIBRATED,
             {"largest_difference_ms": cal.get("largest_difference_ms")})
    if stopped:
        layout.mark_stopped_early(stop_at)                         # REAL writer

    marker_times = sorted({round(m[0], 6) for m in markers})   # one flash per instant
    rng = np.random.default_rng(seed)

    # Resolve the H.264 encoder ONCE here (the export_module probe) and pass
    # its name to the workers, so they don't each re-run the probe.
    ok, encoder, msg = preflight_recording()
    if not ok:
        raise RuntimeError(f"demo: no usable video encoder — {msg}")

    # Draw every random value sequentially in the parent process FIRST (one
    # fixed order, independent of how the camera workers below get scheduled)
    # so a given --seed is reproducible regardless of parallelism.
    tasks = []
    for idx, label, kind in _SLOT_SPECS:
        is_drift = (label == DRIFT_CAMERA_LABEL)
        sub = "Side Cams" if kind == "side" else "POV Cams"
        mp4_path = layout.continuous_dir() / sub / f"session_{label.replace(' ', '_')}.mp4"
        tasks.append({
            "idx": idx, "label": label, "kind": kind, "is_drift": is_drift,
            "start_offset": float(rng.uniform(0.05, 0.30)),
            "capture_fps": DRIFT_FPS if is_drift else NOMINAL_FPS,
            "t0_monotonic": t0_monotonic, "t0_unix": t0_unix,
            "total_duration": total_duration, "marker_times": marker_times,
            "mp4_path": str(mp4_path), "encoder": encoder,
        })

    # The four cameras are independent and H.264 encoding dominates runtime —
    # parallelize across processes to keep the default config fast.
    import concurrent.futures as _fut
    max_workers = min(4, os.cpu_count() or 4)
    with _fut.ProcessPoolExecutor(max_workers=max_workers) as pool:
        cameras = list(pool.map(_render_camera_worker, tasks))
    layout.set_camera_fps({c["label"].replace(" ", "_"):                # REAL writer
                           {"requested": NOMINAL_FPS, "actual": round(c["capture_fps"], 2)}
                           for c in cameras})

    muse_dir = layout.continuous_dir() / "Muse data"
    muse_dir.mkdir(parents=True, exist_ok=True)
    phase_windows = [(r["start"], r["end"] if r["end"] is not None else total_duration,
                     r["phase"].value) for r in phase_records]

    eeg_bands = []
    for eeg_idx, (boxer_letter, device_name) in enumerate(headbands.items(), start=1):
        if not device_name:
            continue
        band_weak = (weak[1], weak[2], weak[3]) if weak and weak[0] == boxer_letter else None
        info = _generate_eeg_band(
            muse_dir, device_name, boxer_letter, rng, t0_unix,
            total_duration, phase_windows, marker_times, weak=band_weak)
        eeg_bands.append(info)
        if info["first_unix"] is not None:
            layout.record_source_start(f"EEG_{eeg_idx:02d}", info["first_unix"])  # REAL writer

    # contact_quality.csv, once a second per boxer, with the REAL writer
    from brain_battle.capture import contact_log as _clog
    from brain_battle.capture.contact_quality import CHANNELS as _CH, summarise as _summary
    cw = _clog.ContactLogWriter(_clog.path_for(layout.root))
    for sec in range(1, int(total_duration) + 1):
        for who in ("A", "B"):
            st = {ch: "good" for ch in _CH}
            if weak and weak[0] == who and weak[2] <= sec < weak[3]:
                st[weak[1]] = "noisy"
            cw.append(float(sec), who, _summary(st), st)
    cw.close()

    # timing_quality.csv, once a second per device, with the REAL writer
    import brain_battle.capture.timing as _timing
    import brain_battle.capture.timing_log as _tlog
    tw = _tlog.TimingQualityWriter(_tlog.path_for(layout.root))
    corr = _timing.corrections(_ss._read_manifest(layout.root) or {})
    devices = [(c["label"].replace(" ", "_"), c["capture_fps"], 1.0) for c in cameras] + \
              [(f"EEG_{e['boxer']}", float(SR_EEG), 3.5) for e in eeg_bands]
    for sec in range(1, int(total_duration) + 1):
        for dev, rate, jit in devices:
            off = corr.get(dev)
            tw.append(float(sec), dev, rate, jit + float(rng.uniform(0, 0.4)), 0,
                      None if off is None else off * 1000.0)
    tw.close()

    # Labels: each labeller's own file, through the REAL store
    import brain_battle.data.labels_store as _ls
    import brain_battle.data.label_vocab as _lv
    vocab = {e["name"]: e for e in _lv.load(base_dir)}
    for labeller, items in (labels or {}).items():
        store = _ls.open_for_labeller(layout.root, labeller)
        for item in items:
            if item[0] == "note":
                _, who, t, text = item
                store.add(_ls.new_note(text, who, float(t), created_by=labeller))
            else:
                name, who, t, t_end = item
                e = vocab.get(name, {})
                store.add(_ls.new_label(name, who, _ls.DURATION if t_end else _ls.POINT,
                                        float(t), t_end=t_end, color=e.get("color", "#3D4FD6"),
                                        created_by=labeller))
        store.save()
        hist(layout.root, _sh.EVENT_LABELS_ADDED, {"count": len(items)}, user=labeller)

    if interrupted:
        hist(layout.root, _sh.EVENT_RECORDING_INTERRUPTED, {"how": "crash"})
    else:
        hist(layout.root, _sh.EVENT_RECORDING_FINISHED,
             {"stopped_early": bool(stopped), "moments": len(moments or [])})

    # A crashed recording has no recording.xdf until the app finds it again and
    # rebuilds it, so the --interrupted fixture leaves it out too.
    xdf = None if interrupted else session_xdf.build_session_xdf(layout.root)   # REAL builder

    return {
        "root": str(layout.root),
        "xdf": xdf,
        "total_duration_sec": total_duration,
        "interrupted": interrupted,
        "stopped_early": bool(stopped),
        "drift_camera": DRIFT_CAMERA_LABEL,
        "cameras": cameras,
        "eeg_bands": eeg_bands,
        "markers": markers,
        "seed": seed,
        "boxer_a": boxer_a,
        "boxer_b": boxer_b,
    }


# ─── Library (a folder of sessions covering every status) ───────────────────────

CAL_GOOD = {"SIDE_01": 52, "SIDE_02": 49, "POV_01": 63, "POV_02": 57, "EEG_A": 31, "EEG_B": 36}
LABELLER_1, LABELLER_2 = "Maya K", "Jonas R"      # demo labellers (fighters never have names)


def generate_library(out: str | None = None, seed: int = 0) -> list[dict]:
    """Fill `out` with 6 short recordings that look like the current app made
    them, covering every Sessions status and everything a tester should see:

      1. K7Q2 vs M3XA — Labelled by two labellers (Compare + agreement), preset
         labels and custom notes, moments with notes, buzzer test done, and a
         weak right-ear signal on Boxer B for ~40 s in Round 2 ("Worth checking").
      2. R2DT vs B9LN — Needs labels: moments, one still without a note.
      3. Z8PC vs J4WE — Exported (labelled, then exported for analysis).
      4. M3XA vs Z8PC — Interrupted (the app stopped mid-round).
      5. B9LN vs K7Q2 — Stopped early, no buzzer test ("Timing not measured").
      6. J4WE vs R2DT — Labelled by one labeller, with a custom note.

    Fighters are codes and colours only. Every recording has the 15 s lead-in
    and the automatic markers. The folder also gets the team's shared
    ``label_list.json`` (the current default) and ``fighter_colours.json``.
    Deterministic for a given seed; dated across recent days."""
    import datetime as _dt
    import brain_battle.data.session_storage as _ss
    import brain_battle.data.session_history as _sh
    import brain_battle.data.label_vocab as _lv
    import brain_battle.data.fighters as _fighters
    from brain_battle.capture.session_controller import LEAD_IN_DEFAULT

    base = out or _default_capture_root()
    os.makedirs(base, exist_ok=True)
    _lv.load(base)                                           # the team's label list
    _fighters.save_colours(base, _fighters.load_colours(base))   # the team's colours
    short = dict(rounds=1, round_sec=12, break_sec=0, baseline_sec=5, lead_in_sec=LEAD_IN_DEFAULT)

    now = _dt.datetime.now().replace(microsecond=0)
    def when(days_ago, hour, minute):
        return (now - _dt.timedelta(days=days_ago)).replace(hour=hour, minute=minute, second=0)

    results = []
    # Lead-in 15 s + baseline: rounds start at lead_in + baseline.
    L = LEAD_IN_DEFAULT

    # 1) the showcase: 2 rounds, two labellers, weak signal, timing measured
    b, rs, br = 8, 45, 10
    r1 = L + b                      # Round 1 starts
    r2 = r1 + rs + br               # Round 2 starts
    r = generate_demo_session(
        out=base, seed=seed, boxer_a="K7Q2", boxer_b="M3XA", colour_a="Red", colour_b="Blue",
        rounds=2, round_sec=rs, break_sec=br, baseline_sec=b, lead_in_sec=L,
        moments=[(r1 + 12.0, "B dropped the guard after a combination"),
                 (r2 + 20.0, "A slipped on the canvas")],
        calibration=CAL_GOOD, weak=("B", "TP10", r2 + 2.0, r2 + 42.0),
        labels={
            LABELLER_1: [("Punch initiated", "A", r1 + 3.0, None), ("Punch landed", "A", r1 + 3.4, None),
                         ("Punch blocked", "B", r1 + 8.0, None), ("Clinch", "Both", r1 + 20.0, r1 + 26.0),
                         ("Punch missed", "B", r2 + 5.0, None), ("Punch dodged", "A", r2 + 5.3, None),
                         ("Referee break", "Both", r2 + 30.0, r2 + 33.0),
                         ("note", "A", r2 + 12.0, "A keeps pressing forward, B backs onto the ropes")],
            LABELLER_2: [("Punch initiated", "A", r1 + 3.2, None), ("Punch landed", "A", r1 + 3.5, None),
                         ("Punch missed", "B", r1 + 8.1, None), ("Clinch", "Both", r1 + 20.3, r1 + 25.5),
                         ("Punch missed", "B", r2 + 5.2, None),
                         ("note", "Both", r2 + 31.0, "Referee separates them after a long clinch")],
        },
        started_at=when(5, 14, 5))
    r["status"] = "labelled · 2 labellers · worth checking"; results.append(r)

    # 2) needs labels: one moment still without a note
    r = generate_demo_session(out=base, seed=seed + 1, boxer_a="R2DT", boxer_b="B9LN",
                              colour_a="Black", colour_b="Red", calibration=CAL_GOOD,
                              moments=[(L + 8.0, "R2DT covered up after a jab"), (L + 13.0, "")],
                              started_at=when(4, 9, 30), **short)
    r["status"] = "needs labels · a moment without a note"; results.append(r)

    # 3) exported
    r = generate_demo_session(out=base, seed=seed + 2, boxer_a="Z8PC", boxer_b="J4WE",
                              colour_a="Blue", colour_b="Red", calibration=CAL_GOOD,
                              moments=[(L + 9.0, "Clean exchange")],
                              labels={LABELLER_2: [("Punch initiated", "B", L + 7.0, None),
                                                   ("Punch landed", "B", L + 7.3, None),
                                                   ("Knockdown", "A", L + 14.0, None)]},
                              started_at=when(3, 11, 45), **short)
    _sh.log_event(r["root"], _sh.EVENT_EXPORT_CREATED,
                  {"name": f"{os.path.basename(r['root'])}.zip"}, user=LABELLER_2,
                  computer=DEMO_COMPUTER)
    r["status"] = "exported"; results.append(r)

    # 4) interrupted (the app stopped mid-round)
    r = generate_demo_session(out=base, seed=seed + 3, interrupted=True,
                              boxer_a="M3XA", boxer_b="Z8PC", colour_a="Blue", colour_b="Black",
                              calibration=CAL_GOOD, started_at=when(2, 16, 20), **short)
    _ss.mark_session_interrupted(r["root"])          # as the app leaves it once found:
    r["xdf"] = session_xdf.ensure_session_xdf(r["root"])   # flagged, recording.xdf rebuilt
    r["status"] = "interrupted"; results.append(r)

    # 5) stopped early, no buzzer test → "Timing not measured"
    short2 = dict(short, rounds=2, break_sec=5)
    r = generate_demo_session(out=base, seed=seed + 4, boxer_a="B9LN", boxer_b="K7Q2",
                              colour_a="Red", colour_b="Blue", calibration=None,
                              stop_at=L + 5 + 12 + 5 + 4.0, started_at=when(1, 10, 0), **short2)
    r["status"] = "stopped early · timing not measured"; results.append(r)

    # 6) labelled by one labeller, with a custom note
    r = generate_demo_session(out=base, seed=seed + 5, boxer_a="J4WE", boxer_b="R2DT",
                              colour_a="Black", colour_b="Blue", calibration=CAL_GOOD,
                              moments=[(L + 10.0, "J4WE switched stance")],
                              labels={LABELLER_1: [("Punch initiated", "A", L + 6.0, None),
                                                   ("Punch blocked", "B", L + 6.3, None),
                                                   ("note", "B", L + 11.0, "R2DT circles away to the left")]},
                              started_at=when(0, 13, 15), **short)
    r["status"] = "labelled · 1 labeller"; results.append(r)
    return results


# ─── CLI ────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=None, required=True, help="output base dir "
                   "(REQUIRED — a demo must never land in your real recordings "
                   "folder by default)")
    p.add_argument("--force", action="store_true",
                   help="allow --out to be the configured recordings folder "
                        "(refused without this, so demo data isn't mistaken for real)")
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--round-sec", type=int, default=60)
    p.add_argument("--break-sec", type=int, default=20)
    p.add_argument("--baseline-sec", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--interrupted", action="store_true",
                   help="generate a session that looks like the app crashed mid-round: "
                        "no RECORDING_END marker, last phase has no end time, "
                        "EEG and video cut off early")
    p.add_argument("--boxer-a", default="K7Q2", help="Boxer A's 4-character code")
    p.add_argument("--boxer-b", default="M3XA", help="Boxer B's 4-character code")
    p.add_argument("--stopped-early", type=float, default=None, metavar="SECONDS",
                   help="stop the recording early (cleanly) at this session time")
    p.add_argument("--library", action="store_true",
                   help="fill --out with 6 short recordings covering every Sessions status "
                        "(labelled by two labellers, needs labels, exported, interrupted, "
                        "stopped early without a buzzer test, labelled by one)")
    return p


def _refuse_real_folder(out: str, force: bool) -> str | None:
    """Return an error message if `out` is the configured recordings folder and
    --force was not given; else None. Keeps demo data out of real recordings."""
    try:
        real = os.path.abspath(_default_capture_root())
        if os.path.abspath(out) == real and not force:
            return (f"Refusing to write a demo into your recordings folder "
                    f"({real}). Pass a different --out, or --force if you really "
                    f"mean it.")
    except Exception:
        pass
    return None


def main(argv=None):
    from brain_battle.capture.session_controller import LEAD_IN_DEFAULT
    args = _build_arg_parser().parse_args(argv)

    err = _refuse_real_folder(args.out, args.force)
    if err:
        print(err, file=sys.stderr)
        return 2

    if args.library:
        rows = generate_library(out=args.out, seed=args.seed)
        print(f"Demo library created ({len(rows)} recordings):")
        for r in rows:
            print(f"  [{r['status']:<42}] {os.path.basename(r['root'])}")
        return 0

    try:
        result = generate_demo_session(
            out=args.out, rounds=args.rounds, round_sec=args.round_sec,
            break_sec=args.break_sec, baseline_sec=args.baseline_sec,
            seed=args.seed, interrupted=args.interrupted,
            boxer_a=args.boxer_a, boxer_b=args.boxer_b,
            stop_at=args.stopped_early, lead_in_sec=LEAD_IN_DEFAULT,
            calibration=CAL_GOOD,
        )
    except ValueError as e:
        print(f"Invalid session config: {e}", file=sys.stderr)
        return 1

    print(f"Demo session created at: {result['root']}")
    print(f"  total duration: {result['total_duration_sec']:.1f}s"
          f"{'  (INTERRUPTED — no RECORDING_END)' if result['interrupted'] else ''}")
    print(f"  cameras ({len(result['cameras'])}):")
    for c in result["cameras"]:
        drift = "  <-- DRIFT (29.5fps true vs 30fps declared)" if c["is_drift"] else ""
        print(f"    {c['label']:<8} start+{c['start_offset_sec']*1000:.0f}ms  "
              f"{c['n_frames']} frames @ {c['capture_fps']:.1f}fps true{drift}")
    print(f"  EEG bands ({len(result['eeg_bands'])}):")
    for e in result["eeg_bands"]:
        print(f"    {e['device_name']:<12} ({e['boxer']})  start+{e['start_offset_sec']*1000:.0f}ms  "
              f"{e['n_samples']} samples -> {e['path']}")
    print(f"  markers: {len(result['markers'])}")
    if result.get("xdf"):
        print(f"  everything on one clock: {result['xdf']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
