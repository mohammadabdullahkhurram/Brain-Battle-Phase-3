"""
privacy.py — boxer names, and hiding them
─────────────────────────────────────────
Boxer names are personal data. By default they are kept — in the recording's
manifest, in ``recording.xdf`` and in exports — because the team needs them to
keep track of who is who. Two switches hide them:

  • **In an export** ("Replace boxer names with Boxer A / Boxer B"; default in
    Settings, choice per export). ``scrub_manifest`` / ``scrub_text`` /
    ``neutral_recording_name`` produce the name-free copies that go into that
    export's zip. The recording on disk is never changed.
  • **On screen** ("Hide boxer names on screen", for presenting or sharing a
    screen). ``shown_name`` is what every screen calls to print a name.

What hiding names does NOT do: it does not blur faces in the videos, and it
does not rename the recording folder on disk. See
docs/research-and-data/personal-data.md.

Pure logic, no Qt.
"""
from __future__ import annotations

import copy
import re

NEUTRAL = {"A": "Boxer A", "B": "Boxer B"}


def neutral(which: str) -> str:
    return NEUTRAL.get(str(which).upper(), "Boxer")


def shown_name(which: str, name, hide: bool = False) -> str:
    """The text to print for a boxer: their name, or 'Boxer A' / 'Boxer B' when
    there is no name or names are hidden."""
    return neutral(which) if (hide or not name) else str(name)


def versus(a, b, hide: bool = False) -> str:
    """'Sam vs Alex' — or 'Boxer A vs Boxer B' when hidden or unnamed."""
    return f"{shown_name('A', a, hide)} vs {shown_name('B', b, hide)}"


def boxer_names(manifest: dict) -> dict:
    boxers = (manifest or {}).get("boxers") or {}
    return {w: ((boxers.get(w) or {}).get("name") or None) for w in ("A", "B")}


def scrub_text(text: str, names: dict) -> str:
    """Replace every occurrence of the boxers' names in free text (whole
    words, any capitalisation) with 'Boxer A' / 'Boxer B'."""
    out = text
    # longest first, so "Sam Lee" is replaced before "Sam"
    for which, name in sorted(names.items(), key=lambda kv: -len(kv[1] or "")):
        name = (name or "").strip()
        if len(name) < 2:
            continue
        out = re.sub(r"(?<!\w)" + re.escape(name) + r"(?!\w)", neutral(which), out,
                     flags=re.IGNORECASE)
    return out


_DATED = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}-\d{2})")


def neutral_recording_name(folder_name: str) -> str:
    """'2026-10-27 14-05 Sam vs Alex (2)' → '2026-10-27 14-05 (2)': the
    recording's name with the boxer names taken out."""
    m = _DATED.match(folder_name or "")
    if not m:
        return "recording"
    suffix = re.search(r"( \(\d+\))$", folder_name)
    return m.group(1) + (suffix.group(1) if suffix else "")


def scrub_manifest(manifest: dict) -> dict:
    """A copy of a session manifest with the boxer names replaced and
    ``names_hidden: true`` added. Headband names are kept (they identify
    equipment, not people)."""
    m = copy.deepcopy(manifest or {})
    boxers = m.get("boxers") or {}
    for which in ("A", "B"):
        entry = boxers.get(which)
        if isinstance(entry, dict):
            entry["name"] = neutral(which)
    m["boxers"] = boxers
    m["names_hidden"] = True
    return m


__all__ = ["shown_name", "versus", "neutral", "boxer_names", "scrub_text",
           "neutral_recording_name", "scrub_manifest", "NEUTRAL"]
