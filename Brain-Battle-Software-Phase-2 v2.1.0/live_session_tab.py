"""
live_session_tab.py
───────────────────
Live Monitor tab — v4 redesign.

Design shifts from v3:
  · Hero timer — the circular dial is now the visual centrepiece, not
    a secondary card. Big tabular number inside, 48 px hero size.
  · Workspace dominates, rail recedes — rail width trimmed, less
    prominent so cameras read as the main content.
  · Real elevation on cards — visually distinct from canvas ambient
    backdrop, not just tinted rectangles.
  · Theme-aware — all styling reads from design_tokens at call time
    and registers a theme-change callback to re-style.

Layout:
    ┌─────────────────────────────────────────────────────────────────┐
    │ Live Monitor · status pill                  phase pill · START  │
    ├──────────────────────────────┬──────────────────────────────────┤
    │                              │                                  │
    │  2×2 camera grid             │  SESSION card                    │
    │  in card frame on            │  ┌────────────────────┐          │
    │  ambient canvas              │  │   ROUND 2          │          │
    │                              │  │                    │          │
    │                              │  │      01:37         │          │
    │                              │  │                    │          │
    │                              │  │   Round 2 of 3     │          │
    │                              │  └────────────────────┘          │
    │                              │  next · BREAK 2                  │
    │                              │                                  │
    │                              │  EEG STREAMS card                │
    │                              │  ● EEG 1 · 4-ch trace            │
    │                              │  ● EEG 2 · 4-ch trace            │
    └──────────────────────────────┴──────────────────────────────────┘
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore    import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QFrame,
    QSizePolicy, QMessageBox,
)

import design_tokens as dt
from usb_camera_panel     import USBCameraPanel
from eeg_module           import EEGLiveWidget
from session_controller   import SessionController, SessionConfig, Phase, PhaseInfo
from session_setup_dialog import SessionSetupDialog
from session_storage      import create_session, SessionLayout, PhaseRecord
from circular_timer       import CircularTimer
from sync_beep            import SyncBeep


# ─── helpers ─────────────────────────────────────────────────────────────────

def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(dt.qss_section_label())
    return lbl

def _status_pill(text: str, kind: str = "idle") -> QLabel:
    lbl = QLabel(text)
    lbl.setStyleSheet(dt.qss_status_pill(kind))
    lbl.setFixedHeight(24)
    return lbl

def _muse_friendly(raw: str) -> str:
    return (raw or "Muse").replace("MuseS-", "Muse S ").replace("Muse-S-", "Muse S ").replace("-", " ")

def _default_capture_root() -> str:
    env = os.environ.get("BRAIN_BATTLE_CAPTURE_DIR")
    if env: return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "Captures")


def _card() -> QFrame:
    card = QFrame(); card.setObjectName("card")
    card.setStyleSheet(dt.qss_card())
    return card


# ─── Main tab ────────────────────────────────────────────────────────────────

class LiveSessionTab(QWidget):
    session_saved = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._layout: Optional[SessionLayout] = None
        self._controller = SessionController(self)
        self._beep       = SyncBeep(self)
        self._eeg_recording_active = False
        self._active_phase: Optional[tuple[Phase, int]] = None
        self._build()
        self._wire_controller()
        self._restyle()  # Apply the correct theme styling for current mode
        dt.on_theme_change(self._restyle)

    # ═══════════════════════════════════════════════════════════════ UI

    def _build(self) -> None:
        # Don't set stylesheet here — _restyle() handles it with the
        # direct widget selector so the theme switch cascade works.

        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        self._toolbar = self._build_toolbar()
        root.addWidget(self._toolbar)

        body = QWidget()
        b = QHBoxLayout(body); b.setContentsMargins(28, 20, 28, 28); b.setSpacing(20)
        b.addWidget(self._build_camera_area(), 1)
        b.addWidget(self._build_right_rail(), 0)
        root.addWidget(body, 1)

    # -- toolbar --------------------------------------------------------------

    def _build_toolbar(self) -> QWidget:
        bar = QWidget(); bar.setFixedHeight(64)
        self._toolbar_bar = bar

        h = QHBoxLayout(bar); h.setContentsMargins(28, 0, 28, 0); h.setSpacing(dt.SPACE_3)

        self._title = QLabel("Live Monitor")
        h.addWidget(self._title)
        h.addSpacing(dt.SPACE_4)

        self._status_pill_lbl = _status_pill("IDLE", "idle")
        h.addWidget(self._status_pill_lbl)
        h.addStretch()

        self._phase_pill = QLabel(""); self._phase_pill.setFixedHeight(24); self._phase_pill.setVisible(False)
        h.addWidget(self._phase_pill); h.addSpacing(dt.SPACE_3)

        self._btn_start = QPushButton("Start Session")
        self._btn_start.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_start.clicked.connect(self._on_start_clicked)
        h.addWidget(self._btn_start)

        self._btn_stop = QPushButton("Stop Session")
        self._btn_stop.setCursor(Qt.CursorShape.PointingHandCursor)
        self._btn_stop.setVisible(False)
        self._btn_stop.clicked.connect(self._on_stop_clicked)
        h.addWidget(self._btn_stop)

        self._restyle_toolbar()
        return bar

    def _restyle_toolbar(self) -> None:
        self._toolbar_bar.setStyleSheet(
            f"background: {dt.canvas_bg()}; border-bottom: 1px solid {dt.border()};"
        )
        self._title.setStyleSheet(
            f"color: {dt.text_primary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_LG}px; font-weight: 700; letter-spacing: {dt.LS_TIGHT};"
        )
        self._btn_start.setStyleSheet(dt.qss_primary_button())
        self._btn_stop.setStyleSheet(dt.qss_danger_button())

    # -- camera area ----------------------------------------------------------

    def _build_camera_area(self) -> QWidget:
        wrap = _card()
        self._camera_card = wrap
        wl = QVBoxLayout(wrap); wl.setContentsMargins(1, 1, 1, 1); wl.setSpacing(0)
        self.usb_panel = USBCameraPanel()
        wl.addWidget(self.usb_panel, 1)
        return wrap

    # -- right rail -----------------------------------------------------------

    def _build_right_rail(self) -> QWidget:
        rail = QWidget()
        rail.setMinimumWidth(320); rail.setMaximumWidth(420)
        rail.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        v = QVBoxLayout(rail); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(dt.SPACE_4)
        v.addWidget(self._build_session_card(), 0)
        v.addWidget(self._build_eeg_card(), 1)
        return rail

    def _build_session_card(self) -> QWidget:
        card = _card()
        self._session_card = card
        v = QVBoxLayout(card); v.setContentsMargins(24, 20, 24, 24); v.setSpacing(dt.SPACE_3)

        hdr = QHBoxLayout()
        self._sess_label = _section_label("Session")
        hdr.addWidget(self._sess_label)
        hdr.addStretch()
        self._plan_lbl = QLabel("— NO PLAN —")
        hdr.addWidget(self._plan_lbl)
        v.addLayout(hdr)

        self._dial = CircularTimer()
        self._dial.setMinimumHeight(200)
        self._dial.setMaximumHeight(320)
        self._dial.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        v.addWidget(self._dial, 1)
        v.addSpacing(8)

        self._next_lbl = QLabel("")
        self._next_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(self._next_lbl)

        self._path_lbl = QLabel("")
        self._path_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._path_lbl.setWordWrap(False)
        self._path_lbl.setVisible(False)  # Hidden until a session starts
        v.addWidget(self._path_lbl)

        self._restyle_session_card()
        return card

    def _restyle_session_card(self) -> None:
        self._sess_label.setStyleSheet(dt.qss_section_label())
        self._plan_lbl.setStyleSheet(dt.qss_data_readout(dt.SIZE_XS))
        self._next_lbl.setStyleSheet(
            f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
            f"font-size: {dt.SIZE_XS}px; letter-spacing: {dt.LS_WIDE};"
        )
        self._path_lbl.setStyleSheet(
            f"color: {dt.text_tertiary()}; font-family: {dt.FONT_MONO}; "
            f"font-size: {dt.SIZE_CAPTION}px;"
        )

    def _build_eeg_card(self) -> QWidget:
        card = _card()
        self._eeg_card = card
        v = QVBoxLayout(card); v.setContentsMargins(16, 14, 16, 16); v.setSpacing(dt.SPACE_2)

        hdr = QHBoxLayout()
        self._eeg_section_lbl = _section_label("EEG Streams")
        hdr.addWidget(self._eeg_section_lbl)
        hdr.addStretch()
        self._eeg_header = QLabel("0 / 2")
        hdr.addWidget(self._eeg_header)
        v.addLayout(hdr)

        self.eeg_widget_1 = EEGLiveWidget(channel_id=1)
        self.eeg_widget_2 = EEGLiveWidget(channel_id=2)
        self.eeg_widget_1.setMinimumHeight(140)
        self.eeg_widget_2.setMinimumHeight(140)
        v.addWidget(self.eeg_widget_1, 1)
        v.addWidget(self.eeg_widget_2, 1)

        self._eeg_status_timer = QTimer(self); self._eeg_status_timer.setInterval(2000)
        self._eeg_status_timer.timeout.connect(self._refresh_eeg_header)
        self._eeg_status_timer.start()

        self._restyle_eeg_card()
        return card

    def _restyle_eeg_card(self) -> None:
        self._eeg_section_lbl.setStyleSheet(dt.qss_section_label())
        self._refresh_eeg_header()

    def _refresh_eeg_header(self) -> None:
        n = sum(1 for w in (self.eeg_widget_1, self.eeg_widget_2)
                if getattr(w, "_connected_device", None))
        self._eeg_header.setText(f"{n} / 2")
        color = dt.success_color() if n == 2 else (dt.accent() if n == 1 else dt.text_tertiary())
        self._eeg_header.setStyleSheet(
            f"color: {color}; font-family: {dt.FONT_MONO}; font-size: {dt.SIZE_SM}px;"
        )

    # ═════════════════════════════════════════════════════════ Theme restyle

    def _restyle(self) -> None:
        """Called when the theme toggles — re-apply all stylesheets."""
        def _repolish(w):
            s = w.style()
            s.unpolish(w); s.polish(w); w.update()
        # Use direct QWidget selector (no objectName dependency) so it
        # applies regardless of QSS cascade quirks.
        if dt.current_theme() == "dark":
            self.setStyleSheet(
                f"LiveSessionTab {{ background: qradialgradient("
                f"cx:0.85, cy:0.2, radius:0.9, fx:0.85, fy:0.2, "
                f"stop:0 rgba(99, 125, 255, 0.12), "
                f"stop:0.5 rgba(99, 125, 255, 0.03), "
                f"stop:1 {dt.canvas_bg()}); }}"
            )
        else:
            self.setStyleSheet(
                f"LiveSessionTab {{ background: qradialgradient("
                f"cx:0.85, cy:0.15, radius:0.9, fx:0.85, fy:0.15, "
                f"stop:0 rgba(79, 70, 229, 0.06), "
                f"stop:0.5 rgba(79, 70, 229, 0.02), "
                f"stop:1 {dt.canvas_bg()}); }}"
            )
        _repolish(self)
        self._restyle_toolbar()
        for card in (self._camera_card, self._session_card, self._eeg_card):
            card.setStyleSheet(dt.qss_card())
            _repolish(card)
        self._restyle_session_card()
        self._restyle_eeg_card()
        if not self._controller.is_running:
            self._status_pill_lbl.setStyleSheet(dt.qss_status_pill("idle"))

    # ═══════════════════════════════════════════════════════════ Controller

    def _wire_controller(self) -> None:
        c = self._controller
        c.session_started.connect(self._on_session_started)
        c.phase_changed.connect(self._on_phase_changed)
        c.tick.connect(self._on_tick)
        c.beep_requested.connect(self._beep.play)
        c.session_finished.connect(self._on_session_finished)

    def _on_start_clicked(self) -> None:
        if self._controller.is_running: return
        connected = sum(1 for w in (self.eeg_widget_1, self.eeg_widget_2)
                        if getattr(w, "_connected_device", None))
        if connected == 0:
            reply = QMessageBox.question(
                self, "No EEG connected",
                "No Muse-S headbands are connected. Start the session anyway?\n\n"
                "Cameras will still record, but no EEG data will be captured.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes: return

        dlg = SessionSetupDialog(self, initial=self._controller.config)
        if dlg.exec() != dlg.DialogCode.Accepted: return
        cfg = dlg.value()
        try: self._controller.configure(cfg)
        except ValueError as e:
            QMessageBox.warning(self, "Invalid session", str(e)); return
        try:
            base_dir = _default_capture_root()
            os.makedirs(base_dir, exist_ok=True)
            self._layout = create_session(base_dir, cfg)
        except Exception as e:
            QMessageBox.critical(self, "Cannot create session folder", f"{e}"); return

        self._btn_start.setVisible(False); self._btn_stop.setVisible(True)
        self._status_pill_lbl.setText("RECORDING")
        self._status_pill_lbl.setStyleSheet(dt.qss_status_pill("recording"))
        self._plan_lbl.setText(self._describe_plan(cfg))
        self._path_lbl.setText(self._layout.root.name)
        self._path_lbl.setVisible(True)
        self._controller.start()

    def _on_stop_clicked(self) -> None:
        if not self._controller.is_running: return
        reply = QMessageBox.question(
            self, "Stop session?",
            "Stop the session now?\n\nThe current phase will be finalised.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if reply != QMessageBox.StandardButton.Yes: return
        self._close_current_phase_recordings()
        self._controller.stop()

    def _on_session_started(self) -> None: pass

    def _on_phase_changed(self, phase_id, label, duration, round_num) -> None:
        self._close_current_phase_recordings()
        total_rounds = self._controller.config.num_rounds
        self._dial.set_phase(phase_id, label, round_num, total_rounds)
        self._dial.set_time(duration, duration)
        self._phase_pill.setText(label.upper())
        self._phase_pill.setStyleSheet(dt.qss_status_pill(phase_id))
        self._phase_pill.setVisible(True)
        self._update_next_label()

        if self._layout is None: return
        phase = Phase(phase_id)
        if phase not in (Phase.BASELINE, Phase.ROUND): return
        phase_folder = self._layout.phase_dir(phase, round_num)
        if phase_folder is None: return
        self._layout.add_phase(PhaseRecord(
            phase=phase.value, label=label, round_num=round_num,
            folder=phase_folder.name, planned_duration=duration,
            start_unix=time.time(), start_monotonic=time.monotonic(),
        ))
        self._active_phase = (phase, round_num)
        muse_dir = self._layout.muse_dir(phase, round_num)
        if muse_dir is not None:
            self._start_eeg_recording(muse_dir, label)
        self.usb_panel.start_recording(phase_folder, label)

    def _on_tick(self, remaining, elapsed, total) -> None:
        self._dial.set_time(remaining, total)

    def _on_session_finished(self) -> None:
        self._close_current_phase_recordings()
        self._dial.set_phase("done", "COMPLETE", 0, 0)
        self._dial.set_time(0, 1)
        self._phase_pill.setText("SESSION COMPLETE")
        self._phase_pill.setStyleSheet(dt.qss_status_pill("done"))
        self._status_pill_lbl.setText("SAVED")
        self._status_pill_lbl.setStyleSheet(dt.qss_status_pill("connected"))
        self._btn_stop.setVisible(False); self._btn_start.setVisible(True)
        self._next_lbl.setText("")
        if self._layout is not None:
            self.session_saved.emit(str(self._layout.root))
            QTimer.singleShot(6000, self._reset_status_badge)

    def _reset_status_badge(self) -> None:
        if self._controller.is_running: return
        self._status_pill_lbl.setText("IDLE")
        self._status_pill_lbl.setStyleSheet(dt.qss_status_pill("idle"))
        self._phase_pill.setVisible(False)
        self._dial.reset()

    def _start_eeg_recording(self, muse_dir, phase_label) -> None:
        muse_dir.mkdir(parents=True, exist_ok=True)
        safe_label = phase_label.replace(" ", "_")
        def _path_for(widget, idx):
            dev = getattr(widget, "_connected_device", None)
            raw = dev["name"] if dev else f"Muse-{idx}"
            fn = f"{safe_label}_{_muse_friendly(raw)}.csv"
            return str(muse_dir / re.sub(r'[<>:"/\\|?*]', '-', fn))
        any_started = False
        if getattr(self.eeg_widget_1, "_connected_device", None):
            self.eeg_widget_1.start_recording(save_path=_path_for(self.eeg_widget_1, 1)); any_started = True
        if getattr(self.eeg_widget_2, "_connected_device", None):
            self.eeg_widget_2.start_recording(save_path=_path_for(self.eeg_widget_2, 2)); any_started = True
        self._eeg_recording_active = any_started

    def _close_current_phase_recordings(self) -> None:
        if self._eeg_recording_active:
            try: self.eeg_widget_1.stop_recording()
            except Exception: pass
            try: self.eeg_widget_2.stop_recording()
            except Exception: pass
            self._eeg_recording_active = False
        if self.usb_panel.any_live():
            self.usb_panel.stop_recording()
        if self._active_phase is not None and self._layout is not None:
            phase, round_num = self._active_phase
            self._layout.close_phase(phase, round_num)
            self._active_phase = None

    def shutdown(self) -> None:
        # Stop EEG threads FIRST so their LSL reader threads quit cleanly
        # before Qt tears down widgets. Then stop cameras (which finalize
        # MP4 moov atoms and release the capture devices).
        for eeg in (getattr(self, "eeg_widget_1", None),
                    getattr(self, "eeg_widget_2", None)):
            if eeg is None: continue
            try: eeg.stop_stream()
            except Exception: pass
            try: eeg.stop_recording()
            except Exception: pass
        try: self.usb_panel.shutdown()
        except Exception: pass
        # Stop the periodic timer that polls EEG header state, too —
        # Qt occasionally fires it into deleted widgets during teardown.
        try:
            if hasattr(self, "_eeg_status_timer"):
                self._eeg_status_timer.stop()
        except Exception: pass

    @staticmethod
    def _describe_plan(cfg):
        parts = []
        if cfg.baseline_sec: parts.append(f"BL {cfg.baseline_sec}s")
        mins = cfg.round_sec // 60; secs = cfg.round_sec % 60
        rd = f"{mins}:{secs:02d}" if mins else f"{secs}s"
        parts.append(f"{cfg.num_rounds}×{rd}")
        if cfg.break_sec: parts.append(f"BR {cfg.break_sec}s")
        return "  ·  ".join(parts)

    def _update_next_label(self):
        plan = self._controller.phase_plan()
        cur  = self._controller.current_phase_info
        if cur is None or not plan:
            self._next_lbl.setText(""); return
        try: i = plan.index(cur)
        except ValueError:
            self._next_lbl.setText(""); return
        if i + 1 < len(plan):
            self._next_lbl.setText(f"NEXT  ·  {plan[i + 1].label}".upper())
        else:
            self._next_lbl.setText("FINAL PHASE")


__all__ = ["LiveSessionTab"]
