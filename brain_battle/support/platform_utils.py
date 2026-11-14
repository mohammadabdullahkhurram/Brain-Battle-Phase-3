"""
platform_utils.py — the few things that differ between macOS and Windows
────────────────────────────────────────────────────────────────────────
The lab runs Brain Battle on both Mac and Windows computers. Everything that
has to know which one it is on lives here, so no screen formats a date, names
the file manager, or spells a keyboard shortcut on its own:

  • ``format_date`` / ``format_date_time`` — "Mon 9 Nov 2026" without the
    ``%-d`` strftime code, which Windows' Python rejects (ValueError).
  • ``file_manager_name`` / ``show_in_file_manager_label`` — "Finder" on macOS,
    "Explorer" on Windows.
  • ``reveal_in_file_manager`` / ``open_folder`` — open the file manager at a
    recording or folder.
  • ``shortcut`` — "⌘Z" on macOS, "Ctrl+Z" elsewhere.
  • ``computer_name`` — this computer's name, for history lines and lock files.

Qt-free and stdlib-only. ``platform_name`` can be passed explicitly to every
function so the tests exercise the Windows wording on a Mac and vice versa.
"""
from __future__ import annotations

import datetime as _dt
import os
import socket
import subprocess
import sys
from typing import Optional


def _plat(platform_name: Optional[str] = None) -> str:
    return platform_name if platform_name is not None else sys.platform


def is_windows(platform_name: Optional[str] = None) -> bool:
    return _plat(platform_name).startswith("win")


def is_mac(platform_name: Optional[str] = None) -> bool:
    return _plat(platform_name) == "darwin"


# ── dates ────────────────────────────────────────────────────────────────────

def format_date(d: _dt.datetime | _dt.date, weekday: bool = True) -> str:
    """'Mon 9 Nov 2026' (day of month without a leading zero) on every OS."""
    head = f"{d:%a} " if weekday else ""
    return f"{head}{d.day} {d:%b} {d:%Y}"


def format_time(d: _dt.datetime) -> str:
    return f"{d:%H:%M}"


def format_date_time(d: _dt.datetime, weekday: bool = True) -> str:
    """'Mon 9 Nov 2026, 14:05'."""
    return f"{format_date(d, weekday)}, {format_time(d)}"


def format_iso(text: str, with_time: bool = True, fallback: Optional[str] = None) -> str:
    """Format an ISO date-time string from a manifest; anything unreadable is
    returned as ``fallback`` (or unchanged)."""
    try:
        d = _dt.datetime.fromisoformat(text)
    except (ValueError, TypeError):
        return text if fallback is None else fallback
    return format_date_time(d) if with_time else format_date(d)


# ── the file manager ─────────────────────────────────────────────────────────

def file_manager_name(platform_name: Optional[str] = None) -> str:
    if is_mac(platform_name):
        return "Finder"
    if is_windows(platform_name):
        return "Explorer"
    return "Files"


def show_in_file_manager_label(platform_name: Optional[str] = None) -> str:
    """Button text: 'Show in Finder' / 'Show in Explorer'."""
    return f"Show in {file_manager_name(platform_name)}"


def reveal_command(path: str, platform_name: Optional[str] = None):
    """The command that opens the file manager with ``path`` selected. A list
    of arguments, except on Windows where Explorer needs one exact string
    (``/select,`` must be glued to the quoted path)."""
    path = os.path.abspath(path)
    if is_mac(platform_name):
        return ["open", "-R", path]
    if is_windows(platform_name):
        return f'explorer /select,"{path}"'
    return ["xdg-open", os.path.dirname(path) or path]


def reveal_in_file_manager(path: str) -> bool:
    """Show a file or folder in Finder / Explorer, selected. Never raises."""
    if not path:
        return False
    try:
        subprocess.Popen(reveal_command(path))
        return True
    except Exception:
        return False


def open_folder(path: str) -> bool:
    """Open a folder itself in the file manager. Never raises."""
    if not path:
        return False
    try:
        if is_windows():
            os.startfile(os.path.abspath(path))        # noqa: only exists on Windows
        elif is_mac():
            subprocess.Popen(["open", os.path.abspath(path)])
        else:
            subprocess.Popen(["xdg-open", os.path.abspath(path)])
        return True
    except Exception:
        return False


# ── keyboard shortcuts as shown to the user ──────────────────────────────────

def shortcut(key: str, shift: bool = False, platform_name: Optional[str] = None) -> str:
    """'⌘Z' / '⇧⌘Z' on macOS, 'Ctrl+Z' / 'Ctrl+Shift+Z' elsewhere."""
    if is_mac(platform_name):
        return f"{'⇧' if shift else ''}⌘{key}"
    return f"Ctrl+{'Shift+' if shift else ''}{key}"


# ── this computer ────────────────────────────────────────────────────────────

def computer_name() -> str:
    try:
        name = socket.gethostname() or ""
    except Exception:
        name = ""
    return (name.split(".")[0] or os.environ.get("COMPUTERNAME") or "this computer")[:60]


def ffmpeg_install_hint(platform_name: Optional[str] = None) -> str:
    """One plain sentence on how to install ffmpeg on this computer."""
    if is_mac(platform_name):
        return "Install it with Homebrew: open Terminal and run  brew install ffmpeg"
    if is_windows(platform_name):
        return ("Install it from the Start menu's Terminal with  winget install Gyan.FFmpeg  "
                "and then restart Brain Battle")
    return "Install it with your package manager, e.g.  sudo apt install ffmpeg"


__all__ = ["is_windows", "is_mac", "format_date", "format_time", "format_date_time",
           "format_iso", "file_manager_name", "show_in_file_manager_label",
           "reveal_command", "reveal_in_file_manager", "open_folder", "shortcut",
           "computer_name", "ffmpeg_install_hint"]
