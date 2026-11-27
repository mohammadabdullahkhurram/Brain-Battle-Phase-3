"""
Timing check and buzzer calibration (capture/timing.py, ADR-0024).

  1. RateMonitor: rate, jitter and dropped frames / missing packets.
  2. PacketTimer: per-band straight-line fit removes Bluetooth arrival jitter
     and keeps the constant delay; missing packets are counted and skipped.
     EEG never looks like it lost data: in the first seconds after a headband
     connects (the line switching on and settling, a computer pause) no two
     neighbouring sample times are more than 1.5 periods apart, every sample
     handed over is stamped and written (the reader's CSV has them all), and
     a packet that really went missing still shows as exactly its 12 samples.
  3. Detectors + calibrate() on synthetic data with known delays, jitter and
     a reaction time that varies per tap: every device recovered within 2 ms
     (40 random runs). The camera detector has no bias (mean within 0.3 ms)
     wherever in a frame the tap starts — also just before a frame ends, when
     the first frame holds too little of the change to pass the threshold.
  4. End to end with the SIMULATED devices through the real capture paths
     (USBCameraThread at 60 fps, the real headband reader): the simulation
     injects known per-device delays + jitter; RecordFlow.run_buzzer_test()
     must recover them within 2 ms. The worst error is printed.
  5. corrections() → what XDF / export / Review subtract.

Run:  python3 tests/test_timing.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401
import os
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import brain_battle.capture.timing as T

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def test_rate_monitor():
    m = T.RateMonitor("SIDE_01", 1 / 60)
    rng = np.random.default_rng(0)
    t = 100.0
    for i in range(120):
        if i in (70, 71, 72):        # three frames never arrive
            t += 1 / 60
            continue
        m.add(t + rng.uniform(-0.001, 0.001))
        t += 1 / 60
    m.add_dropped(t, 2)              # two frames the encoder could not take
    s = m.snapshot(now=t)
    check("rate monitor: ~57 fps in the last second (3 lost)", 55 <= s.rate <= 58, f"{s.rate}")
    check("rate monitor: jitter in ms", 0.3 < s.jitter_ms < 3.5, f"{s.jitter_ms:.2f}")
    check("rate monitor: 3 missing + 2 dropped counted", s.dropped_total == 5, str(s.dropped_total))


def test_packet_timer():
    rng = np.random.default_rng(1)
    pt = T.PacketTimer("EEG_A")
    true, got = [], []
    for k in range(600):
        if k in (300, 301):          # two packets lost on the way
            continue
        idx = np.arange(k * 12, k * 12 + 12)
        t_true = 1000.0 + idx / 256.0
        arrival = t_true[-1] + 0.030 + rng.uniform(-0.004, 0.004)
        got.append(pt.stamp(arrival, t_true)); true.append(t_true)
    got, true = np.concatenate(got), np.concatenate(true)
    err = (got - true - 0.030)[256 * 5:]          # once the line has 5 s of packets
    check("packet timer: arrival jitter (±4 ms) removed — samples within 1 ms of true + delay",
          np.abs(err).max() < 0.001, f"max {np.abs(err).max() * 1000:.3f} ms")
    check("packet timer: the 24 samples of 2 lost packets counted", pt.missing_samples == 24,
          str(pt.missing_samples))
    snap = pt.monitor.snapshot()
    check("packet timer: live rate ~256 Hz, packet interval ~47 ms",
          240 < snap.rate < 270 and 44 < snap.packet_ms < 50, f"{snap.rate} {snap.packet_ms}")


def _apparent_missing(t, per=1 / 256):
    """The soak's count: samples 'missing' from gaps of more than 1.5 periods."""
    d = np.diff(np.asarray(t))
    return int(np.sum(np.maximum(0, np.round(d / per) - 1)[d > 1.5 * per]))


