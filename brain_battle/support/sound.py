"""
sound.py — the one place the app makes a sound
──────────────────────────────────────────────
Every audible cue (the round bell, the buzzer-test beeps, any test-sound
button) plays through ``play(effect, what)``. When the environment variable
``BRAIN_BATTLE_MUTE`` is set to anything but ``""`` / ``"0"``, nothing is sent
to the audio output; the call only logs that the sound would have played.

``tests/_env.py``, ``tools/soak_test.py`` and CI set it, so a test run never
makes a sound on the computer it runs on.
"""

from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)


def muted() -> bool:
    """True when ``BRAIN_BATTLE_MUTE`` asks for silence (tests, soak, CI)."""
    return os.environ.get("BRAIN_BATTLE_MUTE", "").strip() not in ("", "0")


def play(effect, what: str) -> bool:
    """Play ``effect`` (a ``QSoundEffect``, or anything with ``play()``) unless
    muted. ``what`` names the sound for the log. Restarts an effect that is
    still playing. Returns True when it was sent to the audio output. Never
    raises."""
    if muted():
        log.info("muted (BRAIN_BATTLE_MUTE): the %s would have played", what)
        return False
    if effect is None:
        return False
    try:
        playing = getattr(effect, "isPlaying", None)
        if playing is not None and playing():
            effect.stop()
        effect.play()
        return True
    except Exception as e:                            # pragma: no cover
        log.warning("%s playback failed (%s)", what, e)
        return False


__all__ = ["muted", "play"]
