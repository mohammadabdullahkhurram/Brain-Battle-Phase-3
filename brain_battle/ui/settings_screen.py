"""
settings_screen.py
──────────────────
Settings (docs/DESIGN.md "Settings", design F). Everything saves the moment it
is changed — the header says "Changes save automatically". Four cards:

  • **This computer** — "Your name" (only labellers have names; fighters are
    always codes) and the recordings folder (Change…). Per computer
    (``app_settings``).
  • **Label list** — *shared with the team* (``label_list.json`` in the
    recordings folder): chips with ×, "+ Add" (type, then Enter). One-off
    descriptions go in a Custom note while labelling instead.
  • **Fighter colours** — *shared with the team* (``fighter_colours.json``):
    Red, Blue and Black always there; "+ Add colour" (a colour and a name).
  • **Advanced** (folded): Record head movement (on), Record pulse (off),
    Record cameras at 60 fps (on), Send live signals to other programs (LSL,
    off); which headband each boxer wears; video quality.

The round plan and the bell are set on Get ready and remembered there for the
next recording.

Public surface used by the app shell:
  • SettingsScreen(parent)
  • .capture_dir_changed (signal, str) — a new recordings folder was chosen.
  • .labeller_changed (signal, str) — "Your name" was edited.
"""
from __future__ import annotations

import os
import shutil

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QFrame, QLineEdit, QCheckBox, QScrollArea,
    QButtonGroup, QRadioButton, QFileDialog,
)

import brain_battle.ui.design_tokens as dt
import brain_battle.ui.widgets as ui
import brain_battle.data.app_settings as app_settings
import brain_battle.data.fighters as fighters
import brain_battle.data.safe_io as safe_io
import brain_battle.data.label_vocab as label_vocab
from brain_battle.version import __version__
from brain_battle.ui.fonts import register_fonts

VIDEO_QUALITY_ORDER = ["standard", "high", "light"]

ADVANCED_SWITCHES = [
    ("record_motion", "Record head movement",
     "From the headbands' motion sensors. Helps spot hits and noisy moments."),
    ("record_ppg", "Record pulse",
     "Heart rate from the forehead light sensor. Not yet tested with two headbands."),
    ("record_60fps", "Record cameras at 60 fps",
     "Halves the timing error from the cameras (about ±8 ms instead of ±17 ms). Uses nearly "
     "twice the disk space, and the cameras must support it."),
    ("lsl_streams", "Send live signals to other programs (LSL)",
     "Only needed to watch in BrainVision or record with LabRecorder at the same time. The app "
     "shows everything live without it."),
]


def _human_gb(n_bytes: float) -> str:
    gb = n_bytes / (1024 ** 3)
    return f"{gb:.0f} GB" if gb >= 100 else f"{gb:.1f} GB"


def _shared_chip() -> QLabel:
    c = QLabel("Shared with the team")
    c.setObjectName("sharedChip")
    c.setStyleSheet(f"#sharedChip {{ background: {dt.accent_ghost()}; color: {dt.accent_hover()}; "
                    f"border-radius: 11px; padding: 3px 10px; font-family: {dt.FONT_UI}; "
                    f"font-size: {dt.SIZE_XS}px; font-weight: 600; }}")
    return c


def _heading_row(text: str, shared: bool = False) -> QHBoxLayout:
    h = QHBoxLayout(); h.setSpacing(10)
    t = QLabel(text); t.setStyleSheet(dt.css_text(17, 700))
    h.addWidget(t)
    if shared:
        h.addWidget(_shared_chip())
    h.addStretch()
    return h


# Colours offered in the in-app colour chooser (a fighter colour must read well
# as a small badge with white text, so no pale tints).
COLOUR_CHOICES = ["#111827", "#D93025", "#2155D6", "#1E7B3C", "#7C3AED", "#C2410C",
                  "#0E7C86", "#B45309", "#BE185D", "#4B5563", "#0369A1", "#65A30D",
                  "#9333EA", "#DC2626", "#1D4ED8", "#047857"]


