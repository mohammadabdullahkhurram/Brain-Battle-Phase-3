"""
record_flow.py
──────────────
The guided recording flow (DESIGN "New recording", design F): full-screen, its
own header, three screens — Get ready → Recording → Saved. The views live in
``ui/record_screens.py``; this module owns the devices and the recording:

  • the four CameraSlots and two HeadbandSlots (connected on Get ready, they
    keep running into Recording);
  • the SessionController (lead-in, baseline, rounds, breaks; system
    markers; the bell at the start and end of every round);
  • the recording folder (session_storage), the live LSL streams (off by
    default), the 1 Hz contact-quality and timing logs, the buzzer test, the
    moment notes, and recording.xdf when it is finished.

Every write of the recording's own bookkeeping goes through ``_essential``:
a failure is shown on Recording and the Saved screen says "saved, but with
problems" — the recording itself never stops.

Under BRAIN_BATTLE_SIMULATE the devices are simulated through the same real
paths; with the flag unset there is no trace of simulation.

Public surface used by the app shell:
  • RecordFlow(parent)
  • .start()                 — (re)enter at Get ready, scan + connect devices
  • .session_saved (signal)  — "Start labelling" on Saved (root path)
  • .exit_flow (signal)      — Cancel / "Back to sessions"
  • .is_recording()          — for the app's quit-confirmation
  • .shutdown()              — stop devices/threads before teardown
"""
from __future__ import annotations

import datetime as _dt
import os
import re
import threading
import time
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QRectF, QSize
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QMessageBox, QStackedWidget, QDialog, QListWidget,
    QListWidgetItem, QDialogButtonBox, QLabel,
)

import logging

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.app_settings as app_settings
import brain_battle.data.safe_io as safe_io
import brain_battle.capture.camera_source as camera_source
import brain_battle.capture.clock as clock
import brain_battle.capture.contact_log as contact_log
import brain_battle.capture.timing as timing
import brain_battle.capture.timing_log as timing_log
import brain_battle.data.session_history as session_history
import brain_battle.data.session_xdf as session_xdf
from brain_battle.capture.lsl_outlets import LslPublisher
from brain_battle.capture.headband_source import HeadbandSlot
from brain_battle.data.signals import EEG_CHANNELS
from brain_battle.ui.eeg_viewer import EEGViewer
from brain_battle.capture.camera_source import CameraSlot
from brain_battle.capture.session_controller import SessionController, SessionConfig, Phase, _build_timeline
from brain_battle.data.session_storage import (
    create_session, SessionLayout, PhaseRecord, estimate_session_bytes,
    human_bytes, session_total_seconds, disk_space_verdict,
    video_bytes_per_sec_for_mbps, folder_size_bytes, read_phase_markers, short_phase,
)
from brain_battle.capture.round_bell import RoundBell
from brain_battle.capture.keep_awake import KeepAwake
from brain_battle.support.log_setup import attach_session_log, detach_session_log
from brain_battle.support import platform_utils
from brain_battle.capture.usb_camera import list_cameras, preflight_recording, CameraDevice
from brain_battle.ui.fonts import register_fonts
from brain_battle.ui.record_screens import (
    FlowHeader, GetReadyScreen, RecordingScreen, SavedScreen, MomentDialog, TimingDialog,
    mmss as _mmss)
import brain_battle.data.fighters as fighters
from brain_battle.ui.widgets import versus_html as ui_versus
from brain_battle.capture.session_controller import LEAD_IN_DEFAULT

log = logging.getLogger(__name__)

_ILLEGAL = re.compile(r'[<>:"/\\|?*]')


# ── small helpers ────────────────────────────────────────────────────────────



def _join_words(items) -> str:
    """["a", "b", "c"] → "a, b and c"."""
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


