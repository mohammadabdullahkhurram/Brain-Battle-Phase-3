"""
settings_screen.py
────────────────
The real Settings screen (DESIGN §7) — everything that used to be hard-coded
in source now lives here, editing the `app_settings` store. Every control
autosaves immediately (no Save button) and shows a brief, plain "Saved"
confirmation; changes apply to the NEXT recording (the guided flow reloads
`app_settings.load()` each time it starts).

A list of the sections on the left jumps to each one; the sections, top to
bottom: **You** (your name — whose labels these are), recordings folder
(name/path/free space, change / show in Finder or Explorer, whether it is a
shared drive, the BRAIN_BATTLE_CAPTURE_DIR override note), headbands (known-
device dropdowns + Bluetooth search, same-headband guard), video quality (radio
rows with a GB/hour figure computed from the bitrate, never hard-coded),
session defaults (steppers + round bell + Test sound), extra signals and
sharing, **boxer names and privacy** (hide names in exports / on screen), the
shared label list (add/rename/remove/reorder/recolour), and the footer.

Public surface used by the app shell:
  • SettingsScreen(parent)
  • .capture_dir_changed (signal, str) — [Change folder…] picked a new
    recordings folder; the shell re-points SidebarShell + SessionsScreen at it.
  • .labeller_changed (signal, str) — "Your name" was edited.
  • .names_hidden_changed (signal, bool) — "Hide boxer names on screen".
  • .focus_labeller_name() — scroll to and focus the "Your name" field.
"""
from __future__ import annotations

import os
import shutil

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QSizePolicy, QLineEdit, QCheckBox, QScrollArea, QComboBox,
    QButtonGroup, QRadioButton, QColorDialog, QFileDialog, QMessageBox,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.app_settings as app_settings
import brain_battle.data.safe_io as safe_io
from brain_battle.support import platform_utils
import brain_battle.data.label_vocab as label_vocab
from brain_battle.ui.record_flow import Stepper, _mmss
from brain_battle.version import __version__

VIDEO_QUALITY_ORDER = ["standard", "high", "light"]


# ── small style helpers (mirrors record_flow.py's) ─────────────────────────

def _title(text, size=dt.SIZE_DISPLAY):
    l = QLabel(text)
    l.setStyleSheet(dt.css_text(size, 700))
    return l


def _sub(text):
    l = QLabel(text); l.setWordWrap(True)
    l.setStyleSheet(dt.css_text(dt.SIZE_MD, 400, dt.text_tertiary()))
    return l


def _body(text="", color=None, size=None, bold=False):
    """Body text; ``bold`` at the default heading size is a card heading."""
    l = QLabel(text); l.setWordWrap(True)
    heading = bold and size == dt.SIZE_MD and color is None
    l.setStyleSheet(dt.css_text(
        dt.SIZE_LG if heading else (size or dt.SIZE_SM), 700 if bold else 400,
        color or (dt.text_primary() if bold else dt.text_secondary())))
    return l


_card_n = 0


def _card() -> QFrame:
    global _card_n
    _card_n += 1
    c = QFrame(); c.setObjectName(f"settingsCard{_card_n}")
    c.setStyleSheet(dt.qss_card(c.objectName()))
    return c


def _human_gb(n_bytes: float) -> str:
    gb = n_bytes / (1024 ** 3)
    return f"{gb:.0f} GB" if gb >= 100 else f"{gb:.1f} GB"


# ── one row of the label-list editor ────────────────────────────────────────

