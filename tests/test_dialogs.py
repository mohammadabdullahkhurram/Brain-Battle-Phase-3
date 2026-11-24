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
     answer through ``widgets.dialog_responder`` in tests. ``NameDialog``
     ("Who is labelling?"): focused name field, Save disabled until a name is
     typed, Enter saves (not when empty), Esc cancels, the name is tidied.
  4. ``Dropdown``: light, rounded, Inter, blue focus, its own (non-native)
     popup list.
  5. Light even when the computer is in dark mode: with a dark palette handed
     in, ``pin_light_palette`` + the app stylesheet give white text fields with
     dark text — including Review's Custom note box and the history drawer.
     And the whole start-up: a dark system palette and a native (non-Fusion)
     style simulated, then ``apply_light_style`` exactly as ``app.main()``
     calls it, then the real main window — Fusion, every palette role light in
     every group, and the note box, history drawer, a dropdown's open list, a
     dialog and the Documentation screen all light with dark text; a switch
     to dark mode while running is undone.

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


def test_name_dialog():
    from PyQt6.QtTest import QTest
    win = QWidget(); win.resize(1000, 700); win.show()
    d = ui.NameDialog(win)
    check("name dialog: title + one sentence on why",
          d.title() == "Who is labelling?" and "saved under your name" in d.text())
    check("name dialog: it is the app's dialog (dimmed cover, modal, frameless)",
          isinstance(d, ui.AppDialog) and d.isModal()
          and bool(d.windowFlags() & Qt.WindowType.FramelessWindowHint))
    d.show(); _app.processEvents()
    check("name dialog: the name field has focus", d.focusWidget() is d.name_edit)
    check("name dialog: Save disabled while empty", not d.save_btn.isEnabled())
    QTest.keyClick(d.name_edit, Qt.Key.Key_Return)
    check("name dialog: Enter with no name does not close it", d.isVisible())
    QTest.keyClicks(d.name_edit, "   ")
    check("name dialog: spaces alone are not a name", not d.save_btn.isEnabled())
    QTest.keyClicks(d.name_edit, "Maya   K ")
    check("name dialog: Save enabled once a name is typed", d.save_btn.isEnabled())
    QTest.keyClick(d.name_edit, Qt.Key.Key_Return)
    check("name dialog: Enter saves, name tidied", d.result() == 1 and d.name() == "Maya K"
          and not d.isVisible(), d.name())
    d2 = ui.NameDialog(win, "Maya K"); d2.show(); _app.processEvents()
    QTest.keyClick(d2, Qt.Key.Key_Escape)
    check("name dialog: Esc cancels", d2.result() == 0 and not d2.isVisible())
    ui.dialog_responder = lambda dd: (dd.name_edit.setText(" Noor "), 1)[1]
    try:
        got = ui.ask_name(win, pending=True)
        check("ask_name: the tidied name; pending words the button",
              got == "Noor" and ui.answered_dialogs[-1]["buttons"] == ["Cancel", "Save and add the label"])
        ui.dialog_responder = lambda dd: 0
        check("ask_name: None when cancelled", ui.ask_name(win) is None)
    finally:
        ui.dialog_responder = None
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