def quality_text(key: str, fps60: bool) -> str:
    """'Standard 960×540 (recommended) · about 4.1 GB per hour per camera at
    60 fps' — the size follows the 60 fps switch (Advanced)."""
    q = app_settings.VIDEO_QUALITY[key]
    mbps = q["mbps"] * (app_settings.FPS60_BITRATE_FACTOR if fps60 else 1.0)
    return (f"{q['label']} {q['w']}×{q['h']}" + ("  (recommended)" if q.get("recommended") else "")
            + f" · about {app_settings.gb_per_hour(mbps):.1f} GB per hour per camera at "
              f"{60 if fps60 else 30} fps")


def pick_colour(parent, current: str) -> str:
    """The app's colour chooser (no native colour dialog): a grid of colours
    and a hex field. Returns '#RRGGBB', or '' when cancelled."""
    d = ui.AppDialog(parent, "Fighter colour", "Pick a colour, or type its code (#RRGGBB).")
    chosen = {"hex": (current or "#2155D6").upper()}
    grid = QGridLayout(); grid.setSpacing(8)
    edit = QLineEdit(chosen["hex"]); edit.setMaxLength(7); edit.setStyleSheet(dt.qss_input())
    edit.setAccessibleName("Colour code")

    def choose(h):
        chosen["hex"] = h
        edit.setText(h)
    for i, h in enumerate(COLOUR_CHOICES):
        b = QPushButton(); b.setFixedSize(34, 34); b.setToolTip(h); b.setAccessibleName(h)
        b.setCursor(Qt.CursorShape.PointingHandCursor)
        b.setStyleSheet(f"QPushButton {{ background: {h}; border-radius: 17px; "
                        f"border: 2px solid #FFFFFF; }} QPushButton:focus {{ border: 3px solid "
                        f"{dt.accent()}; }}")
        b.clicked.connect(lambda _c=False, h=h: choose(h))
        grid.addWidget(b, i // 8, i % 8)
    d.body.addLayout(grid)
    d.body.addWidget(edit)
    d.add_button("Cancel", 0)
    d.add_button("Use this colour", 1, "primary", default=True)
    if not d.exec():
        return ""
    text = edit.text().strip().upper()
    if not text.startswith("#"):
        text = "#" + text
    return text if QColor(text).isValid() and len(text) == 7 else ""


class _Chip(QFrame):
    """A rounded chip with an optional × (remove)."""
    removed = pyqtSignal()

    def __init__(self, text: str, lead: str = "", dot: str | None = None,
                 removable: bool = True, parent=None):
        super().__init__(parent)
        self.text = text
        self.setObjectName("chip")
        self.setStyleSheet(f"#chip {{ border: 1px solid {dt.border_strong()}; border-radius: 16px; "
                           f"background: {dt.surface()}; }}")
        h = QHBoxLayout(self); h.setContentsMargins(10 if not dot else 6, 3, 6, 3); h.setSpacing(6)
        if dot:
            h.addWidget(ui.Dot(dot, 20))
        if lead:
            k = QLabel(lead); k.setStyleSheet(dt.css_text(dt.SIZE_XS, 500, dt.text_tertiary(),
                                                          f"font-family: {dt.FONT_MONO};"))
            h.addWidget(k)
        lab = QLabel(text); lab.setStyleSheet(dt.css_text(dt.SIZE_MD, 400, dt.text_primary()))
        h.addWidget(lab)
        if removable:
            x = QPushButton("×"); x.setFixedSize(22, 22)
            x.setAccessibleName(f"Remove {text}"); x.setToolTip(f"Remove {text}")
            x.setCursor(Qt.CursorShape.PointingHandCursor)
            x.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; "
                            f"color: {dt.text_tertiary()}; font-size: 16px; }}")
            x.clicked.connect(self.removed.emit)
            h.addWidget(x)
        else:
            h.addSpacing(4)