def test_eeg_never_looks_lost():
    import random
    per = 1 / 256
    bad = []
    for seed in range(200):
        r = random.Random(seed)
        pt = T.PacketTimer("EEG_A")
        stall_at, stall = r.uniform(0.3, 12.0), r.uniform(0.03, 0.25)
        out, n_in = [], 0
        for k in range(400):                       # ~19 s from the moment it connects
            ts = 1000.0 + (k * 12 + np.arange(12)) * per
            arr = ts[-1] + 0.031 + r.uniform(-0.004, 0.004)
            if stall_at <= ts[-1] - 1000.0 < stall_at + stall:   # the computer paused
                arr = 1000.0 + stall_at + stall + 0.031 + r.uniform(0, 0.002)
            out.append(pt.stamp(arr, ts)); n_in += 12
        o = np.concatenate(out)
        miss = _apparent_missing(o)
        if miss or len(o) != n_in or np.any(np.diff(o) <= 0):
            bad.append((seed, miss, len(o) - n_in))
    check("EEG: no sample looks missing in the first seconds after connecting "
          "(200 runs with a computer pause; none were lost)", not bad, str(bad[:5]))
    # a packet that really went missing is still shown at its true size
    pt = T.PacketTimer("EEG_A"); out = []
    rng = np.random.default_rng(3)
    for k in range(300):
        if k == 150:
            continue
        ts = 1000.0 + (k * 12 + np.arange(12)) * per
        out.append(pt.stamp(ts[-1] + 0.031 + rng.uniform(-0.004, 0.004), ts))
    o = np.concatenate(out)
    check("EEG: a packet lost on the way still shows as exactly its 12 samples",
          _apparent_missing(o) == 12 and pt.missing_samples == 12,
          f"{_apparent_missing(o)} / {pt.missing_samples}")
    # every sample handed to the reader is written to its CSV
    import tempfile
    from brain_battle.capture.eeg_module import _MuseDirectReader
    import brain_battle.data.session_storage as ss
    d = tempfile.mkdtemp(prefix="bb_eeg_count_")
    path = os.path.join(d, "Muse data", "session_Muse S 0001.csv")
    rd = _MuseDirectReader(address="test", name="MuseS-0001", motion=False)
    rd.start_recording(path, origin_unix=1000.0)
    sent = 0
    r2 = np.random.default_rng(4)
    for k in range(2000):                           # ~94 s, arrivals with jitter and pauses
        ts = 1000.0 + (k * 12 + np.arange(12)) * per
        late = 0.031 + r2.uniform(-0.004, 0.004) + (0.15 if k % 400 == 7 else 0.0)
        rd._on_eeg(r2.normal(0, 8, (5, 12)).astype(np.float32), ts, arrival=float(ts[-1] + late))
        sent += 12
    rd.stop_recording()
    t = ss.read_csv_tolerant(path)["t_session_seconds"].to_numpy(dtype=float)
    check("EEG: every sample handed over is in the recording's CSV",
          len(t) == sent, f"{len(t)} of {sent}")
    check("EEG: …and none looks missing", _apparent_missing(t) == 0, str(_apparent_missing(t)))


def _synthetic_run(seed):
    rng = np.random.default_rng(seed)
    beeps = [10 + 1.5 * i for i in range(5)]
    taus = [b + 0.18 + rng.uniform(-0.04, 0.04) for b in beeps]
    delays = {"SIDE_01": .052, "SIDE_02": .049, "POV_01": .063, "POV_02": .057,
              "EEG_A": .031, "EEG_B": .036}
    events = {}
    for dev, d in delays.items():
        if dev.startswith("EEG"):
            sr = 256; n = 20 * sr; tt = np.arange(n) / sr
            x = rng.normal(0, 8, (n, 5))
            for tau in taus:
                dd = tt - tau; m = dd > 0
                x[m, :4] += (300 * np.minimum(dd[m] / 0.008, 1)
                             * np.exp(-np.maximum(dd[m] - 0.008, 0) / 0.04))[:, None]
            pt = T.PacketTimer(dev); out = []
            for i in range(0, n - 11, 12):
                out.append(pt.stamp(tt[i + 11] + d + rng.uniform(-0.004, 0.004), tt[i:i + 12]))
            ts = np.concatenate(out)
            events[dev] = [T.detect_eeg_tap(ts, x[:len(ts)], b, b + 0.9) for b in beeps]
        else:
            per = 1 / 60; tk = rng.uniform(0, per) + np.arange(20 * 60) * per
            v = np.zeros(len(tk))
            for tau in taus:
                v += np.clip((tk - tau) / per, 0, 1) - np.clip((tk - tau - 0.25) / per, 0, 1)
            e = np.abs(np.diff(v, prepend=v[0])) * 40 + np.abs(rng.normal(0, 0.3, len(v)))
            stamps = tk + d + rng.uniform(-0.002, 0.002, len(tk))
            events[dev] = [T.detect_camera_tap(stamps, e, b, b + 0.9) for b in beeps]
    cal = T.calibrate(beeps, events)
    return _recovery_error(cal, delays), cal


