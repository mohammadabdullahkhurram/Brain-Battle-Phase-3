"""
log_setup.py — single central logging configuration for Brain Battle.

Usage:
    In modules:      import logging; log = logging.getLogger(__name__)
    At app startup:  from log_setup import init_logging; init_logging()
    Per session:     from log_setup import attach_session_log
                     attach_session_log(session_root)   # adds <root>/session.log

Levels policy (see CLAUDE.md K entry):
  - error/warning — real failures a human should see (file/subprocess/parse/
    device errors, swallowed-exception paths of the videotoolbox-bug class)
  - info          — session lifecycle milestones (created, phase change, saved)
  - debug         — chatty diagnostics and legitimate-quiet teardown races;
                    invisible by default so the log doesn't cry wolf
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
_DATEFMT = "%H:%M:%S"

_initialised = False
_session_handler: logging.FileHandler | None = None


def init_logging(level: int = logging.INFO) -> None:
    """Configure the root logger once: console handler, standard format.
    Safe to call repeatedly; later calls are no-ops."""
    global _initialised
    if _initialised:
        return
    root = logging.getLogger()
    root.setLevel(logging.DEBUG)  # handlers filter; root stays permissive
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(level)
    console.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
    root.addHandler(console)
    _initialised = True


def attach_session_log(session_root: str | os.PathLike,
                       filename: str | None = None) -> str | None:
    """Add a DEBUG-level file handler writing into the session folder, so
    every capture leaves a full diagnostic trail next to its data. Replaces
    the previous session's handler if one exists. Returns the log path, or
    None if the file could not be created (logging must never break capture).
    """
    global _session_handler
    init_logging()
    try:
        name = filename or f"session_{_dt.datetime.now():%Y%m%d_%H%M%S}.log"
        path = os.path.join(os.fspath(session_root), name)
        handler = logging.FileHandler(path, encoding="utf-8")
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(logging.Formatter(_FORMAT, _DATEFMT))
        root = logging.getLogger()
        if _session_handler is not None:
            root.removeHandler(_session_handler)
            try:
                _session_handler.close()
            except OSError:
                pass
        root.addHandler(handler)
        _session_handler = handler
        return path
    except OSError as e:
        logging.getLogger(__name__).warning(
            "could not attach session log in %s: %s", session_root, e)
        return None
