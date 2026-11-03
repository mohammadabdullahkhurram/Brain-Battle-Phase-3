"""
contact_log.py
──────────────
A tiny per-sensor contact-quality log written into the recording folder during
capture (DESIGN §4: "Keep a per-sensor contact-quality log during recording …
so the Saved screen and Review can report weak periods").

File: ``<recording>/contact_quality.csv``
Columns: ``t_session_seconds, boxer, summary, TP9, AF7, AF8, TP10`` — one row
per boxer per sample tick (~1 Hz). The status values are the
``contact_quality`` heuristic's ``good|flat|clipped|noisy``.

``weak_periods()`` turns the log into human-readable spans ("right ear weak for
2m 10s in Round 2") for the Saved screen's "Worth checking" list and Review.
Pure stdlib + the shared contact_quality vocabulary; no Qt.
"""
from __future__ import annotations

import csv
import os
import time
from typing import Optional

from contact_quality import CHANNELS, SENSOR_NAME

FILENAME = "contact_quality.csv"
_HEADER = ["t_session_seconds", "boxer", "summary", *CHANNELS]


class ContactLogWriter:
    """Append-only writer, line-buffered so a crash keeps everything flushed."""

    def __init__(self, path: str):
        self.path = path
        self._f = None
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self._f = open(path, "w", buffering=1, newline="", encoding="utf-8")
            csv.writer(self._f).writerow(_HEADER)
        except OSError:
            self._f = None

    def append(self, t_session: float, boxer: str, summary: str, statuses: dict) -> None:
        if self._f is None:
            return
        try:
            row = [f"{float(t_session):.3f}", boxer, summary,
                   *(statuses.get(ch, "") for ch in CHANNELS)]
            csv.writer(self._f).writerow(row)
        except (OSError, ValueError):
            pass

    def close(self) -> None:
        if self._f is not None:
            try:
                self._f.flush()
                os.fsync(self._f.fileno())
                self._f.close()
            except OSError:
                pass
            self._f = None


def path_for(session_root) -> str:
    return os.path.join(str(session_root), FILENAME)


def _round_at(t: float, phase_markers) -> Optional[str]:
    """Which phase a session time falls in → a plain label like 'Round 2'."""
    active = None
    for row in sorted(phase_markers or [], key=lambda r: r[0]):
        mt = float(row[0])
        label = row[2] if len(row) > 2 else (row[1] if len(row) > 1 else "")
        if mt <= t + 1e-6:
            active = label
        else:
            break
    if not active:
        return None
    u = str(active).upper()
    if u.startswith("ROUND"):
        import re
        m = re.search(r"(\d+)", u)
        return f"Round {m.group(1)}" if m else "a round"
    if u.startswith("BREAK"):
        return "a break"
    if u.startswith("BASELINE"):
        return "the rest before round 1"
    return None


def weak_periods(session_root, phase_markers=None, min_sec: float = 0.0) -> list[dict]:
    """Contiguous weak-contact spans per boxer+sensor, as dicts:
    {boxer, sensor, start, end, duration, round}. `min_sec` filters short runs
    (the Saved screen uses 30 s for its 'Worth checking' list)."""
    p = path_for(session_root)
    if not os.path.isfile(p):
        return []
    # runs[(boxer, ch)] = [start_t, last_t]
    open_runs: dict = {}
    out: list[dict] = []

    def _close(key, end_t):
        run = open_runs.pop(key, None)
        if run is None:
            return
        boxer, ch = key
        start_t, _ = run
        dur = end_t - start_t
        if dur >= min_sec:
            out.append({"boxer": boxer, "sensor": SENSOR_NAME.get(ch, ch),
                        "start": start_t, "end": end_t, "duration": dur,
                        "round": _round_at(start_t, phase_markers)})

    try:
        with open(p, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    t = float(row["t_session_seconds"])
                except (KeyError, TypeError, ValueError):
                    continue
                boxer = row.get("boxer", "")
                for ch in CHANNELS:
                    weak = row.get(ch, "good") not in ("good", "")
                    key = (boxer, ch)
                    if weak:
                        if key not in open_runs:
                            open_runs[key] = [t, t]
                        else:
                            open_runs[key][1] = t
                    else:
                        _close(key, t)
    except OSError:
        return []
    for key, run in list(open_runs.items()):
        _close(key, run[1])
    out.sort(key=lambda r: (-r["duration"], r["boxer"]))
    return out


def describe_weak(period: dict) -> str:
    """One-line 'Worth checking' sentence for a weak-contact span."""
    m, s = divmod(int(round(period["duration"])), 60)
    dur = f"{m}m {s:02d}s" if m else f"{s}s"
    where = f" in {period['round']}" if period.get("round") else ""
    return (f"Weak contact on Boxer {period['boxer']}'s {period['sensor']} "
            f"for about {dur}{where}.")


__all__ = ["ContactLogWriter", "path_for", "weak_periods", "describe_weak", "FILENAME"]
