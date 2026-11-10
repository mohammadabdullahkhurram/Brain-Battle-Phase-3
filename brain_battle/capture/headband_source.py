"""
headband_source.py
──────────────────
One boxer's headband for the guided recording flow. It owns a real
``eeg_module._MuseDirectReader`` (or, under BRAIN_BATTLE_SIMULATE, the
``simulation.SimMuseReader`` subclass — same reader code, synthetic BLE), keeps
a short rolling buffer for the live trace, and exposes battery + per-sensor
contact quality (DESIGN §2/§4). Recording calls straight through to the real
reader's streamed-to-disk CSV path.

Like CameraSlot this is a QObject the flow owns across steps (connect in step 1,
record in step 3), with the step widgets rendering it as a view.
"""
from __future__ import annotations

from typing import Optional

import logging

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal, Qt

import brain_battle.capture.contact_quality as cq

log = logging.getLogger(__name__)
from brain_battle.capture.eeg_module import _RingBuffer, _MuseDirectReader

_CHANNELS = ["TP9", "AF7", "AF8", "TP10"]
_SR = 256
_BUF = 6 * _SR            # ~6 s rolling buffer for the live trace / contact


class HeadbandSlot(QObject):
    changed           = pyqtSignal()        # samples / battery / status changed
    connection_changed = pyqtSignal(bool)   # True on connect, False on loss

    def __init__(self, which: str, parent=None):
        super().__init__(parent)
        self.which = which.upper()
        self.device: Optional[dict] = None
        self._reader = None
        self._connected = False
        self._lost = False
        self.battery: Optional[int] = None
        self._ts = _RingBuffer(_BUF, dtype=np.float64)
        self._chan = {c: _RingBuffer(_BUF, dtype=np.float32) for c in _CHANNELS}
        self._t0 = 0.0
        self._recording = False

    # ── lifecycle ──
    def assign(self, device: Optional[dict]) -> None:
        self.device = device
        self.changed.emit()

    def start(self) -> None:
        if self.device is None or self._reader is not None:
            return
        name = self.device.get("name", "")
        addr = self.device.get("address", "")
        self._lost = False
        try:
            import brain_battle.capture.simulation as simulation
            if simulation.enabled():
                reader = simulation.make_muse_reader(addr, name, boxer=self.which, parent=self)
            else:
                reader = _MuseDirectReader(address=addr, name=name, parent=self)
        except Exception:
            reader = _MuseDirectReader(address=addr, name=name, parent=self)
        reader.samples.connect(self._on_samples, Qt.ConnectionType.QueuedConnection)
        reader.connected.connect(self._on_connected, Qt.ConnectionType.QueuedConnection)
        reader.error.connect(self._on_error, Qt.ConnectionType.QueuedConnection)
        if hasattr(reader, "battery"):
            reader.battery.connect(self._on_battery, Qt.ConnectionType.QueuedConnection)
        self._reader = reader
        reader.start()
        self.changed.emit()

    def stop(self) -> None:
        r = self._reader
        if r is not None:
            try:
                r.stop_recording()
            except Exception:
                pass
            try:
                r.stop()
                r.wait(3000)
            except Exception:
                pass
        self._reader = None
        self._connected = False
        self._recording = False

    def shutdown(self) -> None:
        self.stop()

    def reconnect(self) -> None:
        """Operator pressed Reconnect after a drop — tear down and re-start."""
        was_recording = self._recording
        rec_path = getattr(self, "_rec_path", None)
        rec_origin = getattr(self, "_rec_origin", None)
        self.stop()
        self._clear()
        self.start()
        if was_recording and rec_path:
            # Resume streaming into the SAME CSV (append) so the band's data
            # continues after a reconnect.
            self.start_recording(rec_path, rec_origin, append=True)

    def _clear(self):
        self._ts.clear()
        for b in self._chan.values():
            b.clear()
        self._t0 = 0.0

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
        if self._reader is not None:
            try:
                return self._reader.stop_recording()
            except Exception:
                return None
        self._recording = False

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
        n = min(len(self._ts), _SR)
        for ch in _CHANNELS:
            arr = self._chan[ch].to_array()
            statuses[ch] = cq.channel_status(arr[-n:]) if n >= 2 else "flat"
        return cq.summarise(statuses), statuses

    def trace(self):
        """(ts_rel, {channel: samples}) for the live trace, or (None, None)."""
        if len(self._ts) == 0:
            return None, None
        ts = self._ts.to_array()
        chans = {c: self._chan[c].to_array() for c in _CHANNELS
                 if len(self._chan[c]) == len(ts)}
        return ts, chans

    # ── signal handlers ──
    def _on_samples(self, data: np.ndarray, timestamps: np.ndarray) -> None:
        if self._t0 == 0.0 and len(timestamps):
            self._t0 = float(timestamps[0])
        self._ts.extend(timestamps - self._t0)
        nch = data.shape[1] if data.ndim == 2 else 0
        for i, ch in enumerate(_CHANNELS):
            if nch > i:
                self._chan[ch].extend(data[:, i])
        self.changed.emit()

    def _on_connected(self):
        self._connected = True
        self._lost = False
        self.connection_changed.emit(True)
        self.changed.emit()

    def _on_error(self, msg: str):
        # Finalise the CSV segment recorded so far (flush + close the writer)
        # before dropping the reader, so a later Reconnect can append to it.
        r = self._reader
        self._reader = None
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