def _shades(w, size=None):
    """(share of light pixels, darkest pixel lightness) of a widget's picture."""
    if size:
        w.resize(*size)
    img = w.grab().toImage()
    light = total = 0
    darkest = 255
    step = max(1, min(img.width(), img.height()) // 60)
    for x in range(0, img.width(), step):
        for y in range(0, img.height(), step):
            l = QColor(img.pixel(x, y)).lightness()
            total += 1; light += l > 200; darkest = min(darkest, l)
    return (light / total if total else 0.0), darkest


def test_app_starts_light_in_dark_mode():
    from PyQt6.QtWidgets import QStyleFactory, QAbstractItemView
    old_pal, old_qss, old_style = _app.palette(), _app.styleSheet(), _app.style().name()
    _app.setPalette(_dark_palette())                      # the computer is in dark mode …
    _app.setStyle(QStyleFactory.create("Windows"))        # … and Qt picked a native style
    try:
        dt.apply_light_style(_app)                        # what app.main() does at start-up
        check("start-up: Fusion style, not the native one", dt.base_style_name() == "fusion",
              dt.base_style_name())
        pal, R, G = _app.palette(), QPalette.ColorRole, QPalette.ColorGroup
        bad = [f"{g.name}/{r.name}" for g in (G.Active, G.Inactive)
               for r in (R.Window, R.Base, R.AlternateBase, R.Button, R.Light, R.Midlight)
               if pal.color(g, r).lightness() < 200]
        bad += [f"{g.name}/{r.name}" for g in (G.Active, G.Inactive)
                for r in (R.Text, R.WindowText, R.ButtonText, R.HighlightedText)
                if pal.color(g, r).lightness() > 80]
        check("start-up: every palette role is the light one, active AND inactive", not bad, str(bad))
        check("start-up: tooltips and highlight are the app's, not the system's",
              pal.color(R.ToolTipBase).name().upper() == dt.text_primary().upper()
              and pal.color(R.Highlight).name().upper() == dt.accent_ghost().upper())
        check("start-up: disabled text is grey, not system-dark/white",
              60 < pal.color(G.Disabled, R.Text).lightness() < 200)
        check("start-up: the app stylesheet is on the application (parentless windows too)",
              "QToolTip" in _app.styleSheet())

        import brain_battle.app as bbm
        win = bbm.BrainBattleApp(); win.resize(1280, 860); win.show(); _app.processEvents()
        try:
            note = win._review_page._note_text
            note.setPlainText("B covers up on the ropes"); note.show(); _app.processEvents()
            share, darkest = _shades(note, (360, 110))
            check("dark mode start-up: Review › Custom note box is light with dark text",
                  share > 0.7 and darkest < 110, f"light {share:.0%}, darkest {darkest}")
            dr = win._sessions_page._drawer
            dr.resize(440, 600); dr.show(); _app.processEvents()
            share, darkest = _shades(dr)
            check("dark mode start-up: the history drawer is light with dark text",
                  share > 0.7 and darkest < 110, f"light {share:.0%}, darkest {darkest}")
            dr.hide()
            dd = next(iter(win.findChildren(ui.Dropdown)), None)
            check("dark mode start-up: the window has a dropdown to open", dd is not None)
            if dd is not None:
                if dd.count() < 2:
                    dd.addItems(["first choice", "second choice"])
                dd.show(); dd.showPopup(); _app.processEvents()
                view = dd.view()
                share, darkest = _shades(view, (240, max(80, view.height())))
                check("dark mode start-up: a dropdown's open list is light with dark text",
                      isinstance(view, QAbstractItemView) and share > 0.6 and darkest < 110,
                      f"light {share:.0%}, darkest {darkest}")
                dd.hidePopup()
            d = ui.NameDialog(win, "Maya K"); d.show(); _app.processEvents()
            share, darkest = _shades(d.card)
            check("dark mode start-up: a dialog is light with dark text",
                  share > 0.7 and darkest < 110, f"light {share:.0%}, darkest {darkest}")
            share, darkest = _shades(d.name_edit)
            check("dark mode start-up: the dialog's text field is light with dark text",
                  share > 0.7 and darkest < 110, f"light {share:.0%}, darkest {darkest}")
            d.done(0)
            win._shell.set_current_index(win.IDX_DOCS); _app.processEvents()
            share, darkest = _shades(win._docs_page)
            check("dark mode start-up: the Documentation screen is light with dark text",
                  share > 0.7 and darkest < 110, f"light {share:.0%}, darkest {darkest}")
            # the computer switches to dark mode while the app is open
            # (what Qt does then: the system palette goes dark and colorSchemeChanged
            # fires; the offscreen test platform has no colour scheme of its own)
            _app.setPalette(_dark_palette())
            _app.styleHints().colorSchemeChanged.emit(Qt.ColorScheme.Dark)
            _app.processEvents()
            check("switching the computer to dark mode while running is undone",
                  _app.palette().color(QPalette.ColorRole.Base).lightness() > 200
                  and _app.palette().color(QPalette.ColorRole.Text).lightness() < 80)
        finally:
            win._review_page.shutdown(); win._sessions_page.shutdown()
            win.record_flow.shutdown(); win.close()
    finally:
        _app.setPalette(old_pal); _app.setStyleSheet(old_qss)
        _app.setStyle(QStyleFactory.create(old_style))


if __name__ == "__main__":
    test_no_native_dialogs()
    test_one_dropdown()
    test_app_dialog()
    test_name_dialog()
    test_dropdown()
    test_light_in_dark_mode()
    test_app_starts_light_in_dark_mode()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
