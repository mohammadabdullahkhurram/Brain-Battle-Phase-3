"""
session_setup_dialog.py
───────────────────────
Modal dialog shown when the user clicks START SESSION on the Live Monitor.

Prompts for:
    • Number of rounds        (1 – 20)
    • Round duration (min)    (0.1 – 30.0, in 0.5 min steps)
    • Break duration (sec)    (0 – 600)
    • Baseline duration (sec) (0 – 600)  — optional, defaults to 60

Returns a SessionConfig on accept() / None on reject().

Visual language matches the rest of the Brain Battle app: dark panel, yellow
accent (#637DFF), Courier-New monospace, 1 px hairline borders.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout,
    QLabel, QSpinBox, QDoubleSpinBox, QPushButton,
    QFrame, QWidget,
)

from session_controller import SessionConfig, BASELINE_DURATION_SEC
from session_storage import estimate_session_bytes, human_bytes as _human_bytes


_STYLE = """
QDialog { background-color: #0E0E12; }
QLabel  { color: #ECEDEE; font-family: 'Inter', monospace; font-size: 11px; letter-spacing: 1px; }
QLabel#title    { color: #637DFF; font-size: 14px; letter-spacing: 3px; }
QLabel#subtitle { color: #A1A1AA; font-size: 10px; letter-spacing: 2px; }
QLabel#field_label { color: #A1A1AA; font-size: 10px; letter-spacing: 2px; }
QLabel#unit { color: #A1A1AA; font-size: 10px; letter-spacing: 1px; padding-left: 4px; }

QSpinBox, QDoubleSpinBox {
    background-color: #0E0E12; border: 1px solid #2A2A30; color: #637DFF;
    font-family: 'Inter', monospace; font-size: 16px; letter-spacing: 2px;
    padding: 8px 10px; min-width: 90px;
}
QSpinBox:focus, QDoubleSpinBox:focus { border-color: #637DFF; }
QSpinBox::up-button, QSpinBox::down-button,
QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {
    background-color: #1C1C21; border: 1px solid #2A2A30; width: 16px;
}
QSpinBox::up-button:hover, QSpinBox::down-button:hover,
QDoubleSpinBox::up-button:hover, QDoubleSpinBox::down-button:hover {
    background-color: #24242A;
}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {
    width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-bottom: 5px solid #A1A1AA;
}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {
    width: 0; height: 0;
    border-left: 4px solid transparent; border-right: 4px solid transparent;
    border-top: 5px solid #A1A1AA;
}

QPushButton {
    background-color: #1C1C21; color: #ECEDEE; border: 1px solid #2A2A30;
    padding: 10px 28px; font-family: 'Inter', monospace;
    font-size: 11px; letter-spacing: 2px;
}
QPushButton:hover { background-color: #24242A; color: #ffffff; border-color: #A1A1AA; }
QPushButton#primary {
    background-color: #1F2538; color: #637DFF; border: 1px solid #637DFF;
}
QPushButton#primary:hover { background-color: #2a3010; }

QFrame#divider { background-color: #2A2A30; max-height: 1px; border: none; }
QFrame#card {
    background-color: #1C1C21; border: 1px solid #2A2A30;
}
"""


class SessionSetupDialog(QDialog):
    """Modal configuration dialog for a new capture session."""

    def __init__(self, parent=None, initial: SessionConfig | None = None,
                 n_cameras: int = 0, n_eeg: int = 0):
        super().__init__(parent)
        self.setWindowTitle("Brain Battle — Session Setup")
        self.setModal(True)
        self.setMinimumWidth(480)
        self.setStyleSheet(_STYLE)

        # Source counts drive the live disk-space estimate below the summary.
        self._n_cameras = n_cameras
        self._n_eeg = n_eeg

        cfg = initial or SessionConfig()
        self._build(cfg)

    # ---------------------------------------------------------------- UI build
    def _build(self, cfg: SessionConfig) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 20)
        root.setSpacing(16)

        # Header
        hdr = QVBoxLayout(); hdr.setSpacing(4)
        title = QLabel("CONFIGURE SESSION"); title.setObjectName("title")
        subtitle = QLabel(
            "Baseline → Rounds → Breaks will run automatically once started."
        )
        subtitle.setObjectName("subtitle")
        hdr.addWidget(title); hdr.addWidget(subtitle)
        root.addLayout(hdr)

        divider = QFrame(); divider.setObjectName("divider"); divider.setFixedHeight(1)
        root.addWidget(divider)

        # Config card
        card = QFrame(); card.setObjectName("card")
        card_l = QVBoxLayout(card)
        card_l.setContentsMargins(20, 18, 20, 18); card_l.setSpacing(14)

        grid = QGridLayout()
        grid.setHorizontalSpacing(18); grid.setVerticalSpacing(14)

        # Number of rounds
        self.spin_rounds = QSpinBox()
        self.spin_rounds.setRange(1, 20)
        self.spin_rounds.setValue(cfg.num_rounds)
        self.spin_rounds.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self._field_label("NUMBER OF ROUNDS"),     0, 0)
        grid.addWidget(self.spin_rounds,                          0, 1)
        grid.addWidget(self._unit_label("rounds"),                0, 2)

        # Round duration (minutes, decimal)
        self.spin_round = QDoubleSpinBox()
        self.spin_round.setRange(0.1, 30.0)
        self.spin_round.setDecimals(1)
        self.spin_round.setSingleStep(0.5)
        self.spin_round.setValue(cfg.round_sec / 60.0)
        self.spin_round.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self._field_label("ROUND DURATION"),       1, 0)
        grid.addWidget(self.spin_round,                           1, 1)
        grid.addWidget(self._unit_label("minutes"),               1, 2)

        # Break duration (seconds)
        self.spin_break = QSpinBox()
        self.spin_break.setRange(0, 600)
        self.spin_break.setValue(cfg.break_sec)
        self.spin_break.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self._field_label("BREAK DURATION"),       2, 0)
        grid.addWidget(self.spin_break,                           2, 1)
        grid.addWidget(self._unit_label("seconds"),               2, 2)

        # Baseline duration (seconds)
        self.spin_baseline = QSpinBox()
        self.spin_baseline.setRange(0, 600)
        self.spin_baseline.setValue(cfg.baseline_sec)
        self.spin_baseline.setAlignment(Qt.AlignmentFlag.AlignCenter)
        grid.addWidget(self._field_label("BASELINE"),             3, 0)
        grid.addWidget(self.spin_baseline,                        3, 1)
        grid.addWidget(self._unit_label("seconds  (0 = skip)"),   3, 2)

        card_l.addLayout(grid)

        # Summary label — updates live
        self.summary = QLabel("")
        self.summary.setStyleSheet(
            "color:#A1A1AA; font-size:10px; letter-spacing:1px; "
            "padding-top:6px; border-top:1px solid #2A2A30;"
        )
        card_l.addWidget(self.summary)

        # Estimated disk usage — updates live with the duration/source counts.
        self.estimate = QLabel("")
        self.estimate.setStyleSheet(
            "color:#A1A1AA; font-size:10px; letter-spacing:1px;"
        )
        card_l.addWidget(self.estimate)
        root.addWidget(card)

        # Wire live-summary updates
        for w in (self.spin_rounds, self.spin_round, self.spin_break, self.spin_baseline):
            w.valueChanged.connect(self._refresh_summary)
        self._refresh_summary()

        # Buttons row
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_cancel = QPushButton("CANCEL")
        self.btn_start  = QPushButton("BEGIN SESSION"); self.btn_start.setObjectName("primary")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_start.clicked.connect(self.accept)
        btn_row.addWidget(self.btn_cancel)
        btn_row.addSpacing(8)
        btn_row.addWidget(self.btn_start)
        root.addLayout(btn_row)

    def _field_label(self, txt: str) -> QLabel:
        lbl = QLabel(txt); lbl.setObjectName("field_label")
        lbl.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    def _unit_label(self, txt: str) -> QLabel:
        lbl = QLabel(txt); lbl.setObjectName("unit")
        lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        return lbl

    # ------------------------------------------------------------- live stats
    def _refresh_summary(self) -> None:
        cfg = self.value()
        total = cfg.baseline_sec + cfg.num_rounds * cfg.round_sec \
                + max(0, cfg.num_rounds - 1) * cfg.break_sec
        mm, ss = divmod(total, 60)
        hh, mm = divmod(mm, 60)
        if hh:
            dur_txt = f"{hh:02d}:{mm:02d}:{ss:02d}"
        else:
            dur_txt = f"{mm:02d}:{ss:02d}"
        self.summary.setText(
            f"TOTAL RUNTIME  ≈  {dur_txt}   "
            f"({cfg.num_rounds} rounds"
            f"{', ' + str(cfg.num_rounds - 1) + ' breaks' if cfg.num_rounds > 1 else ''}"
            f"{', baseline ' + str(cfg.baseline_sec) + 's' if cfg.baseline_sec else ''})"
        )

        n_src = self._n_cameras + self._n_eeg
        if n_src > 0:
            est = estimate_session_bytes(cfg, self._n_cameras, self._n_eeg)
            self.estimate.setText(
                f"ESTIMATED SIZE  ≈  {_human_bytes(est)}   "
                f"({self._n_cameras} camera(s), {self._n_eeg} headband(s))"
            )
        else:
            self.estimate.setText(
                "ESTIMATED SIZE  —  connect sources to estimate"
            )

    # ------------------------------------------------------------------- API
    def value(self) -> SessionConfig:
        return SessionConfig(
            num_rounds   = int(self.spin_rounds.value()),
            round_sec    = int(round(self.spin_round.value() * 60)),
            break_sec    = int(self.spin_break.value()),
            baseline_sec = int(self.spin_baseline.value()),
        )


__all__ = ["SessionSetupDialog"]
