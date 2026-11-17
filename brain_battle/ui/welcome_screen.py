"""
welcome_screen.py
─────────────────
The first screen on a new computer (docs/DESIGN.md "First run", design F):
"Where are your team's recordings?"

  ( ) A shared team folder — a network drive, the lab server, or a synced
      OneDrive folder.  [path]  [Choose folder…]  ✓ Found 5 recordings and
      your team's settings in this folder
  ( ) Just this computer — Documents › Brain Battle Data. Nobody else sees
      these recordings. You can switch later.
  ▸ Can't see the shared drive?
  [Continue]   You only see this once on this computer.

Everyone who picks the same folder sees the same recordings, labels, history,
label list and fighter colours. Shown once (when the computer has no settings
file yet); Settings › Change picks another folder later.

Emits ``done(path)`` after saving the choice.
"""
from __future__ import annotations

import os

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QRadioButton, QFileDialog, QFrame,
    QButtonGroup, QPushButton,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.app_settings as app_settings
from brain_battle.support import platform_utils


def count_recordings(folder: str) -> int:
    try:
        return sum(1 for d in os.listdir(folder)
                   if os.path.isfile(os.path.join(folder, d, "session_manifest.json")))
    except OSError:
        return 0


class WelcomeScreen(QWidget):
    done = pyqtSignal(str)

    def __init__(self, capture_dir: str = "", parent=None):
        super().__init__(parent)
        self._shared = capture_dir if capture_dir and capture_dir != app_settings.default_capture_root() else ""
        self.setObjectName("welcomeScreen")
        self.setStyleSheet(f"#welcomeScreen {{ background: {dt.canvas_bg()}; }}")
        outer = QVBoxLayout(self); outer.setAlignment(Qt.AlignmentFlag.AlignCenter)
        card = ui.Card(radius=22); card.setMaximumWidth(600); card.setMinimumWidth(520)
        outer.addWidget(card, 0, Qt.AlignmentFlag.AlignCenter)
        v = QVBoxLayout(card); v.setContentsMargins(40, 38, 40, 34); v.setSpacing(18)
        icon = QLabel(); icon.setPixmap(dt.icon_pixmap("wave", color=dt.accent(), size=34))
        v.addWidget(icon)
        t = QLabel("Where are your team’s recordings?"); t.setWordWrap(True)
        t.setStyleSheet(dt.css_text(28, 700))
        v.addWidget(t)
        v.addWidget(ui.muted("Pick the shared folder your team uses. Everyone who picks the same "
                             "folder sees the same recordings, labels, history, label list and "
                             "fighter colours.", size=dt.SIZE_MD))
        self._group = QButtonGroup(self)
        # shared
        self._shared_box = QFrame(); self._shared_box.setObjectName("optShared")
        sv = QVBoxLayout(self._shared_box); sv.setContentsMargins(16, 14, 16, 14); sv.setSpacing(6)
        self.shared_radio = QRadioButton("A shared team folder")
        self.shared_radio.setStyleSheet(dt.css_text(dt.SIZE_MD, 700))
        self._group.addButton(self.shared_radio)
        sv.addWidget(self.shared_radio)
        sv.addWidget(ui.muted("A network drive, the lab server, or a synced OneDrive folder."))
        row = QHBoxLayout()
        self._path = QLabel(""); self._path.setObjectName("pathBox")
        self._path.setStyleSheet(f"#pathBox {{ border: 1px solid {dt.border()}; border-radius: 10px; "
                                 f"padding: 9px 12px; font-family: {dt.FONT_MONO}; font-size: 12px; }}")
        row.addWidget(self._path, 1)
        choose = ui.secondary_button("Choose folder…")
        choose.clicked.connect(self._choose)
        row.addWidget(choose)
        sv.addLayout(row)
        self._found = ui.body("", color=dt.success_color(), size=dt.SIZE_SM)
        sv.addWidget(self._found)
        v.addWidget(self._shared_box)
        # local
        self._local_box = QFrame(); self._local_box.setObjectName("optLocal")
        lv = QVBoxLayout(self._local_box); lv.setContentsMargins(16, 14, 16, 14); lv.setSpacing(4)
        self.local_radio = QRadioButton("Just this computer")
        self.local_radio.setStyleSheet(dt.css_text(dt.SIZE_MD, 700))
        self._group.addButton(self.local_radio)
        lv.addWidget(self.local_radio)
        lv.addWidget(ui.muted("Saves in Documents › Brain Battle Data. Nobody else sees these "
                              "recordings. You can switch later."))
        v.addWidget(self._local_box)
        self._hint_btn = QPushButton("Can’t see the shared drive?")
        self._hint_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._hint_btn.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; "
                                     f"text-align: left; color: {dt.accent()}; font-weight: 600; "
                                     f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")
        self._hint = ui.muted(platform_utils.connect_drive_hint())
        self._hint.setVisible(False)
        self._hint_btn.clicked.connect(lambda: self._hint.setVisible(not self._hint.isVisible()))
        v.addWidget(self._hint_btn); v.addWidget(self._hint)
        self.continue_btn = ui.primary_button("Continue")
        self.continue_btn.setMinimumHeight(52)
        self.continue_btn.clicked.connect(self.accept)
        v.addWidget(self.continue_btn)
        note = ui.muted("You only see this once on this computer. Change it any time in Settings.")
        note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        v.addWidget(note)
        self.shared_radio.toggled.connect(self._restyle)
        self.local_radio.toggled.connect(self._restyle)
        (self.shared_radio if self._shared else self.local_radio).setChecked(True)
        self.refresh()

    def set_capture_dir(self, path: str) -> None:
        if path and path != app_settings.default_capture_root():
            self._shared = path
        self.refresh()

    def set_shared_folder(self, path: str) -> None:
        self._shared = path
        self.shared_radio.setChecked(True)
        self.refresh()

    def _choose(self):
        path = QFileDialog.getExistingDirectory(self, "Choose your team's folder",
                                                self._shared or os.path.expanduser("~"))
        if path:
            self.set_shared_folder(path)

    def refresh(self):
        from brain_battle.ui.sessions_screen import folder_display
        self._path.setText(folder_display(self._shared) if self._shared else "No folder chosen yet")
        if self._shared:
            n = count_recordings(self._shared)
            has_team = any(os.path.isfile(os.path.join(self._shared, f))
                           for f in ("label_list.json", "fighter_colours.json"))
            self._found.setText(f"✓ Found {n} recording{'s' if n != 1 else ''}"
                                + (" and your team’s settings" if has_team else "")
                                + " in this folder")
        else:
            self._found.setText("")
        self._restyle()

    def _restyle(self, *_):
        for box, radio in ((self._shared_box, self.shared_radio), (self._local_box, self.local_radio)):
            on = radio.isChecked()
            box.setStyleSheet(f"#{box.objectName()} {{ border: {'2px' if on else '1px'} solid "
                              f"{dt.accent() if on else dt.border()}; border-radius: 16px; "
                              f"background: {'#F5F8FE' if on else dt.surface()}; }}")
        self.continue_btn.setEnabled(self.local_radio.isChecked() or bool(self._shared))

    def chosen_folder(self) -> str:
        return self._shared if self.shared_radio.isChecked() and self._shared \
            else app_settings.default_capture_root()

    def accept(self):
        path = self.chosen_folder()
        s = app_settings.load()
        s.capture_dir = path if self.shared_radio.isChecked() else None
        s.save()                                   # the settings file now exists
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        self.done.emit(path)


__all__ = ["WelcomeScreen", "count_recordings"]
