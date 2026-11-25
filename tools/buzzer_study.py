#!/usr/bin/env python3
"""
buzzer_study.py — run the real buzzer test many times and report how well it
recovers the devices' delays.

Each run is what ``tests/test_timing.py`` (end to end) does once: a fresh
RecordFlow with SIMULATED devices (4 camera processes at 60 fps, 2 headband
readers) with known per-device delays + jitter, 8 s to settle, then
``RecordFlow.run_buzzer_test()``. The recovery error of a run is the largest
error of the relative corrections against the delays the devices really
delivered (``test_timing._recovery_error``). Per run it also keeps each
device's error and, per tap, each device's detected time minus the injected
tap time minus its true delay — so a mis-detected tap or device is visible.

    python3 tools/buzzer_study.py --runs 200 --out study/
    python3 tools/buzzer_study.py --runs 20 --out study/ --keep-flow 5   # quicker

Writes ``runs.jsonl`` (one line per run) and ``summary.json`` (median, 95th /
99th percentile, worst, the 10 worst runs) to ``--out`` and prints the
distribution.

The study judges the app, not the computer it runs on: the machine-stall
probes of ``tools/soak_test.py`` run beside it all along
(``stall_probe.MachineStallProbe``: a process that only sleeps, and one that
also keeps 48 MB in use). A run during which the computer itself stalled
(10 ms or more) inside one of its tap windows — ``timing.TAP_WINDOW`` after
each beep, widened by ``TAP_MARGIN_SEC`` — is reported as "machine noise" and
not judged; the summary prints how many runs were excluded and the stalls
that excluded them. Every other run must stay under the 2 ms limit (the limit
is never loosened). NOT JUDGED (exit 0) when more than a quarter of the runs
were excluded (too noisy to judge), or, like the test, on a computer that
cannot keep 60 Hz wake-ups (``tests/_env.timing_judgeable``) — unless a run
that was judged went over the limit. Exit 1 when any judged run is at or over
the limit.

Not part of tools/run_tests.py (200 runs take ~70 minutes); the manual
"heavy-checks" GitHub Actions workflow runs it.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))

LIMIT_MS = 2.0
TAP_MARGIN_SEC = 0.1      # a stall this close to a tap window still counts as in it
MAX_EXCLUDED_SHARE = 0.25 # more runs than this excluded: too noisy to judge
MEMORY_PROBE_MB = 48      # as tools/soak_test.py
DELAYS_MS = {"SIDE_01": 52, "SIDE_02": 49, "POV_01": 63, "POV_02": 57, "EEG_A": 31, "EEG_B": 36}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--runs", type=int, default=200)
    ap.add_argument("--out", required=True)
    ap.add_argument("--warm", type=float, default=8.0, help="seconds to settle (as the test)")
    ap.add_argument("--keep-flow", type=int, default=1,
                    help="runs per fresh flow (1 = a fresh flow every run, as the test)")
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)

    import _env  # noqa: F401  (temp settings, offscreen Qt, silent)
    import numpy as np
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="bb_buzzer_study_")
    os.environ["BRAIN_BATTLE_SIMULATE_DELAYS"] = ",".join(f"{k}={v}" for k, v in DELAYS_MS.items())
    os.environ["BRAIN_BATTLE_SIMULATE_JITTER_MS"] = "CAM=2,EEG=4"
    judgeable, machine = _env.timing_judgeable()
    print(f"MACHINE: {machine} — {'judged' if judgeable else 'NOT JUDGED (over half a frame late)'}",
          flush=True)

    from tools.stall_probe import MachineStallProbe, overlapping
    probe_cpu = MachineStallProbe().start()
    probe_mem = MachineStallProbe(touch_mb=MEMORY_PROBE_MB).start()

    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from brain_battle.ui.record_flow import RecordFlow
    import brain_battle.capture.timing as timing
    import brain_battle.capture.simulation as sim
    import brain_battle.capture.buzzer_test as bz
    from test_timing import _recovery_error

    seen: dict = {}
    real_cal = timing.calibrate

    def spy_cal(beeps, events, **kw):
        seen["events"] = {d: list(v) for d, v in events.items()}
        return real_cal(beeps, events, **kw)
    timing.calibrate = spy_cal
    real_tap = bz.BuzzerTest._simulate_tap

    def spy_tap(t_beep):
        before = set(sim._taps)
        real_tap(t_beep)
        seen.setdefault("taus", []).extend(sorted(set(sim._taps) - before))
    bz.BuzzerTest._simulate_tap = staticmethod(spy_tap)

    def pump(sec):
        end = time.monotonic() + sec
        while time.monotonic() < end:
            app.processEvents(); time.sleep(0.005)

    runs_path = os.path.join(args.out, "runs.jsonl")
    f = open(runs_path, "w", encoding="utf-8")
    flow, errs, recs = None, [], []
    for seed in range(args.runs):
        if flow is None or seed % max(1, args.keep_flow) == 0:
            if flow is not None:
                flow.shutdown(); pump(1.0)
            flow = RecordFlow(); flow.start()
            t_live = time.monotonic()
            while time.monotonic() - t_live < 15 and not (
                    sum(s.is_live for s in flow._cams) == 4
                    and all(b.connected for b in flow._bands.values())):
                pump(0.1)
            pump(args.warm)
        random.seed(seed); seen.clear()
        res: dict = {}
        flow.run_buzzer_test(on_done=lambda c: res.setdefault("cal", c))
        deadline = time.monotonic() + 25
        while "cal" not in res and time.monotonic() < deadline:
            pump(0.05)
        cal = res.get("cal") or {}
        b = cal.get("beep_times") or [0.0]
        truth = {s.stem: sim.delivered_delay(sim.camera_key(s.device.index), b[0] - 1, b[-1] + 1)
                 for s in flow._cams if s.device is not None}
        truth.update({f"EEG_{w}": sim.delivered_delay(f"EEG_{w}", b[0] - 1, b[-1] + 1) for w in "AB"})
        ok = bool(cal) and None not in truth.values()
        err = _recovery_error(cal, truth) if ok else float("inf")
        per_dev = {}
        if ok:
            ref = float(np.median(list(truth.values())))
            e = {d: v["correction_ms"] - (truth[d] - ref) * 1000 for d, v in cal["devices"].items()
                 if v.get("correction_ms") is not None}
            c = float(np.median(list(e.values()))) if e else 0.0
            per_dev = {d: round(x - c, 2) for d, x in e.items()}
        taus = seen.get("taus", [])
        resid = {d: [None if (v is None or i >= len(taus) or truth.get(d) is None)
                     else round((v - taus[i] - truth[d]) * 1000, 2) for i, v in enumerate(evs)]
                 for d, evs in seen.get("events", {}).items()}
        rec = {"seed": seed, "err_ms": round(err, 3) if err != float("inf") else None,
               "per_device_err_ms": per_dev, "per_tap_residual_ms": resid,
               "taps_found": {d: v.get("taps_found") for d, v in cal.get("devices", {}).items()},
               "truth_ms": {k: (None if v is None else round(v * 1000, 2)) for k, v in truth.items()},
               "beep_times": list(cal.get("beep_times") or [])}
        f.write(json.dumps(rec) + "\n"); f.flush()
        errs.append(err); recs.append(rec)
        worst_dev = max(per_dev, key=lambda d: abs(per_dev[d])) if per_dev else "-"
        print(f"run {seed:3d}: error {err:6.2f} ms (worst device {worst_dev})", flush=True)
    if flow is not None:
        flow.shutdown()
    f.close()
    cpu_stalls, mem_stalls = probe_cpu.stop(), probe_mem.stop()

    # machine noise: the computer stalled inside one of the run's tap windows
    lo, hi = timing.TAP_WINDOW
    excluded = []
    for rec in recs:
        hits = []
        for k, b0 in enumerate(rec["beep_times"]):
            a0, a1 = b0 + lo - TAP_MARGIN_SEC, b0 + hi + TAP_MARGIN_SEC
            for kind, st in (("CPU", cpu_stalls), ("memory", mem_stalls)):
                hits += [{"beep": k + 1, "kind": kind, "ms": round(d * 1000, 1),
                          "at_s_after_beep": round(s0 - b0, 3)} for s0, d in overlapping(st, a0, a1)]
        rec["machine_noise"] = hits
        if hits:
            excluded.append(rec)
    judged_recs = [r for r in recs if not r["machine_noise"]]

    a = np.array([e for e in errs], dtype=float)
    finite = a[np.isfinite(a)]
    aj = np.array([(r["err_ms"] if r["err_ms"] is not None else np.inf) for r in judged_recs],
                  dtype=float)
    summary = {
        "runs": len(a), "failed_runs": int(np.sum(~np.isfinite(a))),
        "median_ms": round(float(np.median(finite)), 3) if len(finite) else None,
        "p95_ms": round(float(np.percentile(finite, 95)), 3) if len(finite) else None,
        "p99_ms": round(float(np.percentile(finite, 99)), 3) if len(finite) else None,
        "worst_ms": round(float(np.max(a)), 3) if len(a) else None,
        "over_limit": int(np.sum(a >= LIMIT_MS)), "limit_ms": LIMIT_MS,
        "machine_stalls": {"cpu": len(cpu_stalls), "memory": len(mem_stalls)},
        "excluded_machine_noise": len(excluded),
        "excluded": [{"seed": r["seed"], "err_ms": r["err_ms"], "stalls": r["machine_noise"][:4]}
                     for r in excluded],
        "judged_runs": len(judged_recs),
        "judged_worst_ms": round(float(np.max(aj)), 3) if len(aj) else None,
        "judged_p99_ms": (round(float(np.percentile(aj[np.isfinite(aj)], 99)), 3)
                          if np.isfinite(aj).any() else None),
        "judged_over_limit": int(np.sum(aj >= LIMIT_MS)),
        "machine": machine,
        "worst_runs": sorted(recs, key=lambda r: -(r["err_ms"] if r["err_ms"] is not None else 1e9))[:10],
    }
    print(f"\nBUZZER STUDY: {summary['runs']} runs — median {summary['median_ms']} ms, "
          f"95th {summary['p95_ms']} ms, 99th {summary['p99_ms']} ms, worst {summary['worst_ms']} ms; "
          f"{summary['over_limit']} at or over {LIMIT_MS} ms; {summary['failed_runs']} incomplete", flush=True)
    print(f"MACHINE NOISE: {len(cpu_stalls)} CPU + {len(mem_stalls)} memory stalls of the computer "
          f"during the study; {len(excluded)} of {len(recs)} runs excluded because the computer "
          f"stalled inside a tap window (not judged)", flush=True)
    for r in excluded:
        st = ", ".join(f"beep {h['beep']}: {h['kind']} stall {h['ms']} ms at "
                       f"{h['at_s_after_beep']:+.2f} s" for h in r["machine_noise"][:3])
        print(f"  excluded run {r['seed']:3d} (error {r['err_ms']} ms): {st}", flush=True)
    print(f"JUDGED: {len(judged_recs)} runs — 99th {summary['judged_p99_ms']} ms, worst "
          f"{summary['judged_worst_ms']} ms; {summary['judged_over_limit']} at or over {LIMIT_MS} ms",
          flush=True)
    bad = summary["judged_over_limit"] > 0
    reasons = []
    if not judgeable:
        reasons.append(machine)
    if len(excluded) > MAX_EXCLUDED_SHARE * max(1, len(recs)):
        reasons.append(f"{len(excluded)} of {len(recs)} runs had the computer stall in a tap "
                       f"window (over {MAX_EXCLUDED_SHARE:.0%}): too noisy to judge")
    summary["judged"] = not reasons
    summary["not_judged_because"] = reasons
    with open(os.path.join(args.out, "summary.json"), "w", encoding="utf-8") as g:
        json.dump(summary, g, indent=1)
    if bad:
        print(f"VERDICT: PROBLEMS — {summary['judged_over_limit']} run(s) with no machine stall "
              f"in a tap window at or over {LIMIT_MS} ms", flush=True)
        return 1
    if reasons:
        print(f"NOT JUDGED — {'; '.join(reasons)} (0 judged runs over the limit)", flush=True)
        return 0
    print("VERDICT: OK", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
