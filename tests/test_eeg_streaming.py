"""
Crash-safety test for the streamed EEG recorder (Phase 2, item 2).

The reader streams samples to the CSV via a background writer thread
(header written up front, flush >=1/s, fsync ~5s), so a hard kill mid-session
must still leave a valid CSV containing everything up to the last flush — not
lose the whole recording the way the old RAM-until-stop_recording() path did.

This drives the REAL write path in a subprocess (tests/_eeg_kill_runner.py),
SIGKILLs it mid-recording, and asserts the CSV:
  - parses,
  - has the expected columns,
  - has monotonically non-decreasing timestamps,
  - contains >= (samples handed to the reader − ~1 s worth).

Run:  python3 tests/test_eeg_streaming.py   (nonzero exit on any failure)
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import pandas as pd

_failures = []
SR = 256


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _read_csv_tolerant(path):
    """Parse the CSV, tolerating a single truncated final line (a kill can
    interrupt the row being written). A valid recorder leaves at most that
    one partial row; anything worse is a real failure."""
    try:
        return pd.read_csv(path)
    except Exception:
        # Drop a possibly-partial last line and retry — mirrors what a
        # crash-recovery reader would do.
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        from io import StringIO
        return pd.read_csv(StringIO("\n".join(lines[:-1]) + "\n"))


def test_sigkill_midrecording_leaves_valid_csv():
    runner = os.path.join(ROOT, "tests", "_eeg_kill_runner.py")
    origin = 1_783_000_000.0
    with tempfile.TemporaryDirectory() as tmp:
        csv_path = os.path.join(tmp, "eeg.csv")
        sent_log = os.path.join(tmp, "sent.log")

        proc = subprocess.Popen([sys.executable, runner, csv_path, sent_log, str(origin)])

        # Wait until the subprocess has actually STREAMED a healthy amount —
        # not just a wall-clock delay, since import + QApplication startup can
        # eat a second or two before any sample flows. Poll sent.log until it
        # reports >= TARGET samples (several flush cycles' worth), with a
        # safety timeout so a wedged subprocess can't hang the test forever.
        TARGET = 3 * SR          # ~3 s of streaming once it's warmed up
        safety = time.monotonic() + 30.0
        sent_now = 0

        def _read_sent():
            try:
                with open(sent_log, encoding="utf-8") as f:
                    return int(f.read().strip() or "0")
            except (OSError, ValueError):
                return 0

        while sent_now < TARGET and time.monotonic() < safety:
            time.sleep(0.1)
            sent_now = _read_sent()

        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)

        check("subprocess was killed (negative returncode)",
              proc.returncode is not None and proc.returncode < 0,
              f"returncode={proc.returncode}")
        check("EEG CSV exists after kill", os.path.exists(csv_path))

        with open(sent_log, encoding="utf-8") as f:
            samples_sent = int(f.read().strip() or "0")
        check("subprocess actually streamed samples", samples_sent > SR,
              f"sent={samples_sent}")

        df = _read_csv_tolerant(csv_path)
        check("CSV parses after kill", df is not None and len(df) >= 0,
              f"{len(df)} rows")
        check("CSV has the expected columns",
              list(df.columns) == ["TP9", "AF7", "AF8", "TP10", "AUX",
                                   "timestamps", "t_session_seconds"],
              str(list(df.columns)))

        ts = df["timestamps"].to_numpy()
        check("timestamps are monotonically non-decreasing",
              bool((ts[1:] >= ts[:-1]).all()) if len(ts) > 1 else True)

        # Everything up to the last flush must be present. With line-buffered
        # writes + >=1/s flush, the loss window is ~1 s (256 samples) plus a
        # small queue/scheduling slack.
        tolerance = SR + 64
        check("CSV contains >= (samples sent - ~1s worth)",
              len(df) >= samples_sent - tolerance,
              f"rows={len(df)} sent={samples_sent} (tolerance={tolerance})")
        check("CSV did not somehow contain MORE than was sent",
              len(df) <= samples_sent + CHUNK_SLACK,
              f"rows={len(df)} sent={samples_sent}")


CHUNK_SLACK = 12  # the in-flight chunk sent.log may not have counted yet


if __name__ == "__main__":
    test_sigkill_midrecording_leaves_valid_csv()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
