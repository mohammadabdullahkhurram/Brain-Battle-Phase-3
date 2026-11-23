"""
buzzer_test.py — "Run buzzer test" on Get ready
───────────────────────────────────────────────
Plays 5 short beeps ~1.5 s apart (``timing.N_BEEPS`` /
``timing.BEEP_INTERVAL_SEC``). At every beep both boxers tap their headband
once, in view of the cameras. Afterwards the tap is looked for in every live
camera (frame-difference energy) and in both headbands (EEG artifact,
confirmed by the accelerometer), and ``timing.calibrate`` turns the tap times
into per-device corrections. The result is emitted as the
``timing_calibration`` dict the recording's manifest stores.

The beep time is the capture-clock instant ``play()`` was called. The sound
card's own output delay and the boxers' reaction time are the same for every
device watching one tap, so they cancel in the calibration (ADR-0024).

Under BRAIN_BATTLE_SIMULATE the simulated devices are told when the "taps"
happen (``simulation.schedule_taps``) so the whole path can be tested.
"""
from __future__ import annotations

import logging
import math
import os
import random
import struct
import tempfile
import wave

from PyQt6.QtCore import QObject, QTimer, QUrl, pyqtSignal

import brain_battle.capture.clock as clock
import brain_battle.capture.timing as timing
from brain_battle.support import sound

log = logging.getLogger(__name__)

FIRST_BEEP_AFTER_SEC = 1.0      # time to get ready after pressing the button
SETTLE_AFTER_SEC = 1.2          # wait this long after the last beep, then analyse
_BEEP_WAV = None


def beep_wav_path() -> str:
    """A short 1 kHz beep, written once per run to a temp file (no asset)."""
    global _BEEP_WAV
    if _BEEP_WAV and os.path.isfile(_BEEP_WAV):
        return _BEEP_WAV
    rate, dur = 44100, 0.18
    path = os.path.join(tempfile.gettempdir(), f"brain_battle_beep_{os.getpid()}.wav")
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        n = int(rate * dur)
        frames = bytearray()
        for i in range(n):
            env = min(1.0, i / 200.0, (n - i) / 400.0)
            frames += struct.pack("<h", int(20000 * env * math.sin(2 * math.pi * 1000 * i / rate)))
        w.writeframes(bytes(frames))
    _BEEP_WAV = path
    return path


class BuzzerTest(QObject):
    """One run of the buzzer test over the flow's camera and headband slots."""

    beeped = pyqtSignal(int)            # how many beeps have played (1..5)
    finished = pyqtSignal(object)       # the timing_calibration dict

    def __init__(self, cams, bands: dict, parent=None):
        super().__init__(parent)
        self._cams = [c for c in cams if c.is_live]
        self._bands = {w: b for w, b in bands.items() if b.connected}
        self._beeps: list[float] = []
        self._sfx = None
        self.running = False
        if sound.muted():
            return                  # tests / CI: never touch the audio device at all
        try:
            from PyQt6.QtMultimedia import QSoundEffect
            self._sfx = QSoundEffect(self)
            self._sfx.setSource(QUrl.fromLocalFile(beep_wav_path()))
            self._sfx.setVolume(1.0)
        except Exception as e:                       # pragma: no cover
            log.warning("buzzer test: no sound (%s) — the beeps will be silent", e)

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._beeps = []
        for i in range(timing.N_BEEPS):
            QTimer.singleShot(int(1000 * (FIRST_BEEP_AFTER_SEC + i * timing.BEEP_INTERVAL_SEC)),
                              self._beep)
        QTimer.singleShot(int(1000 * (FIRST_BEEP_AFTER_SEC
                                      + (timing.N_BEEPS - 1) * timing.BEEP_INTERVAL_SEC
                                      + SETTLE_AFTER_SEC)), self._analyse)

    def _beep(self) -> None:
        t = clock.monotonic()
        sound.play(self._sfx, "buzzer-test beep")
        self._beeps.append(t)
        self._simulate_tap(t)
        self.beeped.emit(len(self._beeps))

    @staticmethod
    def _simulate_tap(t_beep: float) -> None:
        try:
            import brain_battle.capture.simulation as simulation
            if simulation.enabled():
                # a person reacts in ~0.15–0.25 s; both boxers tap together
                simulation.schedule_taps([t_beep + 0.18 + random.uniform(-0.04, 0.04)])
        except Exception:
            pass

    def analyse(self) -> dict:
        """Find the taps in what every device delivered and calibrate."""
        beeps = list(self._beeps)
        if not beeps:
            return timing.calibrate([], {})
        lo, hi = timing.TAP_WINDOW
        t0 = beeps[0] - 1.0
        events = {}
        for slot in self._cams:
            ft, e = slot.energy_since(t0)
            events[timing.camera_device(slot.stem)] = [
                timing.detect_camera_tap(ft, e, b + lo, b + hi) for b in beeps]
        for who, band in self._bands.items():
            ts_u, x = band.eeg_since(clock.mono_to_unix(t0))
            at_u, acc = band.acc_since(clock.mono_to_unix(t0))
            ts = ts_u - (clock.mono_to_unix(0.0))          # unix → capture clock
            at = at_u - (clock.mono_to_unix(0.0)) if len(at_u) else None
            events[timing.band_device(who)] = [
                timing.detect_eeg_tap(ts, x, b + lo, b + hi,
                                      at, acc if len(at_u) else None) for b in beeps]
        return timing.calibrate(beeps, events)

    def _analyse(self) -> None:
        try:
            cal = self.analyse()
        except Exception as e:
            log.error("buzzer test analysis failed: %r", e)
            cal = timing.calibrate(self._beeps, {})
        self.running = False
        try:
            import brain_battle.capture.simulation as simulation
            simulation.clear_taps()
        except Exception:
            pass
        self.finished.emit(cal)


__all__ = ["BuzzerTest", "beep_wav_path"]
