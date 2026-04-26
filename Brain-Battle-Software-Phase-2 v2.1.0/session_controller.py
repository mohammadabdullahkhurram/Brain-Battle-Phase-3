"""
session_controller.py
─────────────────────
State machine that drives the automated Brain Battle capture loop.

Flow:
    IDLE → BASELINE (60 s) → ROUND 1 → BREAK 1 → ROUND 2 → BREAK 2 → … → DONE

The controller is pure logic: it holds session config, advances phases on a
single QTimer tick (1 Hz), and emits signals that the UI (circular timer,
folder writer, EEG recorder, beeper) subscribes to. It does not touch the
filesystem or the audio system directly — that keeps it trivially testable.

Signals
    phase_changed(phase_id:str, phase_label:str, duration_sec:int, round_num:int)
        Fired on every transition. round_num is 0 for BASELINE and N for ROUND N / BREAK N.
    tick(remaining_sec:int, elapsed_sec:int, total_sec:int)
        Fired every second inside any active phase.
    beep_requested()
        Fired at BASELINE start and at every ROUND start (NOT on breaks).
    session_started()
        Once when the whole session begins.
    session_finished()
        Once when the last round's recording wraps up.
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

BASELINE_DURATION_SEC = 60  # Fixed one-minute baseline per spec.


class Phase(str, Enum):
    IDLE     = "idle"
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
    label:      str         # human-readable: "BASELINE", "ROUND 1", "BREAK 2"…
    duration:   int         # seconds
    round_num:  int         # 0 for baseline, N for round/break N


def _build_timeline(cfg: SessionConfig) -> list[PhaseInfo]:
    """Build the ordered list of phases for a session."""
    timeline: list[PhaseInfo] = []
    if cfg.baseline_sec > 0:
        timeline.append(PhaseInfo(Phase.BASELINE, "BASELINE", cfg.baseline_sec, 0))
    for n in range(1, cfg.num_rounds + 1):
        timeline.append(PhaseInfo(Phase.ROUND, f"ROUND {n}", cfg.round_sec, n))
        # No break after the final round — per typical boxing protocol.
        if n < cfg.num_rounds and cfg.break_sec > 0:
            timeline.append(PhaseInfo(Phase.BREAK, f"BREAK {n}", cfg.break_sec, n))
    return timeline


# ─── Controller ────────────────────────────────────────────────────────────────

class SessionController(QObject):
    """
    Drives the automated baseline + rounds + breaks loop.

    Public methods:
        configure(cfg)   — set (or reset) the session plan
        start()          — begin the session (emits session_started, phase_changed)
        stop()           — cancel the session mid-flight (emits session_finished)

    The controller stays idle until start() is called.
    """

    phase_changed    = pyqtSignal(str, str, int, int)   # phase_id, label, duration_sec, round_num
    tick             = pyqtSignal(int, int, int)         # remaining_sec, elapsed_sec, total_sec
    beep_requested   = pyqtSignal()
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
        self._advance()
        self._timer.start()

    def stop(self) -> None:
        if not self.is_running:
            return
        self._timer.stop()
        self._phase = Phase.DONE
        self._idx = -1
        self.session_finished.emit()

    # -- internals -------------------------------------------------------------

    def _advance(self) -> None:
        """Move to the next phase in the timeline, or finish."""
        self._idx += 1
        self._elapsed_in_phase = 0

        if self._idx >= len(self._timeline):
            self._timer.stop()
            self._phase = Phase.DONE
            self.session_finished.emit()
            return

        info = self._timeline[self._idx]
        self._phase = info.phase
        self.phase_changed.emit(
            info.phase.value, info.label, info.duration, info.round_num
        )
        # Beep on BASELINE start and on every ROUND start, not on breaks.
        if info.phase in (Phase.BASELINE, Phase.ROUND):
            self.beep_requested.emit()
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
    "BASELINE_DURATION_SEC",
]
