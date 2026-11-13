"""
eeg_module.py — Muse-S headband: Bluetooth scan, connect, stream, record
────────────────────────────────────────────────────────────────────────
What lives here:

  • ``_MuseScanThread``   — finds Muse headbands in Bluetooth range.
  • ``_MuseDirectReader`` — one headband, connected directly over Bluetooth
    (muselsl's ``Muse`` class on the ``bleak`` backend, in-process — no helper
    programs). It emits every chunk of samples as a Qt signal and, while a
    recording is running, streams them to crash-safe CSV files.
  • ``_CsvStreamWriter``  — the crash-safe "append rows from a background
    thread" file writer the reader uses for each signal it records.
  • ``_RingBuffer``       — fixed-size rolling sample store for live displays.

Signals recorded per headband:

  EEG   5 channels (TP9, AF7, AF8, TP10, AUX) · 256 Hz · microvolts
  ACC   accelerometer X/Y/Z · 52 Hz · g              (optional, Settings)
  GYRO  gyroscope X/Y/Z · 52 Hz · degrees/second     (optional, Settings)
  PPG   3 optical pulse channels · 64 Hz             (optional, Settings)

Every timestamp comes from the one capture clock (``capture/clock.py``), so
EEG, video frames and markers share a time axis by construction.

── UNVERIFIED on real hardware ───────────────────────────────────────────────
The arithmetic that places samples on the session axis is unit-tested. What
cannot be confirmed without a real Muse band is how far Bluetooth delivery
delays a sample before the computer stamps it (see AGENTS.md "Deferred work").
The movement and pulse sensors have never been recorded from a real band by
this project at all.
"""
from __future__ import annotations

import csv as _csv
import logging
import os
import queue
import threading
import time
from typing import Optional

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

import brain_battle.capture.clock as clock
from brain_battle.data.signals import (
    SAMPLE_RATE, EEG_CHANNELS, SENSORS, SENSOR_DIR_NAME)

log = logging.getLogger(__name__)



class _RingBuffer:
    """Fixed-size pre-allocated NumPy ring buffer for streaming samples.

    Writes are O(chunk); ``to_array()`` returns the samples oldest → newest
    (a view until the ring wraps, one concatenate after)."""

    __slots__ = ("_buf", "_capacity", "_n_written")

    def __init__(self, capacity: int, dtype=np.float64):
        self._capacity = int(capacity)
        self._buf = np.zeros(self._capacity, dtype=dtype)
        self._n_written = 0  # total samples ever written (monotonic)

    def extend(self, samples) -> None:
        """Append samples, overwriting the oldest when full."""
        if samples is None:
            return
        n = len(samples)
        if n == 0:
            return
        if n >= self._capacity:
            # A chunk bigger than the whole buffer: keep its newest `capacity`
            # samples at index 0, and set _n_written to exactly `capacity` so
            # to_array()'s rotation head (n_written % capacity) stays correct.
            self._buf[:] = samples[-self._capacity:]
            self._n_written = self._capacity
            return
        start = self._n_written % self._capacity
        end = start + n
        if end <= self._capacity:
            self._buf[start:end] = samples
        else:
            split = self._capacity - start
            self._buf[start:] = samples[:split]
            self._buf[:n - split] = samples[split:]
        self._n_written += n

    def __len__(self) -> int:
        return min(self._n_written, self._capacity)

    def to_array(self) -> np.ndarray:
        n = len(self)
        if n == 0:
            return np.empty(0, dtype=self._buf.dtype)
        if self._n_written <= self._capacity:
            return self._buf[:n]
        head = self._n_written % self._capacity
        return np.concatenate((self._buf[head:], self._buf[:head]))

    def clear(self) -> None:
        self._n_written = 0


def boxer_labels() -> dict:
    """{device_name: "BOXER A"/"BOXER B"} from the saved headband mapping
    (edited on the Settings screen)."""
    try:
        import brain_battle.data.app_settings as app_settings
        return app_settings.boxer_labels()
    except Exception:
        return {}


def muse_friendly(raw: str) -> str:
    """'MuseS-7538' → 'Muse S 7538' for display + filenames."""
    return (raw or "Muse").replace("MuseS-", "Muse S ").replace(
        "Muse-S-", "Muse S ").replace("-", " ")