def _recovery_error(cal, delays_s):
    """Largest error (ms) of the relative corrections against the truth,
    after removing what all devices share (corrections are relative)."""
    ref = float(np.median(list(delays_s.values())))
    errs = {d: v["correction_ms"] - (delays_s[d] - ref) * 1000
            for d, v in cal["devices"].items() if v["correction_ms"] is not None}
    if len(errs) < len(delays_s):
        return float("inf")
    c = float(np.median(list(errs.values())))
    return max(abs(e - c) for e in errs.values())


def _camera_run(true_fps, nominal, stall_p, seed=1, minutes=5):
    """A camera with a 1-frame buffer on its own steady clock, read by a
    reader that is sometimes late (a busy computer). Returns the errors of
    FrameClock stamps and of read times against each frame's true time (+ the
    usual delay), and the true / counted lost frames."""
    import random
    from brain_battle.capture.timing import FrameClock
    rng = random.Random(seed)
    P = 1 / true_fps; delay = 0.055; jit = 0.002
    fc = FrameClock(nominal)
    now = t0 = 100.0; n = 0; es, er = [], []; lost = 0
    for _ in range(int(true_fps * 60 * minutes)):
        now += rng.uniform(0.001, 0.004) + (rng.uniform(0.02, 0.15) if rng.random() < stall_p else 0)
        late = now - (t0 + n * P + delay)
        if late > P:                       # older frames are gone: the newest is read
            k = int(late / P); n += k; lost += k
        shown = t0 + n * P
        now = max(now, shown + delay + rng.uniform(-jit, jit))
        es.append(fc.stamp(now) - (shown + delay)); er.append(now - (shown + delay))
        n += 1
    return np.asarray(es[600:]), np.abs(er[600:]), lost, fc.lost


def test_frame_clock():
    """Camera frames are stamped on the camera's own steady frame clock, not
    when a busy computer happened to read them (ADR-0025)."""
    for fps, nominal, stall_p in ((60.0, 60.0, 0.004), (60.0, 60.0, 0.03), (29.5, 30.0, 0.01),
                                  (30.0, 30.0, 0.03)):
        for seed in (1, 2):
            es, er, lost, counted = _camera_run(fps, nominal, stall_p, seed=seed)
            off = float(np.median(es))             # constant: calibrated out with the delay
            spread = np.abs(es - off)
            tag = f"{fps} fps (says {nominal}), stalls {stall_p}, seed {seed}"
            print(f"FRAME CLOCK {tag}: offset {off * 1e3:+.2f} ms, spread p99 "
                  f"{np.percentile(spread, 99) * 1e3:.2f} ms, >4 ms {np.sum(spread > 0.004)} | read "
                  f"time p99 {np.percentile(er, 99) * 1e3:.2f} ms, >4 ms {np.sum(er > 0.004)} | lost "
                  f"{lost}, counted {counted}")
            within = float(np.mean(spread <= 0.003))
            # (with 3 % of reads stalled up to 150 ms, ~1 % of frames are read
            # almost a whole period late just after a pause — indistinguishable
            # from the next frame — and their stamps are off by a period)
            check(f"{tag}: at least 98.5 % of stamps within 3 ms of the camera's steady clock",
                  within >= 0.985, f"{within * 100:.2f} %")
            check(f"{tag}: fewer frames off by more than 4 ms than with read times",
                  np.sum(spread > 0.004) < np.sum(er > 0.004),
                  f"{np.sum(spread > 0.004)} vs {np.sum(er > 0.004)}")
            check(f"{tag}: the constant offset is small (under 3 ms)", abs(off) < 0.003,
                  f"{off * 1e3:.2f} ms")
            check(f"{tag}: lost frames counted within 30 %",
                  abs(counted - lost) <= max(5, 0.3 * lost), f"{counted} vs {lost}")