class SettingsScreen(QWidget):
    capture_dir_changed = pyqtSignal(str)
    labeller_changed    = pyqtSignal(str)
    help_requested      = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        register_fonts()
        self._settings = app_settings.load()
        self._labels = []
        self._colours = []
        self._scan_thread = None
        self.setObjectName("settingsScreen")
        self.setStyleSheet(f"#settingsScreen {{ background: {dt.canvas_bg()}; }}")
        self._build()
        self._refresh_all()

    # ── the "Changes save automatically" note the shell shows in the header ──
    def header_extra(self) -> QWidget:
        l = QLabel("Changes save automatically")
        l.setStyleSheet(dt.css_text(dt.SIZE_SM, 400, dt.success_color()))
        return l

    # ── construction ──
    def _build(self):
        outer = QVBoxLayout(self); outer.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(); self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        host = QWidget(); host.setObjectName("settingsHost")
        host.setStyleSheet(f"#settingsHost {{ background: {dt.canvas_bg()}; }}")
        center = QHBoxLayout(host); center.setContentsMargins(24, 32, 24, 40)
        col = QWidget(); col.setMaximumWidth(820)
        center.addStretch(1); center.addWidget(col, 100); center.addStretch(1)
        v = QVBoxLayout(col); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(16)
        t = QLabel("Settings"); t.setStyleSheet(dt.css_text(28, 700))
        v.addWidget(t)
        v.addWidget(self._build_computer_card())
        v.addWidget(self._build_labels_card())
        v.addWidget(self._build_colours_card())
        v.addWidget(self._build_advanced_card())
        v.addWidget(ui.muted(f"Brain Battle v{__version__} · Constructor University",
                             size=dt.SIZE_XS))
        v.addStretch()
        self._scroll.setWidget(host)
        outer.addWidget(self._scroll)
        self._saved = QLabel("")          # brief "✓ Saved" (kept for tests / screen readers)

    def _card(self):
        c = ui.Card(radius=16)
        v = QVBoxLayout(c); v.setContentsMargins(24, 20, 24, 20); v.setSpacing(12)
        return c, v

    # ── This computer ──
    def _build_computer_card(self):
        card, v = self._card()
        v.addLayout(_heading_row("This computer"))
        g = QGridLayout(); g.setHorizontalSpacing(14); g.setVerticalSpacing(6)
        lab = QLabel("Your name"); lab.setFixedWidth(170)
        lab.setStyleSheet(dt.css_text(dt.SIZE_MD, 500, dt.text_secondary()))
        self._labeller_edit = QLineEdit(); self._labeller_edit.setMinimumHeight(40)
        self._labeller_edit.setPlaceholderText("Asked the first time you label")
        self._labeller_edit.setAccessibleName("Your name")
        self._labeller_edit.setStyleSheet(dt.qss_input())
        self._labeller_edit.editingFinished.connect(self._on_labeller_edited)
        g.addWidget(lab, 0, 0); g.addWidget(self._labeller_edit, 0, 1, 1, 2)
        g.addWidget(ui.muted("Only labellers have names. Fighters are always codes."), 1, 1, 1, 2)
        flab = QLabel("Recordings folder"); flab.setFixedWidth(170)
        flab.setStyleSheet(dt.css_text(dt.SIZE_MD, 500, dt.text_secondary()))
        self._folder_line = QLabel(""); self._folder_line.setWordWrap(True)
        self._folder_line.setObjectName("folderLine")
        self._folder_line.setStyleSheet(f"#folderLine {{ background: {dt.canvas_bg()}; "
                                        f"border-radius: 10px; padding: 10px 12px; "
                                        f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; }}")
        self._change_folder_btn = ui.secondary_button("Change")
        self._change_folder_btn.clicked.connect(self._on_change_folder)
        g.addWidget(flab, 2, 0); g.addWidget(self._folder_line, 2, 1)
        g.addWidget(self._change_folder_btn, 2, 2)
        g.setColumnStretch(1, 1)
        v.addLayout(g)
        self._folder_env_note = ui.muted("")
        self._folder_env_note.setVisible(False)
        v.addWidget(self._folder_env_note)
        self._folder_problem = ui.StatusLine("", "bad", size=dt.SIZE_SM)
        self._folder_problem.setVisible(False)
        v.addWidget(self._folder_problem)
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

    def _refresh_folder_card(self):
        env = os.environ.get("BRAIN_BATTLE_CAPTURE_DIR")
        d = self._settings.capture_dir_resolved()
        state = safe_io.folder_status(d, create=True)
        try:
            free = f" · {_human_gb(shutil.disk_usage(d).free)} free"
        except OSError:
            free = ""
        shared = " · shared (network) drive" if safe_io.is_network_path(d) else ""
        from brain_battle.ui.sessions_screen import folder_display
        self._folder_line.setText(f"{folder_display(d)}{free}{shared}")
        self._folder_line.setToolTip(d)
        problem = safe_io.folder_problem_text(d, state)
        self._folder_problem.set(problem, "bad")
        self._folder_problem.setVisible(bool(problem))
        self._folder_env_note.setText(
            "The recordings folder is set outside the app (BRAIN_BATTLE_CAPTURE_DIR) — "
            "Change is turned off here." if env else "")
        self._folder_env_note.setVisible(bool(env))
        self._change_folder_btn.setEnabled(not env)

    def _on_change_folder(self):
        start = self._settings.capture_dir_resolved()
        path = QFileDialog.getExistingDirectory(self, "Choose a recordings folder", start)
        if path:
            self.apply_new_folder(path)

    def apply_new_folder(self, path: str):
        """Apply a newly chosen recordings folder (testable without the native
        dialog). Existing recordings are NOT moved; the shared label list is
        seeded in the new folder if it has none."""
        self._settings.capture_dir = path
        self._settings.save()
        try:
            os.makedirs(path, exist_ok=True)
        except OSError:
            pass
        label_vocab.load(path)   # seeds label_list.json if missing
        self._refresh_folder_card()
        self._load_labels()
        self._load_colours()
        self._flash_saved()
        self.capture_dir_changed.emit(path)

    # ── Label list (shared) ──
    def _build_labels_card(self):
        card, v = self._card()
        v.addLayout(_heading_row("Label list", shared=True))
        self._labels_host = QWidget()
        self._labels_flow = ui.FlowLayout(self._labels_host)
        v.addWidget(self._labels_host)
        self._add_label_btn = QPushButton("+ Add")
        self._add_label_btn.setMinimumHeight(34)
        self._add_label_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_label_btn.setStyleSheet(self._dashed_qss())
        self._add_label_btn.clicked.connect(self._on_add_label)
        self._new_label = QLineEdit(); self._new_label.setPlaceholderText("New label, then Enter")
        self._new_label.setMinimumHeight(34); self._new_label.setFixedWidth(220)
        self._new_label.setStyleSheet(dt.qss_input())
        self._new_label.returnPressed.connect(self._commit_new_label)
        v.addWidget(ui.muted("Labels everyone uses again and again. One-off descriptions go in "
                             "a Custom note while labelling, so they don't clutter this list."))
        return card

    @staticmethod
    def _dashed_qss():
        return (f"QPushButton {{ border: 1px dashed {dt.border_strong()}; background: {dt.surface()}; "
                f"color: {dt.accent_hover()}; border-radius: 10px; padding: 0 14px; "
                f"font-family: {dt.FONT_UI}; font-size: {dt.SIZE_MD}px; font-weight: 600; }}")

    def _load_labels(self):
        self._labels = label_vocab.load(self._settings.capture_dir_resolved())
        self._render_labels()

    def _render_labels(self, adding: bool = False):
        while self._labels_flow.count():
            it = self._labels_flow.takeAt(0)
            w = it.widget() if it else None
            if w is not None and w not in (self._add_label_btn, self._new_label):
                w.hide(); w.setParent(None); w.deleteLater()
        for i, entry in enumerate(self._labels):
            chip = _Chip(entry["name"], lead=str(i + 1))
            chip.removed.connect(lambda i=i: self._confirm_remove_label(i))
            self._labels_flow.addWidget(chip)
        tail = self._new_label if adding else self._add_label_btn
        self._labels_flow.addWidget(tail)
        self._new_label.setVisible(adding); self._add_label_btn.setVisible(not adding)
        if adding:
            self._new_label.clear(); self._new_label.setFocus()
        self._labels_host.updateGeometry()

    def label_names(self) -> list:
        return [e["name"] for e in self._labels]

    def _save_labels(self):
        self._labels = label_vocab.renumber(self._labels)
        label_vocab.save(self._settings.capture_dir_resolved(), self._labels)
        self._flash_saved()

    def _confirm_remove_label(self, i):
        """× on a label: it is the whole team's list, so ask first."""
        if not (0 <= i < len(self._labels)):
            return
        name = self._labels[i]["name"]
        if ui.ask(self, f"Remove “{name}” from the label list?",
                  "Everyone using this recordings folder shares this list. Labels already "
                  "placed in recordings keep their name.", "Remove", "Keep it",
                  tone="danger", danger=True):
            self._remove_label(i)

    def _confirm_remove_colour(self, name):
        if ui.ask(self, f"Remove the colour “{name}”?",
                  "Everyone using this recordings folder shares these colours. Recordings "
                  "that used it keep it.", "Remove", "Keep it", tone="danger", danger=True):
            self.remove_colour(name)

    def _remove_label(self, i):
        if 0 <= i < len(self._labels):
            del self._labels[i]
            self._save_labels()
            self._render_labels()

    def _on_add_label(self):
        self._render_labels(adding=True)

    def add_label(self, name: str) -> bool:
        """Add a label to the shared list (Enter in the "+ Add" box)."""
        name = " ".join(str(name or "").split())[:40]
        if not name or name.lower() in {e["name"].lower() for e in self._labels}:
            return False
        colors = [e.get("color") for e in self._labels]
        self._labels.append({"key": len(self._labels) + 1, "name": name,
                             "color": label_vocab.next_color(colors), "kind": label_vocab.POINT})
        self._save_labels()
        return True

    def _commit_new_label(self):
        self.add_label(self._new_label.text())
        self._render_labels(adding=False)

    # ── Fighter colours (shared) ──
    def _build_colours_card(self):
        card, v = self._card()
        v.addLayout(_heading_row("Fighter colours", shared=True))
        self._colours_host = QWidget()
        self._colours_flow = ui.FlowLayout(self._colours_host)
        v.addWidget(self._colours_host)
        self._add_colour_btn = QPushButton("+ Add colour")
        self._add_colour_btn.setMinimumHeight(34)
        self._add_colour_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self._add_colour_btn.setStyleSheet(self._dashed_qss())
        self._add_colour_btn.clicked.connect(lambda: self._render_colours(adding=True))
        self._colour_form = QWidget()
        cf = QHBoxLayout(self._colour_form); cf.setContentsMargins(0, 0, 0, 0); cf.setSpacing(6)
        self._new_colour_hex = "#0F7A62"
        self._colour_swatch = QPushButton(); self._colour_swatch.setFixedSize(40, 34)
        self._colour_swatch.setAccessibleName("Colour")
        self._colour_swatch.clicked.connect(self._pick_new_colour)
        self._paint_swatch()
        self._new_colour_name = QLineEdit(); self._new_colour_name.setPlaceholderText("Name, e.g. Green")
        self._new_colour_name.setFixedWidth(160); self._new_colour_name.setMinimumHeight(34)
        self._new_colour_name.setStyleSheet(dt.qss_input())
        self._new_colour_name.returnPressed.connect(self._commit_new_colour)
        addb = ui.primary_button("Add", compact=True)
        addb.clicked.connect(self._commit_new_colour)
        cf.addWidget(self._colour_swatch); cf.addWidget(self._new_colour_name); cf.addWidget(addb)
        self._colour_msg = ui.muted("")
        v.addWidget(self._colour_msg)
        v.addWidget(ui.muted("Black, red and blue are always there. The colour each fighter wears "
                             "is picked on the Get ready screen."))
        return card

    def _paint_swatch(self):
        self._colour_swatch.setStyleSheet(
            f"QPushButton {{ background: {self._new_colour_hex}; border: 1px solid "
            f"{dt.border_strong()}; border-radius: 8px; }}")

    def _pick_new_colour(self):
        hexv = pick_colour(self, self._new_colour_hex)
        if hexv:
            self._new_colour_hex = hexv
            self._paint_swatch()

    def _load_colours(self):
        self._colours = fighters.load_colours(self._settings.capture_dir_resolved())
        self._render_colours()

    def _render_colours(self, adding: bool = False):
        while self._colours_flow.count():
            it = self._colours_flow.takeAt(0)
            w = it.widget() if it else None
            if w is not None and w not in (self._add_colour_btn, self._colour_form):
                w.hide(); w.setParent(None); w.deleteLater()
        for c in self._colours:
            chip = _Chip(c["name"], dot=c["color"], removable=not fighters.is_preset(c["name"]))
            chip.removed.connect(lambda n=c["name"]: self._confirm_remove_colour(n))
            self._colours_flow.addWidget(chip)
        tail = self._colour_form if adding else self._add_colour_btn
        self._colours_flow.addWidget(tail)
        self._colour_form.setVisible(adding); self._add_colour_btn.setVisible(not adding)
        if adding:
            self._new_colour_name.clear(); self._new_colour_name.setFocus()

    def colour_names(self) -> list:
        return [c["name"] for c in self._colours]

    def add_colour(self, name: str, color: str) -> str | None:
        """Add a fighter colour for the team. Returns a problem sentence, or None."""
        new, problem = fighters.add_colour(self._colours, name, color)
        if problem:
            self._colour_msg.setText(problem)
            return problem
        self._colours = new
        fighters.save_colours(self._settings.capture_dir_resolved(), self._colours)
        self._colour_msg.setText(f"{new[-1]['name']} added to fighter colours")
        self._flash_saved()
        return None

    def remove_colour(self, name: str):
        self._colours = fighters.remove_colour(self._colours, name)
        fighters.save_colours(self._settings.capture_dir_resolved(), self._colours)
        self._colour_msg.setText(f"{name} removed")
        self._render_colours()
        self._flash_saved()

    def _commit_new_colour(self):
        if self.add_colour(self._new_colour_name.text(), self._new_colour_hex) is None:
            self._render_colours(adding=False)

    # ── Advanced ──
    def _build_advanced_card(self):
        card, v = self._card()
        self._adv_toggle = QPushButton("▸  Advanced")
        self._adv_toggle.setCheckable(True)
        self._adv_toggle.setCursor(Qt.CursorShape.PointingHandCursor)
        self._adv_toggle.setStyleSheet(f"QPushButton {{ border: 0; background: transparent; "
                                       f"text-align: left; font-family: {dt.FONT_UI}; "
                                       f"font-size: 17px; font-weight: 700; color: {dt.text_primary()}; }}")
        self._adv_toggle.toggled.connect(self._toggle_advanced)
        v.addWidget(self._adv_toggle)
        self._adv_body = QWidget()
        b = QVBoxLayout(self._adv_body); b.setContentsMargins(0, 6, 0, 0); b.setSpacing(12)
        self._signal_boxes = {}
        for key, title, note in ADVANCED_SWITCHES:
            box = QCheckBox(title)
            box.setStyleSheet(dt.qss_checkbox() + " QCheckBox { font-weight: 600; }")
            box.toggled.connect(lambda on, k=key: self._on_signal_toggled(k, on))
            b.addWidget(box)
            n = ui.muted(note); n.setContentsMargins(28, 0, 0, 0)
            b.addWidget(n)
            self._signal_boxes[key] = box
        b.addWidget(ui.hline())
        b.addWidget(self._build_headbands())
        b.addWidget(ui.hline())
        b.addWidget(self._build_quality())
        self._adv_body.setVisible(False)
        v.addWidget(self._adv_body)
        return card

    def _toggle_advanced(self, on):
        self._adv_toggle.setText(("▾  " if on else "▸  ") + "Advanced")
        self._adv_body.setVisible(on)

    def open_advanced(self):
        self._adv_toggle.setChecked(True)

    def _refresh_signals(self):
        for key, box in self._signal_boxes.items():
            box.blockSignals(True)
            box.setChecked(bool(getattr(self._settings, key)))
            box.blockSignals(False)

    def _on_signal_toggled(self, key, on):
        setattr(self._settings, key, bool(on))
        self._settings.save()
        self._flash_saved()
        if key == "record_60fps":
            self._refresh_quality()

    def _build_headbands(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        v.addWidget(ui.body("Which headband each boxer wears", weight=600))
        grid = QGridLayout(); grid.setHorizontalSpacing(10); grid.setVerticalSpacing(8)
        self._hb_combo = {}
        for row, which in enumerate(("A", "B")):
            grid.addWidget(ui.neutral_badge(which, 22), row, 0)
            grid.addWidget(ui.body(f"Boxer {which}"), row, 1)
            combo = ui.Dropdown()
            combo.setAccessibleName(f"Default headband for Boxer {which}")
            combo.currentTextChanged.connect(lambda name, wh=which: self._on_headband_changed(wh, name))
            grid.addWidget(combo, row, 2)
            self._hb_combo[which] = combo
        grid.setColumnStretch(2, 1)
        v.addLayout(grid)
        self._hb_warning = ui.body("", color=dt.error_color(), size=dt.SIZE_XS)
        self._hb_warning.setVisible(False)
        v.addWidget(self._hb_warning)
        self._search_btn = ui.secondary_button("Search for a new headband")
        self._search_btn.clicked.connect(self._search_headbands)
        v.addWidget(self._search_btn, 0, Qt.AlignmentFlag.AlignLeft)
        return w

    def _refresh_headband_combos(self):
        known = self._settings.known_headband_names()
        for which, combo in self._hb_combo.items():
            combo.blockSignals(True)
            combo.clear(); combo.addItems(known)
            current = self._settings.headband_for(which) or ""
            idx = combo.findText(current)
            if idx < 0 and current:
                combo.addItem(current); idx = combo.count() - 1
            combo.setCurrentIndex(max(0, idx))
            combo.blockSignals(False)

    def _on_headband_changed(self, which, name):
        other = "B" if which == "A" else "A"
        if name and name == self._hb_combo[other].currentText():
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
                self._on_headbands_found(simulation.muse_devices())
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
        th.error.connect(self._on_scan_error)
        self._scan_thread = th
        th.start()

    def _on_scan_error(self, _msg):
        self._on_headbands_found([])

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

    def _build_quality(self):
        w = QWidget()
        v = QVBoxLayout(w); v.setContentsMargins(0, 0, 0, 0); v.setSpacing(8)
        v.addWidget(ui.body("Video quality", weight=600))
        self._quality_group = QButtonGroup(self); self._quality_group.setExclusive(True)
        self._quality_rows = {}
        for key in VIDEO_QUALITY_ORDER:
            rb = QRadioButton(quality_text(key, self._settings.record_60fps))
            rb.setStyleSheet(f"QRadioButton {{ font-family: {dt.FONT_UI}; font-size: {dt.SIZE_SM}px; "
                             f"color: {dt.text_secondary()}; }}")
            self._quality_group.addButton(rb)
            v.addWidget(rb)
            self._quality_rows[key] = rb
            rb.toggled.connect(lambda checked, k=key: checked and self._on_quality_changed(k))
        return w

    def _refresh_quality(self):
        for key, row in self._quality_rows.items():      # sizes follow the 60 fps switch
            row.setText(quality_text(key, self._settings.record_60fps))
        rb = self._quality_rows.get(self._settings.video_quality) or self._quality_rows["standard"]
        rb.blockSignals(True); rb.setChecked(True); rb.blockSignals(False)

    def _on_quality_changed(self, key):
        if self._settings.video_quality == key:
            return
        self._settings.video_quality = key
        self._settings.save()
        self._flash_saved()

    # ── saving ──
    def _flash_saved(self):
        self._saved.setText("✓ Saved")
        QTimer.singleShot(1500, lambda: self._saved.setText(""))

    def refresh(self):
        """Reload from disk — whenever Settings is shown, so things changed
        elsewhere (another computer sharing the folder, a Bluetooth scan) show."""
        self._settings = app_settings.load()
        self._refresh_all()

    def _refresh_all(self):
        self._refresh_you()
        self._refresh_folder_card()
        self._refresh_signals()
        self._refresh_headband_combos()
        self._refresh_quality()
        self._load_labels()
        self._load_colours()


__all__ = ["SettingsScreen", "ADVANCED_SWITCHES"]
