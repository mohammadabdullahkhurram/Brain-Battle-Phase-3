"""
generate_demo_session.py
─────────────────────────
Generates a realistic fake Brain Battle v3 session folder so the rest of the
app can be built and tested without hardware (see CLAUDE.md "Project
situation" — real cameras/Muse-S bands are currently unavailable).

What's REUSED from the real pipeline (so the demo can never silently drift
from the format the app actually reads/writes):
  - `session_storage.create_session` / `SessionLayout.set_capture_origin` /
    `.append_marker` / `.add_phase` / `.record_source_start` — the EXACT
    manifest + phase_markers.csv writers the live app uses.
  - `session_controller._build_timeline` — the EXACT phase-plan logic
    (baseline → rounds → breaks, no break after the last round) a live
    session would run.
  - `usb_camera.USBCameraThread._open_timestamp_sidecar` /
    `._stamp_frame` / `._close_timestamp_sidecar` — the EXACT per-frame
    sidecar CSV writer. The thread is constructed but never `.start()`ed
    (no real camera, no QThread spun up) — we just call its private
    sidecar-writing methods directly with synthetic timestamps.
  - `eeg_module._MuseDirectReader._on_eeg` / `.start_recording` /
    `.stop_recording` — the EXACT per-chunk accumulation + CSV flush path
    the real BLE pipeline uses (the same technique
    `tests/test_capture_clock.py::test_reader_writes_t_session_column`
    already proved works headlessly). We feed it synthetic 12-sample
    chunks instead of real BLE callbacks.
  - `usb_camera_panel._SLOT_SPECS`, `eeg_module.BOXER_LABELS`, and
    `live_session_tab._muse_friendly`/`._default_capture_root` — so camera
    labels, boxer↔device mapping, and filenames can never diverge from the
    real app's naming.

What's RE-IMPLEMENTED here (no headless-callable equivalent exists):
  - The actual video FRAME pixels (burned-in text, phase-marker flashes) —
    there is no synthetic-frame generator anywhere in the real pipeline;
    `USBCameraThread.run()`'s frame loop is tied to a real `cv2.VideoCapture`.
    We still write frames through `cv2.VideoWriter` with the SAME fourcc
    (`mp4v`) and resolution (960x540) the real app uses, so the container
    format matches exactly.
  - The synthetic EEG SIGNAL itself (noise/alpha/blink/motion/marker-spike) —
    there is no synthetic-EEG generator in `eeg_module.py` to reuse; this is
    plausible fake data, not real physiology.
  - The PhaseRecord start/end analytics timestamps and the marker CSV's
    `unix_time` column are stamped against the synthetic session timeline we
    compute here (not real wall-clock generation time) — straightforward
    arithmetic, not pipeline logic, so there's nothing to "reuse" for it.
    Nothing in the alignment/review path reads these fields; only
    `capture_clock.t0_unix`/`t0_monotonic` (set via the real
    `set_capture_origin`) and each source's own `t_session_seconds` matter,
    and those ARE on one consistent synthetic axis throughout.

Usage:
    python3 tools/generate_demo_session.py
    python3 tools/generate_demo_session.py --out /tmp/demo --rounds 3 --seed 7
    python3 tools/generate_demo_session.py --interrupted

Deterministic for a given --seed. Default config (baseline 20s, 2 rounds of
60s with a 20s break between = 160s of session time) generates in well
under a minute on ordinary hardware.
"""
from __future__ import annotations

import argparse
import math
import os
import re
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import numpy as np
import cv2

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication  # noqa: E402

from session_controller import SessionConfig, Phase, _build_timeline  # noqa: E402
from session_storage import create_session, PhaseRecord  # noqa: E402
from usb_camera import CameraDevice, USBCameraThread  # noqa: E402
from usb_camera_panel import _SLOT_SPECS  # noqa: E402
from eeg_module import _MuseDirectReader, BOXER_LABELS  # noqa: E402
from live_session_tab import _muse_friendly, _default_capture_root  # noqa: E402

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

