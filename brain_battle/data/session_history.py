"""
session_history.py
──────────────────
Per-recording, append-only history log: one event per line in
``<recording folder>/history.jsonl`` (JSON Lines).

Each line is a JSON object:
    {
      "time":        "2026-10-27T14:05:12.482+01:00",  # ISO 8601 with tz
      "user":        "Maya K",                          # who did it
      "user_source": "labeller_name",                   # "labeller_name" | "os"
      "computer":    "LAB-PC-2",                        # which computer
      "app_version": "3.0.0-dev",
      "event":       "labels_added",
      "details":     { ... }                            # event-specific, may be {}
    }

Why JSON Lines + append-only: the file is **only ever appended to**, never
rewritten, so concurrent writers and crashes can't corrupt earlier entries.
Each write is flushed + fsync'd so a hard kill keeps everything up to the last
completed line; a kill mid-line can leave a single truncated trailing line,
which the reader skips.

`user` is the "Your name" set in Settings (`user_source: "labeller_name"`), or
the operating-system login name when no name has been set yet
(`user_source: "os"`). `computer` is the computer's name, so a shared folder's
history shows which machine did what. Phase 7's accounts would add
`user_source: "account"`.

Pure filesystem + stdlib: no Qt, so it's trivially testable and usable from any
thread.
"""
from __future__ import annotations

import datetime as _dt
import getpass
import json
import os
from pathlib import Path
from typing import Optional

from brain_battle.version import __version__

HISTORY_FILENAME = "history.jsonl"

# ── Event names (the vocabulary; keep in sync with docs/ROADMAP Part B) ──
EVENT_RECORDING_STARTED     = "recording_started"
EVENT_RECORDING_FINISHED    = "recording_finished"
EVENT_RECORDING_INTERRUPTED = "recording_interrupted"
EVENT_RECORDING_RECOVERED   = "recording_recovered"
EVENT_OPENED_FOR_REVIEW     = "opened_for_review"
EVENT_MOMENT_MARKED         = "moment_marked"
EVENT_MOMENT_NOTED          = "moment_noted"
EVENT_CAMERA_STALLED        = "camera_stalled"
EVENT_CAMERA_RECOVERED      = "camera_recovered"
EVENT_HEADBAND_RECONNECTED  = "headband_reconnected"
EVENT_TIMING_CALIBRATED     = "timing_calibrated"
EVENT_LABELS_ADDED          = "labels_added"
EVENT_LABELS_REMOVED        = "labels_removed"
EVENT_LABELS_MIGRATED       = "labels_migrated"
EVENT_LABELS_EXPORTED       = "labels_exported"
EVENT_EXPORT_CREATED        = "export_created"
EVENT_LABELS_CLAIMED        = "labels_claimed"


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:
        return os.environ.get("USER") or os.environ.get("USERNAME") or "unknown"


def current_user() -> tuple[str, str]:
    """(name, source): "Your name" from Settings if set, else the OS login."""
    try:
        import brain_battle.data.app_settings as app_settings
        name = app_settings.labeller_name()
        if name:
            return name, "labeller_name"
    except Exception:
        pass
    return _os_user(), "os"


def _now_iso() -> str:
    """Local time, ISO 8601, WITH timezone offset (astimezone fills tzinfo)."""
    return _dt.datetime.now().astimezone().isoformat()


def history_path(session_root: str | os.PathLike) -> Path:
    return Path(session_root) / HISTORY_FILENAME


def log_event(session_root: str | os.PathLike, event: str,
              details: Optional[dict] = None, *,
              user: Optional[str] = None, when: Optional[str] = None) -> bool:
    """Append one event line to the session's history.jsonl (append + flush +
    fsync; the file is never rewritten). Returns True on success. Never raises
    — history logging must not break capture/review; failures are swallowed
    (and returned as False) so callers can keep going.

    `details` is any JSON-serialisable dict. `user`/`when` are injectable for
    testing; by default the OS login and the current local time are used."""
    if user is None:
        user, source = current_user()
    else:
        source = "labeller_name"
    from brain_battle.support.platform_utils import computer_name
    rec = {
        "time":        when or _now_iso(),
        "user":        user,
        "user_source": source,
        "computer":    computer_name(),
        "app_version": __version__,
        "event":       event,
        "details":     details or {},
    }
    try:
        path = history_path(session_root)
        # The recording folder is never created here: if it is not there (a
        # shared drive that went away) the event is dropped, rather than an
        # empty look-alike folder being left behind.
        # Append only (one write per line; flush + fsync), so a hard kill keeps
        # every completed line and several computers can add to the same file.
        from brain_battle.data.safe_io import append_line
        append_line(path, json.dumps(rec, ensure_ascii=False))
        return True
    except Exception:
        return False


