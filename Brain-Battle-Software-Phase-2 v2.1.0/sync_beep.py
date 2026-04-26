"""
sync_beep.py
────────────
Produces a loud audible synchronisation beep used at:
  • Baseline start
  • Start of every round

The beep is intentionally short, loud, and tonally distinct so it:
  (a) cues the boxers and operators that a new phase has started, and
  (b) leaves a sharp spike on camera audio tracks that the audio-based
      cross-correlation sync engine (sync_engine.py) can lock onto during
      post-session alignment.

Implementation strategy:
  • Generate a one-second 1 kHz sine burst (with 5 ms linear fade-in/out to
    avoid speaker clicks) at 48 kHz, write to a WAV file on first use.
  • Play it through QSoundEffect which supports low-latency fire-and-forget
    playback on macOS, Windows, and Linux.
  • If QtMultimedia is unavailable (headless unit test / server), fall back
    to writing the WAV to disk silently — no exceptions.
"""

from __future__ import annotations

import os
import struct
import tempfile
import wave

try:
    from PyQt6.QtCore       import QUrl, QObject
    from PyQt6.QtMultimedia import QSoundEffect
    _QT_AUDIO_AVAILABLE = True
except Exception:                                     # pragma: no cover
    _QT_AUDIO_AVAILABLE = False
    class QObject: pass                               # type: ignore


# ─── WAV synthesis (stdlib only — no numpy requirement for this tiny file) ────

def _synth_beep_wav(
    path: str,
    frequency_hz: float = 1000.0,
    duration_sec: float = 1.0,
    sample_rate: int    = 48000,
    amplitude:   float  = 0.9,    # 0…1 relative to int16 full scale
    fade_sec:    float  = 0.005,
) -> str:
    """Write a mono int16 PCM WAV file containing a short sine burst."""
    import math
    total = int(sample_rate * duration_sec)
    fade  = max(1, int(sample_rate * fade_sec))
    peak  = int(32767 * max(0.0, min(1.0, amplitude)))

    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)                # int16
        w.setframerate(sample_rate)
        # Build in chunks to keep memory tiny.
        chunk = 2048
        buf   = bytearray()
        for n in range(total):
            # Gain envelope: linear fade in, plateau, linear fade out.
            if n < fade:
                gain = n / fade
            elif n > total - fade:
                gain = (total - n) / fade
            else:
                gain = 1.0
            sample = int(peak * gain * math.sin(2 * math.pi * frequency_hz * n / sample_rate))
            buf.extend(struct.pack("<h", sample))
            if len(buf) >= chunk * 2:
                w.writeframesraw(bytes(buf))
                buf.clear()
        if buf:
            w.writeframesraw(bytes(buf))
    return path


def _default_beep_path() -> str:
    return os.path.join(tempfile.gettempdir(), "brain_battle_sync_beep.wav")


def ensure_beep_wav() -> str:
    """Create the beep WAV on first call; return its path."""
    path = _default_beep_path()
    if not os.path.isfile(path) or os.path.getsize(path) < 1024:
        _synth_beep_wav(path)
    return path


# ─── Player ───────────────────────────────────────────────────────────────────

class SyncBeep(QObject):
    """
    Plays the synchronisation beep. Owns a persistent QSoundEffect so repeated
    plays don't re-decode the WAV every time.
    """

    def __init__(self, parent=None, volume: float = 1.0):
        if _QT_AUDIO_AVAILABLE:
            super().__init__(parent)
            self._path = ensure_beep_wav()
            self._sfx  = QSoundEffect(self)
            self._sfx.setSource(QUrl.fromLocalFile(self._path))
            self._sfx.setVolume(max(0.0, min(1.0, volume)))
            self._sfx.setLoopCount(1)
        else:
            self._path = ensure_beep_wav()
            self._sfx  = None

    def play(self) -> None:
        """Fire-and-forget beep. Safe to call even mid-playback."""
        if self._sfx is None:
            return
        # Restart from the beginning if already playing.
        if self._sfx.isPlaying():
            self._sfx.stop()
        self._sfx.play()

    def path(self) -> str:
        return self._path


__all__ = ["SyncBeep", "ensure_beep_wav"]
