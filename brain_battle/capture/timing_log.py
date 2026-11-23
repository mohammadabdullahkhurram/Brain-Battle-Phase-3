"""
timing_log.py — timing_quality.csv
──────────────────────────────────
Once a second during a recording, one row per device: its actual rate, its
jitter, what it lost in that second, and the correction the buzzer test found
for it (if one was run). Camera stalls / reopenings and a camera that could
only do 30 fps are written as rows too (``event``). The file is in every
recording and in every export, so it can be seen afterwards where timing can
improve (ADR-0024).

    t_session_seconds,device,rate,jitter_ms,dropped,offset_ms,event
    12.000,SIDE_01,60.00,1.21,0,4.10,
    12.000,EEG_A,256.00,3.92,0,-2.35,
    31.418,POV_02,0.00,,0,,stalled
    33.902,POV_02,0.00,,0,,reopened after 2.5 s

Line-buffered and flushed every row, fsync'd every ~5 s, so a crash loses at
most the last row. ``append`` raises on a write error — the caller sends it
through ``RecordFlow._essential`` so the operator is told.
"""
from __future__ import annotations

import csv
import os
import time
from typing import Optional

FILENAME = "timing_quality.csv"
COLUMNS = ["t_session_seconds", "device", "rate", "jitter_ms", "dropped", "offset_ms", "event"]


def path_for(session_root) -> str:
    return os.path.join(str(session_root), FILENAME)


class TimingQualityWriter:
    def __init__(self, path: str):
        self.path = path
        self._f = None
        self._w = None
        self._last_fsync = time.monotonic()
        self.error: Optional[str] = None
        try:
            new = not os.path.isfile(path) or os.path.getsize(path) == 0
            self._f = open(path, "a", newline="", encoding="utf-8", buffering=1)
            self._w = csv.writer(self._f)
            if new:
                self._w.writerow(COLUMNS)
                self._f.flush()
        except OSError as e:
            self.error = str(e)
            self._f = None

    @property
    def is_open(self) -> bool:
        return self._f is not None

    def append(self, t_session: float, device: str, rate=None, jitter_ms=None,
               dropped: int = 0, offset_ms=None, event: str = "") -> None:
        if self._f is None:
            raise OSError("timing_quality.csv is not open")
        self._w.writerow([f"{float(t_session):.3f}", device,
                          "" if rate is None else f"{float(rate):.2f}",
                          "" if jitter_ms is None else f"{float(jitter_ms):.2f}",
                          int(dropped or 0),
                          "" if offset_ms is None else f"{float(offset_ms):.2f}",
                          event or ""])
        self._f.flush()
        now = time.monotonic()
        if now - self._last_fsync > 5.0:
            self._last_fsync = now
            try:
                os.fsync(self._f.fileno())
            except OSError:
                pass
        return True

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.flush()
                os.fsync(self._f.fileno())
                self._f.close()
            except OSError:
                pass
            self._f = None


__all__ = ["FILENAME", "COLUMNS", "TimingQualityWriter", "path_for"]
