"""
eeg_module.py  —  Brain Battle EEG widgets  (v2)

Changes from v1
───────────────
- SCAN opens a modal device-picker dialog instead of a dropdown
- Each widget locks on a device via _DEVICE_REGISTRY — prevents two blocks
  connecting to the same Muse band; already-claimed devices shown greyed out
- Header always shows connected Muse ID + BOXER A/B label (7538 = Boxer A,
  7564 = Boxer B)
- Two view modes: WAVEFORM (4-channel rolling traces) and BAND POWER
  (delta/theta/alpha/beta/gamma bar chart); toggle buttons in header
- Larger fonts, generous button padding — no more cut-off text
- EEGReviewWidget.load() accepts optional device_name for header display
"""

from __future__ import annotations

import subprocess
import sys
import time
from collections import deque
from typing import Optional

import numpy as np
import pandas as pd
from scipy.signal import welch

import matplotlib
matplotlib.use("QtAgg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, QTimer, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QFrame, QSizePolicy, QDialog,
    QListWidget, QListWidgetItem, QAbstractItemView,
)

# ─── Style constants ──────────────────────────────────────────────────────────

_BG     = "#0E0E12"
_FG     = "#ECEDEE"
_GRID   = "#24242A"
_ACCENT = "#637DFF"

_CHAN_COLORS = {
    "TP9":  "#4a9eff",
    "AF7":  "#a78bfa",
    "TP10": "#34d399",
    "AF8":  "#f97316",
}
_CHANNELS = ["TP9", "AF7", "TP10", "AF8"]

_BANDS = {
    "δ":  (0.5,  4.0,  "#6366f1"),
    "θ":  (4.0,  8.0,  "#8b5cf6"),
    "α":  (8.0,  13.0, "#06b6d4"),
    "β":  (13.0, 30.0, "#10b981"),
    "γ":  (30.0, 45.0, "#f59e0b"),
}

ROLLING_WINDOW_SEC = 10
SAMPLE_RATE        = 256
BUFFER_SECONDS     = 30


class _RingBuffer:
    """Fixed-size pre-allocated NumPy ring buffer for streaming EEG samples.

    Replaces ``collections.deque`` for the per-channel sample stores. The deque
    approach was forcing every redraw to call ``np.array(deque)`` which is
    O(N) per call and allocates fresh memory each tick — a 7680-sample buffer
    × 5 buffers per widget × 2 widgets × 20 Hz = ~1.5 M list-to-array
    conversions per second, the dominant cause of UI freezes when EEG is
    streaming. With this ring, the contiguous read view is O(min(N, window))
    and reuses the backing memory.

    This is the standard pattern used by LSL viewers, MNE-LSL, and OpenViBE
    for real-time biosignal display. Sample writes are O(k) where k is the
    chunk size (typically ≤ 12 from a Muse-S pull_chunk). Reads return a
    NumPy view into the underlying array — no copy unless the caller asks
    for one.
    """

    __slots__ = ("_buf", "_capacity", "_n_written")

    def __init__(self, capacity: int, dtype=np.float64):
        self._capacity = int(capacity)
        self._buf = np.zeros(self._capacity, dtype=dtype)
        self._n_written = 0  # total samples ever written (monotonic)

    def extend(self, samples: np.ndarray) -> None:
        """Append samples to the buffer, overwriting oldest if at capacity."""
        if samples is None:
            return
        n = len(samples)
        if n == 0:
            return
        if n >= self._capacity:
            # Incoming chunk is bigger than the whole buffer — keep only
            # the most recent `_capacity` samples.
            self._buf[:] = samples[-self._capacity:]
            self._n_written += n
            return
        start = self._n_written % self._capacity
        end = start + n
        if end <= self._capacity:
            self._buf[start:end] = samples
        else:
            # Wraps around — split the write
            split = self._capacity - start
            self._buf[start:] = samples[:split]
            self._buf[:n - split] = samples[split:]
        self._n_written += n

    def __len__(self) -> int:
        return min(self._n_written, self._capacity)

    def to_array(self) -> np.ndarray:
        """Return contiguous chronological view of current samples (oldest →
        newest). Copies only if the ring has wrapped. The typical
        steady-state cost after the buffer fills is one np.concatenate."""
        n = len(self)
        if n == 0:
            return np.empty(0, dtype=self._buf.dtype)
        if self._n_written <= self._capacity:
            # Hasn't wrapped yet — first n samples are chronological.
            return self._buf[:n]
        # Has wrapped — head is at (_n_written % _capacity), oldest sample
        # is at that position, newest is just before it.
        head = self._n_written % self._capacity
        return np.concatenate((self._buf[head:], self._buf[:head]))

    def clear(self) -> None:
        self._n_written = 0


# Known Muse IDs → boxer labels
BOXER_LABELS = {
    "MuseS-7538": "BOXER A",
    "MuseS-7564": "BOXER B",
}

# ─── Exclusive device registry ────────────────────────────────────────────────
# Maps device_name → owning EEGLiveWidget. Prevents two blocks sharing a device.

_DEVICE_REGISTRY: dict[str, "EEGLiveWidget"] = {}

# Module-level cache of scan results from the last successful Muse scan.
# Lets the second picker reuse the first scan's results instead of triggering
# a fresh bleak scan — critical on macOS where running a BLE scan while an
# active connection exists can disrupt the connection (the underlying
# CoreBluetooth scan/connect operations contend for the BLE adapter).
_LAST_SCAN_RESULTS: list[dict] = []


def _claim_device(name: str, owner: "EEGLiveWidget") -> bool:
    if name in _DEVICE_REGISTRY and _DEVICE_REGISTRY[name] is not owner:
        return False
    _DEVICE_REGISTRY[name] = owner
    return True


def _release_device(name: str, owner: "EEGLiveWidget"):
    if _DEVICE_REGISTRY.get(name) is owner:
        del _DEVICE_REGISTRY[name]


def _claimed_devices() -> set[str]:
    return set(_DEVICE_REGISTRY.keys())


def _any_muse_connected() -> bool:
    """True if any EEG widget currently holds an active streaming connection.
    Used to decide whether a fresh BLE scan would risk disrupting it."""
    return any(
        getattr(w, "_is_streaming", False)
        for w in _DEVICE_REGISTRY.values()
    )


# ─── Tab-button style helper ──────────────────────────────────────────────────

def _tab_style(active: bool) -> str:
    import design_tokens as _dt
    if active:
        return (
            f"background:{_dt.accent_ghost()}; "
            f"border:1px solid {_dt.accent()}; "
            f"color:{_dt.accent()}; "
            f"border-radius:{_dt.RADIUS_SM}px; "
            f"font-family:{_dt.FONT_UI}; font-size:10px; font-weight:600; "
            f"letter-spacing:0.8px; padding:0 10px;"
        )
    return (
        f"background:transparent; "
        f"border:1px solid {_dt.border_strong()}; "
        f"color:{_dt.text_secondary()}; "
        f"border-radius:{_dt.RADIUS_SM}px; "
        f"font-family:{_dt.FONT_UI}; font-size:10px; "
        f"letter-spacing:0.8px; padding:0 10px;"
    )


def _style_ax(ax):
    ax.set_facecolor(_BG)
    ax.tick_params(colors=_FG, labelsize=8)
    for spine in ax.spines.values():
        spine.set_edgecolor(_GRID)
    ax.grid(True, color=_GRID, linewidth=0.4, linestyle="--")


# ─── BLE scan thread ─────────────────────────────────────────────────────────

