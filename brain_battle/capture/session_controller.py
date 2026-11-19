"""
session_controller.py
─────────────────────
State machine that drives the automated Brain Battle capture loop.

Flow:
    IDLE → LEAD-IN (15 s) → BASELINE → ROUND 1 → BREAK 1 → ROUND 2 → … → DONE

Recording starts with a fixed 15-second **lead-in** before the baseline, so
every device is running and settled before the first thing that matters
happens. The lead-in is not configurable (``LEAD_IN_SEC``).

The controller is pure logic: it holds session config, advances phases on a
single QTimer tick (1 Hz), and emits signals that the UI (timer, folder writer,
EEG recorder, round bell) subscribes to. It does not touch the filesystem or
the audio system directly — that keeps it trivially testable.

Signals
    phase_changed(phase_id:str, phase_label:str, duration_sec:int, round_num:int)
        Fired on every transition. round_num is 0 for LEAD-IN / BASELINE and N
        for ROUND N / BREAK N.
    marker(text:str, phase_id:str, round_num:int)
        One **system marker** — the automatic markers written to
        phase_markers.csv and the XDF Markers stream, in order:
        RECORDING_START, BASELINE_START, BASELINE_END, ROUND_1_START,
        ROUND_1_END, BREAK_1_START, BREAK_1_END, …, ROUND_N_END,
        RECORDING_END. A phase that is cut short by Stop gets its _END marker
        at the stop time (then RECORDING_END); RECORDING_END is always the
        last marker.
    tick(remaining_sec:int, elapsed_sec:int, total_sec:int)
        Fired every second inside any active phase.
    bell_requested()
        Fired at the START and at the END of every round (including the last).
        Never for the lead-in, the baseline or a break.
    session_started()
        Once when the whole session begins (before RECORDING_START).
    session_finished()
        Once when the recording wraps up (after RECORDING_END).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

try:
    # Prefer Qt signals when available so the UI can subscribe directly.
    from PyQt6.QtCore import QObject, QTimer, pyqtSignal
    _QT_AVAILABLE = True
except ImportError:  # pragma: no cover - fallback for headless unit tests
    _QT_AVAILABLE = False

    class QObject:  # type: ignore
        pass

    class QTimer:  # type: ignore
        def __init__(self, *a, **k): pass
        def setInterval(self, *a, **k): pass
        def start(self, *a, **k): pass
        def stop(self): pass
        @property
        def timeout(self):
            class _S:
                def connect(self, *a, **k): pass
            return _S()

    def pyqtSignal(*a, **k):  # type: ignore
        class _Sig:
            def __init__(self): self._slots = []
            def connect(self, fn): self._slots.append(fn)
            def emit(self, *args):
                for fn in list(self._slots):
                    fn(*args)
        return _Sig()


# ─── Configuration ─────────────────────────────────────────────────────────────

BASELINE_DURATION_SEC = 60  # Default one-minute baseline.
LEAD_IN_DEFAULT = 15        # Recording starts this long before the baseline.
# Read at the moment a timeline is built. Only the test suite changes it
# (tests/_env.py shortens it so simulated recordings stay quick).
LEAD_IN_SEC = LEAD_IN_DEFAULT

MARKER_RECORDING_START = "RECORDING_START"
MARKER_RECORDING_END = "RECORDING_END"
# Recordings made before the lead-in existed ended with this marker instead of
# RECORDING_END; it is still read everywhere.
MARKER_SESSION_END_LEGACY = "SESSION_END"
END_MARKERS = (MARKER_RECORDING_END, MARKER_SESSION_END_LEGACY)


class Phase(str, Enum):
    IDLE     = "idle"
    LEAD_IN  = "lead_in"
    BASELINE = "baseline"
    ROUND    = "round"
    BREAK    = "break"
    DONE     = "done"


@dataclass
class SessionConfig:
    num_rounds:     int = 3          # how many rounds in the session
    round_sec:      int = 180        # 3 min default
    break_sec:      int = 60         # 1 min default
    baseline_sec:   int = BASELINE_DURATION_SEC

    def validate(self) -> Optional[str]:
        if self.num_rounds < 1:
            return "Number of rounds must be at least 1."
        if self.round_sec < 5:
            return "Round duration must be at least 5 seconds."
        if self.break_sec < 0:
            return "Break duration cannot be negative."
        if self.baseline_sec < 0:
            return "Baseline duration cannot be negative."
        return None


@dataclass
class PhaseInfo:
    phase:      Phase
    label:      str         # human-readable: "LEAD-IN", "BASELINE", "ROUND 1", "BREAK 2"…
    duration:   int         # seconds
    round_num:  int         # 0 for lead-in / baseline, N for round/break N

    @property
    def token(self) -> str:
        """Marker stem: 'BASELINE', 'ROUND_1', 'BREAK_2' ('' for the lead-in,
        which has no START/END markers of its own — RECORDING_START is its
        start)."""
        if self.phase == Phase.LEAD_IN:
            return ""
        return self.label.upper().replace(" ", "_")


def _build_timeline(cfg: SessionConfig, lead_in: bool = True) -> list[PhaseInfo]:
    """The ordered phases of a session: the 15 s lead-in, the baseline (if
    any), then rounds with breaks between them (none after the last round)."""
    timeline: list[PhaseInfo] = []
    lead = globals()["LEAD_IN_SEC"]
    if lead_in and lead > 0:
        timeline.append(PhaseInfo(Phase.LEAD_IN, "LEAD-IN", lead, 0))
    if cfg.baseline_sec > 0:
        timeline.append(PhaseInfo(Phase.BASELINE, "BASELINE", cfg.baseline_sec, 0))
    for n in range(1, cfg.num_rounds + 1):
        timeline.append(PhaseInfo(Phase.ROUND, f"ROUND {n}", cfg.round_sec, n))
        # No break after the final round — per typical boxing protocol.
        if n < cfg.num_rounds and cfg.break_sec > 0:
            timeline.append(PhaseInfo(Phase.BREAK, f"BREAK {n}", cfg.break_sec, n))
    return timeline


def planned_markers(cfg: SessionConfig) -> list[tuple]:
    """Every system marker a full recording of this plan writes, as
    ``(t_session_seconds, text, phase_id, round_num)`` — the same sequence the
    controller emits live (used by the demo generator and the tests)."""
    out = [(0.0, MARKER_RECORDING_START, Phase.LEAD_IN.value, 0)]
    t = 0.0
    for info in _build_timeline(cfg):
        if info.token:
            out.append((t, f"{info.token}_START", info.phase.value, info.round_num))
        t += info.duration
        if info.token:
            out.append((t, f"{info.token}_END", info.phase.value, info.round_num))
    out.append((t, MARKER_RECORDING_END, Phase.DONE.value, 0))
    return out


def total_seconds(cfg: SessionConfig, lead_in: bool = True) -> int:
    """Planned length of a recording, lead-in included."""
    return sum(p.duration for p in _build_timeline(cfg, lead_in=lead_in))


# ─── Controller ────────────────────────────────────────────────────────────────

class SessionController(QObject):
    """
    Drives the automated lead-in + baseline + rounds + breaks loop.

    Public methods:
        configure(cfg)   — set (or reset) the session plan
        start()          — begin the session
        stop()           — cancel the session mid-flight (emits RECORDING_END
                           and session_finished)

    The controller stays idle until start() is called.
    """

    phase_changed    = pyqtSignal(str, str, int, int)   # phase_id, label, duration_sec, round_num
    marker           = pyqtSignal(str, str, int)         # text, phase_id, round_num
    tick             = pyqtSignal(int, int, int)         # remaining_sec, elapsed_sec, total_sec
    bell_requested   = pyqtSignal()
    session_started  = pyqtSignal()
    session_finished = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent) if _QT_AVAILABLE else super().__init__()
        self._cfg: SessionConfig = SessionConfig()
        self._timeline: list[PhaseInfo] = []
        self._idx: int = -1
        self._elapsed_in_phase: int = 0
        self._phase: Phase = Phase.IDLE

        if _QT_AVAILABLE:
            self._timer = QTimer(self)
            self._timer.setInterval(1000)
            self._timer.timeout.connect(self._on_tick)
        else:
            self._timer = QTimer()

    # -- config & introspection ------------------------------------------------

    def configure(self, cfg: SessionConfig) -> None:
        err = cfg.validate()
        if err:
            raise ValueError(err)
        self._cfg = cfg

    @property
    def config(self) -> SessionConfig:
        return self._cfg

    @property
    def phase(self) -> Phase:
        return self._phase

    @property
    def current_phase_info(self) -> Optional[PhaseInfo]:
        if 0 <= self._idx < len(self._timeline):
            return self._timeline[self._idx]
        return None

    @property
    def is_running(self) -> bool:
        return self._phase not in (Phase.IDLE, Phase.DONE)

    def phase_plan(self) -> list[PhaseInfo]:
        """Preview the full ordered phase list — useful for UI displays."""
        return list(self._timeline)

    # -- control ---------------------------------------------------------------

    def start(self) -> None:
        if self.is_running:
            return
        self._timeline = _build_timeline(self._cfg)
        if not self._timeline:
            return
        self._idx = -1
        self.session_started.emit()
        self.marker.emit(MARKER_RECORDING_START, Phase.LEAD_IN.value, 0)
        self._advance()
        self._timer.start()

    def stop(self) -> None:
        """Stop early. The phase in progress gets its _END marker at the stop
        time (no bell), then RECORDING_END is written."""
        if not self.is_running:
            return
        self._timer.stop()
        info = self.current_phase_info
        if info is not None and info.token:
            self.marker.emit(f"{info.token}_END", info.phase.value, info.round_num)
        self._phase = Phase.DONE
        self._idx = -1
        self.marker.emit(MARKER_RECORDING_END, Phase.DONE.value, 0)
        self.session_finished.emit()

    # -- internals -------------------------------------------------------------

    def _close_current(self) -> None:
        """The current phase ran its full length: its _END marker (and the bell
        if it was a round)."""
        info = self.current_phase_info
        if info is None:
            return
        if info.token:
            self.marker.emit(f"{info.token}_END", info.phase.value, info.round_num)
        if info.phase == Phase.ROUND:
            self.bell_requested.emit()

    def _advance(self) -> None:
        """Move to the next phase in the timeline, or finish."""
        self._close_current()
        self._idx += 1
        self._elapsed_in_phase = 0

        if self._idx >= len(self._timeline):
            self._timer.stop()
            self._phase = Phase.DONE
            self.marker.emit(MARKER_RECORDING_END, Phase.DONE.value, 0)
            self.session_finished.emit()
            return

        info = self._timeline[self._idx]
        self._phase = info.phase
        if info.token:
            self.marker.emit(f"{info.token}_START", info.phase.value, info.round_num)
        self.phase_changed.emit(
            info.phase.value, info.label, info.duration, info.round_num
        )
        if info.phase == Phase.ROUND:
            self.bell_requested.emit()
        # Emit the initial tick so UI shows the full duration immediately.
        self.tick.emit(info.duration, 0, info.duration)

    def _on_tick(self) -> None:
        info = self.current_phase_info
        if info is None:
            return
        self._elapsed_in_phase += 1
        remaining = max(0, info.duration - self._elapsed_in_phase)
        self.tick.emit(remaining, self._elapsed_in_phase, info.duration)
        if self._elapsed_in_phase >= info.duration:
            self._advance()


__all__ = [
    "Phase", "SessionConfig", "PhaseInfo", "SessionController",
    "BASELINE_DURATION_SEC", "LEAD_IN_SEC", "LEAD_IN_DEFAULT", "planned_markers", "total_seconds",
    "MARKER_RECORDING_START", "MARKER_RECORDING_END", "MARKER_SESSION_END_LEGACY",
    "END_MARKERS",
]
