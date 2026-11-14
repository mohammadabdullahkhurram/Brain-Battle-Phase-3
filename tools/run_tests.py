"""
run_tests.py — run every test script and report pass / fail per script
──────────────────────────────────────────────────────────────────────
The tests are plain scripts (``python3 tests/test_<name>.py``, non-zero exit on
failure). Running all of them one after another takes about eleven minutes;
this runs them several at a time and prints one line per script:

    python3 tools/run_tests.py              # everything
    python3 tools/run_tests.py xdf player   # only scripts whose name contains a word
    python3 tools/run_tests.py --jobs 1     # one at a time (slow, simplest to read)

Scripts that measure real-time behaviour (playback pacing, a simulated
recording, crash-kill timing) or that publish LSL streams under fixed names are
run one at a time in a single "serial lane", so they neither disturb each other
nor get slowed down by a neighbour. Everything else shares the remaining
workers. Each script's full output is kept in a log file whose folder is
printed at the end; failing scripts have their FAIL lines echoed. Because some
scripts measure real time, a script that fails while others are running is run
once more on its own, and only counts as failed if it fails again.

Exit code: 0 if every script passed, 1 otherwise.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import os
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Timing-sensitive or LSL-publishing scripts: never two of these at once.
SERIAL = {"test_record_flow", "test_settings_screen", "test_lsl_streams", "test_layout_fit",
          "test_player", "test_video_recording", "test_eeg_streaming", "test_crash_recovery",
          "test_history_log", "test_smoke", "test_round_bell", "test_shared_folder"}


def _run(path: str, log_dir: str):
    name = os.path.splitext(os.path.basename(path))[0]
    log = os.path.join(log_dir, name + ".log")
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", PYTHONUTF8="1")
    t0 = time.monotonic()
    with open(log, "w", encoding="utf-8") as f:
        rc = subprocess.call([sys.executable, path], cwd=ROOT, env=env,
                             stdout=f, stderr=subprocess.STDOUT)
    return name, rc, time.monotonic() - t0, log


def _run_lane(paths, log_dir, report):
    for p in paths:
        report(_run(p, log_dir))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the test scripts in tests/.")
    ap.add_argument("words", nargs="*", help="only run scripts whose name contains one of these")
    ap.add_argument("--jobs", type=int, default=min(4, os.cpu_count() or 2),
                    help="scripts to run at once (default: up to 4)")
    args = ap.parse_args(argv)

    scripts = sorted(os.path.join(ROOT, "tests", f) for f in os.listdir(os.path.join(ROOT, "tests"))
                     if f.startswith("test_") and f.endswith(".py"))
    if args.words:
        scripts = [s for s in scripts if any(w in os.path.basename(s) for w in args.words)]
    if not scripts:
        print("No test scripts matched.")
        return 1

    # CI keeps the logs of a failed run as an artifact (GITHUB_ACTIONS is set
    # by GitHub's runners): put them where the workflow looks for them.
    if os.environ.get("GITHUB_ACTIONS"):
        log_dir = os.path.join(ROOT, "test-logs")
        os.makedirs(log_dir, exist_ok=True)
    else:
        log_dir = tempfile.mkdtemp(prefix="brain_battle_tests_")
    results = []

    def report(res):
        name, rc, secs, log = res
        results.append(res)
        print(f"{'PASS' if rc == 0 else 'FAIL'}  {name:<26} {secs:6.1f}s", flush=True)
        if rc != 0:
            with open(log, encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
            shown = [ln for ln in lines if ln.startswith("FAIL")] or lines[-12:]
            for ln in shown[:20]:
                print("        " + ln[:200])

    serial = [s for s in scripts if os.path.splitext(os.path.basename(s))[0] in SERIAL]
    parallel = [s for s in scripts if s not in serial]
    t0 = time.monotonic()
    jobs = max(1, args.jobs)
    if jobs == 1:
        _run_lane(scripts, log_dir, report)
    else:
        with futures.ThreadPoolExecutor(max_workers=jobs) as pool:
            pending = [pool.submit(_run_lane, serial, log_dir, report)]
            pending += [pool.submit(lambda p=p: report(_run(p, log_dir))) for p in parallel]
            for f in pending:
                f.result()

    # Several scripts measure real time, so a busy machine can fail one that
    # is fine. Give each failure one more go with nothing else running; only a
    # script that fails alone too counts as failed.
    first_fail = [r for r in results if r[1] != 0]
    if first_fail and jobs > 1:
        print("\nRe-running the failed scripts one at a time…")
        by_name = {os.path.splitext(os.path.basename(s))[0]: s for s in scripts}
        results = [r for r in results if r[1] == 0]
        for r in first_fail:
            report(_run(by_name[r[0]], log_dir))
    failed = sorted(r[0] for r in results if r[1] != 0)
    print(f"\n{len(results) - len(failed)} of {len(results)} scripts passed "
          f"in {time.monotonic() - t0:.0f}s.  Logs: {log_dir}")
    if failed:
        print("FAILED: " + ", ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
