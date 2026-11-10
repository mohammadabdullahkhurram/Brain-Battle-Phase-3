"""
round_bell.py
─────────────
The round bell: a short, audible boxing-bell sound played for the boxers and
operators at the start of each round (and the rest period before round 1). It
is a cue for the people in the room only — nothing more.

It plays NO part in timing or alignment. Alignment is driven entirely by the
capture-time clock (per-frame and per-EEG-sample timestamps on one
`t_session_seconds` axis), and in-app cameras record no audio track at all, so
there is nothing on the audio for anything to "lock onto". Whether the bell
sounds is a plain on/off preference in Settings (the "Ring a bell when each
round starts" option, DESIGN §7), wired through the record flow.

(The module was once `sync_beep.py` / `SyncBeep`, named for an audio
cross-correlation sync engine that has since been removed; it was renamed to
`round_bell` / `RoundBell` once the names no longer described a sync role.)

The sound itself is `assets/sounds/round_bell.wav` — a real boxing-bell
recording (see docs/project/credits.md for licence/attribution), processed by
`tools/make_round_bell.py`. It plays through QSoundEffect, which supports
low-latency fire-and-forget playback on macOS, Windows, and Linux without
blocking the UI thread. If the WAV is missing, or QtMultimedia can't play it
for any reason, `play()` silently does nothing (after logging a warning once)
— the bell is a nice-to-have cue, never something worth crashing over.
"""

from __future__ import annotations

import logging
import os

from brain_battle.paths import ASSETS_DIR

log = logging.getLogger(__name__)

try:
    from PyQt6.QtCore       import QUrl, QObject
    from PyQt6.QtMultimedia import QSoundEffect
    _QT_AUDIO_AVAILABLE = True
except Exception:                                     # pragma: no cover
    _QT_AUDIO_AVAILABLE = False
    class QObject: pass                               # type: ignore


def bundled_bell_path() -> str:
    """Path to the shipped round-bell WAV, regardless of cwd."""
    return os.path.join(str(ASSETS_DIR), "sounds", "round_bell.wav")


# ─── Player ───────────────────────────────────────────────────────────────────

class RoundBell(QObject):
    """
    Plays the round bell. Owns a persistent QSoundEffect so repeated plays
    don't re-decode the WAV every time. Never raises: a missing file or
    unavailable audio backend just means `play()` is a no-op.
    """

    def __init__(self, parent=None, volume: float = 1.0):
        if _QT_AUDIO_AVAILABLE:
            super().__init__(parent)
        self._path = bundled_bell_path()
        self._sfx = None
        if not _QT_AUDIO_AVAILABLE:
            return
        if not os.path.isfile(self._path):
            log.warning("round bell WAV not found at %s — bell will stay silent", self._path)
            return
        try:
            self._sfx = QSoundEffect(self)
            self._sfx.setSource(QUrl.fromLocalFile(self._path))
            self._sfx.setVolume(max(0.0, min(1.0, volume)))
            self._sfx.setLoopCount(1)
        except Exception as e:                        # pragma: no cover
            log.warning("could not set up round bell playback (%s) — bell will stay silent", e)
            self._sfx = None

    def play(self) -> None:
        """Fire-and-forget bell. Safe to call even mid-playback."""
        if self._sfx is None:
            return
        try:
            # Restart from the beginning if already playing.
            if self._sfx.isPlaying():
                self._sfx.stop()
            self._sfx.play()
        except Exception as e:                        # pragma: no cover
            log.warning("round bell playback failed (%s)", e)

    def path(self) -> str:
        return self._path


__all__ = ["RoundBell", "bundled_bell_path"]
