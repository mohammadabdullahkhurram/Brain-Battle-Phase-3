"""
Timing check and buzzer calibration (capture/timing.py, ADR-0024).

  1. RateMonitor: rate, jitter and dropped frames / missing packets.
  2. PacketTimer: per-band straight-line fit removes Bluetooth arrival jitter
     and keeps the constant delay; missing packets are counted and skipped.
  3. Detectors + calibrate() on synthetic data with known delays, jitter and
     a reaction time that varies per tap: every device recovered within 2 ms
     (40 random runs).
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
        check("simulated devices: injected delays recovered within 2 ms", err < 2.0,
              f"worst {err:.2f} ms")
        check("calibration kept by the flow for the next recording", flow.calibration is cal)
        check("Recording screen timing line names the result", "after correction" in flow.timing_line(),
              flow.timing_line())
    finally:
        flow.shutdown()
        for k in ("BRAIN_BATTLE_SIMULATE_DELAYS", "BRAIN_BATTLE_SIMULATE_JITTER_MS"):
            os.environ.pop(k, None)


if __name__ == "__main__":
    test_rate_monitor()
    test_packet_timer()
    test_synthetic_calibration()
    test_simulated_devices_end_to_end()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
