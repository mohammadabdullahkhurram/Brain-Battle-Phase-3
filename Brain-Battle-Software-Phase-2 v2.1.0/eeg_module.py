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

# Known Muse IDs → boxer labels
BOXER_LABELS = {
    "MuseS-7538": "BOXER A",
    "MuseS-7564": "BOXER B",
}

# ─── Exclusive device registry ────────────────────────────────────────────────
# Maps device_name → owning EEGLiveWidget. Prevents two blocks sharing a device.

_DEVICE_REGISTRY: dict[str, "EEGLiveWidget"] = {}


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
    """
    Runs `python -m muselsl list` as a subprocess to avoid the asyncio
    event-loop crash that occurs when calling list_muses() directly inside
    a QThread (bleak requires a running event loop on the calling thread).

    muselsl list prints lines like:
        {'name': 'MuseS-7538', 'address': '00:55:DA:B9:75:38'}
    We parse those and emit them as a list of dicts.
    """
    found = pyqtSignal(list)
    error = pyqtSignal(str)

    def run(self):
        import re
        try:
            result = subprocess.run(
                [sys.executable, "-m", "muselsl", "list"],
                capture_output=True,
                text=True,
                timeout=20,
            )
            # muselsl list prints lines like:
            #   Found device MuseS-7538, MAC Address 00:55:DA:B9:75:38
            # Parse both stdout and stderr since some backends log to stderr
            combined = result.stdout + "\n" + result.stderr
            devices = []
            seen = set()
            for line in combined.splitlines():
                # Pattern: "Found device <name>, MAC Address <addr>"
                m = re.search(
                    r"Found device\s+([^,]+),\s+MAC Address\s+([0-9A-Fa-f:]+)",
                    line
                )
                if m:
                    name = m.group(1).strip()
                    addr = m.group(2).strip()
                    if name not in seen:
                        seen.add(name)
                        devices.append({"name": name, "address": addr})
            self.found.emit(devices)
        except subprocess.TimeoutExpired:
            self.error.emit("Scan timed out after 20s.")
        except Exception as exc:
            self.error.emit(str(exc))


