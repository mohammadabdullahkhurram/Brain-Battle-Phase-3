"""
Subprocess runner for tests/test_eeg_streaming.py — NOT a test itself.

Streams synthetic 12-sample EEG chunks through the REAL _MuseDirectReader
write path (start_recording → _on_eeg → writer thread) at ~256 Hz, writing
the cumulative count of samples HANDED TO the reader into a sidecar
"sent.log" (overwrite + fsync each chunk, so it's always valid on disk).

The parent test launches this, lets it stream for a bit, then SIGKILLs it —
simulating a hard crash mid-recording — and checks the EEG CSV is still
valid and contains everything up to the last flush.

Usage (by the parent test only):
    python3 tests/_eeg_kill_runner.py <csv_path> <sent_log_path> [origin_unix]
"""
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
# brain_battle_main isn't imported here, but eeg_module construction of a
# QThread-derived reader wants a QApplication to exist.
from PyQt6.QtWidgets import QApplication

SR = 256
CHUNK = 12


def main():
    csv_path = sys.argv[1]
    sent_log_path = sys.argv[2]
    origin = float(sys.argv[3]) if len(sys.argv) > 3 else 1_783_000_000.0

    _app = QApplication.instance() or QApplication([])  # noqa: F841
    from eeg_module import _MuseDirectReader

    reader = _MuseDirectReader(address="kill-test", name="synthetic")
    reader.start_recording(csv_path, origin_unix=origin)

    sent_log = open(sent_log_path, "w", encoding="utf-8")
    total = 0
    start_offset = 0.4
    rng = np.random.default_rng(0)
    chunk_idx = 0
    period = CHUNK / SR  # ~46.9 ms — pace at ~real-time so the 1 s flush
                         # cadence corresponds to ~256 samples.
    next_t = time.monotonic()
    while True:
        data = rng.standard_normal((5, CHUNK)).astype(np.float32)
        ts = origin + start_offset + (np.arange(CHUNK) + chunk_idx * CHUNK) / SR
        reader._on_eeg(data, ts.astype(np.float64))
        total += CHUNK
        chunk_idx += 1
        # Record samples handed to the reader, always-valid on disk.
        sent_log.seek(0)
        sent_log.truncate()
        sent_log.write(str(total))
        sent_log.flush()
        os.fsync(sent_log.fileno())
        next_t += period
        sleep = next_t - time.monotonic()
        if sleep > 0:
            time.sleep(sleep)


if __name__ == "__main__":
    main()
