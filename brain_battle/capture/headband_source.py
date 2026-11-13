"""
headband_source.py
──────────────────
One boxer's headband for the guided recording flow. It owns a real
``eeg_module._MuseDirectReader`` (or, under BRAIN_BATTLE_SIMULATE, the
``simulation.SimMuseReader`` subclass — same reader code, synthetic Bluetooth),
keeps a short rolling buffer for the live EEG display, and exposes battery +
per-sensor contact quality. Recording calls straight through to the reader's
streamed-to-disk CSV path; when an ``LslPublisher`` is attached, every chunk is
also published as a live LSL stream.

Like CameraSlot this is a QObject the flow owns across steps (connect in step 1,
record in step 3), with the step widgets rendering it as a view.
"""
from __future__ import annotations

import logging
from typing import Optional

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, Qt

import brain_battle.capture.contact_quality as cq
import brain_battle.data.app_settings as app_settings
from brain_battle.capture.eeg_module import (
    _RingBuffer, _MuseDirectReader, EEG_CHANNELS, SAMPLE_RATE)

log = logging.getLogger(__name__)

_CONTACT_CHANNELS = EEG_CHANNELS[:4]     # the four skin sensors (not AUX)
_BUF = 32 * SAMPLE_RATE                  # rolling buffer: the longest live timebase


class HeadbandSlot(QObject):
    changed            = pyqtSignal()       # samples / battery / status changed
    connection_changed = pyqtSignal(bool)   # True on connect, False on loss

    def __init__(self, which: str, parent=None):
        super().__init__(parent)
        self.which = which.upper()
        self.device: Optional[dict] = None
        self.publisher = None               # an LslPublisher, set by the flow
        self.battery: Optional[int] = None
        self._reader = None
        self._connected = False
        self._lost = False
        self._ts = _RingBuffer(_BUF, dtype=np.float64)
        self._chan = {c: _RingBuffer(_BUF, dtype=np.float32) for c in EEG_CHANNELS}
        self._recording = False
        self._rec_path = None
        self._rec_origin = None

    # ── lifecycle ──
    def assign(self, device: Optional[dict]) -> None:
        self.device = device
        self.changed.emit()

    @property
    def device_name(self) -> str:
        return (self.device or {}).get("name", "")

    def _make_reader(self):
        import brain_battle.capture.simulation as simulation
        s = app_settings.load()
        kw = dict(address=(self.device or {}).get("address", ""), name=self.device_name,
                  motion=s.record_motion, ppg=s.record_ppg, parent=self)
        if simulation.enabled():
            return simulation.make_muse_reader(boxer=self.which, **kw)
        return _MuseDirectReader(**kw)

    def start(self) -> None:
        if self.device is None or self._reader is not None:
            return
        self._lost = False
        reader = self._make_reader()
        q = Qt.ConnectionType.QueuedConnection
        reader.samples.connect(self._on_samples, q)
        reader.sensor.connect(self._on_sensor, q)
        reader.connected.connect(self._on_connected, q)
        reader.error.connect(self._on_error, q)
        reader.battery.connect(self._on_battery, q)
        self._reader = reader
        reader.start()
        self.changed.emit()

    def stop(self) -> None:
        r = self._reader
        if r is not None:
            try:
                r.stop_recording()
                r.stop()
                r.wait(3000)
            except Exception as e:
                log.warning("stopping Boxer %s's headband: %s", self.which, e)
        self._reader = None
        self._connected = False
        self._recording = False

    shutdown = stop

    def reconnect(self) -> None:
        """Operator pressed Reconnect after a drop — tear down and re-start. A
        recording in progress resumes into the SAME files (append)."""
        was_recording = self._recording
        self.stop()
        self._ts.clear()
        for b in self._chan.values():
            b.clear()
        self.start()
        if was_recording and self._rec_path:
            self.start_recording(self._rec_path, self._rec_origin, append=True)

    # ── recording ──
    def start_recording(self, path, origin_unix, append: bool = False) -> None:
        self._rec_path = path
        self._rec_origin = origin_unix
        if self._reader is None:
            return
        try:
            self._reader.start_recording(path, origin_unix=origin_unix, append=append)
            self._recording = True
        except Exception as e:
            log.error("EEG start_recording failed for Boxer %s: %s", self.which, e)
            self._recording = False

    def stop_recording(self):
        self._recording = False
        if self._reader is not None:
            try:
                return self._reader.stop_recording()
            except Exception as e:
                log.error("EEG stop_recording failed for Boxer %s: %s", self.which, e)
        return None

    def last_recorded_first_unix(self):
        return getattr(self._reader, "_last_first_unix", None) if self._reader else None

    # ── state queries ──
    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def lost(self) -> bool:
        return self._lost

    def contact(self):
        """(summary_line, {channel: status}) over the most recent ~1 s."""
        statuses = {}
        n = min(len(self._ts), SAMPLE_RATE)
        for ch in _CONTACT_CHANNELS:
            arr = self._chan[ch].to_array()
            statuses[ch] = cq.channel_status(arr[-n:]) if n >= 2 else "flat"
        return cq.summarise(statuses), statuses

    def trace(self):
        """(unix_timestamps, {channel: samples}) for the live display, or
        (None, None) before any sample has arrived."""
        if len(self._ts) == 0:
            return None, None
        return self._ts.to_array(), {c: b.to_array() for c, b in self._chan.items()}

    # ── signal handlers ──
    def _on_samples(self, data: np.ndarray, timestamps: np.ndarray) -> None:
        self._ts.extend(timestamps)
        for i, ch in enumerate(EEG_CHANNELS):
            if data.ndim == 2 and data.shape[1] > i:
                self._chan[ch].extend(data[:, i])
        if self.publisher is not None:
            self.publisher.push_eeg(self.which, data, timestamps, self.device_name)
        self.changed.emit()

    def _on_sensor(self, kind: str, data: np.ndarray, timestamps: np.ndarray) -> None:
        if self.publisher is not None:
            self.publisher.push_sensor(kind, self.which, data, timestamps, self.device_name)

    def _on_connected(self):
        self._connected = True
        self._lost = False
        self.connection_changed.emit(True)
        self.changed.emit()

    def _on_error(self, msg: str):
        # Finalise the CSV segment recorded so far (flush + close the writer)
        # before dropping the reader, so a later Reconnect can append to it.
        log.warning("Boxer %s's headband: %s", self.which, msg)
        r, self._reader = self._reader, None
        if r is not None:
            try:
                r.stop_recording()
            except Exception:
                pass
        self._connected = False
        self._lost = True
        self.connection_changed.emit(False)
        self.changed.emit()

    def _on_battery(self, level: int):
        self.battery = int(level)
        self.changed.emit()


__all__ = ["HeadbandSlot"]