# ─── LSL reader thread ────────────────────────────────────────────────────────

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

    def _do_scan(self):
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
        self._ts_buf    = deque(maxlen=buf)
        self._chan_bufs: dict[str, deque] = {c: deque(maxlen=buf) for c in _CHANNELS}
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
        """
        Start the muselsl stream subprocess + LSL inlet for live monitoring.
        Does NOT start recording — call start_recording() separately.
        """
        if self._is_streaming:
            return
        name = device_name or (
            self._connected_device["name"] if self._connected_device else ""
        )
        cmd = [sys.executable, "-m", "muselsl", "stream"]
        if name:
            cmd += ["-n", name]
        # Use PIPE not DEVNULL — DEVNULL blocks muselsl's asyncio writes on
        # macOS after ~30s causing CoreBluetooth to drop the BLE connection.
        # Redirect subprocess output to DEVNULL — eliminates the need for
        # pipe-drain threads that were the source of "QThread destroyed while
        # thread '' is still running" crashes on macOS app exit. Anonymous
        # threading.Thread instances blocking on p.readline() never terminate
        # cleanly; Qt teardown sees them still alive and aborts. muselsl's
        # stdout/stderr is only useful for debugging, not required for
        # operation.
        self._stream_proc = subprocess.Popen(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
        )
        self._stream_thread = None
        # 3s for muselsl to connect BLE and advertise LSL stream
        QTimer.singleShot(3000, lambda: self._start_inlet(name))

    def _start_inlet(self, device_name: str):
        self._reader = _LSLReaderThread(stream_name=device_name, parent=self)
        self._reader.samples.connect(self._on_samples)
        self._reader.error.connect(self._on_stream_error)
        self._reader.connected.connect(self._on_inlet_connected)
        self._reader.start()
        self._is_streaming = True
        self._t0 = 0.0
        # Show "connecting..." while BLE + LSL handshake completes
        self._placeholder.setText("[ CONNECTING... ]")
        self._placeholder.setVisible(True)
        self._connect_btn.setText("DISCONNECT")
        self._connect_btn.setStyleSheet(
            "background:transparent; border:1px solid #ef4444; color:#ef4444;"
            "font-family:'Inter'; font-size:11px; padding:0 16px;"
        )
        self._pick_btn.setEnabled(False)

    def _on_inlet_connected(self):
        """Called when first real EEG chunk arrives — BLE+LSL confirmed working."""
        self._draw_timer.start()
        self._placeholder.setVisible(False)
        self._show_view(self._view_mode)
        self._status_dot.setStyleSheet("color: #22c55e; font-size: 13px;")

    def start_recording(self, save_path: str):
        """
        Begin saving EEG to a named CSV file.
        The muselsl stream must already be running (start_stream called first,
        or CONNECT was clicked). If the stream is not yet up, start it first.
        Recording runs until stop_recording() is called.
        """
        if not self._is_streaming:
            # Auto-start the stream first, then record after it advertises
            device_name = (
                self._connected_device["name"] if self._connected_device else ""
            )
            self.start_stream(device_name=device_name)
            # Delay record start to let stream come up (stream has 3s delay + 1s buffer)
            QTimer.singleShot(4500, lambda: self._launch_record_proc(save_path))
        else:
            self._launch_record_proc(save_path)

    def _launch_record_proc(self, save_path: str):
        """
        Launch muselsl record with correct short flags.
        muselsl record flags: -f/--filename, -d/--duration (no -n flag).
        Device selection is automatic — it picks the active LSL stream.
        Uses a 24h duration ceiling; stop_recording() sends SIGINT to flush early.
        """
        import pathlib
        if self._record_proc and self._record_proc.poll() is None:
            return  # Already recording

        pathlib.Path(save_path).parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            sys.executable, "-m", "muselsl", "record",
            "-f", save_path,
            "-d", "86400",
        ]
        self._record_proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,   # capture so we can detect errors
            stderr=subprocess.PIPE,
        )
        self._rec_indicator_active = True

    def stop_recording(self):
        """
        Stop saving the CSV cleanly — runs in a background thread so the UI
        does not freeze while muselsl flushes its buffer.

        muselsl record only writes the final CSV on KeyboardInterrupt.
        A plain terminate()/SIGTERM kills it before the flush.
        We send SIGINT so it catches KeyboardInterrupt and calls _save().
        """
        proc = self._record_proc
        self._record_proc = None
        self._rec_indicator_active = False

        if proc and proc.poll() is None:
            stopper = _RecordStopper(proc, parent=self)
            stopper.start()


    def stop_stream(self):
        """Fully disconnect — stops recording, inlet, and stream subprocess."""
        import design_tokens as _dt
        self.stop_recording()
        self._draw_timer.stop()
        if self._reader:
            self._reader.stop()
            self._reader = None
        if self._stream_proc:
            self._stream_proc.terminate()
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
        if self._t0 == 0.0 and len(timestamps):
            self._t0 = float(timestamps[0])
        rel = timestamps - self._t0
        self._ts_buf.extend(rel.tolist())
        for i, ch in enumerate(_CHANNELS):
            if data.shape[1] > i:
                self._chan_bufs[ch].extend(data[:, i].tolist())

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
        # Push the latest samples into the pyqtgraph widgets. The new
        # widgets handle their own windowing / auto-ranging internally;
        # we just hand them a NumPy view.
        if not self._ts_buf:
            return
        ts = np.array(self._ts_buf)

        if self._view_mode == "waveform":
            channels = {
                ch: np.array(self._chan_bufs[ch])
                for ch in _CHANNELS
                if len(self._chan_bufs[ch]) == len(ts)
            }
            self._wave_canvas.set_data(ts, channels)
        else:
            per_channel: dict[str, dict] = {}
            for ch in _CHANNELS:
                arr = np.array(self._chan_bufs[ch])
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