class _MuseScanThread(QThread):
    """Scans for Muse-S bands in range, returns name+address pairs.

    Uses `muselsl.stream.list_muses(backend='bleak')` directly inside an
    asyncio loop on the thread, instead of parsing the subprocess output
    of `python -m muselsl list`. The subprocess parsing was the source of
    a real bug on macOS: macOS CoreBluetooth exposes device addresses as
    UUIDs (e.g. `D6AF3274-7C8D-XXXX-XXXX-XXXXXXXXXXXX`) not MAC addresses,
    and the regex we used to scrape `Found device X, MAC Address Y`
    didn't include hyphens in the address character class. We were
    capturing only `D6AF3274` from the full UUID, which then failed to
    match when bleak tried to connect (`Device with address D6AF3274
    was not found`).

    Calling `list_muses` in-process returns Python dicts directly with
    the full untruncated address — no parsing required.
    """
    found = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            from muselsl.stream import list_muses
            muses = list_muses(backend="bleak")
            devices = []
            seen = set()
            for m in muses or []:
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
            try: loop.close()
            except Exception: pass


# ─── LSL reader thread ────────────────────────────────────────────────────────

class _MuseDirectReader(QThread):
    """Direct BLE connection to a Muse-S band — no subprocess, no LSL.

    Replaces the previous `subprocess(muselsl stream) → LSL pubsub → inlet`
    pipeline with an in-process `muselsl.muse.Muse` instance talking BLE
    via `bleak`. This is what the older muse_camera_synchronization project
    did and what every serious BLE app does. The previous architecture had
    three problems that this fixes:

    1. **Two BLE-touching subprocesses per Muse.** `muselsl stream` to publish
       LSL, then `muselsl record` opening a separate LSL inlet to write CSV.
       Plus our own LSL inlet for live preview. With two Muses, that's six
       BLE-adjacent processes contending for OS resources. Connecting one
       Muse made the other struggle because all those subprocesses were
       fighting for BLE adapter time.

    2. **LSL network pubsub for in-process data.** We were running an entire
       network protocol stack to read data from our own machine. LSL is
       designed for cross-machine sync, not in-process IPC. Overhead per
       sample was non-trivial.

    3. **No control over disconnect detection.** muselsl subprocess crashes
       were silent because we redirected stderr to DEVNULL (had to, the pipe
       drain threads were causing app-shutdown crashes — see v2.4 changelog).

    Direct architecture:
        QThread.run() → asyncio loop → bleak BLE client → muselsl.muse.Muse
                                            ↓ callback (eeg samples)
                                          emit `samples` signal
                                            ↓ QueuedConnection
                                      EEGLiveWidget._on_samples()

    Same Qt signal interface as the old `_LSLReaderThread` so the EEGLiveWidget
    code doesn't need to know which transport is in use.
    """

    samples   = pyqtSignal(object, object)   # (data NxC, timestamps N)
    error     = pyqtSignal(str)
    connected = pyqtSignal()                  # emitted on first chunk

    def __init__(self, address: str, name: str = "", parent=None):
        super().__init__(parent)
        self.address = address
        self.name = name
        self._running = False
        self._muse = None
        self._loop = None
        self._first_chunk_seen = False

        # Optional CSV-recording state. Old code held all samples in memory
        # and flushed on disconnect. For a typical 15-min session this is
        # ~9 MB per Muse — well within memory budget. Eliminates the need
        # for a separate `muselsl record` subprocess.
        self._record_path: Optional[str] = None
        self._rec_samples: list[np.ndarray] = []     # list of (channels=5, N=12) arrays
        self._rec_timestamps: list[np.ndarray] = []  # list of (N=12,) arrays

    # ── public API ────────────────────────────────────────────────────────────

    def start_recording(self, path: str) -> None:
        """Begin accumulating samples into an in-memory buffer.
        Calls to flush_recording() write the CSV. Safe to call before
        the BLE connection is established — samples received from that
        point onward are captured."""
        self._record_path = path
        self._rec_samples = []
        self._rec_timestamps = []

    def stop_recording(self) -> str | None:
        """Flush accumulated samples to CSV at the path set by
        start_recording(). Returns the path written or None if nothing
        was recorded. Safe to call from any thread — the actual file
        write happens here, not on the BLE thread."""
        path = self._record_path
        if not path or not self._rec_timestamps:
            self._record_path = None
            return None
        try:
            timestamps = np.concatenate(self._rec_timestamps)
            # samples list elements are (5_channels, 12_samples) → stack to
            # (5, total_N) then transpose to (total_N, 5) for CSV row order.
            eeg = np.concatenate(self._rec_samples, axis=1).T
            df = pd.DataFrame(
                eeg, columns=["TP9", "AF7", "AF8", "TP10", "AUX"]
            )
            df["timestamps"] = timestamps
            import pathlib
            pathlib.Path(path).parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=False)
        except Exception as e:
            print(f"[muse-direct] CSV write failed for {path}: {e}")
            path = None
        finally:
            self._record_path = None
            self._rec_samples = []
            self._rec_timestamps = []
        return path

    def stop(self) -> None:
        """Request a clean shutdown. Safe to call from any thread."""
        self._running = False
        loop = self._loop
        if loop is not None and loop.is_running():
            # Schedule disconnect on the loop's thread. Don't await it
            # from here — calling await across thread boundaries deadlocks.
            try:
                loop.call_soon_threadsafe(self._request_stop)
            except Exception:
                pass

    # ── BLE callback (runs on bleak's asyncio thread) ────────────────────────

    def _on_eeg(self, data, timestamps) -> None:
        """Muse fires this for every 12-sample chunk it decodes.
        `data` is shape (5, 12), `timestamps` is shape (12,).

        This callback runs on bleak's CoreBluetooth dispatch queue thread
        (macOS) or its asyncio loop thread (Linux/Windows). We do as little
        work as possible here:
          1. Mark connected on first chunk.
          2. Append raw arrays to record buffer (if recording).
          3. Emit a contiguous copy to the main thread via Qt's queued
             connection.

        The copies are deliberate. muselsl reuses its internal buffers
        (`self.data` in Muse._handle_eeg gets overwritten on every chunk),
        so the array reference we receive points to memory that will be
        mutated by the next chunk. Without a copy, the main thread's
        receiver could see torn or stale data. Cheap: ~60 floats per chunk.
        """
        if not self._first_chunk_seen:
            self._first_chunk_seen = True
            self.connected.emit()

        # Make a real copy of the data — muselsl's _handle_eeg reuses its
        # internal `self.data` buffer for every chunk, so without an
        # explicit copy here, references handed off via the signal could
        # be overwritten before the receiver thread processes them.
        try:
            data_copy = np.array(data, dtype=np.float32, copy=True)
            ts_copy   = np.array(timestamps, dtype=np.float64, copy=True)
        except Exception as e:
            print(f"[muse-direct] sample copy failed: {e}")
            return

        if self._record_path is not None:
            # channels-first layout is most efficient for storage —
            # transpose only once at flush time.
            self._rec_samples.append(data_copy)
            self._rec_timestamps.append(ts_copy)

        # Emit to main thread. Transpose to (N_samples, N_channels) which
        # is what _on_samples expects. np.ascontiguousarray ensures the
        # transposed view is materialised into a real contiguous buffer
        # so the receiver gets cache-friendly memory.
        try:
            arr = np.ascontiguousarray(data_copy.T)  # (12, 5), C-contiguous
            self.samples.emit(arr, ts_copy)
        except Exception as e:
            print(f"[muse-direct] samples emit failed: {e}")

    # ── BLE thread ────────────────────────────────────────────────────────────

    def _request_stop(self) -> None:
        """Called via call_soon_threadsafe — runs on the bleak loop thread."""
        if self._muse is not None:
            try: self._muse.disconnect()
            except Exception: pass
            self._muse = None
        if self._loop is not None:
            self._loop.stop()

    def _schedule_keep_alive(self) -> None:
        """Schedule the next keep-alive ping 30s out, recursively.
        Runs on the asyncio loop's thread (our QThread). Send 'k' via
        the existing _write_cmd_str path inside muselsl which is what
        Muse.keep_alive() does."""
        if self._loop is None or not self._running:
            return
        try:
            self._loop.call_later(30.0, self._do_keep_alive)
        except Exception as e:
            print(f"[muse-direct] keep_alive schedule failed: {e}")

    def _do_keep_alive(self) -> None:
        """Fired by call_later. Send the ping and re-schedule."""
        if not self._running or self._muse is None:
            return
        try:
            self._muse.keep_alive()
        except Exception as e:
            # A failed ping isn't fatal — bleak may handle the
            # reconnect, or the next data chunk will surface the
            # disconnect via the existing error path.
            print(f"[muse-direct] keep_alive ping failed: {e}")
        # Re-schedule for the next interval
        self._schedule_keep_alive()

    def run(self) -> None:
        """QThread entry. Sets up the bleak asyncio loop and runs Muse()."""
        import asyncio
        try:
            from muselsl.muse import Muse
        except ImportError:
            self.error.emit("muselsl not installed — run: pip install muselsl")
            return

        self._running = True
        self._first_chunk_seen = False
        # Each Muse needs its own asyncio loop. New loop on this thread.
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)

        try:
            self._muse = Muse(
                self.address,
                callback_eeg=self._on_eeg,
                backend="bleak",
                name=self.name or None,
            )
            # connect() blocks until BLE handshake completes (or fails).
            # On bleak it kicks off coroutines on our loop; the call
            # itself returns a bool when done.
            ok = self._muse.connect()
            if not ok:
                self.error.emit(f"Failed to connect to {self.name or self.address}")
                return
            self._muse.start()

            # Schedule a periodic keep-alive ping to prevent macOS
            # CoreBluetooth from dropping the connection during quiet
            # periods. Muse.keep_alive() sends a single 'k' control
            # command. macOS will tear down a BLE link if no traffic
            # flows for ~60 seconds; a ping every 30s keeps it warm.
            # This is the standard pattern for long-running bleak
            # connections and matches what muselsl's own `stream` command
            # does internally.
            self._schedule_keep_alive()

            # Run the loop until stop() schedules its termination.
            self._loop.run_forever()
        except Exception as e:
            self.error.emit(f"Muse connection error: {e}")
        finally:
            try:
                if self._muse is not None:
                    self._muse.disconnect()
            except Exception: pass
            self._muse = None
            try:
                self._loop.close()
            except Exception: pass
            self._loop = None
            self._running = False