# ─── Bluetooth scan ───────────────────────────────────────────────────────────

class _MuseScanThread(QThread):
    """Scans for Muse headbands in range and emits ``found`` with a list of
    ``{"name", "address"}`` dicts.

    Calls ``muselsl.stream.list_muses`` in-process rather than parsing the
    output of ``muselsl list``: on macOS a device "address" is a long
    CoreBluetooth UUID, and scraping it from text once truncated it."""
    found = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from muselsl.stream import list_muses
            devices, seen = [], set()
            for m in list_muses(backend="bleak") or []:
                name = (m.get("name") or "").strip()
                addr = (m.get("address") or "").strip()
                if not name or not addr or name in seen:
                    continue
                seen.add(name)
                devices.append({"name": name, "address": addr})
            self.found.emit(devices)
        except Exception as exc:
            self.error.emit(str(exc))
        finally:
            try:
                loop.close()
            except Exception:
                pass


# ─── Crash-safe streamed CSV ──────────────────────────────────────────────────

class _CsvStreamWriter:
    """Streams sample chunks to a CSV from a background thread.

    The header is written and fsync'd when the file opens, so the file is valid
    from the first instant. ``put`` only enqueues (it never touches the disk —
    it is called from the Bluetooth callback); the writer thread formats rows,
    flushes at least once a second and fsyncs about every five. After a hard
    kill at any moment the CSV parses and holds everything up to the last
    flush.

    Columns: the channel names, then ``timestamps`` (Unix seconds), then —
    when ``origin_unix`` is given — ``t_session_seconds``.
    """

    def __init__(self, path: str, channels, origin_unix: Optional[float] = None,
                 append: bool = False):
        self.path = path
        self.first_unix: Optional[float] = None
        self._origin = origin_unix
        self._queue: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        cols = list(channels) + ["timestamps"]
        if origin_unix is not None:
            cols.append("t_session_seconds")
        try:
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            # `append` (a headband reconnecting mid-recording) continues the
            # same file without a second header. Line-buffered, so a kill can
            # at worst truncate the one row being written.
            resume = append and os.path.isfile(path) and os.path.getsize(path) > 0
            f = open(path, "a" if resume else "w", buffering=1, newline="",
                     encoding="utf-8")
            if not resume:
                f.write(",".join(cols) + "\n")
                f.flush()
                os.fsync(f.fileno())
        except OSError as e:
            log.error("could not open %s: %s", path, e)
            return
        self._thread = threading.Thread(target=self._loop, args=(f,),
                                        name="csv-stream-writer", daemon=True)
        self._thread.start()

    @property
    def ok(self) -> bool:
        return self._thread is not None

    def put(self, data: np.ndarray, ts: np.ndarray) -> None:
        """Enqueue one chunk: ``data`` is (channels, N), ``ts`` is (N,)."""
        if self._thread is not None:
            self._queue.put_nowait((data, ts))

    def _loop(self, f) -> None:
        writer = _csv.writer(f)
        origin = self._origin
        last_flush = last_fsync = time.monotonic()
        try:
            while True:
                try:
                    data, ts = self._queue.get(timeout=0.2)
                except queue.Empty:
                    data = None
                if data is not None:
                    if self.first_unix is None and len(ts):
                        self.first_unix = float(ts[0])
                    for i in range(data.shape[1]):
                        row = [*data[:, i], ts[i]]
                        if origin is not None:
                            row.append(ts[i] - origin)
                        writer.writerow(row)
                now = time.monotonic()
                if now - last_flush >= 1.0:
                    f.flush()
                    last_flush = now
                    if now - last_fsync >= 5.0:
                        try:
                            os.fsync(f.fileno())
                        except OSError:
                            pass
                        last_fsync = now
                if self._stop.is_set() and self._queue.empty():
                    break
        except Exception as e:  # never let the writer thread die silently
            log.error("CSV writer for %s failed: %s", self.path, e)
        finally:
            try:
                f.flush()
                os.fsync(f.fileno())
                f.close()
            except OSError:
                pass

    def close(self) -> Optional[str]:
        """Drain, flush, close. Returns the path (None if it never opened)."""
        if self._thread is None:
            return None
        self._stop.set()
        self._thread.join(timeout=10.0)
        if self._thread.is_alive():
            log.error("CSV writer for %s did not stop in 10 s — the file may be "
                      "missing its final flush", self.path)
        self._thread = None
        return self.path


