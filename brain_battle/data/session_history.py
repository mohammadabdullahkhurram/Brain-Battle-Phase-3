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


__all__ = [
    "HISTORY_FILENAME", "history_path", "log_event", "read_history",
    "EVENT_RECORDING_STARTED", "EVENT_RECORDING_FINISHED",
    "EVENT_RECORDING_INTERRUPTED", "EVENT_RECORDING_RECOVERED",
    "EVENT_OPENED_FOR_REVIEW", "EVENT_MOMENT_MARKED",
    "EVENT_LABELS_ADDED", "EVENT_LABELS_REMOVED",
    "EVENT_LABELS_MIGRATED", "EVENT_LABELS_EXPORTED", "EVENT_EXPORT_CREATED",
    "EVENT_LABELS_CLAIMED", "current_user",
]
