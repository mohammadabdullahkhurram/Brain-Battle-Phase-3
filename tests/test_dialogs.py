"""
The app's own dialogs, dropdowns and text fields (owner's click-through,
2026-11-23; docs/design/prototype.html ``.dialog``).

Covers:
  1. No native alert or message box anywhere: no QMessageBox, QColorDialog,
     QInputDialog, QFontDialog, QErrorMessage or QProgressDialog in the app;
     QFileDialog only for the operating system's folder / file picker; every
     dialog class is the app's ``AppDialog``.
  2. No system-styled dropdown: no bare ``QComboBox(`` outside ui/widgets.py
     (everything uses ``widgets.Dropdown``).
  3. ``AppDialog``: frameless (not a native window frame), a white rounded card,
     title + text, buttons on the right, warn / danger borders, Esc gives the
     cancel answer, it covers the window it belongs to; ``ask`` / ``tell``
     answer through ``widgets.dialog_responder`` in tests.
  4. ``Dropdown``: light, rounded, Inter, blue focus, its own (non-native)
     popup list.
  5. Light even when the computer is in dark mode: with a dark palette handed
     in, ``pin_light_palette`` + the app stylesheet give white text fields with
     dark text — including Review's Custom note box and the history drawer.

Run:  python3 tests/test_dialogs.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import os
import re
import sys
import glob

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPalette, QColor, QKeyEvent
from PyQt6.QtCore import QEvent
from PyQt6.QtWidgets import QApplication, QTextEdit, QLineEdit, QWidget

_app = QApplication.instance() or QApplication(sys.argv)

import brain_battle.ui.design_tokens as dt  # noqa: E402
import brain_battle.ui.widgets as ui        # noqa: E402

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _sources():
    files = glob.glob(os.path.join(ROOT, "brain_battle", "**", "*.py"), recursive=True)
    return {os.path.relpath(f, ROOT): open(f, encoding="utf-8").read() for f in files}


# ─── 1. no native dialogs ──────────────────────────────────────────────────────

def test_no_native_dialogs():
    native = ("QMessageBox", "QColorDialog", "QInputDialog", "QFontDialog",
              "QErrorMessage", "QProgressDialog")
    hits = {}
    for rel, src in _sources().items():
        code = "\n".join(l for l in src.splitlines() if not l.lstrip().startswith("#"))
        found = [n for n in native if n in code]
        for m in re.finditer(r"QFileDialog\.(\w+)", code):
            if m.group(1) not in ("getExistingDirectory", "getSaveFileName", "getOpenFileName"):
                found.append("QFileDialog." + m.group(1))
        if "QApplication.beep" in code:
            found.append("QApplication.beep")
        if found:
            hits[rel] = found
    check("no native alert / message box / colour dialog in the app "
          "(only the OS folder / file picker)", not hits, str(hits))

    bad = {}
    for rel, src in _sources().items():
        if rel.endswith(os.path.join("ui", "widgets.py")):
            continue
        for m in re.finditer(r"^class (\w+)\(([^)]*)\)", src, re.M):
            if re.search(r"\bQDialog\b", m.group(2)):
                bad.setdefault(rel, []).append(m.group(1))
    check("every dialog class is the app's AppDialog (none derive from QDialog directly)",
          not bad, str(bad))


# ─── 2. no system-styled dropdowns ─────────────────────────────────────────────

def test_one_dropdown():
    hits = [rel for rel, src in _sources().items()
            if "QComboBox(" in src and not rel.endswith(os.path.join("ui", "widgets.py"))]
    check("no bare QComboBox outside ui/widgets.py (everything uses widgets.Dropdown)",
          not hits, str(hits))


# ─── 3. AppDialog ──────────────────────────────────────────────────────────────

def test_app_dialog():
    win = QWidget(); win.resize(1000, 700); win.show()
    d = ui.AppDialog(win, "Stop the recording now?", "Round 2 isn’t finished.", tone="warn")
    d.add_button("Keep recording", 0, "primary"); d.add_button("Stop now", 1)
    check("frameless (never a native window frame)",
          bool(d.windowFlags() & Qt.WindowType.FramelessWindowHint))
    check("modal", d.isModal())
    check("the card is white with 20 px corners",
          "background: #FFFFFF" in d.card.styleSheet() and "border-radius: 20px" in d.card.styleSheet(),
          d.card.styleSheet())
    check("warn: orange border", "#E8890C" in d.card.styleSheet())
    dd = ui.AppDialog(win, "A recording is still running", "…", tone="danger")
    check("danger: red border and red title",
          dt.record_red() in dd.card.styleSheet() and dt.error_color() in dd.title_label.styleSheet())
    check("title and text shown", d.title_label.text() == "Stop the recording now?"
          and d.text_label.text() == "Round 2 isn’t finished.")
    first = d.button_row.itemAt(0)
    check("buttons sit on the right (a stretch comes first)",
          first is not None and first.spacerItem() is not None)
    check("button order as given", d.button_texts() == ["Keep recording", "Stop now"])
    d.show(); _app.processEvents()
    check("it covers the window it belongs to", d.geometry() == win.geometry(),
          f"{d.geometry()} vs {win.geometry()}")
    d.set_cancel_result(0)
    d.keyPressEvent(QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier))
    check("Esc closes with the cancel answer", d.result() == 0 and not d.isVisible())
    d2 = ui.AppDialog(win, "x", "y")
    b = d2.add_button("Go", 7, "primary")
    d2.show(); b.click(); _app.processEvents()
    check("a button closes with its own result", d2.result() == 7)
    # ask / tell through the test responder
    ui.answered_dialogs.clear()
    ui.dialog_responder = lambda _d: 1
    try:
        yes = ui.ask(win, "Leave?", "The cameras disconnect.", "Leave", "Stay")
        ui.tell(win, "Not enough disk space", "Free up space.", tone="danger")
    finally:
        ui.dialog_responder = None
    check("ask() returns the answer", yes is True)
    check("ask/tell record what was asked",
          [a["title"] for a in ui.answered_dialogs] == ["Leave?", "Not enough disk space"]
          and ui.answered_dialogs[0]["buttons"] == ["Stay", "Leave"]
          and ui.answered_dialogs[1]["tone"] == "danger", str(ui.answered_dialogs))
    ui.answered_dialogs.clear()
    ui.dialog_responder = lambda _d: 0
    try:
        ui.ask(win, "Stop?", "…", "Stop now", "Keep recording", safe_first=True)
    finally:
        ui.dialog_responder = None
    check("safe_first: the safe answer comes first (primary)",
          ui.answered_dialogs[0]["buttons"] == ["Keep recording", "Stop now"])
    win.close()


# ─── 4. Dropdown ───────────────────────────────────────────────────────────────

def test_dropdown():
    d = ui.Dropdown([("Side camera 1", 1), ("Side camera 2", 2)])
    qss = d.styleSheet()
    check("dropdown: scoped stylesheet (never leaks)", f"QComboBox#{d.objectName()}" in qss)
    check("dropdown: light field, rounded, Inter",
          dt.surface() in qss and "border-radius" in qss and dt.FONT_UI in qss)
    check("dropdown: blue border when focused or open", f"border: 1px solid {dt.accent()}" in qss)
    check("dropdown: its own list popup, not the system menu", "combobox-popup: 0" in qss)
    check("dropdown: items keep their data", d.itemData(1) == 2 and d.itemText(0) == "Side camera 1")
    d.resize(220, 40)
    img = d.grab().toImage()
    c = QColor(img.pixel(img.width() - 60, 8))     # inside the field, clear of text and chevron
    check("dropdown: paints a light field", c.lightness() > 200, c.name())


# ─── 5. light even in dark mode ────────────────────────────────────────────────

def _dark_palette():
    p = QPalette()
    for role in (QPalette.ColorRole.Window, QPalette.ColorRole.Base, QPalette.ColorRole.Button,
                 QPalette.ColorRole.AlternateBase):
        p.setColor(role, QColor("#1E1E1E"))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText,
                 QPalette.ColorRole.ButtonText):
        p.setColor(role, QColor("#EEEEEE"))
    return p


def _centre_lightness(w):
    w.resize(max(w.width(), 240), max(w.height(), 60))
    img = w.grab().toImage()
    return QColor(img.pixel(img.width() // 2, img.height() // 2)).lightness()


def test_light_in_dark_mode():
    old_pal, old_qss = _app.palette(), _app.styleSheet()
    try:
        _app.setPalette(_dark_palette())
        dt.pin_light_palette(_app)
        _app.setStyleSheet(dt.qss_app())
        check("pinned palette: text fields are white",
              _app.palette().color(QPalette.ColorRole.Base).name().upper() == dt.surface().upper())
        check("pinned palette: text is dark",
              _app.palette().color(QPalette.ColorRole.Text).lightness() < 80)
        for cls in (QTextEdit, QLineEdit):
            w = cls(); w.show(); _app.processEvents()
            check(f"an unstyled {cls.__name__} is light in dark mode", _centre_lightness(w) > 200)
            w.close()
        # Review › Custom note
        from brain_battle.ui.review_label_screen import ReviewLabelScreen
        rv = ReviewLabelScreen()
        try:
            note = rv._note_text
            note.show(); _app.processEvents()
            check("Review › Custom note box is light (white field)", _centre_lightness(note) > 200)
            check("Review › Custom note text is dark",
                  dt.text_primary() in note.styleSheet() or
                  note.palette().color(QPalette.ColorRole.Text).lightness() < 80)
            mom = rv._moment_note
            mom.show(); _app.processEvents()
            check("Review › moment note box is light", _centre_lightness(mom) > 200)
        finally:
            rv.shutdown()
        # History drawer
        from brain_battle.ui.sessions_screen import HistoryDrawer
        dr = HistoryDrawer(); dr.resize(440, 600); dr.show(); _app.processEvents()
        img = dr.grab().toImage()
        check("the history drawer is a light panel",
              QColor(img.pixel(220, 400)).lightness() > 200, QColor(img.pixel(220, 400)).name())
        dr.close()
    finally:
        _app.setPalette(old_pal); _app.setStyleSheet(old_qss)


if __name__ == "__main__":
    test_no_native_dialogs()
    test_one_dropdown()
    test_app_dialog()
    test_dropdown()
    test_light_in_dark_mode()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
