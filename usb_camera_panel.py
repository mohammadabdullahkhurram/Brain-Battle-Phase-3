"""
usb_camera_panel.py
───────────────────
Four-slot USB camera grid for the Live Monitor tab.

Layout mirrors ``RTMPConfigPanel``'s 2×2 grid so the live-monitor design is
stable no matter which ingest path the operator picks. The four slots are
labelled per the spec:

    SIDE 01  │  SIDE 02       (USB-C DSLRs or webcams)
    ────────────────────
    POV 01   │  POV 02        (FPV receivers on a USB capture card)

The panel exposes a simple API the ``LiveSessionTab`` calls at phase
boundaries:

    panel.start_recording(phase_folder, phase_label)
    panel.stop_recording()

…which delegates to each slot's ``USBCameraThread`` so every connected
camera writes its own MP4 into the correct subfolder (``Side Cams/`` or
``POV Cams/``) for the current phase.

Rescan and multi-select are handled per-slot in the ``CameraPickerDialog``;
this panel only deals with session-level recording orchestration.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore    import Qt
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
)

from usb_camera_slot import USBCameraSlot


_SLOT_SPECS = [
    # (index, display label, kind for per-round folder routing)
    (1, "SIDE 01", "side"),
    (2, "SIDE 02", "side"),
    (3, "POV 01",  "pov"),
    (4, "POV 02",  "pov"),
]


class USBCameraPanel(QWidget):
    """2×2 grid of USBCameraSlots with a header bar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._slots: list[USBCameraSlot] = []
        self._slot_kinds: dict[int, str] = {}
        self._build()

    # ── UI ──
    def _build(self) -> None:
        import design_tokens as dt
        self._dt = dt
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(0)

        # Header strip.
        self._header = QWidget(); self._header.setFixedHeight(40)
        hl = QHBoxLayout(self._header); hl.setContentsMargins(14, 0, 14, 0); hl.setSpacing(12)

        self._hdr_lbl = QLabel("USB CAMERAS")
        self._hint = QLabel("2× SIDE  +  2× POV  (UVC / capture-card)")
        hl.addWidget(self._hdr_lbl); hl.addSpacing(18); hl.addWidget(self._hint); hl.addStretch()

        self._rescan_btn = QPushButton("RESCAN ALL")
        self._rescan_btn.setFixedHeight(26)
        hl.addWidget(self._rescan_btn)
        layout.addWidget(self._header)

        # 2×2 grid of slots.
        grid = QGridLayout()
        grid.setSpacing(8); grid.setContentsMargins(8, 8, 8, 8)
        for i, (idx, label, kind) in enumerate(_SLOT_SPECS):
            slot = USBCameraSlot(slot_index=idx, label=label)
            self._slot_kinds[idx] = kind
            row, col = divmod(i, 2)
            grid.addWidget(slot, row, col)
            self._slots.append(slot)
        layout.addLayout(grid)

        self._restyle()

    def _restyle(self) -> None:
        dt = self._dt
        self._header.setStyleSheet(
            f"background-color: {dt.canvas_bg()}; "
            f"border-bottom: 1px solid {dt.border()};"
        )
        self._hdr_lbl.setStyleSheet(
            f"color: {dt.accent()}; font-size: 10px; letter-spacing: 1.8px;"
            f" font-family: {dt.FONT_UI}; font-weight: 700;"
        )
        self._hint.setStyleSheet(
            f"color: {dt.text_secondary()}; font-size: 10px; letter-spacing: 1px;"
            f" font-family: {dt.FONT_UI};"
        )
        self._rescan_btn.setStyleSheet(dt.qss_secondary_button() +
            f" QPushButton {{ font-size: 11px; padding: 0 18px; }}")

    # ── Public API ──
    def get_slots(self) -> list[USBCameraSlot]:
        return list(self._slots)

    def slot_kind(self, slot_index: int) -> str:
        return self._slot_kinds.get(slot_index, "side")

    def start_recording(self, phase_folder: Path, phase_label: str,
                        clock=None) -> list[Path]:
        """
        Kick off recording on every live slot. Returns the list of paths
        each live camera is writing to. Slots that aren't connected are
        silently skipped — matches the RTMPConfigPanel graceful-degradation
        behaviour. Side Cams/ and POV Cams/ subfolders are created lazily,
        only when a slot of that kind actually records, so an absent
        subfolder unambiguously means "no camera connected to that side"
        rather than "feature broken".

        `clock` is the shared capture-time origin (t0_monotonic, t0_unix)
        forwarded to every camera thread so all frame-timestamp sidecars
        sit on one reference clock.
        """
        started: list[Path] = []
        safe_label = phase_label.replace(" ", "_")
        live_slots = [s for s in self._slots if s.is_live]
        logging.getLogger(__name__).info(
            "[session start] %d of %d camera slots live", len(live_slots), len(self._slots))
        for slot in self._slots:
            if not slot.is_live:
                logging.getLogger(__name__).info(
                    "[session start] skip slot %s (%s) — not connected", slot.slot_index, slot.label)
                continue
            sub = "Side Cams" if self.slot_kind(slot.slot_index) == "side" else "POV Cams"
            safe_name = slot.label.replace(" ", "_")
            out_dir = phase_folder / sub
            out_dir.mkdir(parents=True, exist_ok=True)
            out_path = out_dir / f"{safe_label}_{safe_name}.mp4"
            slot.start_recording(out_path, clock=clock)
            started.append(out_path)
            logging.getLogger(__name__).info(
                "[session start] recording slot %s (%s) -> %s", slot.slot_index, slot.label, out_path)
        return started

    def stop_recording(self) -> None:
        for slot in self._slots:
            if slot.is_live:
                slot.stop_recording()

    def shutdown(self) -> None:
        """Called on app quit — stop every capture thread cleanly."""
        for slot in self._slots:
            slot.shutdown()

    def any_live(self) -> bool:
        return any(s.is_live for s in self._slots)


__all__ = ["USBCameraPanel"]