def _phase_plan(cfg: SessionConfig, interrupted: bool):
    """Walk the REAL _build_timeline() output and compute, on one synthetic
    axis, every phase's (start, end) elapsed time, the marker rows a live
    session would write, and — for --interrupted — where to cut it off.

    Returns (phase_records, markers, total_duration) where:
      phase_records: list of dict(phase=Phase, label, round_num, start, end_or_None)
      markers:       list of (t_session_sec, phase_id, label, round_num)
      total_duration: the (possibly truncated) session length in seconds
    """
    timeline = _build_timeline(cfg)
    phase_records = []
    markers = []
    elapsed = 0.0
    for info in timeline:
        marker_label = f"{info.label.upper()}_START"
        markers.append((elapsed, info.phase.value, marker_label, info.round_num))
        phase_records.append({
            "phase": info.phase, "label": info.label, "round_num": info.round_num,
            "start": elapsed, "end": elapsed + info.duration,
        })
        elapsed += info.duration
    total_duration = elapsed

    if not interrupted:
        markers.append((total_duration, "done", "SESSION_END", 0))
        return phase_records, markers, total_duration

    # --interrupted: cut 40% into the FINAL phase — no SESSION_END, the last
    # phase record stays open (end=None), later phase records never existed.
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
    img = np.empty((HEIGHT, WIDTH, 3), dtype=np.uint8)
    if flash:
        img[:] = (255, 255, 255)
        color = (0, 0, 0)
    else:
        img[:] = (24, 24, 24)
        color = (255, 255, 255)
    cv2.putText(img, camera_label, (30, 90), cv2.FONT_HERSHEY_SIMPLEX, 1.4, color, 3, cv2.LINE_AA)
    cv2.putText(img, f"frame {frame_idx}", (30, 170), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
    cv2.putText(img, f"t_session={t_session:.3f}s", (30, 230), cv2.FONT_HERSHEY_SIMPLEX, 1.0, color, 2, cv2.LINE_AA)
    return img


def _render_camera_worker(task: dict) -> dict:
    """Runs in its own process (see generate_demo_session's ProcessPoolExecutor
    below) — the four cameras are fully independent, and `cv2.VideoWriter`'s
    mp4v encode is the dominant cost (~4-5 ms/frame), so parallelizing across
    cameras is what keeps the default config under a minute. Everything
    random was already drawn deterministically in the parent process before
    dispatch (`start_offset` arrives as a plain float); nothing here uses
    randomness, so process scheduling order doesn't affect determinism.
    """
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication as _QApp
    global _qapp_ref
    _qapp_ref = _QApp.instance() or _QApp([])

    label = task["label"]
    idx = task["idx"]
    start_offset = task["start_offset"]
    capture_fps = task["capture_fps"]
    t0_monotonic = task["t0_monotonic"]
    t0_unix = task["t0_unix"]
    total_duration = task["total_duration"]
    marker_times = task["marker_times"]
    mp4_path = Path(task["mp4_path"])
    mp4_path.parent.mkdir(parents=True, exist_ok=True)

    n_frames = max(0, round((total_duration - start_offset) * capture_fps))

    dev = CameraDevice(index=idx - 1, name=label)
    cam_thread = USBCameraThread(dev)
    cam_thread._rec_clock = (t0_monotonic, t0_unix)
    cam_thread._open_timestamp_sidecar(mp4_path)

    fourcc = cv2.VideoWriter_fourcc(*"mp4v")   # same fourcc usb_camera.py writes
    writer = cv2.VideoWriter(str(mp4_path), fourcc, NOMINAL_FPS, (WIDTH, HEIGHT))

    flashes = _flash_frame_set(marker_times, start_offset, capture_fps, n_frames)
    for i in range(n_frames):
        t_session = start_offset + i / capture_fps
        t_monotonic = t0_monotonic + t_session
        frame = _render_frame(label, i, t_session, flash=(i in flashes))
        writer.write(frame)
        cam_thread._stamp_frame(t_monotonic)

    writer.release()
    cam_thread._close_timestamp_sidecar()

    return {
        "label": label, "path": str(mp4_path), "kind": task["kind"],
        "start_offset_sec": start_offset, "capture_fps": capture_fps,
        "declared_fps": NOMINAL_FPS, "n_frames": n_frames,
        "is_drift": task["is_drift"],
    }


# ─── EEG (per boxer band) ──────────────────────────────────────────────────────

def _synth_eeg_signal(rng, n_samples, sr, phase_windows, marker_times, start_offset):
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
                       total_duration, phase_windows, marker_times):
    reader = _MuseDirectReader(address=f"demo-{boxer_letter}", name=device_name)
    fn = f"session_{_muse_friendly(device_name)}.csv"
    path = muse_dir / _ILLEGAL.sub("-", fn)

    start_offset = float(rng.uniform(0.2, 1.0))
    reader.start_recording(str(path), origin_unix=t0_unix)

    n_samples = max(0, round((total_duration - start_offset) * SR_EEG))
    channels, aux = _synth_eeg_signal(
        rng, n_samples, SR_EEG, phase_windows, marker_times, start_offset)

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
        reader._on_eeg(data, ts.astype(np.float64))

    written = reader.stop_recording()
    return {
        "device_name": device_name, "boxer": boxer_letter, "path": written,
        "start_offset_sec": start_offset, "n_samples": n_samples,
        "first_unix": reader._last_first_unix,
    }


