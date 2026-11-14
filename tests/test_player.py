"""
pyav_player tests — the single-clock, app-driven playback.

Covers:
  1. Single-clock correctness through the real decoder: at several playheads
     (incl. the drift camera near the end), each tile's frame-for-t has sidecar
     time within one frame of t AND decodes pixel-identically to a ground-truth
     sequential decode.
  2. Real-time pacing: ~10 s of 1× playback advances the playhead ~10 s (±2 %),
     because the playhead is clock-based, not tick-count-based. Reports achieved
     paint fps per tile.
  3. Scrub coalescing: a flurry of 50 seeks costs only a couple of decodes and
     ends on the last requested position.

The demo (with the deliberately drifting POV 01 camera) is generated ONCE in
the __main__ guard (generate_demo_session uses a ProcessPoolExecutor whose
children re-import this module).

Run:  python3 tests/test_player.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import os
import sys
import time
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

import brain_battle.playback.pyav_player as pp

_failures = []
DEMO = None       # set in __main__
_fps_report = {}


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _camera_specs(root):
    specs = []
    cont = os.path.join(root, "continuous")
    for sub in ("Side Cams", "POV Cams"):
        d = os.path.join(cont, sub)
        if os.path.isdir(d):
            for fn in sorted(os.listdir(d)):
                if fn.endswith(".mp4"):
                    specs.append((os.path.join(d, fn), fn))
    return specs


def _ground_truth_frame(mp4_path, index):
    """Decode the file from the start and return the index-th frame (rgb24)."""
    import av
    with av.open(mp4_path) as container:
        stream = container.streams.video[0]
        i = 0
        for frame in container.decode(stream):
            if i == index:
                return frame.to_ndarray(format="rgb24")
            i += 1
    return None


# ── 1. single-clock correctness ────────────────────────────────────────────────

def test_single_clock_correctness():
    specs = _camera_specs(DEMO["root"])
    check("found 4 cameras", len(specs) == 4, str(len(specs)))
    cams = [pp.PyAVCamera(p, n) for p, n in specs]
    dur = max(c.last_t for c in cams)

    playheads = [0.3, dur * 0.25, dur * 0.5, dur * 0.75, dur - 0.05]
    worst_ms = 0.0
    for t in playheads:
        for cam in cams:
            idx = cam.index_for_t(t)
            if idx is None:
                continue
            # sidecar time within one frame of t
            err = abs(cam.t_for_index(idx) - t)
            worst_ms = max(worst_ms, err * 1000)
            check(f"{cam.name} @ {t:.2f}s: within one frame",
                  err <= (1.0 / 29.0) + 1e-6, f"{err*1000:.1f}ms")
            # decoded frame is pixel-identical to a ground-truth decode
            got = cam.decode_index(idx)
            truth = _ground_truth_frame(cam.path, idx)
            same = got is not None and truth is not None and np.array_equal(got, truth)
            check(f"{cam.name} @ {t:.2f}s: decode == ground truth (frame {idx})", same)

    # the drift camera specifically, at its very last frame
    drift = next((c for c in cams if "POV_01" in c.path or "POV 01" in c.name), None)
    if drift is not None:
        last = drift.n_frames - 1
        got = drift.decode_index(last)
        truth = _ground_truth_frame(drift.path, last)
        check("drift camera last frame decodes correctly",
              got is not None and np.array_equal(got, truth),
              f"frame {last}, true t={drift.last_t:.2f}s")
    print(f"      worst frame-selection error across all playheads: {worst_ms:.1f} ms")
    for c in cams:
        c.close()


# ── 2. real-time pacing + fps ───────────────────────────────────────────────────

def test_realtime_pacing():
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import QEventLoop, QTimer
    app = QApplication.instance() or QApplication(sys.argv)

    specs = _camera_specs(DEMO["root"])
    decoder = pp.MultiCamDecoder([pp.PyAVCamera(p, n) for p, n in specs])
    ctl = pp.PlayerController(decoder, decoder.duration)

    paints = {"n": 0, "tiles": 0}
    def on_frames(t, frames):
        paints["n"] += 1
        paints["tiles"] += len(frames)
    ctl.frames_ready.connect(on_frames)

    WINDOW = 10.0
    ctl.seek(0.0)
    t_start = ctl.t
    wall0 = time.monotonic()
    ctl.play()
    loop = QEventLoop()
    QTimer.singleShot(int(WINDOW * 1000), loop.quit)
    loop.exec()
    wall = time.monotonic() - wall0
    advanced = ctl.t - t_start
    ctl.pause()

    err_pct = abs(advanced - wall) / wall * 100
    check(f"playhead advanced ~{wall:.1f}s in real time (±2%)",
          err_pct <= 2.0, f"advanced {advanced:.2f}s vs wall {wall:.2f}s ({err_pct:.2f}%)")

    fps_per_tile = paints["n"] / wall
    _fps_report["paint_fps_per_tile"] = fps_per_tile
    _fps_report["batches"] = paints["n"]
    _fps_report["duration"] = decoder.duration
    print(f"      paint fps per tile: {fps_per_tile:.1f}  "
          f"({paints['n']} 4-cam batches over {wall:.1f}s, demo {decoder.duration:.0f}s)")
    check("playback actually painted frames", paints["n"] > 0)
    ctl.shutdown()


# ── 3. scrub coalescing ─────────────────────────────────────────────────────────

def test_scrub_coalescing():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)

    specs = _camera_specs(DEMO["root"])
    decoder = pp.MultiCamDecoder([pp.PyAVCamera(p, n) for p, n in specs])
    worker = pp.DecodeWorker(decoder)
    worker.start()

    dur = decoder.duration
    positions = [dur * (i / 49.0) for i in range(50)]
    for t in positions:
        worker.request(t)
    last = positions[-1]

    # let it settle
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if worker.last_t == last and worker.decode_count >= 1:
            # give it a beat to make sure nothing else is pending
            time.sleep(0.05)
            break
        time.sleep(0.01)

    check("coalesced 50 seeks into a few decodes",
          worker.decode_count <= 5, f"{worker.decode_count} decodes")
    check("ended on the last requested position",
          worker.last_t == last, f"last_t={worker.last_t} want {last}")
    worker.stop()
    decoder.close()


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    out = tempfile.mkdtemp(prefix="bb_player_")
    # ~36 s so the drift accumulates visibly toward the end
    DEMO = generate_demo_session(out=out, rounds=1, round_sec=30,
                                 break_sec=0, baseline_sec=6, seed=3)
    test_single_clock_correctness()
    test_realtime_pacing()
    test_scrub_coalescing()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