# ─── LSL reader thread (legacy, kept for backward compat) ─────────────────────

class _LSLReaderThread(QThread):
    samples   = pyqtSignal(object, object)
    error     = pyqtSignal(str)
    connected = pyqtSignal()   # emitted on first chunk — confirms BLE+LSL working

    def __init__(self, stream_name: str = "", parent=None):
        super().__init__(parent)
        self.stream_name = stream_name
        self._running = False

    def run(self):
        try:
            import pylsl
        except ImportError:
            self.error.emit("pylsl not installed — run: pip install pylsl")
            return

        self._running = True

        while self._running:
            inlet = None
            try:
                # Resolve with short timeout so we retry quickly
                streams = pylsl.resolve_byprop("type", "EEG", timeout=1.0)
                if not streams:
                    import time; time.sleep(0.5)
                    continue

                chosen = streams[0]
                if self.stream_name:
                    for s in streams:
                        if self.stream_name.lower() in s.name().lower():
                            chosen = s; break

                # recover=True: pylsl automatically reconnects on transmission
                # errors instead of raising LostError — transparent to our code.
                # max_buflen=360: 360s buffer survives extended reconnect attempts.
                inlet = pylsl.StreamInlet(
                    chosen, max_buflen=360, recover=True
                )
                inlet.open_stream()

                consecutive_empty = 0
                received_first_chunk = False

                while self._running:
                    try:
                        chunk, ts = inlet.pull_chunk(timeout=0.1, max_samples=12)
                    except Exception:
                        # Any inlet error — break inner loop and re-resolve
                        break

                    if chunk:
                        consecutive_empty = 0
                        if not received_first_chunk:
                            received_first_chunk = True
                            self.connected.emit()
                        self.samples.emit(
                            np.array(chunk, dtype=np.float32),
                            np.array(ts,    dtype=np.float64),
                        )
                    else:
                        pass  # pylsl recover=True handles reconnection
            except Exception:
                pass
            finally:
                if inlet:
                    try: inlet.close_stream()
                    except Exception: pass
                inlet = None

            if self._running:
                import time; time.sleep(0.5)

    def stop(self):
        self._running = False
        self.quit()
        self.wait(5000)


# ─── Device picker dialog ─────────────────────────────────────────────────────