class RecordFlow(QWidget):
    session_saved  = pyqtSignal(str)     # "Label this recording" (session root)
    exit_flow      = pyqtSignal()        # Cancel / "Back to sessions"
    help_requested = pyqtSignal(str)     # the CURRENT step's doc anchor
    _xdf_done      = pyqtSignal(str, bool)   # (session root, built OK) — from the build thread

    # Step 1 "Get ready" holds both the devices (ConnectStep) and the plan
    # (SetupStep), so STEP_CONNECT and STEP_SETUP are the same screen.
    STEP_READY, STEP_RECORD, STEP_SAVED = 0, 1, 2
    STEP_CONNECT = STEP_SETUP = STEP_READY
    _STEP_ANCHORS = {
        STEP_READY: "step-1-get-ready",
        STEP_RECORD: "step-2-recording",
        STEP_SAVED: "step-3-saved",
    }

    def __init__(self, parent=None):
        super().__init__(parent)
        register_fonts()        # the bundled fonts, however this screen is made
        self._settings = app_settings.load()
        self._cams = camera_source.make_slots(self)
        self._bands = {"A": HeadbandSlot("A", self), "B": HeadbandSlot("B", self)}
        self._controller = SessionController(self)
        self._beep = RoundBell(self)
        self._keep_awake = KeepAwake()
        self._layout: Optional[SessionLayout] = None
        self._cfg: Optional[SessionConfig] = None
        self._names = {"A": "", "B": ""}
        self._bell = False
        self._session_t0 = None
        self._session_t0_unix = None
        self._moments = []
        self._live_markers = []
        self._contact_writer = None
        self._timing_writer = None
        self._calibration = None          # the buzzer test's result (this Get ready)
        self._buzzer = None
        self._seen_stalls = {}            # slot index → stall events already logged
        self._seen_lost = {}              # boxer → headband drops already logged
        self._active_phase = None
        self._stopping_early = False
        self._cam_rec_paths = {}
        self._band_rec_paths = {}
        self._saved_root = None
        self._write_problems = []        # what could not be saved (plain words), in order
        self._scan_thread = None
        self._lsl: Optional[LslPublisher] = None
        self._xdf_thread: Optional[threading.Thread] = None
        self._xdf_root: Optional[str] = None     # recording waiting for its XDF
        self._xdf_wait_started = 0.0
        self._xdf_done.connect(self._on_xdf_done)
        self.setObjectName("recordFlow")
        self.setStyleSheet(f"#recordFlow {{ background: {dt.canvas_bg()}; }}")
        self._build()
        self._wire_controller()

        self._tick = QTimer(self); self._tick.setInterval(60)
        self._tick.timeout.connect(self._on_ui_tick)
        self._contact_timer = QTimer(self); self._contact_timer.setInterval(1000)
        self._contact_timer.timeout.connect(self._log_contact)
        # After a recording stops, wait for every camera file to be closed
        # before building recording.xdf from the finished files.
        self._xdf_timer = QTimer(self); self._xdf_timer.setInterval(150)
        self._xdf_timer.timeout.connect(self._maybe_build_xdf)

    # ── construction ──
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        self.header = FlowHeader()
        self.header.cancel.connect(self.cancel)
        self.header.stop.connect(self.ask_stop)
        self._cancel = self.header.cancel_btn
        root.addWidget(self.header)
        self._stack = QStackedWidget()
        self._get_ready = GetReadyScreen(self)
        # older names for the one Get ready screen (devices + plan)
        self._setup_step = self._connect_step = self._ready_page = self._get_ready
        self._recording_step = RecordingScreen(self)
        self._saved_step = SavedScreen(self)
        for w in (self._get_ready, self._recording_step, self._saved_step):
            self._stack.addWidget(w)
        root.addWidget(self._stack, 1)
        self._moment_dialog = None
        self._timing_dialog = None

    # ── public: entry / navigation ──
    def start(self):
        """(Re)enter the flow at step 1 and (re)scan + connect devices."""
        if self._controller.is_running:
            return
        self._settings = app_settings.load()
        self._reset_session_state()
        self._stop_devices()
        # Live LSL streams (LabRecorder / LSL viewers can see them from now on).
        self._lsl = LslPublisher(enabled=self._settings.lsl_streams)
        self._lsl.open_markers()
        for slot in self._cams:
            self._lsl.open_video(slot.stem)
        for band in self._bands.values():
            band.publisher = self._lsl
        self._names = {"A": "", "B": ""}
        self._setup_step.populate_from_settings()
        self._set_step(self.STEP_READY)
        self._scan_and_assign()
        self._tick.start()

    # ── programmatic set-up (Get ready's widgets, tools, tests) ──
    def set_plan(self, cfg: SessionConfig) -> None:
        """Rounds, round / break / baseline length for the next recording."""
        self._setup_step.set_plan(cfg)

    def set_fighters(self, code_a: str, code_b: str, colour_a: str = "Red",
                     colour_b: str = "Blue") -> None:
        """Both fighters' codes and colours for the next recording."""
        self._setup_step.set_fighters(code_a, code_b, colour_a, colour_b)

    def goto_setup(self):
        """Show "Get ready" (devices + plan are one screen now; kept so callers
        that asked for the old second step still land in the right place)."""
        self._set_step(self.STEP_READY)

    def _set_step(self, idx):
        self._stack.setCurrentIndex(idx)
        self.header.set_step(min(idx, 2) + 1)

    def _reset_session_state(self):
        self._calibration = None
        self._seen_stalls = {}; self._seen_lost = {}
        self._layout = None; self._cfg = None
        self._moments = []; self._active_phase = None
        self._live_markers = []
        self._stopping_early = False
        self._cam_rec_paths = {}; self._band_rec_paths = {}
        self._saved_root = None
        self._write_problems = []

    # ── device scanning / assignment ──
    def _scan_and_assign(self):
        from PyQt6.QtCore import QSettings
        devs = list_cameras()
        remembered = QSettings("Constructor University", "Brain Battle")
        used = set()
        # honour remembered slot→device-name assignments across launches
        for slot in self._cams:
            want = remembered.value(f"cameras/slot{slot.index}")
            match = next((d for d in devs if d.name == want and d.index not in used), None)
            if match:
                slot.assign(match); used.add(match.index)
            else:
                slot.assign(None)
        rest = [d for d in devs if d.index not in used]
        for slot in self._cams:
            if slot.device is None and rest:
                d = rest.pop(0); slot.assign(d); used.add(d.index)
        for slot in self._cams:
            if slot.device is not None:
                slot.start()
                remembered.setValue(f"cameras/slot{slot.index}", slot.device.name)
        self._scan_headbands()

    def _scan_headbands(self):
        try:
            import brain_battle.capture.simulation as simulation
            if simulation.enabled():
                for d in simulation.muse_devices():
                    who = d.get("boxer", "")
                    if who in self._bands:
                        self._bands[who].assign({"name": d["name"], "address": d["address"]})
                        self._bands[who].start()
                return
        except Exception:
            pass
        # Real BLE scan, mapped to A/B via the settings headband mapping.
        from brain_battle.capture.eeg_module import _MuseScanThread
        th = _MuseScanThread(self)
        th.found.connect(self._on_headbands_found)
        th.error.connect(lambda m: log.warning("headband scan error: %s", m))
        self._scan_thread = th
        th.start()

    def _on_headbands_found(self, devices):
        want = {self._settings.headband_for("A"): "A", self._settings.headband_for("B"): "B"}
        for d in devices:
            who = want.get(d.get("name"))
            if who and not self._bands[who].connected:
                self._bands[who].assign({"name": d["name"], "address": d["address"]})
                self._bands[who].start()

    def search_again(self):
        self._stop_devices()
        self._scan_and_assign()

    def open_camera_picker(self, slot_index):
        claimed = {s.device.index for s in self._cams if s.device is not None
                   and s.index != slot_index}
        dlg = _CameraPicker(claimed, self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.chosen is not None:
            slot = next(s for s in self._cams if s.index == slot_index)
            slot.shutdown()
            slot.assign(dlg.chosen)
            slot.start()
            from PyQt6.QtCore import QSettings
            QSettings("Constructor University", "Brain Battle").setValue(
                f"cameras/slot{slot_index}", dlg.chosen.name)

    def reconnect_band(self, which):
        b = self._bands.get(which)
        if b is not None:
            b.reconnect()

    def boxer_name(self, which):
        """The fighter's code (as typed on Get ready, or of the recording)."""
        if self._names.get(which):
            return self._names[which]
        return self._get_ready.names()[0 if which == "A" else 1]

    # ── recording ──
    def _wire_controller(self):
        c = self._controller
        c.session_started.connect(self._on_session_started)
        c.phase_changed.connect(self._on_phase_changed)
        c.marker.connect(self._on_system_marker)
        c.tick.connect(self._on_phase_tick)
        c.session_finished.connect(self._on_session_finished)

    def begin_recording(self):
        if self._controller.is_running:
            return
        self._cfg = self._setup_step.config()
        a, b = self._setup_step.names()
        self._names = {"A": a, "B": b}
        self._bell = self._setup_step.bell()
        err = self._cfg.validate()
        if err:
            QMessageBox.warning(self, "Invalid session", err); return

        rec_ok, _enc, rec_msg = preflight_recording()
        if not rec_ok:
            QMessageBox.critical(self, "Can't record video", rec_msg); return

        base = self._settings.capture_dir_resolved()
        state = safe_io.folder_status(base, create=True)
        if state != safe_io.FOLDER_OK:
            QMessageBox.critical(self, "The recordings folder can't be used",
                                 safe_io.folder_problem_text(base, state)); return
        if not self._disk_guard(base):
            return

        import brain_battle.data.fighters as fighters
        problem = fighters.codes_problem(a, b) or fighters.colours_problem(*self._setup_step.colours())
        if problem:
            QMessageBox.warning(self, "Fighter codes and colours", problem + "."); return
        colours = fighters.load_colours(base)
        ca, cb = self._setup_step.colours()
        boxer_a = fighters.boxer_entry(a, ca, fighters.colour_hex(ca, colours),
                                       self._settings.headband_for("A"))
        boxer_b = fighters.boxer_entry(b, cb, fighters.colour_hex(cb, colours),
                                       self._settings.headband_for("B"))
        self._colours = {"A": (ca, boxer_a["colour_hex"]), "B": (cb, boxer_b["colour_hex"])}
        # Get ready's plan and bell are remembered for the next recording
        try:
            self._settings.session_defaults = {
                "num_rounds": self._cfg.num_rounds, "round_sec": self._cfg.round_sec,
                "break_sec": self._cfg.break_sec, "baseline_sec": self._cfg.baseline_sec}
            self._settings.round_bell = bool(self._bell)
            self._settings.save()
        except Exception as e:
            log.warning("could not remember the plan: %r", e)
        try:
            self._layout = create_session(base, self._cfg, boxer_a=boxer_a, boxer_b=boxer_b)
            attach_session_log(self._layout.root)
        except Exception as e:
            QMessageBox.critical(
                self, "The recording could not be started",
                f"The folder for this recording could not be made in {base} ({e}). Check that "
                f"the drive is connected and that you are allowed to save there."); return

        # round bell: the start AND the end of every round (SessionController)
        try:
            self._controller.bell_requested.disconnect(self._beep.play)
        except TypeError:
            pass
        if self._bell:
            self._controller.bell_requested.connect(self._beep.play)

        self._controller.configure(self._cfg)
        self._recording_step.reset_for_recording()
        self._set_step(self.STEP_RECORD)
        self._keep_awake.start()
        self._install_key_filter()
        self._controller.start()

    def _disk_guard(self, base) -> bool:
        import shutil
        n_cam = sum(1 for s in self._cams if s.is_live)
        n_eeg = sum(1 for b in self._bands.values() if b.connected)
        try:
            free = shutil.disk_usage(base).free
        except OSError:
            return True
        cams = n_cam or len(self._cams)
        vbps = video_bytes_per_sec_for_mbps(self._settings.video_mbps())
        est = estimate_session_bytes(self._cfg, cams, n_eeg, vbps)
        verdict = disk_space_verdict(self._cfg, cams, n_eeg, free, vbps)
        if verdict == "blocked":
            QMessageBox.critical(self, "Not enough disk space",
                                 f"This recording needs about {human_bytes(est)}, but only "
                                 f"{human_bytes(free)} is free. Free up space or shorten the "
                                 f"session, then try again.")
            return False
        if verdict == "low":
            r = QMessageBox.question(self, "Low disk space",
                                     f"This recording needs about {human_bytes(est)}. Only "
                                     f"{human_bytes(free)} is free. Start anyway?",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return False
        return True

    # ── writes that must not fail quietly ──
    # What each essential file holds, in the words the operator sees.
    _WHAT = {"plan": "the recording's details", "times": "the round and break times",
             "moments": "a moment you marked", "history": "the recording's history",
             "contact": "the sensor-contact notes", "timing": "the timing report"}

    def _essential(self, what, fn, *args, **kwargs):
        """Run one write of the recording's own bookkeeping (manifest, markers,
        history). A failure is logged, remembered for the Saved screen and shown
        on the Recording screen straight away — it never stops the recording."""
        try:
            result = fn(*args, **kwargs)
            if result is False:              # session_history.log_event's "not written"
                raise OSError("the line was not written")
            return result
        except Exception as e:
            log.error("could not save %s (%s): %r", self._WHAT[what],
                      getattr(fn, "__name__", "write"), e)
            self._note_write_problem(what)
            return None

    def _note_write_problem(self, what):
        text = self._WHAT[what]
        if text not in self._write_problems:
            self._write_problems.append(text)
        self._recording_step.show_write_problem(self.write_problem_text())

    def write_problems(self) -> list:
        """What could not be saved during this recording (empty = all saved)."""
        return list(self._write_problems)

    def write_problem_text(self) -> str:
        if not self._write_problems:
            return ""
        return (f"Could not save {_join_words(self._write_problems)} to the recordings folder. "
                f"The cameras and headbands keep recording. Check that the drive is still "
                f"connected and has free space.")

    def _on_session_started(self):
        if self._layout is None:
            return
        # ONE origin for every source, on the one capture clock.
        origin = clock.session_origin()
        self._session_t0 = origin["t0_monotonic"]
        self._session_t0_unix = origin["t0_unix"]
        cam_clock = (self._session_t0, self._session_t0_unix)
        self._essential("plan", self._layout.set_capture_origin, **origin)
        self._essential("history", session_history.log_event, self._layout.root,
                        session_history.EVENT_RECORDING_STARTED,
                        {"rounds": self._cfg.num_rounds, "round_sec": self._cfg.round_sec,
                         "break_sec": self._cfg.break_sec, "baseline_sec": self._cfg.baseline_sec})
        cont = self._layout.continuous_dir()
        # cameras
        self._cam_rec_paths = {}
        for slot in self._cams:
            if slot.is_live:
                path = slot.recording_path(cont)
                slot.start_recording(path, cam_clock, self._lsl)
                self._cam_rec_paths[slot.index] = path
        # headbands
        from brain_battle.capture.eeg_module import muse_friendly
        muse_dir = cont / "Muse data"
        self._essential("plan", muse_dir.mkdir, parents=True, exist_ok=True)
        self._band_rec_paths = {}
        for which, band in self._bands.items():
            if band.connected and band.device:
                fn = f"session_{muse_friendly(band.device.get('name',''))}.csv"
                path = str(muse_dir / _ILLEGAL.sub("-", fn))
                band.start_recording(path, self._session_t0_unix)
                self._band_rec_paths[which] = path
        # contact-quality log + timing report (both once a second)
        self._contact_writer = contact_log.ContactLogWriter(contact_log.path_for(self._layout.root))
        if not self._contact_writer.is_open:
            self._note_write_problem("contact")
        self._timing_writer = timing_log.TimingQualityWriter(timing_log.path_for(self._layout.root))
        if not self._timing_writer.is_open:
            self._note_write_problem("timing")
        self._essential("plan", self._layout.set_timing_calibration, self._calibration)
        if self._calibration:
            self._essential("history", session_history.log_event, self._layout.root,
                            session_history.EVENT_TIMING_CALIBRATED,
                            {"largest_difference_ms": self._calibration.get("largest_difference_ms")})
        want_fps = float(self._settings.video_dims()[2])
        self._essential("plan", self._layout.set_camera_fps,
                        {s.stem: {"requested": want_fps, "actual": round(s.actual_fps, 2)}
                         for s in self._cams if s.is_live})
        for s in self._cams:
            if s.fps_note and self._timing_writer.is_open:
                self._essential("timing", self._timing_writer.append, 0.0, s.stem, s.actual_fps,
                                None, 0, None, "30 fps (could not record at 60)")
        self._seen_stalls = {s.index: len(s.stall_events) for s in self._cams}
        self._seen_lost = {w: b.reconnections for w, b in self._bands.items()}
        self._contact_timer.start()

    def _on_phase_changed(self, phase_id, label, duration, round_num):
        total_rounds = self._cfg.num_rounds
        self._recording_step.set_phase(phase_id, label, round_num, total_rounds, duration)
        self._recording_step.set_time(duration, duration, self._elapsed_session())
        if self._layout is None:
            return
        phase = Phase(phase_id)
        if phase in (Phase.LEAD_IN, Phase.BASELINE, Phase.ROUND, Phase.BREAK):
            self._essential("plan", self._layout.add_phase, PhaseRecord(
                phase=phase.value, label=label, round_num=round_num, folder="continuous",
                planned_duration=duration, start_unix=time.time(),
                start_monotonic=clock.monotonic()))
            if self._active_phase is not None:
                pp, pr = self._active_phase
                self._essential("plan", self._layout.close_phase, pp, pr)
            self._active_phase = (phase, round_num)

    def _on_system_marker(self, text, phase_id, round_num):
        """An automatic marker (RECORDING_START, ROUND_1_END, …): stamp it on
        the capture clock, append it to phase_markers.csv and publish it."""
        if self._layout is None or self._session_t0 is None:
            return
        t = clock.monotonic() - self._session_t0
        self._essential("times", self._layout.append_marker, t, phase_id, text, round_num)
        self._publish_marker(text, t)

    def _on_phase_tick(self, remaining, elapsed, total):
        self._recording_step.set_time(remaining, total, self._elapsed_session())

    def _elapsed_session(self):
        if self._session_t0 is None:
            return 0.0
        return clock.monotonic() - self._session_t0

    def _on_session_finished(self):
        self._finish(stopped_early=self._stopping_early)

    def stop_and_save(self):
        if not self._controller.is_running:
            return
        self._stopping_early = True
        self._controller.stop()   # → _on_session_finished

    def _finish(self, stopped_early=False, goto_saved=True):
        # RECORDING_END was just written by _on_system_marker (the controller
        # emits it before session_finished).
        if self._layout is not None and self._session_t0 is not None and stopped_early:
            t = clock.monotonic() - self._session_t0
            self._essential("plan", self._layout.mark_stopped_early, t)
        if self._active_phase is not None and self._layout is not None:
            ph, rn = self._active_phase
            self._essential("plan", self._layout.close_phase, ph, rn)
            self._active_phase = None
        self._contact_timer.stop()
        if self._contact_writer is not None:
            self._contact_writer.close(); self._contact_writer = None
        if self._timing_writer is not None:
            self._log_timing()                 # the last second
            self._timing_writer.close(); self._timing_writer = None
        # stop recordings (cameras + bands), record each band's first-sample time
        for slot in self._cams:
            slot.stop_recording()
        for idx, which in enumerate(("A", "B"), start=1):
            band = self._bands[which]
            band.stop_recording()
            if self._layout is not None:
                first = band.last_recorded_first_unix()
                if first is not None:
                    self._essential("plan", self._layout.record_source_start,
                                    f"EEG_{idx:02d}", first)
        self._keep_awake.stop()
        self._remove_key_filter()
        if self._layout is not None:
            self._essential("history", session_history.log_event, self._layout.root,
                            session_history.EVENT_RECORDING_FINISHED)
            self._saved_root = self._xdf_root = str(self._layout.root)
            self._xdf_wait_started = time.monotonic()
            self._xdf_timer.start()
        if goto_saved and self._saved_root:
            self._saved_step.populate(self._build_saved_info())
            self._set_step(self.STEP_SAVED)

    def _publish_marker(self, text, t_session):
        """A phase start / marked moment happened: show it on the live EEG and
        publish it on the LSL marker stream."""
        if self._session_t0 is None:
            return
        pretty = "Moment" if text == session_xdf.MOMENT_MARKER else short_phase(text)
        colour = dt.record_red() if text == session_xdf.MOMENT_MARKER else dt.text_secondary()
        self._live_markers.append((self._session_t0_unix + t_session, pretty, colour))
        if self._lsl is not None:
            self._lsl.push_marker(text, self._session_t0 + t_session)

    def live_markers(self):
        """[(unix time, text, colour)] for the live EEG display."""
        return self._live_markers

    def session_origin_unix(self):
        """The running recording's start (Unix seconds), or None when idle."""
        return self._session_t0_unix if self._controller.is_running else None

    # ── recording.xdf (built from the finished files, off the UI thread) ──
    def _maybe_build_xdf(self):
        waited = time.monotonic() - self._xdf_wait_started
        if not all(s.recording_finalised for s in self._cams) and waited < 20.0:
            return
        if self._xdf_thread is not None:
            return                      # a build is running; try again next tick
        self._xdf_timer.stop()
        root, self._xdf_root = self._xdf_root, None
        if not root:
            return

        def work():
            try:
                ok = session_xdf.build_session_xdf(root) is not None
            except Exception as e:
                log.error("building recording.xdf failed: %r", e)
                ok = False
            self._xdf_done.emit(root, ok)

        self._xdf_thread = threading.Thread(target=work, name="xdf-build", daemon=True)
        self._xdf_thread.start()

    def _on_xdf_done(self, root, ok):
        self._xdf_thread = None
        if not self._controller.is_running:
            detach_session_log()        # this recording's own log file is complete
        if root == self._saved_root:
            self._saved_step.set_xdf_state(ok)
            for text in self._frame_rate_notes(root):
                self._saved_step.add_worth_checking(text)

    def _frame_rate_notes(self, root) -> list:
        """A line for each camera whose real frame rate was more than 1 % off
        the rate it was asked for (only knowable once its file is closed)."""
        from brain_battle.data.session_storage import camera_frame_rates
        want = float(self._settings.video_dims()[2])
        titles = {s.stem: s.title for s in self._cams}
        notes = []
        try:
            for stem, _n, fps in camera_frame_rates(root):
                if abs(fps - want) / want > 0.01:
                    notes.append(
                        f"{titles.get(stem, stem)} recorded {fps:.1f} frames per second instead "
                        f"of {want:.0f}. Playback and the analysis files use each frame's real "
                        f"time, so nothing is out of step — but the camera or its cable may "
                        f"need a look.")
        except Exception as e:
            log.warning("frame-rate check failed: %r", e)
        return notes

    def wait_for_xdf(self, timeout: float = 30.0) -> bool:
        """Block (processing events) until the pending recording.xdf build has
        finished. Used on shutdown and by tests."""
        from PyQt6.QtWidgets import QApplication
        deadline = time.monotonic() + timeout
        while (self._xdf_timer.isActive() or self._xdf_thread is not None) \
                and time.monotonic() < deadline:
            QApplication.processEvents()
            time.sleep(0.01)
        return not self._xdf_timer.isActive() and self._xdf_thread is None

    # ── mark this moment ──
    def mark_moment(self):
        """M: a moment at this instant; a small note window opens. Recording,
        the timer and capture never pause; a second M while the window is open
        is ignored (the first moment is still being described)."""
        if not self._controller.is_running or self._layout is None:
            return
        if self._moment_dialog is not None and self._moment_dialog.isVisible():
            return
        t = clock.monotonic() - self._session_t0
        entry = self._essential("moments", self._layout.add_moment, t)
        self._publish_marker(session_xdf.MOMENT_MARKER, t)
        if entry is None:                    # not saved: don't count it as marked
            return
        self._moments.append(entry)
        self._essential("history", session_history.log_event, self._layout.root,
                        session_history.EVENT_MOMENT_MARKED,
                        {"t_session_seconds": entry["t_session_seconds"]})
        self._show_moment_count()
        dlg = MomentDialog(len(self._moments) - 1, t, self)
        dlg.saved.connect(self.set_moment_note)
        self._moment_dialog = dlg
        if os.environ.get("QT_QPA_PLATFORM") != "offscreen":
            dlg.show()
        else:                                 # headless (tests): created, not shown
            dlg.setVisible(False)

    def set_moment_note(self, index: int, note: str):
        """Save note — while still recording (the popup)."""
        if self._layout is None or not (0 <= index < len(self._layout.moments)):
            return
        entry = self._essential("moments", self._layout.set_moment_note, index, note)
        if entry is not None:
            self._moments[index] = entry
            self._essential("history", session_history.log_event, self._layout.root,
                            session_history.EVENT_MOMENT_NOTED,
                            {"t_session_seconds": entry["t_session_seconds"], "note": entry["note"]})
        self._show_moment_count()

    def _show_moment_count(self):
        noted = sum(1 for m in self._moments if m.get("note"))
        self._recording_step.show_moment(0, len(self._moments), noted)

    # ── header / dialogs ──
    def ask_stop(self):
        """Stop recording (header): ask first — the round is not finished."""
        if not self._controller.is_running:
            return
        r = QMessageBox.question(self, "Stop the recording now?", self.stop_prompt_text(),
                                 QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                 QMessageBox.StandardButton.No)
        if r == QMessageBox.StandardButton.Yes:
            self.stop_and_save()

    def open_timing_dialog(self):
        """Test timing (Get ready) / the timing line (Recording). The dialog
        never pauses recording."""
        rec = self.is_recording()
        dlg = TimingDialog(self, recording=rec, parent=self)
        self._timing_dialog = dlg
        if rec:
            dlg.show()
        else:
            dlg.exec()
            self._get_ready._recompute()

    def swap_headbands(self):
        """⇄: the headband that was Boxer A's is now Boxer B's (and remembered)."""
        if self.is_recording():
            return
        s = self._settings
        s.headbands = {"A": s.headband_for("B"), "B": s.headband_for("A")}
        s.save()
        for b in self._bands.values():
            b.shutdown()
        da, db = self._bands["A"].device, self._bands["B"].device
        self._bands["A"].assign(db); self._bands["B"].assign(da)
        for b in self._bands.values():
            if b.device is not None:
                b.start()

    def disk_check(self, cfg) -> tuple:
        """(enough space and folder usable?, plain note) for the checklist."""
        import shutil
        base = self._settings.capture_dir_resolved()
        state = safe_io.folder_status(base, create=True)
        if state != safe_io.FOLDER_OK:
            return False, ("The recordings folder can't be reached — connect its drive or choose "
                           "another folder in Settings." if state == safe_io.FOLDER_MISSING else
                           "This computer can't save into the recordings folder — choose another "
                           "in Settings.")
        try:
            free = shutil.disk_usage(base).free
        except OSError:
            return True, "Free space unknown"
        n_cam = sum(1 for s in self._cams if s.is_live) or len(self._cams)
        n_eeg = sum(1 for b in self._bands.values() if b.connected) or 2
        vbps = video_bytes_per_sec_for_mbps(self._settings.video_mbps())
        est = estimate_session_bytes(cfg, n_cam, n_eeg, vbps)
        verdict = disk_space_verdict(cfg, n_cam, n_eeg, free, vbps)
        note = f"Needs about {human_bytes(est)} · {human_bytes(free)} free"
        if verdict == "blocked":
            return False, f"Not enough space: {note}. Free up space or shorten the session."
        if safe_io.is_network_path(base):
            note += " · on a network drive (recording to this computer is safer)"
        return True, note

    def fighter_colour_hex(self, which):
        c = getattr(self, "_colours", None) or {}
        if which in c:
            return c[which][1]
        name = self._get_ready.card(which).colour
        return fighters.colour_hex(name, fighters.load_colours(self._settings.capture_dir_resolved()))

    def record_another(self):
        """Saved → Record another: back to Get ready with the devices on."""
        self._stop_devices()
        self._tick.stop()
        self.start()

    # ── recording-screen text helpers ──
    def next_phase_text(self):
        plan = self._controller.phase_plan()
        cur = self._controller.current_phase_info
        if cur is None or not plan:
            return ""
        try:
            i = plan.index(cur)
        except ValueError:
            return ""
        if i + 1 >= len(plan):
            return "Last round — the recording stops by itself when it ends."
        nxt = plan[i + 1]
        nm = {"baseline": "Baseline", "round": f"Round {nxt.round_num}",
              "break": "Break"}.get(nxt.phase.value, nxt.label)
        txt = f"Next: {nm}, {_mmss(nxt.duration)}"
        if self._bell and i + 2 < len(plan) and plan[i + 2].phase == Phase.ROUND:
            txt += f" · the bell rings when round {plan[i + 2].round_num} starts"
        elif self._bell and nxt.phase == Phase.ROUND:
            txt += f" · the bell rings when round {nxt.round_num} starts"
        return txt

    def stop_prompt_text(self):
        cur = self._controller.current_phase_info
        where = ""
        if cur is not None:
            nm = {"lead_in": "The lead-in", "baseline": "The baseline",
                  "round": f"Round {cur.round_num}",
                  "break": "The break"}.get(cur.phase.value, cur.label)
            where = f"{nm} is still running. "
        return (f"Stop recording now? {where}Everything recorded so far is kept, but the "
                f"rest of the session will not happen.")

    def health_text(self):
        problems = []
        ncam = 0
        for s in self._cams:
            if s.device is None:
                continue
            if s.stalled:
                problems.append(f"{s.title} stopped sending pictures — reconnecting")
            elif s.rec_failed:
                problems.append(f"{s.title} stopped recording")
            elif s.state == "error":
                problems.append(f"{s.title} is not connected")
            elif s.dropped:
                problems.append(f"{s.title} is losing frames")
            else:
                ncam += 1
        nb = 0
        for which, b in self._bands.items():
            if b.device is None:
                continue
            if b.lost:
                problems.append(f"Boxer {which}'s headband lost connection")
            elif not b.connected:
                problems.append(f"Boxer {which}'s headband is connecting")
            else:
                nb += 1
        if not problems:
            return f"✓ All {ncam} camera{'s' if ncam != 1 else ''} and {nb} headband" \
                   f"{'s' if nb != 1 else ''} are recording"
        return "! " + "; ".join(problems) + "."

    def health_ok(self):
        return self.health_text().startswith("✓")

    def _log_contact(self):
        """Once a second while recording: sensor contact and the timing report."""
        if self._session_t0 is None:
            return
        t = clock.monotonic() - self._session_t0
        w = self._contact_writer
        if w is not None and w.is_open and not w.failed:
            for which, b in self._bands.items():
                if b.connected:
                    summary, statuses = b.contact()
                    if self._essential("contact", w.append, t, which, summary, statuses) is None \
                            and w.failed:
                        break
        self._log_timing(t)

    def _log_timing(self, t=None):
        """One timing_quality.csv row per device; camera stalls / reopenings
        and headband drops also as rows and in the history."""
        w = self._timing_writer
        if w is None or not w.is_open or self._session_t0 is None or self._layout is None:
            return
        t = (clock.monotonic() - self._session_t0) if t is None else t
        corr = timing.corrections(self._calibration or {})
        rows = []
        for s in self._cams:
            if s.device is None or s.index not in self._cam_rec_paths:
                continue
            snap = s.timing_snapshot()
            off = corr.get(timing.camera_device(s.stem))
            rows.append((t, s.stem, snap.rate if snap else 0.0, snap.jitter_ms if snap else None,
                         snap.dropped if snap else 0, None if off is None else off * 1000, ""))
            new = s.stall_events[self._seen_stalls.get(s.index, 0):]
            self._seen_stalls[s.index] = len(s.stall_events)
            for t_ev, kind, secs in new:
                t_rel = t_ev - self._session_t0
                if kind == "stalled":
                    rows.append((t_rel, s.stem, 0.0, None, 0, None, "stalled"))
                    self._essential("history", session_history.log_event, self._layout.root,
                                    session_history.EVENT_CAMERA_STALLED,
                                    {"camera": s.title, "t_session_seconds": round(t_rel, 3)})
                else:
                    rows.append((t_rel, s.stem, 0.0, None, 0, None,
                                 f"reopened after {secs:.1f} s"))
                    self._essential("history", session_history.log_event, self._layout.root,
                                    session_history.EVENT_CAMERA_RECOVERED,
                                    {"camera": s.title, "t_session_seconds": round(t_rel, 3),
                                     "gap_seconds": round(secs, 2)})
        for which, b in self._bands.items():
            if b.device is None or which not in self._band_rec_paths:
                continue
            seen = self._seen_lost.get(which, 0)
            for _ in range(max(0, b.reconnections - seen)):
                self._essential("history", session_history.log_event, self._layout.root,
                                session_history.EVENT_HEADBAND_RECONNECTED,
                                {"boxer": which, "t_session_seconds": round(t, 3)})
            self._seen_lost[which] = b.reconnections
            snap = b.timing_snapshot()
            off = corr.get(timing.band_device(which))
            rows.append((t, timing.band_device(which), snap.rate if snap else 0.0,
                         snap.jitter_ms if snap else None, snap.dropped if snap else 0,
                         None if off is None else off * 1000,
                         "headband lost" if b.lost else ""))
        for r in rows:
            if self._essential("timing", w.append, *r) is None:
                break

    # ── timing check (the dialog on Get ready / Recording) ──
    def timing_rows(self) -> list:
        """Live numbers per device for the "Test timing" dialog:
        [{"device", "name", "rate", "delay_ms", "jitter_ms", "dropped",
          "after_ms", "kind"}]."""
        cal = (self._calibration or {}).get("devices") or {}
        out = []
        for s in self._cams:
            if s.device is None:
                continue
            snap = s.timing_snapshot()
            c = cal.get(timing.camera_device(s.stem)) or {}
            out.append({"device": s.stem, "name": s.title, "kind": "camera",
                        "rate": f"{snap.rate:.0f} fps" if snap else "—",
                        "delay_ms": c.get("delay_ms"),
                        "jitter_ms": snap.jitter_ms if snap else None,
                        "dropped": f"{snap.dropped_total if snap else 0} frames",
                        "after_ms": c.get("spread_ms")})
        for which, b in self._bands.items():
            if b.device is None:
                continue
            snap = b.timing_snapshot()
            c = cal.get(timing.band_device(which)) or {}
            code = self.boxer_name(which)
            out.append({"device": timing.band_device(which), "kind": "headband",
                        "name": f"Headband {which}" + (f" · {code}" if code else ""),
                        "rate": (f"{snap.rate:.0f} Hz · packet / {snap.packet_ms:.0f} ms"
                                 if snap and snap.packet_ms else "—"),
                        "delay_ms": c.get("delay_ms"),
                        "jitter_ms": snap.jitter_ms if snap else None,
                        "dropped": f"{snap.dropped_total if snap else 0} packets",
                        "after_ms": c.get("spread_ms")})
        return out

    @property
    def calibration(self):
        return self._calibration

    def run_buzzer_test(self, on_done=None, on_beep=None):
        """Get ready → Test timing → Run buzzer test."""
        from brain_battle.capture.buzzer_test import BuzzerTest
        if self._buzzer is not None and self._buzzer.running:
            return self._buzzer
        bt = BuzzerTest(self._cams, self._bands, self)

        def done(cal):
            self._calibration = cal if cal.get("devices") else None
            if on_done is not None:
                on_done(cal)
        bt.finished.connect(done)
        if on_beep is not None:
            bt.beeped.connect(on_beep)
        self._buzzer = bt
        bt.start()
        return bt

    def timing_line(self) -> str:
        """The Recording screen's one-line timing summary."""
        cal = self._calibration
        if not cal:
            return "Timing: not measured this session · details"
        worst = cal.get("largest_difference_ms")
        if worst is None:
            return "Timing: buzzer test incomplete · details"
        mark = "✓" if worst < timing.GOAL_MS else "!"
        return f"Timing: largest difference {worst:.0f} ms after correction {mark} · details"

    # ── Saved-screen data (from REAL files) ──
    def _build_saved_info(self):
        """What the Saved screen lists, worked out from the files on disk."""
        import brain_battle.data.session_storage as _ss
        root = self._saved_root
        man = _ss._read_manifest(root) or {}
        a, b = fighters.display(man, "A"), fighters.display(man, "B")
        ia, ib = fighters.info(man, "A"), fighters.info(man, "B")
        markers = read_phase_markers(root)
        rounds = self._cfg.num_rounds if self._cfg else 0
        end = markers[-1][0] if markers else 0.0
        stopped_early = bool(man.get("stopped_early"))
        when = platform_utils.format_date_time(_dt.datetime.now())
        how = (f"Stopped early at {_mmss(end)}" if stopped_early
               else f"{rounds} round{'s' if rounds != 1 else ''} · {max(1, round(end / 60))} min")
        meta = (ui_versus(a, ia["colour_hex"], b, ib["colour_hex"])
                + f" &nbsp;·&nbsp; {when} · {how}")
        items, problems, worth = [], [], []
        if self._write_problems:
            problems.append(f"Could not save {_join_words(self._write_problems)} — the recordings "
                            f"folder could not be written to at the time.")
        # cameras
        saved_cams, lost, bad = [], 0, []
        for slot in self._cams:
            path = self._cam_rec_paths.get(slot.index)
            if not path:
                continue
            if os.path.isfile(path) and not slot.rec_failed:
                saved_cams.append(slot.title)
            else:
                bad.append(slot.title)
            lost += slot.dropped
        if saved_cams or bad:
            ok = not bad
            detail = ", ".join(saved_cams) + "." if saved_cams else ""
            detail += f" {lost} frames lost." if lost else " No frames lost."
            if bad:
                detail += f" Incomplete: {', '.join(bad)}."
                problems.append(f"{_join_words(bad)} video is missing or incomplete.")
            items.append((ok, f"{len(saved_cams)} camera video{'s' if len(saved_cams) != 1 else ''}",
                          detail.strip()))
        # brain recordings
        got = [w for w in ("A", "B") if self._band_rec_paths.get(w)
               and os.path.isfile(self._band_rec_paths[w]) and os.path.getsize(self._band_rec_paths[w]) > 0]
        missing = [w for w in ("A", "B") if self._band_rec_paths.get(w) and w not in got]
        if got or missing:
            names = " and ".join(f"{fighters.display(man, w)}’s" for w in got)
            items.append((not missing, f"{len(got)} brain recording{'s' if len(got) != 1 else ''}",
                          (f"{names} headband{'s' if len(got) > 1 else ''}, for the whole session."
                           + (" Head movement too." if self._settings.record_motion else ""))
                          if got else "Not saved."))
            for w in missing:
                problems.append(f"Boxer {w}'s brain recording was not saved.")
        # automatic markers
        if "the round and break times" in self._write_problems:
            items.append((False, "Some automatic markers were not saved", "See the warning below."))
        else:
            items.append((True, f"{len(markers)} automatic markers",
                          f"Recording start ({LEAD_IN_DEFAULT} s before the baseline), baseline start "
                          f"and end, the start and end of every round and break, and recording end."))
        # moments
        n = len(self._moments); noted = sum(1 for m in self._moments if m.get("note"))
        items.append((True, f"{n} moment{'s' if n != 1 else ''} you marked",
                      (f"{noted} with a note" + (f", {n - noted} still to describe in Review."
                                                 if n - noted else ".")) if n else "None this time."))
        # timing
        cal = man.get("timing_calibration")
        if cal:
            worst = cal.get("largest_difference_ms")
            items.append((bool(cal.get("complete")) and worst is not None and worst < timing.GOAL_MS,
                          "Timing report",
                          "Buzzer-test corrections applied. " + timing.summary_text(cal)
                          + " Logged every second in timing_quality.csv."))
        else:
            items.append((False, "Timing not measured",
                          "No buzzer test this session, so device delays are not corrected. Frame "
                          "rates and dropped packets are still logged in timing_quality.csv."))
        # worth checking
        for p_ in problems:
            worth.append(p_)
        for p_ in contact_log.weak_periods(root, markers, min_sec=30):
            worth.append(contact_log.describe_weak(p_))
        for slot in self._cams:
            stalls = [e for e in slot.stall_events if e[1] == "recovered"]
            if stalls:
                worth.append(f"{slot.title} stopped sending pictures {len(stalls)} time"
                             f"{'s' if len(stalls) != 1 else ''} and was reconnected; the gap"
                             f"{'s are' if len(stalls) != 1 else ' is'} in its video.")
            if slot.fps_note:
                worth.append(slot.fps_note)
        for which, band in self._bands.items():
            if band.lost:
                worth.append(f"Boxer {which}'s headband disconnected during the recording.")
        for w_ in worth:
            items.append((False, "Worth checking", w_))
        size = human_bytes(folder_size_bytes(root))
        folder_line = (f"Saved in <b>{os.path.basename(os.path.dirname(root))} › "
                       f"{os.path.basename(root)}</b> · {size}")
        return {"meta": meta, "items": items, "worth_checking": worth, "checklist": items,
                "folder_line": folder_line, "problems": problems, "stopped_early": stopped_early}

    def saved_headline(self) -> str:
        """The Saved screen's headline for the last recording: plain "Recording
        saved", or "saved, but with problems" when something could not be
        written or a device failed."""
        info = self._build_saved_info() if self._saved_root else {"problems": []}
        return ("Recording saved, but with problems" if info.get("problems")
                else "Recording saved")

    def _leave_to_sessions(self):
        self._stop_devices()
        self._tick.stop()
        self.exit_flow.emit()

    def start_labelling(self):
        self._stop_devices()
        self._tick.stop()
        if self._saved_root:
            self.session_saved.emit(self._saved_root)

    def reveal_saved(self):
        if self._saved_root:
            try:
                platform_utils.reveal_in_file_manager(self._saved_root)
            except Exception as e:
                log.warning("showing the recording in the file manager failed: %s", e)

    # ── cancel / quit protection ──
    def is_recording(self):
        return self._controller.is_running

    def _anything_connected(self):
        return (any(s.device is not None for s in self._cams)
                or any(b.device is not None for b in self._bands.values()))

    def cancel(self):
        if self.is_recording():
            r = QMessageBox.question(
                self, "Stop the recording?",
                "A recording is in progress. Stop it and leave? Everything recorded "
                "so far is kept.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
            self._stopping_early = True
            self._controller.stop()         # finalises + saves what was recorded
            self._leave_to_sessions()
            return
        if self._anything_connected():
            r = QMessageBox.question(
                self, "Leave setup?",
                "Leave the recording setup? The cameras and headbands will disconnect.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No)
            if r != QMessageBox.StandardButton.Yes:
                return
        self._leave_to_sessions()

    # ── key filter for M = mark moment ──
    def _install_key_filter(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.instance().installEventFilter(self)

    def _remove_key_filter(self):
        from PyQt6.QtWidgets import QApplication
        app = QApplication.instance()
        if app:
            app.removeEventFilter(self)

    def eventFilter(self, obj, event):
        from PyQt6.QtCore import QEvent
        from PyQt6.QtWidgets import QLineEdit, QTextEdit
        if event.type() == QEvent.Type.KeyPress and self._controller.is_running:
            from PyQt6.QtWidgets import QApplication
            w = QApplication.focusWidget()
            if not isinstance(w, (QLineEdit, QTextEdit)) and event.key() == Qt.Key.Key_M:
                self.mark_moment(); return True
        return super().eventFilter(obj, event)

    # ── ui tick (live previews / traces) ──
    def _on_ui_tick(self):
        cur = self._stack.currentWidget()
        if hasattr(cur, "on_tick"):
            try:
                cur.on_tick()
            except Exception:
                pass

    # ── teardown ──
    def _stop_devices(self):
        for slot in self._cams:
            slot.shutdown()
        for b in self._bands.values():
            b.shutdown()
            b.publisher = None
        if self._lsl is not None:
            self._lsl.close()
            self._lsl = None

    def shutdown(self):
        try:
            if self._controller.is_running:
                self._stopping_early = True
                self._controller.stop()
        except Exception:
            pass
        self._contact_timer.stop()
        if self._contact_writer is not None:
            try: self._contact_writer.close()
            except Exception: pass
            self._contact_writer = None
        if self._timing_writer is not None:
            try: self._timing_writer.close()
            except Exception: pass
            self._timing_writer = None
        try: self._keep_awake.stop()
        except Exception: pass
        self._remove_key_filter()
        self._tick.stop()
        self._stop_devices()          # closes every camera file …
        self.wait_for_xdf()           # … so the pending recording.xdf can finish
        if not self._controller.is_running:
            detach_session_log()


__all__ = ["RecordFlow"]