def sensor_csv_path(eeg_csv_path: str, kind: str) -> str:
    """Where a headband's ACC/GYRO/PPG CSV goes for a given EEG CSV:
    ``continuous/Muse sensors/<same stem>_<KIND>.csv``."""
    muse_dir = os.path.dirname(os.path.abspath(eeg_csv_path))
    stem = os.path.splitext(os.path.basename(eeg_csv_path))[0]
    return os.path.join(os.path.dirname(muse_dir), SENSOR_DIR_NAME, f"{stem}_{kind}.csv")


# ─── One headband ─────────────────────────────────────────────────────────────

class _MuseDirectReader(QThread):
    """A direct Bluetooth connection to one Muse headband.

        QThread.run() → asyncio loop → bleak → muselsl.muse.Muse
                                         │ callbacks (Bluetooth thread)
                                         ├─ enqueue for the CSV writer(s)
                                         └─ emit Qt signals → UI thread

    ``motion`` adds the accelerometer + gyroscope, ``ppg`` the pulse sensor.
    """

    samples   = pyqtSignal(object, object)       # EEG: (data N×5, unix timestamps N)
    sensor    = pyqtSignal(str, object, object)  # kind, (data N×C, unix timestamps N)
    battery   = pyqtSignal(int)                  # percent
    error     = pyqtSignal(str)
    connected = pyqtSignal()                     # first EEG chunk arrived

    def __init__(self, address: str, name: str = "", motion: bool = False,
                 ppg: bool = False, parent=None):
        super().__init__(parent)
        self.address = address
        self.name = name
        self.motion = bool(motion)
        self.ppg = bool(ppg)
        self._running = False
        self._muse = None
        self._loop = None
        self._first_chunk_seen = False
        self._record_path: Optional[str] = None
        self._eeg_writer: Optional[_CsvStreamWriter] = None
        self._sensor_writers: dict[str, _CsvStreamWriter] = {}
        self._last_first_unix: Optional[float] = None   # first recorded EEG sample

    # ── recording ─────────────────────────────────────────────────────────────

    def start_recording(self, path: str, origin_unix: Optional[float] = None,
                        append: bool = False) -> None:
        """Start streaming to CSV: EEG to ``path``, and each enabled extra
        sensor to ``continuous/Muse sensors/``. Safe to call before the band
        has connected — samples are captured from whenever they start arriving.

        ``origin_unix`` is the session's start on the capture clock; with it
        every row also gets ``t_session_seconds``."""
        if self._eeg_writer is not None:
            self.stop_recording()
        self._last_first_unix = None
        w = _CsvStreamWriter(path, EEG_CHANNELS, origin_unix, append)
        if not w.ok:
            return
        kinds = (["ACC", "GYRO"] if self.motion else []) + (["PPG"] if self.ppg else [])
        self._sensor_writers = {}
        for kind in kinds:
            sw = _CsvStreamWriter(sensor_csv_path(path, kind), SENSORS[kind][1],
                                  origin_unix, append)
            if sw.ok:
                self._sensor_writers[kind] = sw
        self._record_path = path
        self._eeg_writer = w
        if origin_unix is not None:
            log.warning("EEG recording started for %s. Reminder: how late Bluetooth "
                        "delivers samples is UNVERIFIED on real hardware "
                        "(see AGENTS.md, deferred work #0).", self.name or self.address)

    def stop_recording(self) -> Optional[str]:
        """Stop streaming, flush and close the files; return the EEG CSV path
        (None if nothing was recording). Idempotent, callable from any thread."""
        w, self._eeg_writer = self._eeg_writer, None
        sensors, self._sensor_writers = self._sensor_writers, {}
        self._record_path = None
        for sw in sensors.values():
            sw.close()
        if w is None:
            return None
        path = w.close()
        self._last_first_unix = w.first_unix
        return path

    # ── Bluetooth callbacks (muselsl hands us channels-first arrays) ──────────

    def _on_eeg(self, data, timestamps) -> None:
        """One 12-sample EEG chunk: ``data`` (5, 12), ``timestamps`` (12,).
        muselsl reuses its buffers, so copy before handing anything on."""
        if not self._first_chunk_seen:
            self._first_chunk_seen = True
            self.connected.emit()
        try:
            data = np.array(data, dtype=np.float32, copy=True)
            ts = np.array(timestamps, dtype=np.float64, copy=True)
        except Exception as e:
            log.warning("EEG chunk copy failed: %s", e)
            return
        w = self._eeg_writer
        if w is not None:
            w.put(data, ts)
            if self._last_first_unix is None and len(ts):
                self._last_first_unix = float(ts[0])
        self.samples.emit(np.ascontiguousarray(data.T), ts)

    def _on_sensor(self, kind: str, data, timestamps) -> None:
        try:
            data = np.array(data, dtype=np.float32, copy=True)
            ts = np.array(timestamps, dtype=np.float64, copy=True)
        except Exception as e:
            log.warning("%s chunk copy failed: %s", kind, e)
            return
        w = self._sensor_writers.get(kind)
        if w is not None:
            w.put(data, ts)
        self.sensor.emit(kind, np.ascontiguousarray(data.T), ts)

    def _on_imu(self, kind: str, data, timestamps) -> None:
        """ACC / GYRO packets carry three samples under ONE arrival time;
        spread them back over the three sample periods they cover."""
        rate = SENSORS[kind][0]
        t_last = float(timestamps[-1])
        n = np.shape(data)[1]
        self._on_sensor(kind, data, t_last - (np.arange(n - 1, -1, -1) / rate))

    def _on_telemetry(self, timestamp, battery, *_rest) -> None:
        try:
            self.battery.emit(int(round(float(battery))))
        except Exception:
            pass

    # ── thread control ────────────────────────────────────────────────────────

    def stop(self) -> None:
        """Request a clean shutdown. Safe to call from any thread."""
        self._running = False
        loop = self._loop
        if loop is not None and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._request_stop)
            except Exception:
                pass

    def _request_stop(self) -> None:
        if self._muse is not None:
            try:
                self._muse.disconnect()
            except Exception:
                pass
            self._muse = None
        if self._loop is not None:
            self._loop.stop()

    def _keep_alive(self) -> None:
        """macOS drops a quiet Bluetooth link after about a minute; a ping
        every 30 s keeps it up (what muselsl's own stream command does)."""
        if not self._running or self._muse is None or self._loop is None:
            return
        try:
            self._muse.keep_alive()
        except Exception as e:
            log.warning("headband keep-alive failed: %s", e)
        self._loop.call_later(30.0, self._keep_alive)

    def run(self) -> None:
        import asyncio
        try:
            from muselsl.muse import Muse
        except ImportError:
            self.error.emit("muselsl is not installed — run: pip install muselsl")
            return

        self._running = True
        self._first_chunk_seen = False
        self._loop = asyncio.new_event_loop()     # one loop per headband
        asyncio.set_event_loop(self._loop)
        try:
            self._muse = Muse(
                self.address,
                callback_eeg=self._on_eeg,
                callback_telemetry=self._on_telemetry,
                callback_acc=(lambda d, t: self._on_imu("ACC", d, t)) if self.motion else None,
                callback_gyro=(lambda d, t: self._on_imu("GYRO", d, t)) if self.motion else None,
                callback_ppg=(lambda d, t: self._on_sensor("PPG", d, t)) if self.ppg else None,
                backend="bleak",
                name=self.name or None,
                # p50 is the Muse-S preset that switches the pulse sensor on.
                preset=50 if self.ppg else None,
                time_func=clock.unix_now,          # the one capture clock
            )
            if not self._muse.connect():
                self.error.emit(f"Failed to connect to {self.name or self.address}")
                return
            self._muse.start()
            self._loop.call_later(30.0, self._keep_alive)
            self._loop.run_forever()
        except Exception as e:
            self.error.emit(f"Muse connection error: {e}")
        finally:
            try:
                if self._muse is not None:
                    self._muse.disconnect()
            except Exception:
                pass
            self._muse = None
            try:
                self._loop.close()
            except Exception:
                pass
            self._loop = None
            self._running = False


__all__ = ["SAMPLE_RATE", "EEG_CHANNELS", "SENSORS",
           "boxer_labels", "muse_friendly", "sensor_csv_path"]