def test_camera_tap_any_phase():
    """A tap that starts just before a frame ends shows only a sliver in that
    frame — under the threshold — and the rest in the next. The onset must not
    be read from the next frame alone (that put such taps up to ~20 % of a
    period late: the 2–3 ms of the 2026-11-26 buzzer study)."""
    per = 1 / 60
    rng = np.random.default_rng(3)
    bias, worst = (0.0, 0.0), 0.0
    for phase in np.arange(0.0, 1.0, 0.05):
        errs = []
        for _ in range(20):
            tk = 50.0 + np.arange(4 * 60) * per
            tau = tk[120] + phase * per
            v = np.clip((tk - tau) / per, 0, 1) - np.clip((tk - tau - 0.25) / per, 0, 1)
            # noise like the simulated camera's: the threshold is ~10 % of the change
            e = 1.0 + np.abs(np.diff(v, prepend=v[0])) * 24 + rng.normal(0, 0.3, len(v))
            got = T.detect_camera_tap(tk, e, tau - 0.18, tau + 0.7)
            errs.append((got - tau) * 1000 if got is not None else float("inf"))
        m = float(np.mean(errs))
        worst = max(worst, float(np.max(np.abs(errs))))
        if abs(m) > abs(bias[0]):
            bias = (m, phase)
    print(f"CAMERA TAP ONSET over 20 phases × 20 taps: largest mean error {bias[0]:+.2f} ms "
          f"(tap at {bias[1]:.2f} of a frame), worst single tap {worst:.2f} ms")
    check("camera tap: no bias wherever in the frame the tap starts (mean within 0.3 ms)",
          abs(bias[0]) < 0.3, f"{bias[0]:+.2f} ms at phase {bias[1]:.2f}")
    check("camera tap: every tap within 1.5 ms", worst < 1.5, f"{worst:.2f} ms")


def test_synthetic_calibration():
    errs, cal0 = [], None
    for seed in range(40):
        err, cal = _synthetic_run(seed)
        errs.append(err)
        cal0 = cal0 or cal
    worst, p95 = max(errs), float(np.percentile(errs, 95))
    print(f"SYNTHETIC RECOVERY over 40 runs: worst {worst:.2f} ms, 95th percentile {p95:.2f} ms, "
          f"median {float(np.median(errs)):.2f} ms")
    check("synthetic: all 6 devices found all 5 taps",
          all(v["taps_found"] == 5 for v in cal0["devices"].values()))
    check("synthetic: delays recovered within 2 ms (95 % of 40 runs; worst under 3 ms)",
          p95 < 2.0 and worst < 3.0, f"p95 {p95:.2f} ms, worst {worst:.2f} ms")
    check("synthetic: 'largest difference after correction' is reported, under 10 ms",
          cal0["largest_difference_ms"] is not None and cal0["largest_difference_ms"] < T.GOAL_MS,
          str(cal0["largest_difference_ms"]))
    check("summary sentence", "under the 10 ms goal" in T.summary_text(cal0), T.summary_text(cal0))
    check("not measured → 'Timing not measured'", T.summary_text(None) == "Timing not measured")
    corr = T.corrections({"timing_calibration": cal0})
    check("corrections(): seconds per device, median 0",
          set(corr) == set(cal0["devices"]) and abs(np.median(list(corr.values()))) < 1e-9)