class _LabelRow(QFrame):
    """key badge · colour swatch · name (editable) · kind dropdown · ↑ ↓ · ✕"""
    changed = pyqtSignal()
    move_up = pyqtSignal()
    move_down = pyqtSignal()
    removed = pyqtSignal()

    def __init__(self, entry: dict, parent=None):
        super().__init__(parent)
        self.entry = dict(entry)
        h = QHBoxLayout(self); h.setContentsMargins(4, 4, 4, 4); h.setSpacing(8)

        self._key = QLabel(str(entry.get("key", "")))
        self._key.setFixedSize(26, 26)
        self._key.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._key.setStyleSheet(
            f"background: {dt.canvas_bg()}; color: {dt.text_secondary()}; "
            f"border: 1px solid {dt.border_strong()}; border-radius: 13px; "
            f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; font-weight: 600;")
        h.addWidget(self._key)

        self._swatch = QPushButton()
        self._swatch.setFixedSize(26, 26)
        self._swatch.setCursor(Qt.CursorShape.PointingHandCursor)
        self._apply_swatch_color()
        self._swatch.clicked.connect(self._pick_color)
        h.addWidget(self._swatch)

        self._name = QLineEdit(entry.get("name", ""))
        self._name.setStyleSheet(dt.qss_input())
        self._name.setMinimumHeight(34)
        self._name.editingFinished.connect(self._on_name_changed)
        h.addWidget(self._name, 1)

        self._kind = QComboBox()
        self._kind.setStyleSheet(dt.qss_combo())
        self._kind.addItem(label_vocab.KIND_LABEL[label_vocab.POINT], label_vocab.POINT)
        self._kind.addItem(label_vocab.KIND_LABEL[label_vocab.DURATION], label_vocab.DURATION)
        self._kind.setCurrentIndex(1 if entry.get("kind") == label_vocab.DURATION else 0)
        self._kind.currentIndexChanged.connect(self._on_kind_changed)
        h.addWidget(self._kind)

        up = QPushButton("↑"); up.setFixedSize(30, 30)
        up.setToolTip("Move up"); up.setAccessibleName("Move this label up")
        up.setCursor(Qt.CursorShape.PointingHandCursor)
        up.setStyleSheet(dt.qss_secondary_button() + " QPushButton { padding: 0; }")
        up.clicked.connect(self.move_up.emit)
        h.addWidget(up)
        down = QPushButton("↓"); down.setFixedSize(30, 30)
        down.setToolTip("Move down"); down.setAccessibleName("Move this label down")
        down.setCursor(Qt.CursorShape.PointingHandCursor)
        down.setStyleSheet(dt.qss_secondary_button() + " QPushButton { padding: 0; }")
        down.clicked.connect(self.move_down.emit)
        h.addWidget(down)

        rm = QPushButton("✕"); rm.setFixedSize(30, 30)
        rm.setToolTip("Remove from the list (labels already placed are kept)")
        rm.setAccessibleName("Remove this label")
        rm.setCursor(Qt.CursorShape.PointingHandCursor)
        rm.setStyleSheet(dt.qss_secondary_button() + " QPushButton { padding: 0; }")
        rm.clicked.connect(self.removed.emit)
        h.addWidget(rm)

    def set_key(self, key: int):
        self.entry["key"] = key
        self._key.setText(str(key))

    def _apply_swatch_color(self):
        c = self.entry.get("color", "#3D4FD6")
        self._swatch.setStyleSheet(
            f"QPushButton {{ background: {c}; border: 1px solid {dt.border_strong()}; "
            f"border-radius: 6px; }}")

    def _pick_color(self):
        from PyQt6.QtGui import QColor
        start = QColor(self.entry.get("color", "#3D4FD6"))
        col = QColorDialog.getColor(start, self, "Choose a colour")
        if col.isValid():
            self.entry["color"] = col.name()
            self._apply_swatch_color()
            self.changed.emit()

    def _on_name_changed(self):
        name = self._name.text().strip() or self.entry.get("name", "Label")
        self._name.setText(name)
        if name != self.entry.get("name"):
            self.entry["name"] = name
            self.changed.emit()

    def _on_kind_changed(self, _idx):
        kind = self._kind.currentData()
        if kind != self.entry.get("kind"):
            self.entry["kind"] = kind
            self.changed.emit()


# ── the screen ──────────────────────────────────────────────────────────────

class SettingsScreen(QWidget):
    capture_dir_changed  = pyqtSignal(str)   # [Change folder…] picked a new folder
    labeller_changed     = pyqtSignal(str)   # "Your name" was edited
    names_hidden_changed = pyqtSignal(bool)  # "Hide boxer names on screen"
    help_requested       = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._settings = app_settings.load()
        self._labels: list[dict] = []
        self._scan_thread = None
        self.setObjectName("settingsScreen")
        self.setStyleSheet(f"#settingsScreen {{ background: {dt.canvas_bg()}; }}")
        self._build()
        self._refresh_all()

    # ── construction ──
    def _build(self):
        root = QVBoxLayout(self); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)

        header = QHBoxLayout(); header.setContentsMargins(36, 28, 36, 12); header.setSpacing(10)
        htext = QVBoxLayout(); htext.setSpacing(2)
        htext.addWidget(_title("Settings"))
        htext.addWidget(_sub("These settings apply to your next recording. "
                             "Changes save automatically."))
        header.addLayout(htext); header.addStretch()
        self._saved = _body("", color=dt.success_color(), size=dt.SIZE_MD, bold=True)
        header.addWidget(self._saved, 0, Qt.AlignmentFlag.AlignVCenter)
        help_btn = ui.help_button()
        help_btn.clicked.connect(self.help_requested.emit)
        header.addWidget(help_btn, 0, Qt.AlignmentFlag.AlignTop)
        root.addLayout(header)

        body = QHBoxLayout(); body.setContentsMargins(36, 0, 0, 0); body.setSpacing(20)
        # Left: the sections, one click away (the page is long)
        nav = QVBoxLayout(); nav.setContentsMargins(0, 4, 0, 0); nav.setSpacing(2)
        self._nav_buttons = []
        self._sections = []
        navw = QWidget(); navw.setLayout(nav); navw.setFixedWidth(190)
        body.addWidget(navw)

        self._scroll = scroll = QScrollArea(); scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(); host.setObjectName("settingsHost")
        host.setStyleSheet(f"#settingsHost {{ background: {dt.canvas_bg()}; }}")
        v = QVBoxLayout(host); v.setContentsMargins(0, 4, 36, 28); v.setSpacing(14)
        for name, build in (("You", self._build_you_card),
                            ("Recordings folder", self._build_folder_card),
                            ("Headbands", self._build_headbands_card),
                            ("Video quality", self._build_quality_card),
                            ("Session defaults", self._build_defaults_card),
                            ("Extra signals", self._build_signals_card),
                            ("Names and privacy", self._build_privacy_card),
                            ("Label list", self._build_labels_card)):
            card = build()
            v.addWidget(card)
            self._sections.append((name, card))
            b = ui.ghost_button(name, compact=True)
            b.setMinimumHeight(36)
            b.setStyleSheet(dt.qss_ghost_button(True) + " QPushButton { text-align: left; }")
            b.clicked.connect(lambda _c=False, c=card: self._scroll.ensureWidgetVisible(c, 0, 12))
            nav.addWidget(b); self._nav_buttons.append(b)
        nav.addStretch()
        v.addWidget(_body(f"Brain Battle v{__version__} · Constructor University",
                          color=dt.text_tertiary(), size=dt.SIZE_XS))
        scroll.setWidget(host)
        body.addWidget(scroll, 1)
        root.addLayout(body, 1)

    # ── "Saved" toast ──
    def _flash_saved(self):
        self._saved.setText("✓ Saved")
        QTimer.singleShot(1500, lambda: self._saved.setText(""))

    def refresh(self):
        """Reload from disk — call whenever the Settings page becomes active,
        so headbands/labels discovered elsewhere (a recording's BLE scan, a
        change on another machine sharing this recordings folder) show up."""
        self._settings = app_settings.load()
        self._refresh_all()

    def _refresh_all(self):
        self._refresh_you()
        self._refresh_folder_card()
        self._refresh_headband_combos()
        self._refresh_quality()
        self._refresh_defaults()
        self._refresh_signals()
        self._refresh_privacy()
        self._load_labels()

    # ── 0. You ──
    def _build_you_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(8)
        v.addWidget(_body("You", bold=True, size=dt.SIZE_MD))
        v.addWidget(_body("Your name is saved with every label you place, and your labels are "
                          "kept in your own file inside each recording — so two people can "
                          "label the same recording without overwriting each other. Use the "
                          "same name on every computer you label on.",
                          color=dt.text_tertiary(), size=dt.SIZE_SM))
        row = QHBoxLayout(); row.setSpacing(10)
        lab = _body("Your name", size=dt.SIZE_MD); lab.setWordWrap(False)
        row.addWidget(lab)
        self._labeller_edit = QLineEdit(); self._labeller_edit.setMinimumHeight(40)
        self._labeller_edit.setMaximumWidth(320)
        self._labeller_edit.setPlaceholderText("for example: Maya K")
        self._labeller_edit.setStyleSheet(dt.qss_input())
        self._labeller_edit.editingFinished.connect(self._on_labeller_edited)
        row.addWidget(self._labeller_edit, 1); row.addStretch(1)
        v.addLayout(row)
        return card

    def _refresh_you(self):
        if not self._labeller_edit.hasFocus():
            self._labeller_edit.setText(self._settings.labeller_name)

    def _on_labeller_edited(self):
        name = " ".join(self._labeller_edit.text().split())[:60]
        if name == self._settings.labeller_name:
            return
        if not name:                       # a name is never cleared back to "nobody"
            self._labeller_edit.setText(self._settings.labeller_name); return
        self._settings.labeller_name = name
        self._settings.save()
        self._labeller_edit.setText(name)
        self._flash_saved()
        self.labeller_changed.emit(name)

    def focus_labeller_name(self):
        self._scroll.ensureWidgetVisible(self._labeller_edit)
        self._labeller_edit.setFocus(); self._labeller_edit.selectAll()

    # ── names and privacy ──
    def _build_privacy_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(8)
        v.addWidget(_body("Boxer names and privacy", bold=True, size=dt.SIZE_MD))
        v.addWidget(_body("Boxer names are kept with each recording, in its analysis file "
                          "(recording.xdf) and in exports, so the team can keep track of who is "
                          "who. These switches hide them where you don't want them.",
                          color=dt.text_tertiary(), size=dt.SIZE_SM))
        self._hide_export_cb = QCheckBox("Replace boxer names with Boxer A / Boxer B in exports")
        self._hide_export_cb.setStyleSheet(dt.qss_checkbox())
        self._hide_export_cb.toggled.connect(lambda on: self._on_privacy_toggled(
            "hide_names_in_exports", on))
        v.addWidget(self._hide_export_cb)
        n1 = _body("Sets what the export window starts with — you can still choose for each "
                   "export. A name-free export has no names in its files or its file name; the "
                   "videos still show the boxers.", color=dt.text_tertiary(), size=dt.SIZE_XS)
        n1.setContentsMargins(28, 0, 0, 4); v.addWidget(n1)
        self._hide_screen_cb = QCheckBox("Hide boxer names on screen")
        self._hide_screen_cb.setStyleSheet(dt.qss_checkbox())
        self._hide_screen_cb.toggled.connect(lambda on: self._on_privacy_toggled(
            "hide_names_on_screen", on))
        v.addWidget(self._hide_screen_cb)
        n2 = _body("Shows Boxer A / Boxer B instead of names in Sessions and Review & Label — "
                   "for presenting or sharing your screen. The same switch is in the sidebar "
                   f"(Hide names), or press {platform_utils.shortcut('H', shift=True)}.",
                   color=dt.text_tertiary(), size=dt.SIZE_XS)
        n2.setContentsMargins(28, 0, 0, 0); v.addWidget(n2)
        return card

    def _refresh_privacy(self):
        for cb, key in ((self._hide_export_cb, "hide_names_in_exports"),
                        (self._hide_screen_cb, "hide_names_on_screen")):
            cb.blockSignals(True)
            cb.setChecked(bool(getattr(self._settings, key)))
            cb.blockSignals(False)

    def _on_privacy_toggled(self, key, on):
        setattr(self._settings, key, bool(on))
        self._settings.save()
        self._flash_saved()
        if key == "hide_names_on_screen":
            self.names_hidden_changed.emit(bool(on))

    # ── 1. Recordings folder ──
    def _build_folder_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(10)
        v.addWidget(_body("Recordings folder", bold=True, size=dt.SIZE_MD))
        v.addWidget(_body("Recordings are named by their date and time. The folder can be on "
                         "a shared drive: everyone who uses it then sees the same recordings, "
                         "the same label list and each other's labels, and nobody overwrites "
                         "anybody's work.",
                         color=dt.text_tertiary(), size=dt.SIZE_SM))
        self._folder_name = _body("", bold=True, size=dt.SIZE_MD)
        self._folder_path = _body("", color=dt.text_tertiary(), size=dt.SIZE_XS)
        self._folder_free = _body("", color=dt.text_tertiary(), size=dt.SIZE_XS)
        v.addWidget(self._folder_name); v.addWidget(self._folder_path)
        v.addWidget(self._folder_free)
        self._folder_env_note = _body("", color=dt.warning_color(), size=dt.SIZE_XS)
        self._folder_env_note.setVisible(False)
        v.addWidget(self._folder_env_note)
        self._folder_problem = ui.StatusLine("", "bad", size=dt.SIZE_SM)
        self._folder_problem.setVisible(False)
        v.addWidget(self._folder_problem)
        row = QHBoxLayout(); row.setSpacing(10)
        self._change_folder_btn = QPushButton("Change folder…")
        self._change_folder_btn.setMinimumHeight(40)
        self._change_folder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._change_folder_btn.setStyleSheet(dt.qss_secondary_button())
        self._change_folder_btn.clicked.connect(self._on_change_folder)
        row.addWidget(self._change_folder_btn)
        finder_btn = QPushButton(platform_utils.show_in_file_manager_label())
        finder_btn.setMinimumHeight(40)
        finder_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        finder_btn.setStyleSheet(dt.qss_secondary_button())
        finder_btn.clicked.connect(self._on_show_in_finder)
        row.addWidget(finder_btn)
        row.addStretch()
        v.addLayout(row)
        return card

    def _refresh_folder_card(self):
        env = os.environ.get("BRAIN_BATTLE_CAPTURE_DIR")
        d = self._settings.capture_dir_resolved()
        self._folder_name.setText(os.path.basename(os.path.abspath(d)) or d)
        self._folder_path.setText(d)
        state = safe_io.folder_status(d, create=True)
        try:
            free = shutil.disk_usage(d).free
            shared = " · on a shared (network) drive" if safe_io.is_network_path(d) else ""
            self._folder_free.setText(f"{_human_gb(free)} free{shared}")
        except OSError:
            self._folder_free.setText("free space unknown")
        problem = safe_io.folder_problem_text(d, state)
        self._folder_problem.set(problem, "bad")
        self._folder_problem.setVisible(bool(problem))
        if env:
            self._folder_env_note.setText(
                "The recordings folder is set outside the app (BRAIN_BATTLE_"
                "CAPTURE_DIR) — Change folder is turned off here.")
            self._folder_env_note.setVisible(True)
            self._change_folder_btn.setEnabled(False)
        else:
            self._folder_env_note.setVisible(False)
            self._change_folder_btn.setEnabled(True)

    def _on_change_folder(self):
        start = self._settings.capture_dir_resolved()
        path = QFileDialog.getExistingDirectory(self, "Choose a recordings folder", start)
        if path:
            self.apply_new_folder(path)

    def apply_new_folder(self, path: str):
        """Apply a newly-chosen recordings folder. Split out from the dialog
        handler so it's directly testable without driving a native dialog.
        Existing recordings are NOT moved; `label_list.json` is seeded in the
        new folder if missing."""
        self._settings.capture_dir = path
        self._settings.save()
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        label_vocab.load(path)   # seeds label_list.json if missing
        self._refresh_folder_card()
        self._load_labels()
        self._flash_saved()
        self.capture_dir_changed.emit(path)

    def _on_show_in_finder(self):
        platform_utils.open_folder(self._settings.capture_dir_resolved())

    # ── 2. Headbands ──
    def _build_headbands_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(10)
        v.addWidget(_body("Headbands", bold=True, size=dt.SIZE_MD))
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        self._hb_combo = {}
        for row, which in enumerate(("A", "B")):
            line, soft, text = dt.boxer_colors(which)
            badge = QLabel(which); badge.setFixedSize(22, 22)
            badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
            badge.setStyleSheet(f"background: {soft}; color: {text}; border: 1px solid {line}; "
                               f"border-radius: 11px; font-weight: 700; font-size: 11px;")
            grid.addWidget(badge, row, 0)
            grid.addWidget(_body(f"Boxer {which}"), row, 1)
            combo = QComboBox(); combo.setStyleSheet(dt.qss_input())
            combo.setMinimumHeight(36)
            combo.currentTextChanged.connect(
                lambda name, w=which: self._on_headband_changed(w, name))
            grid.addWidget(combo, row, 2)
            self._hb_combo[which] = combo
        grid.setColumnStretch(2, 1)
        v.addLayout(grid)
        self._hb_warning = _body("", color=dt.error_color(), size=dt.SIZE_XS)
        self._hb_warning.setVisible(False)
        v.addWidget(self._hb_warning)
        self._search_btn = QPushButton("Search for a new headband")
        self._search_btn.setMinimumHeight(40)
        self._search_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._search_btn.setStyleSheet(dt.qss_secondary_button())
        self._search_btn.clicked.connect(self._search_headbands)
        v.addWidget(self._search_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _refresh_headband_combos(self):
        known = self._settings.known_headband_names()
        for which, combo in self._hb_combo.items():
            combo.blockSignals(True)
            combo.clear()
            combo.addItems(known)
            current = self._settings.headband_for(which) or ""
            idx = combo.findText(current)
            if idx < 0 and current:
                combo.addItem(current); idx = combo.count() - 1
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

    def _on_headband_changed(self, which, name):
        other = "B" if which == "A" else "A"
        other_name = self._hb_combo[other].currentText()
        if name and other_name and name == other_name:
            self._hb_warning.setText("Boxer A and Boxer B need different headbands.")
            self._hb_warning.setVisible(True)
            self._refresh_headband_combos()
            return
        self._hb_warning.setVisible(False)
        self._settings.headbands[which] = name
        self._settings.save()
        self._flash_saved()

    def _search_headbands(self):
        self._search_btn.setEnabled(False)
        self._search_btn.setText("Searching…")
        try:
            import brain_battle.capture.simulation as simulation
            if simulation.enabled():
                devices = simulation.muse_devices()
                self._on_headbands_found(devices)
                return
        except Exception:
            pass
        try:
            from brain_battle.capture.eeg_module import _MuseScanThread
        except Exception:
            self._on_headbands_found([])
            return
        th = _MuseScanThread(self)
        th.found.connect(self._on_headbands_found)
        th.error.connect(lambda _m: self._on_headbands_found([]))
        self._scan_thread = th
        th.start()

    def _on_headbands_found(self, devices):
        for d in devices or []:
            name = (d or {}).get("name")
            if name:
                app_settings.remember_headband(name)
        self._settings = app_settings.load()
        self._refresh_headband_combos()
        self._search_btn.setEnabled(True)
        self._search_btn.setText("Search for a new headband")
        self._flash_saved()

    # ── 3. Video quality ──
    def _build_quality_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(10)
        v.addWidget(_body("Video quality", bold=True, size=dt.SIZE_MD))
        self._quality_group = QButtonGroup(self)
        self._quality_group.setExclusive(True)
        self._quality_rows = {}
        for key in VIDEO_QUALITY_ORDER:
            q = app_settings.VIDEO_QUALITY[key]
            row = QHBoxLayout(); row.setSpacing(10)
            rb = QRadioButton()
            self._quality_group.addButton(rb)
            row.addWidget(rb)
            col = QVBoxLayout(); col.setSpacing(1)
            rec = "  (recommended)" if q.get("recommended") else ""
            col.addWidget(_body(f"{q['label']} {q['w']}×{q['h']} {q['fps']} fps{rec}",
                                bold=True, size=dt.SIZE_SM))
            gb = app_settings.gb_per_hour(q["mbps"])
            col.addWidget(_body(f"about {gb:.1f} GB per hour per camera",
                                color=dt.text_tertiary(), size=dt.SIZE_XS))
            row.addLayout(col, 1)
            v.addLayout(row)
            self._quality_rows[key] = rb
            rb.toggled.connect(lambda checked, k=key: checked and self._on_quality_changed(k))
        return card

    def _refresh_quality(self):
        key = self._settings.video_quality
        rb = self._quality_rows.get(key) or self._quality_rows["standard"]
        rb.blockSignals(True); rb.setChecked(True); rb.blockSignals(False)

    def _on_quality_changed(self, key):
        if self._settings.video_quality == key:
            return
        self._settings.video_quality = key
        self._settings.save()
        self._flash_saved()

    # ── 4. Session defaults ──
    def _build_defaults_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(10)
        v.addWidget(_body("Session defaults", bold=True, size=dt.SIZE_MD))
        grid = QGridLayout(); grid.setHorizontalSpacing(18); grid.setVerticalSpacing(12)
        self._st_rounds = Stepper("Rounds", 3, 1, 20, 1, lambda n: str(n))
        self._st_round = Stepper("Round length", 180, 10, 1800, 30, _mmss)
        self._st_break = Stepper("Break between rounds", 60, 0, 600, 15, _mmss)
        self._st_rest = Stepper("Rest before round 1", 60, 0, 600, 15, _mmss)
        for i, st in enumerate((self._st_rounds, self._st_round, self._st_break, self._st_rest)):
            grid.addWidget(st, i // 2, i % 2)
            st.changed.connect(self._on_defaults_changed)
        v.addLayout(grid)
        bell_row = QHBoxLayout(); bell_row.setSpacing(12)
        self._bell = QCheckBox("Ring a bell when each round starts")
        self._bell.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                                 f"font-size: {dt.SIZE_SM}px;")
        self._bell.toggled.connect(self._on_bell_toggled)
        bell_row.addWidget(self._bell)
        test_btn = QPushButton("Test sound")
        test_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        test_btn.setStyleSheet(dt.qss_secondary_button())
        test_btn.clicked.connect(self._on_test_sound)
        bell_row.addWidget(test_btn)
        bell_row.addStretch()
        v.addLayout(bell_row)
        return card

    def _refresh_defaults(self):
        cfg = self._settings.session_config()
        self._st_rounds.set_value(cfg.num_rounds)
        self._st_round.set_value(cfg.round_sec)
        self._st_break.set_value(cfg.break_sec)
        self._st_rest.set_value(cfg.baseline_sec)
        self._bell.blockSignals(True)
        self._bell.setChecked(bool(self._settings.round_bell))
        self._bell.blockSignals(False)

    def _on_defaults_changed(self):
        self._settings.session_defaults = {
            "num_rounds": self._st_rounds.value(), "round_sec": self._st_round.value(),
            "break_sec": self._st_break.value(), "baseline_sec": self._st_rest.value()}
        self._settings.save()
        self._flash_saved()

    def _on_bell_toggled(self, checked):
        self._settings.round_bell = bool(checked)
        self._settings.save()
        self._flash_saved()

    def _on_test_sound(self):
        if getattr(self, "_beep", None) is None:
            from brain_battle.capture.round_bell import RoundBell
            self._beep = RoundBell(self)
        self._beep.play()

    # ── 5. Extra signals + sharing with other lab software ──
    _SIGNAL_OPTIONS = [
        ("record_motion", "Record head movement (the headband's motion sensors)",
         "Useful later for spotting moments where movement, not the brain, moved the "
         "brain-activity trace. Saved next to the brain recordings."),
        ("record_ppg", "Record pulse (the headband's optical sensor)",
         "Not yet tried with real headbands. If the headbands stop connecting "
         "reliably after you switch this on, switch it off again."),
        ("lsl_streams", "Share live signals with other lab software on this network",
         "Lets programs such as LabRecorder or an EEG viewer see the headbands, the "
         "round markers and the camera frame times while this app is open (Lab "
         "Streaming Layer). Your recordings are saved the same way whether this is on or off."),
    ]

    def _build_signals_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(18, 16, 18, 16); v.setSpacing(6)
        v.addWidget(_body("Extra signals and sharing", bold=True, size=dt.SIZE_MD))
        self._signal_boxes = {}
        for key, text, note in self._SIGNAL_OPTIONS:
            box = QCheckBox(text)
            box.setMinimumHeight(30)
            box.setStyleSheet(f"color: {dt.text_secondary()}; font-family: {dt.FONT_UI}; "
                              f"font-size: {dt.SIZE_SM}px;")
            box.toggled.connect(lambda on, k=key: self._on_signal_toggled(k, on))
            v.addWidget(box)
            v.addWidget(_body(note, color=dt.text_tertiary(), size=dt.SIZE_XS))
            self._signal_boxes[key] = box
        return card

    def _refresh_signals(self):
        for key, box in self._signal_boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(getattr(self._settings, key)))
            box.blockSignals(False)

    def _on_signal_toggled(self, key, on):
        setattr(self._settings, key, bool(on))
        self._settings.save()
        self._flash_saved()

    # ── 6. Label list ──
    def _build_labels_card(self) -> QFrame:
        card = _card()
        v = QVBoxLayout(card); v.setContentsMargins(20, 18, 20, 18); v.setSpacing(10)
        v.addWidget(_body("Label list", bold=True, size=dt.SIZE_MD))
        v.addWidget(_body("Shared by everyone using this recordings folder. Order "
                         "sets the number key (1-9) used to place a label.",
                         color=dt.text_tertiary(), size=dt.SIZE_XS))
        self._labels_box = QVBoxLayout(); self._labels_box.setSpacing(6)
        v.addLayout(self._labels_box)
        add_btn = QPushButton("+ Add label")
        add_btn.setMinimumHeight(38)
        add_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        add_btn.setStyleSheet(dt.qss_secondary_button())
        add_btn.clicked.connect(self._on_add_label)
        v.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return card

    def _load_labels(self):
        self._labels = label_vocab.load(self._settings.capture_dir_resolved())
        self._render_label_rows()

    def _render_label_rows(self):
        while self._labels_box.count():
            it = self._labels_box.takeAt(0)
            if it.widget():
                it.widget().deleteLater()
        for entry in self._labels:
            row = _LabelRow(entry)
            row.changed.connect(self._save_labels)
            row.move_up.connect(lambda r=row: self._move_label(r, -1))
            row.move_down.connect(lambda r=row: self._move_label(r, 1))
            row.removed.connect(lambda r=row: self._remove_label(r))
            self._labels_box.addWidget(row)

    def _current_rows(self) -> list[_LabelRow]:
        return [self._labels_box.itemAt(i).widget() for i in range(self._labels_box.count())]

    def _save_labels(self):
        self._labels = label_vocab.renumber([r.entry for r in self._current_rows()])
        for row, entry in zip(self._current_rows(), self._labels):
            row.set_key(entry["key"])
        label_vocab.save(self._settings.capture_dir_resolved(), self._labels)
        self._flash_saved()

    def _move_label(self, row, delta):
        rows = self._current_rows()
        i = rows.index(row)
        j = i + delta
        if not (0 <= j < len(rows)):
            return
        self._labels[i], self._labels[j] = self._labels[j], self._labels[i]
        self._render_label_rows()
        self._save_labels()

    def _remove_label(self, row):
        rows = self._current_rows()
        i = rows.index(row)
        del self._labels[i]
        self._render_label_rows()
        self._save_labels()

    def _on_add_label(self):
        colors = [e.get("color") for e in self._labels]
        entry = {"key": len(self._labels) + 1, "name": "New label",
                 "color": label_vocab.next_color(colors), "kind": label_vocab.POINT}
        self._labels.append(entry)
        self._render_label_rows()
        self._save_labels()


__all__ = ["SettingsScreen"]