class DevicePickerDialog(QDialog):
    """
    Modal dialog: scans for Muse-S devices, lets user pick one.
    Devices already claimed by another block are greyed out and unselectable.
    """

    def __init__(self, exclude_names: set = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Select Muse-S Device")
        self.setMinimumWidth(400)
        self.setMinimumHeight(320)
        self.setStyleSheet("""
            QDialog { background-color: #0E0E12; }
            QLabel  { color: #ECEDEE; font-family: 'Inter'; font-size: 12px; }
            QListWidget {
                background: #0E0E12; border: 1px solid #2A2A30;
                color: #ECEDEE; font-family: 'Inter'; font-size: 12px;
                outline: none;
            }
            QListWidget::item { padding: 12px 16px; border-bottom: 1px solid #24242A; }
            QListWidget::item:selected { background: #1F2538; color: #637DFF; }
            QListWidget::item:disabled { color: #2A2A30; background: transparent; }
            QPushButton {
                background: #1C1C21; border: 1px solid #2A2A30; color: #ECEDEE;
                font-family: 'Inter'; font-size: 12px;
                padding: 8px 20px; min-width: 90px;
            }
            QPushButton:hover { border-color: #A1A1AA; color: #ffffff; }
            QPushButton#scan_btn {
                background: #1F2538; border-color: #637DFF; color: #637DFF;
            }
            QPushButton#connect_btn {
                background: #1F2538; border-color: #637DFF; color: #637DFF;
            }
            QPushButton:disabled { color: #2A2A30; border-color: #2A2A30; }
        """)

        self.exclude_names = exclude_names or set()
        self.selected_device: Optional[dict] = None

        layout = QVBoxLayout(self)
        layout.setSpacing(14)
        layout.setContentsMargins(20, 20, 20, 20)

        self._status = QLabel("Click SCAN to search for nearby Muse-S devices.")
        self._status.setStyleSheet(
            "color: #A1A1AA; font-family: 'Inter'; font-size: 12px;"
        )
        layout.addWidget(self._status)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.setMinimumHeight(180)
        self._list.itemSelectionChanged.connect(self._on_sel_changed)
        layout.addWidget(self._list)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(10)

        self._scan_btn = QPushButton("SCAN FOR DEVICES")
        self._scan_btn.setObjectName("scan_btn")
        self._scan_btn.clicked.connect(self._do_scan)

        self._ok_btn = QPushButton("CONNECT")
        self._ok_btn.setObjectName("connect_btn")
        self._ok_btn.setEnabled(False)
        self._ok_btn.clicked.connect(self._accept)

        cancel = QPushButton("CANCEL")
        cancel.clicked.connect(self.reject)

        btn_row.addWidget(self._scan_btn)
        btn_row.addStretch()
        btn_row.addWidget(self._ok_btn)
        btn_row.addWidget(cancel)
        layout.addLayout(btn_row)

    def showEvent(self, event):
        """When the picker opens, decide whether to use cached scan results
        or trigger a fresh BLE scan.

        Why this matters on macOS: CoreBluetooth runs scan and connect
        operations on the same shared BLE adapter. If a Muse is actively
        streaming and we kick off a fresh scan from the second picker,
        the adapter contention can knock the first Muse off (the
        2-minutes-then-disconnect symptom user reported). Best to reuse
        the device list from the previous scan in that case.

        - If no Muse is connected AND no cached devices → auto-scan.
        - If we have cached devices (regardless of connection state) →
          populate from cache; user can hit RESCAN explicitly to force
          a fresh scan if their device list seems stale.
        """
        super().showEvent(event)
        if _LAST_SCAN_RESULTS:
            # Reuse cached results — safe even with active connections.
            self._on_found(list(_LAST_SCAN_RESULTS))
            # _on_found calls _stop_animation which sets the button text
            # back to "SCAN FOR DEVICES" — override after the fact so the
            # user sees this is a rescan, not a first scan.
            self._scan_btn.setText("RESCAN")
        elif not _any_muse_connected():
            # No cache yet and nothing connected → scan automatically.
            self._do_scan(force=True)
        else:
            # Something connected but no cache — let the user decide
            # whether to risk a scan. Warn them in the status text.
            self._status.setText(
                "A Muse-S is already connected. Scanning may briefly "
                "interrupt that connection on macOS — click SCAN if needed."
            )

    def _do_scan(self, force: bool = False):
        """Trigger a fresh BLE scan. If a Muse is connected and force is
        False (the default for the SCAN button), prompt the user.

        Direct invocation paths set force=True. This guard is here as a
        safety net specifically against the macOS adapter-contention
        issue described in showEvent.
        """
        if not force and _any_muse_connected():
            from PyQt6.QtWidgets import QMessageBox
            reply = QMessageBox.question(
                self,
                "Scan with active connection?",
                "A Muse-S is currently streaming. Running a new Bluetooth "
                "scan may briefly disrupt that connection on macOS.\n\n"
                "Scan anyway?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        self._scan_btn.setEnabled(False)
        self._ok_btn.setEnabled(False)
        self._list.clear()
        self._status.setText("Searching for Muse-S devices via Bluetooth…")
        self._dot_count = 0
        self._dot_timer = QTimer(self)
        self._dot_timer.setInterval(500)
        self._dot_timer.timeout.connect(self._animate_scan_btn)
        self._dot_timer.start()
        self._thread = _MuseScanThread(self)
        self._thread.found.connect(self._on_found)
        self._thread.error.connect(self._on_error)
        self._thread.start()

    def _animate_scan_btn(self):
        self._dot_count = (self._dot_count + 1) % 4
        dots = "." * self._dot_count
        self._scan_btn.setText(f"SCANNING{dots}   ")

    def _stop_animation(self):
        if hasattr(self, "_dot_timer") and self._dot_timer.isActive():
            self._dot_timer.stop()
        self._scan_btn.setText("SCAN FOR DEVICES")
        self._scan_btn.setEnabled(True)

    def _on_found(self, devices: list):
        self._stop_animation()
        self._list.clear()

        # Persist into the module-level cache only when this came from a
        # real scan thread (not from showEvent's cache replay). The cache
        # replay calls _on_found directly without setting up a thread, so
        # checking for a present _thread attribute is sufficient.
        if getattr(self, "_thread", None) is not None:
            global _LAST_SCAN_RESULTS
            _LAST_SCAN_RESULTS = list(devices)

        if not devices:
            self._status.setText(
                "No devices found. Make sure Muse-S is on and nearby."
            )
            return
        self._status.setText(
            f"Found {len(devices)} device(s). Select one then click CONNECT."
        )
        for d in devices:
            name = d.get("name", "Unknown")
            addr = d.get("address", "")
            boxer = BOXER_LABELS.get(name, "")
            in_use = name in self.exclude_names
            text = f"  {name}   {addr}"
            if boxer:
                text += f"   [{boxer}]"
            if in_use:
                text += "   — in use"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, d)
            if in_use:
                item.setFlags(
                    item.flags()
                    & ~Qt.ItemFlag.ItemIsSelectable
                    & ~Qt.ItemFlag.ItemIsEnabled
                )
            self._list.addItem(item)

    def _on_error(self, msg: str):
        self._stop_animation()
        self._status.setText(f"Error: {msg}")

    def _on_sel_changed(self):
        self._ok_btn.setEnabled(bool(self._list.selectedItems()))

    def _accept(self):
        items = self._list.selectedItems()
        if items:
            self.selected_device = items[0].data(Qt.ItemDataRole.UserRole)
            self.accept()


# ─── Band-power helper ────────────────────────────────────────────────────────

def _band_powers(y: np.ndarray, sr: int = SAMPLE_RATE) -> dict:
    if len(y) < sr // 2:
        return {b: 0.0 for b in _BANDS}
    freqs, psd = welch(y, fs=sr, nperseg=min(len(y), sr * 2))
    # np.trapz was removed in NumPy 2.0; np.trapezoid is the replacement.
    # Fall back to trapz for NumPy < 2.0 so older installs still work.
    _trapz = getattr(np, "trapezoid", None) or np.trapz
    total = _trapz(psd, freqs) or 1.0
    return {
        name: float(_trapz(psd[(freqs >= lo) & (freqs <= hi)],
                           freqs[(freqs >= lo) & (freqs <= hi)]) / total)
        for name, (lo, hi, _) in _BANDS.items()
    }


# ─── 1. EEGLiveWidget ────────────────────────────────────────────────────────

class EEGLiveWidget(QFrame):
    """
    Live rolling EEG display for one Muse-S device.
    Two views: WAVEFORM | BAND POWER. Device chosen via DevicePickerDialog.
    """

    def __init__(self, channel_id: int = 1, parent=None):
        super().__init__(parent)
        self.channel_id = channel_id
        self.setObjectName("panel")
        self.setMinimumHeight(200)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        buf = BUFFER_SECONDS * SAMPLE_RATE
        # NumPy ring buffers — see _RingBuffer for the rationale.
        # Critical for keeping the UI responsive when EEG is streaming.
        self._ts_buf = _RingBuffer(buf, dtype=np.float64)
        self._chan_bufs: dict[str, _RingBuffer] = {
            c: _RingBuffer(buf, dtype=np.float32) for c in _CHANNELS
        }
        self._stream_proc:      Optional[subprocess.Popen] = None
        self._record_proc:      Optional[subprocess.Popen] = None
        self._reader:           Optional[_LSLReaderThread] = None
        self._t0:               float = 0.0
        self._is_streaming:     bool  = False
        self._connected_device: Optional[dict] = None
        self._save_path:        str   = ""
        self._view_mode:        str   = "waveform"
        self._rec_indicator_active: bool = False

        self._build_ui()
        self._build_waveform_plot()
        self._build_bandpower_plot()
        self._show_view("waveform")

        self._draw_timer = QTimer(self)
        self._draw_timer.setInterval(50)
        self._draw_timer.timeout.connect(self._redraw)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        import design_tokens as _dt
        self._dt = _dt
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # Single compact header — title left, view toggles + connect button right.
        # DEVICE picker moved into overflow menu (⋯) to keep header under 32px.
        self._bar = QWidget()
        self._bar.setFixedHeight(34)
        bl = QHBoxLayout(self._bar)
        bl.setContentsMargins(14, 0, 10, 0); bl.setSpacing(6)

        self._status_dot = QLabel("●"); self._status_dot.setFixedWidth(12)
        self._title_lbl = QLabel(f"EEG {self.channel_id}")

        self._wave_btn = QPushButton("WAVE")
        self._wave_btn.setFixedHeight(22); self._wave_btn.setFixedWidth(50)
        self._wave_btn.clicked.connect(lambda: self._show_view("waveform"))

        self._band_btn = QPushButton("BANDS")
        self._band_btn.setFixedHeight(22); self._band_btn.setFixedWidth(60)
        self._band_btn.clicked.connect(lambda: self._show_view("bandpower"))

        self._connect_btn = QPushButton("Connect")
        self._connect_btn.setFixedHeight(22); self._connect_btn.setMinimumWidth(70)
        self._connect_btn.setEnabled(False)
        self._connect_btn.clicked.connect(self._toggle_stream)

        # Overflow (⋯) opens device picker
        self._pick_btn = QPushButton("⋯")
        self._pick_btn.setFixedHeight(22); self._pick_btn.setFixedWidth(24)
        self._pick_btn.clicked.connect(self._open_picker)
        self._pick_btn.setToolTip("Select Muse-S device")

        bl.addWidget(self._status_dot)
        bl.addWidget(self._title_lbl)
        bl.addStretch()
        bl.addWidget(self._wave_btn)
        bl.addWidget(self._band_btn)
        bl.addSpacing(4)
        bl.addWidget(self._connect_btn)
        bl.addWidget(self._pick_btn)

        outer.addWidget(self._bar)

        # Canvas area — the point of the widget, gets all the vertical space.
        self._canvas_container = QWidget()
        self._canvas_container.setMinimumHeight(100)
        self._canvas_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self._canvas_layout = QVBoxLayout(self._canvas_container)
        self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._canvas_layout.setSpacing(0)
        outer.addWidget(self._canvas_container, 1)

        # Placeholder lives INSIDE the canvas area so it can't collide with
        # the header buttons. Centered in the canvas, plain text.
        self._placeholder = QLabel(
            f"Select & connect a Muse-S to begin streaming"
        )
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setWordWrap(True)
        self._canvas_layout.addWidget(self._placeholder)

        self._restyle()
        _dt.on_theme_change(self._restyle)

    def _restyle(self):
        dt = self._dt
        self._bar.setStyleSheet(
            f"background-color: {dt.canvas_bg()}; "
            f"border-bottom: 1px solid {dt.border()};"
        )
        if not getattr(self, "_is_streaming", False):
            self._status_dot.setStyleSheet(f"color: {dt.text_tertiary()}; font-size: 12px;")
        self._title_lbl.setStyleSheet(
            f"color: {dt.text_secondary()}; font-size: 12px; font-family: {dt.FONT_UI}; font-weight: 600;"
        )
        active = getattr(self, "_active_view", "waveform")
        self._wave_btn.setStyleSheet(_tab_style(active == "waveform"))
        self._band_btn.setStyleSheet(_tab_style(active == "bandpower"))
        self._pick_btn.setStyleSheet(
            f"QPushButton {{ background: transparent; color: {dt.text_secondary()}; "
            f"border: 1px solid {dt.border_strong()}; border-radius: {dt.RADIUS_SM}px; "
            f"font-family: {dt.FONT_UI}; font-size: 14px; font-weight: 700; }}"
            f"QPushButton:hover {{ color: {dt.text_primary()}; border-color: {dt.text_secondary()}; }}"
        )
        self._connect_btn.setStyleSheet(
            f"QPushButton {{ background: {dt.accent_ghost()}; color: {dt.accent()}; "
            f"border: 1px solid {dt.accent()}; border-radius: {dt.RADIUS_SM}px; "
            f"font-family: {dt.FONT_UI}; font-size: 11px; font-weight: 600; padding: 0 10px; }}"
            f"QPushButton:hover {{ background: {dt.accent()}; color: #FFFFFF; }}"
            f"QPushButton:disabled {{ background: transparent; color: {dt.text_tertiary()}; "
            f"border-color: {dt.border_strong()}; }}"
        )
        self._canvas_container.setStyleSheet(f"background-color: {dt.card_bg()};")
        self._placeholder.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-size: 11px; font-family: {dt.FONT_UI};"
            f"padding: 20px;"
        )

    # ── Plot construction ─────────────────────────────────────────────────────

    def _build_waveform_plot(self):
        # pyqtgraph-backed replacement for the matplotlib FigureCanvas.
        # ~10× the frame rate, lower CPU usage, and no QtAgg blit stalls.
        # See eeg_plot_pg.WaveformPlot for rendering details.
        from eeg_plot_pg import WaveformPlot
        self._wave_canvas = WaveformPlot(window_sec=ROLLING_WINDOW_SEC)
        self._canvas_layout.addWidget(self._wave_canvas)
        self._wave_canvas.setVisible(False)

    def _build_bandpower_plot(self):
        from eeg_plot_pg import BandPowerPlot
        self._band_canvas = BandPowerPlot(bands=_BANDS)
        self._canvas_layout.addWidget(self._band_canvas)
        self._band_canvas.setVisible(False)

    def _show_view(self, mode: str):
        self._view_mode = mode
        self._wave_btn.setStyleSheet(_tab_style(mode == "waveform"))
        self._band_btn.setStyleSheet(_tab_style(mode == "bandpower"))
        if self._is_streaming:
            self._wave_canvas.setVisible(mode == "waveform")
            self._band_canvas.setVisible(mode == "bandpower")

    # ── Device picker ─────────────────────────────────────────────────────────

    def _open_picker(self):
        already_claimed = _claimed_devices()
        if self._connected_device:
            already_claimed -= {self._connected_device["name"]}

        dlg = DevicePickerDialog(exclude_names=already_claimed, parent=self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_device:
            device = dlg.selected_device
            name = device.get("name", "")
            if not _claim_device(name, self):
                return
            if self._connected_device and self._connected_device["name"] != name:
                _release_device(self._connected_device["name"], self)
            self._connected_device = device
            boxer = BOXER_LABELS.get(name, "")
            # Keep the title compact — EEG widgets live in a narrow right
            # rail in the new Live Monitor layout, so "MuseS-7538 [BOXER A]"
            # is already longer than fits. Use just "EEG 1 · A · 7538".
            short_id = name.split("-")[-1] if "-" in name else name[-4:]
            short_boxer = boxer.replace("BOXER ", "") if boxer else ""
            if short_boxer:
                label = f"EEG {self.channel_id}  ·  {short_boxer}  ·  {short_id}"
            else:
                label = f"EEG {self.channel_id}  ·  {short_id}"
            self._title_lbl.setText(label)
            self._title_lbl.setStyleSheet(
                "color: #ECEDEE; font-size: 12px; font-family: 'Inter';"
            )
            self._connect_btn.setEnabled(True)

    # ── Stream control ────────────────────────────────────────────────────────

    def start_stream(self, device_name: str = "", save_path: str = ""):
        """Connect to the selected Muse band directly over BLE.

        Replaces the old subprocess(muselsl stream) + LSL inlet pipeline
        with an in-process `_MuseDirectReader` (which wraps
        `muselsl.muse.Muse` + bleak). The Qt signal interface is identical
        to the old `_LSLReaderThread`, so the rest of the widget code
        doesn't change.

        Why: with two Muses, the old approach spawned 2 stream + 2 record
        subprocesses (4 total BLE-touching processes plus an LSL pubsub
        layer for in-process IPC). Connecting one Muse made the other
        struggle because the OS BLE stack was being contended by far too
        many processes. Direct BLE = zero subprocess overhead.
        """
        if self._is_streaming:
            return
        if not self._connected_device:
            print("[muse-direct] start_stream called with no selected device")
            return
        name = self._connected_device.get("name", device_name)
        address = self._connected_device.get("address", "")
        if not address:
            self._on_stream_error(
                f"No BLE address for {name} — pick the device again from the list."
            )
            return

        self._reader = _MuseDirectReader(address=address, name=name, parent=self)
        # Explicit Qt.QueuedConnection. Without this, AutoConnection inspects
        # sender/receiver QObject thread affinity at .connect() time — but
        # the reader's affinity is still the parent (main thread) until
        # .start() runs, so AutoConnection caches DirectConnection. When the
        # BLE callback then emits from bleak's dispatch queue thread, Qt
        # does the right thing at runtime, but QueuedConnection is the
        # canonical pattern for cross-thread signals and removes any
        # ambiguity about scheduling. Critical for keeping the main UI
        # thread responsive when EEG data is flowing.
        self._reader.samples.connect(self._on_samples, Qt.ConnectionType.QueuedConnection)
        self._reader.error.connect(self._on_stream_error, Qt.ConnectionType.QueuedConnection)
        self._reader.connected.connect(self._on_inlet_connected, Qt.ConnectionType.QueuedConnection)
        self._reader.start()
        self._is_streaming = True
        self._t0 = 0.0
        # Show "connecting..." while BLE handshake completes. The direct
        # path typically takes 3-8s on first connect (BLE pairing).
        self._placeholder.setText("[ CONNECTING... ]")
        self._placeholder.setVisible(True)
        self._connect_btn.setText("DISCONNECT")
        self._connect_btn.setStyleSheet(
            "background:transparent; border:1px solid #ef4444; color:#ef4444;"
            "font-family:'Inter'; font-size:11px; padding:0 16px;"
        )
        self._pick_btn.setEnabled(False)

    def _start_inlet(self, device_name: str):
        """Legacy stub — direct BLE no longer needs a separate inlet step.
        Kept so any external callers that referenced this method don't
        crash. The work it used to do is now part of start_stream."""
        pass

    def _on_inlet_connected(self):
        """Called when first real EEG chunk arrives — BLE confirmed working."""
        self._draw_timer.start()
        self._placeholder.setVisible(False)
        self._show_view(self._view_mode)
        self._status_dot.setStyleSheet("color: #22c55e; font-size: 13px;")

    def start_recording(self, save_path: str):
        """Begin saving EEG to a CSV at `save_path`.

        Direct BLE makes this trivial — we already receive every sample
        as a Python callback, so recording is just "tell the reader to
        accumulate samples to memory and flush on stop." No second
        subprocess, no second LSL inlet, no second BLE consumer.

        If the live stream isn't running yet (rare — the start-session
        flow connects EEG before recording), auto-start it.
        """
        if not self._is_streaming:
            device_name = (
                self._connected_device["name"] if self._connected_device else ""
            )
            self.start_stream(device_name=device_name)
            # BLE connect can take up to ~8s on first pair. We can't write
            # samples until the reader has a connection, but the reader
            # buffers anything it receives once start_recording() is set
            # on it. Set the path immediately so any samples received during
            # warmup are captured.
            QTimer.singleShot(500, lambda: self._enable_recording(save_path))
        else:
            self._enable_recording(save_path)

    def _enable_recording(self, save_path: str):
        """Tell the direct reader to start accumulating samples for CSV."""
        if self._reader is not None and hasattr(self._reader, "start_recording"):
            self._reader.start_recording(save_path)
            self._rec_indicator_active = True
        else:
            print(f"[muse-direct] cannot start recording — no active reader")

    # Legacy method kept so the EEG record button + auto-start flow don't
    # break if anything still calls them. Direct path uses _enable_recording.
    def _launch_record_proc(self, save_path: str):
        self._enable_recording(save_path)

    def stop_recording(self):
        """Flush the in-memory sample buffer to CSV at the path set by
        start_recording().

        With the direct-BLE architecture this is just a memory→disk write
        of the accumulated samples — no separate subprocess to SIGINT and
        wait for. The reader keeps everything in RAM during the session
        (~9 MB per Muse for a 15-min session, comfortable).

        Still kept fast: the actual file write happens on the current
        thread but with a single np.concatenate + DataFrame.to_csv call.
        Typical write time for a 15-min CSV is <500 ms on a slow disk.
        """
        self._rec_indicator_active = False
        if self._reader is None:
            return
        try:
            path = self._reader.stop_recording()
            if path:
                print(f"[muse-direct] saved {path}")
        except Exception as e:
            print(f"[muse-direct] stop_recording flush failed: {e}")

        # Legacy subprocess cleanup, in case any code path still set
        # _record_proc. Belt-and-braces during the migration to direct BLE.
        proc = getattr(self, "_record_proc", None)
        self._record_proc = None
        if proc and proc.poll() is None:
            stopper = _RecordStopper(proc, parent=self)
            stopper.start()


    def stop_stream(self):
        """Fully disconnect — flush recording, stop BLE reader, reset UI."""
        import design_tokens as _dt
        self.stop_recording()
        self._draw_timer.stop()
        if self._reader:
            self._reader.stop()
            # Wait for the bleak loop to actually exit so we don't leave a
            # dangling QThread that crashes the app on shutdown. 3 seconds
            # is enough for a clean BLE disconnect; force-terminate
            # otherwise (the OS will clean up the bond).
            self._reader.wait(3000)
            self._reader = None
        # Legacy stream-subprocess cleanup (no-op on new direct path,
        # kept for safety during migration)
        if self._stream_proc:
            try: self._stream_proc.terminate()
            except Exception: pass
            self._stream_proc = None
        self._stream_thread = None
        self._is_streaming = False
        self._wave_canvas.setVisible(False)
        self._band_canvas.setVisible(False)
        self._placeholder.setVisible(True)
        self._status_dot.setStyleSheet(f"color: {_dt.text_tertiary()}; font-size: 13px;")
        self._connect_btn.setText("CONNECT")
        self._connect_btn.setStyleSheet(_dt.qss_secondary_button() +
            f" QPushButton {{ font-size: 11px; padding: 0 14px; }}")
        self._pick_btn.setEnabled(True)

    def closeEvent(self, event):
        self.stop_stream()
        super().closeEvent(event)

    def _toggle_stream(self):
        """CONNECT/DISCONNECT button handler — controls monitoring only."""
        if self._is_streaming:
            self.stop_stream()
        elif self._connected_device:
            self.start_stream(device_name=self._connected_device["name"])

    # ── Sample ingestion ──────────────────────────────────────────────────────

    def _on_samples(self, data: np.ndarray, timestamps: np.ndarray):
        # Hot path. This runs once per chunk pulled from LSL (typically every
        # ~50ms with ≤12 samples). Avoid Python list conversions — write the
        # numpy arrays directly into the ring buffers.
        if self._t0 == 0.0 and len(timestamps):
            self._t0 = float(timestamps[0])
        # Subtract t0 in-place to avoid an extra allocation for `rel`
        rel = timestamps - self._t0
        self._ts_buf.extend(rel)
        n_channels = data.shape[1] if data.ndim == 2 else 0
        for i, ch in enumerate(_CHANNELS):
            if n_channels > i:
                self._chan_bufs[ch].extend(data[:, i])

    def _on_stream_error(self, msg: str):
        """
        Called when the LSL reader thread emits an error — device disconnected,
        stream lost, or no data. Stop recording (save what we have), update UI,
        and show a visible warning banner.
        """
        # Save whatever was recorded so far
        self.stop_recording()

        # Clean up stream state without calling stop_stream() fully
        # (stop_stream calls stop_recording again which is safe but redundant)
        self._draw_timer.stop()
        if self._reader:
            self._reader = None
        self._stream_proc = None
        self._stream_thread = None
        self._is_streaming = False

        # Show disconnect banner in the plot area
        self._wave_canvas.setVisible(False)
        self._band_canvas.setVisible(False)
        disconnect_text = "\n".join(["!! DISCONNECTED !!", msg[:60], "", "Select device and reconnect."])
        self._placeholder.setText(disconnect_text)
        self._placeholder.setStyleSheet(
            "color: #ef4444; font-size: 12px; font-family: 'Inter';"
            "letter-spacing: 1px; padding: 24px; text-align: center;"
        )
        self._placeholder.setVisible(True)

        # Update header
        device_name = self._connected_device["name"] if self._connected_device else "DEVICE"
        short_id = device_name.split("-")[-1] if "-" in device_name else device_name[-4:]
        self._title_lbl.setText(f"EEG {self.channel_id}  ·  {short_id}  ·  LOST")
        self._title_lbl.setStyleSheet(
            "color: #ef4444; font-size: 12px; font-family: 'Inter';"
        )
        self._status_dot.setStyleSheet("color: #ef4444; font-size: 13px;")
        self._connect_btn.setText("RECONNECT")
        self._connect_btn.setMinimumWidth(82)  # "RECONNECT" needs a hair more than "CONNECT"
        self._connect_btn.setStyleSheet(
            "background:#2a0f0f; border:1px solid #ef4444; color:#ef4444;"
            "font-family:'Inter'; font-size:10px; padding:0 12px;"
        )
        self._connect_btn.setEnabled(True)
        self._pick_btn.setEnabled(True)

    # ── Rendering ─────────────────────────────────────────────────────────────

    def _redraw(self):
        # Skip entirely when the widget isn't visible on screen. This is the
        # single biggest win in apps with multiple tabs: when the user is on
        # the Sync or Labelling tab, the Live Monitor EEG widgets shouldn't
        # be doing FFTs or canvas updates. Qt's isVisibleTo(window) is the
        # standard check.
        try:
            window = self.window()
            if window is not None and not self.isVisibleTo(window):
                return
        except Exception:
            pass

        if len(self._ts_buf) == 0:
            return
        ts = self._ts_buf.to_array()

        if self._view_mode == "waveform":
            channels = {
                ch: self._chan_bufs[ch].to_array()
                for ch in _CHANNELS
                if len(self._chan_bufs[ch]) == len(ts)
            }
            self._wave_canvas.set_data(ts, channels)
        else:
            # Throttle band-power FFTs to ~4 Hz (every ~250ms). FFT over
            # 4 seconds × 256Hz × 4 channels is expensive enough that
            # running it at 20 Hz (the timer rate) was the second-biggest
            # cause of slowdown. Visually, the bars don't need to update
            # any faster than 4 Hz — the underlying signal varies on a
            # much slower timescale than 50ms.
            now = time.monotonic()
            if (now - getattr(self, "_last_band_calc", 0.0)) < 0.25:
                return
            self._last_band_calc = now
            per_channel: dict[str, dict] = {}
            for ch in _CHANNELS:
                arr = self._chan_bufs[ch].to_array()
                if len(arr) < SAMPLE_RATE:
                    per_channel[ch] = {b: 0.0 for b in _BANDS}
                else:
                    per_channel[ch] = _band_powers(arr[-SAMPLE_RATE * 4:])
            self._band_canvas.set_bands(per_channel)


class _RecordStopper(QThread):
    """Sends SIGINT to a muselsl record process and waits for it to flush."""

    def __init__(self, proc: subprocess.Popen, parent=None):
        super().__init__(parent)
        self._proc = proc

    def run(self):
        import signal
        try:
            if sys.platform == "win32":
                self._proc.send_signal(signal.CTRL_C_EVENT)
            else:
                self._proc.send_signal(signal.SIGINT)
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait()
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass



# ─── 2. EEGReviewWidget ──────────────────────────────────────────────────────

class EEGReviewWidget(QFrame):
    """
    Post-session EEG viewer used in Tabs 2 and 3.
    Two views: WAVEFORM | BAND POWER. Playhead cursor follows timeline scrub.
    """

    def __init__(self, channel_id: int = 1, parent=None):
        super().__init__(parent)
        self.channel_id = channel_id
        self.setObjectName("panel")
        self.setMinimumHeight(160)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        self._df:        Optional[pd.DataFrame] = None
        self._offset_sec: float = 0.0
        self._playhead:   float = 0.0
        self._vlines:     dict  = {}
        self._view_mode:  str   = "waveform"

        self._build_ui()
        self._build_waveform_plot()
        self._build_bandpower_plot()
        self._show_view("waveform")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        bar = QWidget()
        bar.setFixedHeight(38)
        bar.setStyleSheet("background-color: #0E0E12; border-bottom: 1px solid #2A2A30;")
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(14, 0, 14, 0)
        bl.setSpacing(12)

        self._status_dot = QLabel("●")
        self._status_dot.setFixedWidth(14)
        self._status_dot.setStyleSheet("color: #2A2A30; font-size: 13px;")

        self._title_lbl = QLabel(f"EEG {self.channel_id}  —  NO DATA")
        self._title_lbl.setStyleSheet(
            "color: #A1A1AA; font-size: 12px; font-family: 'Inter';"
        )

        self._offset_lbl = QLabel("")
        self._offset_lbl.setStyleSheet(
            "color: #A1A1AA; font-size: 11px; font-family: 'Inter';"
        )

        self._wave_btn = QPushButton("WAVEFORM")
        self._wave_btn.setFixedHeight(26); self._wave_btn.setMinimumWidth(86)
        self._wave_btn.setStyleSheet(_tab_style(True))
        self._wave_btn.clicked.connect(lambda: self._show_view("waveform"))

        self._band_btn = QPushButton("BAND POWER")
        self._band_btn.setFixedHeight(26); self._band_btn.setMinimumWidth(98)
        self._band_btn.setStyleSheet(_tab_style(False))
        self._band_btn.clicked.connect(lambda: self._show_view("bandpower"))

        bl.addWidget(self._status_dot)
        bl.addWidget(self._title_lbl)
        bl.addStretch()
        bl.addWidget(self._offset_lbl)
        bl.addSpacing(8)
        bl.addWidget(self._wave_btn)
        bl.addWidget(self._band_btn)
        outer.addWidget(bar)

        self._canvas_container = QWidget()
        self._canvas_container.setStyleSheet(f"background-color: {_BG};")
        self._canvas_layout = QVBoxLayout(self._canvas_container)
        self._canvas_layout.setContentsMargins(0, 0, 0, 0)
        self._canvas_layout.setSpacing(0)
        outer.addWidget(self._canvas_container)

        self._placeholder = QLabel(f"[ EEG {self.channel_id}  —  UPLOAD CSV TO VIEW ]")
        self._placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._placeholder.setStyleSheet(
            "color: #2A2A30; font-size: 13px; font-family: 'Inter';"
            "letter-spacing: 2px; padding: 24px;"
        )
        self._canvas_layout.addWidget(self._placeholder)

    # ── Plot construction ─────────────────────────────────────────────────────

    def _build_waveform_plot(self):
        self._wave_fig = Figure(figsize=(10, 2.2), dpi=90, tight_layout=True)
        self._wave_fig.patch.set_facecolor(_BG)
        self._wave_axes: dict[str, plt.Axes] = {}
        for i, ch in enumerate(_CHANNELS):
            ax = self._wave_fig.add_subplot(1, 4, i + 1)
            _style_ax(ax)
            ax.set_title(ch, fontsize=9, color=_CHAN_COLORS[ch], pad=4,
                         fontfamily="monospace")
            ax.set_xlabel("s", fontsize=8, color=_FG)
            (line,) = ax.plot([], [], color=_CHAN_COLORS[ch],
                              linewidth=0.7, antialiased=True)
            ax._bb_line = line
            vl = ax.axvline(x=0, color=_ACCENT, linewidth=1.4,
                            alpha=0.9, visible=False)
            self._vlines[ch] = vl
            self._wave_axes[ch] = ax
        self._wave_canvas = FigureCanvas(self._wave_fig)
        self._wave_canvas.setStyleSheet(f"background-color: {_BG};")
        self._canvas_layout.addWidget(self._wave_canvas)
        self._wave_canvas.setVisible(False)

    def _build_bandpower_plot(self):
        self._band_fig = Figure(figsize=(10, 2.2), dpi=90, tight_layout=True)
        self._band_fig.patch.set_facecolor(_BG)
        self._band_axes: dict[str, plt.Axes] = {}
        names  = list(_BANDS.keys())
        colors = [v[2] for v in _BANDS.values()]
        for i, ch in enumerate(_CHANNELS):
            ax = self._band_fig.add_subplot(1, 4, i + 1)
            _style_ax(ax)
            ax.set_title(ch, fontsize=9, color=_CHAN_COLORS[ch], pad=4,
                         fontfamily="monospace")
            ax.set_xticks(range(len(names)))
            ax.set_xticklabels(names, fontsize=9, color=_FG)
            ax.set_ylim(0, 1)
            bars = ax.bar(range(len(names)), [0] * len(names),
                          color=colors, width=0.6)
            ax._bb_bars = bars
            self._band_axes[ch] = ax
        self._band_canvas = FigureCanvas(self._band_fig)
        self._band_canvas.setStyleSheet(f"background-color: {_BG};")
        self._canvas_layout.addWidget(self._band_canvas)
        self._band_canvas.setVisible(False)

    def _show_view(self, mode: str):
        self._view_mode = mode
        self._wave_btn.setStyleSheet(_tab_style(mode == "waveform"))
        self._band_btn.setStyleSheet(_tab_style(mode == "bandpower"))
        if self._df is not None:
            self._wave_canvas.setVisible(mode == "waveform")
            self._band_canvas.setVisible(mode == "bandpower")

    # ── Public API ────────────────────────────────────────────────────────────

    def load(self, df: pd.DataFrame, offset_sec: float = 0.0,
             device_name: str = ""):
        self._df = df.copy()
        self._offset_sec = offset_sec
        first_ts = float(df["timestamps"].iloc[0])
        self._df["global_t"] = (df["timestamps"] - first_ts) - offset_sec
        self._render_waveform()
        self._render_bandpower_full()
        duration = float(
            self._df["global_t"].iloc[-1] - self._df["global_t"].iloc[0]
        )
        sign = "+" if offset_sec >= 0 else ""
        self._offset_lbl.setText(f"offset {sign}{offset_sec:.3f}s  ·  {duration:.0f}s")
        label = device_name or f"MUSE-S {self.channel_id}"
        boxer = BOXER_LABELS.get(device_name, "")
        display = f"EEG {self.channel_id}  —  {label}"
        if boxer:
            display += f"  [{boxer}]"
        self._title_lbl.setText(display)
        self._title_lbl.setStyleSheet(
            "color: #ECEDEE; font-size: 12px; font-family: 'Inter';"
        )
        self._status_dot.setStyleSheet("color: #22c55e; font-size: 13px;")
        self._placeholder.setVisible(False)
        for vl in self._vlines.values():
            vl.set_visible(True)
        self._show_view(self._view_mode)

    def set_playhead(self, global_t: float):
        if self._df is None:
            return
        self._playhead = global_t
        if self._view_mode == "waveform":
            VIEW_HALF = 5.0
            for ch, ax in self._wave_axes.items():
                self._vlines[ch].set_xdata([global_t, global_t])
                ax.set_xlim(global_t - VIEW_HALF, global_t + VIEW_HALF)
            self._wave_canvas.draw_idle()
        else:
            self._render_bandpower_at(global_t, half=5.0)

    def clear(self):
        self._df = None
        for ax in self._wave_axes.values():
            ax._bb_line.set_data([], [])
        for vl in self._vlines.values():
            vl.set_visible(False)
        self._wave_canvas.draw_idle()
        self._placeholder.setVisible(True)
        self._wave_canvas.setVisible(False)
        self._band_canvas.setVisible(False)
        self._status_dot.setStyleSheet("color: #2A2A30; font-size: 13px;")
        self._title_lbl.setText(f"EEG {self.channel_id}  —  NO DATA")

    # ── Internal rendering ────────────────────────────────────────────────────

    def _render_waveform(self):
        if self._df is None:
            return
        t = self._df["global_t"].to_numpy(dtype=np.float64)
        for ch, ax in self._wave_axes.items():
            if ch not in self._df.columns:
                continue
            y = self._df[ch].to_numpy(dtype=np.float32)
            ax._bb_line.set_data(t, y)
            ax.set_xlim(t[0], t[-1])
            if len(y) > 1:
                lo, hi = float(np.nanmin(y)), float(np.nanmax(y))
                m = max((hi - lo) * 0.05, 5.0)
                ax.set_ylim(lo - m, hi + m)
        self._wave_canvas.draw_idle()

    def _render_bandpower_full(self):
        if self._df is None:
            return
        for ch, ax in self._band_axes.items():
            if ch not in self._df.columns:
                continue
            pw = _band_powers(self._df[ch].to_numpy(dtype=np.float32))
            vals = [pw.get(b, 0.0) for b in _BANDS]
            for bar, v in zip(ax._bb_bars, vals):
                bar.set_height(v)
            ax.set_ylim(0, max(max(vals) * 1.2, 0.05))
        self._band_canvas.draw_idle()

    def _render_bandpower_at(self, global_t: float, half: float):
        if self._df is None:
            return
        mask = (
            (self._df["global_t"] >= global_t - half) &
            (self._df["global_t"] <  global_t + half)
        )
        w = self._df[mask]
        if len(w) < SAMPLE_RATE // 2:
            return
        for ch, ax in self._band_axes.items():
            if ch not in w.columns:
                continue
            pw = _band_powers(w[ch].to_numpy(dtype=np.float32))
            vals = [pw.get(b, 0.0) for b in _BANDS]
            for bar, v in zip(ax._bb_bars, vals):
                bar.set_height(v)
            ax.set_ylim(0, max(max(vals) * 1.2, 0.05))
        self._band_canvas.draw_idle()