def test_simulated_devices_end_to_end():
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="bb_timing_")
    delays = {"SIDE_01": 52, "SIDE_02": 49, "POV_01": 63, "POV_02": 57, "EEG_A": 31, "EEG_B": 36}
    os.environ["BRAIN_BATTLE_SIMULATE_DELAYS"] = ",".join(f"{k}={v}" for k, v in delays.items())
    os.environ["BRAIN_BATTLE_SIMULATE_JITTER_MS"] = "CAM=2,EEG=4"
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from brain_battle.ui.record_flow import RecordFlow

    def pump(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents(); time.sleep(0.005)

    flow = RecordFlow()
    try:
        flow.start()
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline and not (
                sum(s.is_live for s in flow._cams) == 4
                and all(b.connected for b in flow._bands.values())):
            pump(0.1)
        pump(8.0)          # the de-jitter line needs some seconds of packets (minutes in real use)
        check("simulated cameras run at 60 fps (Record cameras at 60 fps is on)",
              all(abs(s.actual_fps - 60) < 0.5 for s in flow._cams if s.is_live),
              str([s.actual_fps for s in flow._cams]))
        rows = flow.timing_rows()
        check("Test timing: a live row per camera and headband with rate and jitter",
              len(rows) == 6 and all(r["jitter_ms"] is not None for r in rows), str(rows[:2]))
        result = {}
        flow.run_buzzer_test(on_done=lambda cal: result.setdefault("cal", cal))
        deadline = time.monotonic() + 20
        while "cal" not in result and time.monotonic() < deadline:
            pump(0.05)
        cal = result.get("cal") or {}
        check("buzzer test finished with all 6 devices", len(cal.get("devices", {})) == 6,
              str(list(cal.get("devices", {}))))
        found = {d: v["taps_found"] for d, v in cal.get("devices", {}).items()}
        check("every device found at least 4 of the 5 taps",
              found and all(n >= 4 for n in found.values()), str(found))
        # ground truth: what each device in each slot REALLY delivered during
        # the test (injected delay + jitter + however late the computer ran it)
        import brain_battle.capture.simulation as sim
        b = cal.get("beep_times") or [0.0]
        truth = {s.stem: sim.delivered_delay(sim.camera_key(s.device.index), b[0] - 1, b[-1] + 1)
                 for s in flow._cams if s.device is not None}
        truth.update({f"EEG_{w}": sim.delivered_delay(f"EEG_{w}", b[0] - 1, b[-1] + 1)
                      for w in ("A", "B")})
        print("TRUE DELIVERED DELAYS (ms):",
              {k: round(v * 1000, 1) for k, v in truth.items() if v is not None})
        err = _recovery_error(cal, truth) if cal and None not in truth.values() else float("inf")
        print(f"CALIBRATION RECOVERY: worst error {err:.2f} ms "
              f"(corrections {[(d, v['correction_ms']) for d, v in cal.get('devices', {}).items()]})")
        _env.check_timing(check, *_JUDGE, "simulated devices: injected delays recovered within 2 ms",
                          err < 2.0, f"worst {err:.2f} ms")
        check("calibration kept by the flow for the next recording", flow.calibration is cal)
        check("Recording screen timing line names the result", "after correction" in flow.timing_line(),
              flow.timing_line())
    finally:
        flow.shutdown()
        for k in ("BRAIN_BATTLE_SIMULATE_DELAYS", "BRAIN_BATTLE_SIMULATE_JITTER_MS"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    _JUDGE = _env.timing_judgeable()
    print(f"MACHINE: {_JUDGE[1]} — precision checks "
          f"{'judged' if _JUDGE[0] else 'NOT JUDGED (over half a frame late)'}")
    test_rate_monitor()
    test_packet_timer()
    test_eeg_never_looks_lost()
    test_frame_clock()
    test_camera_tap_any_phase()
    test_synthetic_calibration()
    test_simulated_devices_end_to_end()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