# ─── Orchestration ─────────────────────────────────────────────────────────────

def generate_demo_session(out: str | None = None, rounds: int = 2, round_sec: int = 60,
                          break_sec: int = 20, baseline_sec: int = 20, seed: int = 0,
                          interrupted: bool = False) -> dict:
    """Build one complete demo session folder. Returns a summary dict
    (session root, per-camera + per-band generation parameters, markers,
    total duration, interrupted flag) — used both by main()'s printed
    summary and by tests/test_demo_session.py's assertions."""
    global _qapp_ref
    _qapp_ref = QApplication.instance() or QApplication(sys.argv)

    cfg = SessionConfig(num_rounds=rounds, round_sec=round_sec,
                        break_sec=break_sec, baseline_sec=baseline_sec)
    err = cfg.validate()
    if err:
        raise ValueError(err)

    base_dir = out or _default_capture_root()
    os.makedirs(base_dir, exist_ok=True)
    layout = create_session(base_dir, cfg)   # REAL writer: folder + continuous/ + initial manifest

    t0_monotonic = time.monotonic()
    t0_unix = time.time()
    layout.set_capture_origin(t0_monotonic, t0_unix)   # REAL writer

    phase_records, markers, total_duration = _phase_plan(cfg, interrupted)

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

    marker_times = [m[0] for m in markers]
    rng = np.random.default_rng(seed)

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
            "mp4_path": str(mp4_path),
        })

    # The four cameras are independent and mp4v encoding dominates runtime —
    # parallelize across processes so the default config stays well under a
    # minute (serial generation of ~19k frames takes ~100s on this machine).
    import concurrent.futures as _fut
    max_workers = min(4, os.cpu_count() or 4)
    with _fut.ProcessPoolExecutor(max_workers=max_workers) as pool:
        cameras = list(pool.map(_render_camera_worker, tasks))

    muse_dir = layout.continuous_dir() / "Muse data"
    muse_dir.mkdir(parents=True, exist_ok=True)
    phase_windows = [(r["start"], r["end"] if r["end"] is not None else total_duration,
                     r["phase"].value) for r in phase_records]

    eeg_bands = []
    for eeg_idx, (device_name, boxer_label) in enumerate(BOXER_LABELS.items(), start=1):
        boxer_letter = boxer_label.replace("BOXER ", "")
        info = _generate_eeg_band(
            muse_dir, device_name, boxer_letter, rng, t0_unix,
            total_duration, phase_windows, marker_times)
        eeg_bands.append(info)
        if info["first_unix"] is not None:
            layout.record_source_start(f"EEG_{eeg_idx:02d}", info["first_unix"])  # REAL writer

    return {
        "root": str(layout.root),
        "total_duration_sec": total_duration,
        "interrupted": interrupted,
        "drift_camera": DRIFT_CAMERA_LABEL,
        "cameras": cameras,
        "eeg_bands": eeg_bands,
        "markers": markers,
        "seed": seed,
    }


# ─── CLI ────────────────────────────────────────────────────────────────────────

def _build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", default=None, help="output base dir "
                   "(default: BRAIN_BATTLE_CAPTURE_DIR or the app's own Captures/ folder)")
    p.add_argument("--rounds", type=int, default=2)
    p.add_argument("--round-sec", type=int, default=60)
    p.add_argument("--break-sec", type=int, default=20)
    p.add_argument("--baseline-sec", type=int, default=20)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--interrupted", action="store_true",
                   help="generate a session that looks like the app crashed mid-round: "
                        "no SESSION_END marker, last phase has no end time, "
                        "EEG and video cut off early")
    return p


def main(argv=None):
    args = _build_arg_parser().parse_args(argv)
    try:
        result = generate_demo_session(
            out=args.out, rounds=args.rounds, round_sec=args.round_sec,
            break_sec=args.break_sec, baseline_sec=args.baseline_sec,
            seed=args.seed, interrupted=args.interrupted,
        )
    except ValueError as e:
        print(f"Invalid session config: {e}", file=sys.stderr)
        return 1

    print(f"Demo session created at: {result['root']}")
    print(f"  total duration: {result['total_duration_sec']:.1f}s"
          f"{'  (INTERRUPTED — no SESSION_END)' if result['interrupted'] else ''}")
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