def read_history(session_root: str | os.PathLike) -> list[dict]:
    """Read all events, tolerating a truncated/corrupt final line (a hard kill
    mid-write). Returns [] if the file is absent. A malformed line is skipped
    rather than aborting the whole read."""
    path = history_path(session_root)
    if not path.is_file():
        return []
    out: list[dict] = []
    try:
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    except OSError:
        return out
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            obj = json.loads(ln)
        except (ValueError, TypeError):
            # Truncated/corrupt line (typically only the last one) — skip it.
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out



def _mmss(t) -> str:
    try:
        t = max(0, int(round(float(t))))
    except (TypeError, ValueError):
        return ""
    return f"{t // 60}:{t % 60:02d}"


def describe(event: dict) -> tuple[str, str]:
    """(who, what) in plain words for one history line — the History drawer
    on Sessions and the Activity tab in Review & Label. ``who`` is
    "Recording" for what the recording itself did."""
    ev = (event or {}).get("event", "")
    d = (event or {}).get("details") or {}
    user = (event or {}).get("user") or "Someone"
    t = d.get("t_session_seconds", d.get("t_start"))
    at = f" at {_mmss(t)}" if t is not None else ""
    rec = {
        EVENT_RECORDING_STARTED: "started" + (f" · {d.get('rounds')} rounds" if d.get("rounds") else ""),
        EVENT_RECORDING_FINISHED: "finished" + (" (stopped early)" if d.get("stopped_early") else ""),
        EVENT_RECORDING_INTERRUPTED: "interrupted · saved up to where it stopped",
        EVENT_RECORDING_RECOVERED: "recovered after an interruption",
        EVENT_MOMENT_MARKED: f"moment marked{at}",
        EVENT_CAMERA_STALLED: f"{d.get('camera', 'a camera')} stopped sending pictures{at}",
        EVENT_CAMERA_RECOVERED: f"{d.get('camera', 'a camera')} reconnected{at}",
        EVENT_HEADBAND_RECONNECTED: f"Boxer {d.get('boxer', '?')}'s headband reconnected{at}",
        EVENT_TIMING_CALIBRATED: "timing checked with the buzzer test"
        + (f" · largest difference {d['largest_difference_ms']:.0f} ms"
           if isinstance(d.get("largest_difference_ms"), (int, float)) else ""),
    }
    if ev in rec:
        return "Recording", rec[ev]
    if ev == EVENT_LABELS_ADDED:
        what = d.get("label") or "a label"
        if d.get("type") == "note":
            what = "a note"
        who = d.get("who")
        return user, f"added {what}" + (f" ({who})" if who else "") + at
    if ev == EVENT_LABELS_REMOVED:
        return user, f"removed {d.get('label') or 'a label'}{at}"
    if ev == EVENT_MOMENT_NOTED:
        return user, f"added a note to the moment{at}"
    if ev == EVENT_EXPORT_CREATED:
        return user, "exported it for analysis"
    if ev == EVENT_OPENED_FOR_REVIEW:
        return user, "opened it"
    if ev == "interrupted_dismissed":
        return user, "dismissed the interrupted notice"
    if ev in (EVENT_LABELS_MIGRATED, EVENT_LABELS_CLAIMED):
        return user, "took over earlier labels"
    return user, ev.replace("_", " ")


__all__ = [
    "describe",
    "HISTORY_FILENAME", "history_path", "log_event", "read_history",
    "EVENT_RECORDING_STARTED", "EVENT_RECORDING_FINISHED",
    "EVENT_RECORDING_INTERRUPTED", "EVENT_RECORDING_RECOVERED",
    "EVENT_OPENED_FOR_REVIEW", "EVENT_MOMENT_MARKED", "EVENT_MOMENT_NOTED",
    "EVENT_CAMERA_STALLED", "EVENT_CAMERA_RECOVERED", "EVENT_HEADBAND_RECONNECTED",
    "EVENT_TIMING_CALIBRATED",
    "EVENT_LABELS_ADDED", "EVENT_LABELS_REMOVED",
    "EVENT_LABELS_MIGRATED", "EVENT_LABELS_EXPORTED", "EVENT_EXPORT_CREATED",
    "EVENT_LABELS_CLAIMED", "current_user",
]
