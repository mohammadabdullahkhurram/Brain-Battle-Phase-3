"""
ReviewLabelScreen integration tests — labelling through the real merged screen:
duration flow (incl. Esc cancel), undo/redo, a custom note added by clicking
the real "Add note" button (timeline Notes row, labels file, Activity,
reopen, Undo), keyboard focus rules, autosave +
history events, and the seeded vocabulary file. Playback itself is covered by
test_player.py; here we drive the label side.

The demo is generated ONCE in the __main__ guard (ProcessPool children
re-import this module).

Run:  python3 tests/test_review_tab.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication, QLineEdit, QPushButton

import brain_battle.data.label_vocab as label_vocab
import brain_battle.data.session_history as session_history

_app = QApplication.instance() or QApplication(sys.argv)

_failures = []
DEMO_ROOT = None


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _fresh_tab():
    import brain_battle.ui.review_label_screen as rl
    tab = rl.ReviewLabelScreen()
    tab.load_session_folder(DEMO_ROOT)
    tab.wait_loaded()
    tab._seek(2.0)
    return tab


def _labels_on_disk():
    """This labeller's own file: <recording>/labels/test-labeller.json."""
    import brain_battle.data.labels_store as labels_store
    p = labels_store.labeller_path(DEMO_ROOT, _env.LABELLER)
    if not os.path.isfile(p):
        return []
    return json.loads(open(p, encoding="utf-8").read()).get("labels", [])


def _history_events():
    return [e.get("event") for e in session_history.read_history(DEMO_ROOT)]


def _key(tab, key, mods=Qt.KeyboardModifier.NoModifier):
    return tab.handle_key(key, mods)


def test_fast_open_labels_first():
    """labels.json + the label list + timeline marks are available immediately
    on Open; the (slow) video + brain data load in the background."""
    import brain_battle.ui.review_label_screen as rl
    import brain_battle.data.labels_store as labels_store
    import time as _t
    # seed a few labels on disk so there is something to show at once
    store = labels_store.open_for_labeller(DEMO_ROOT, _env.LABELLER)
    for nm, t0 in (("Jab", 3.0), ("Cross", 5.0), ("Hook", 7.0)):
        store.add(labels_store.new_label(nm, "A", labels_store.POINT, t0, created_by="x"))
    store.save()

    tab = rl.ReviewLabelScreen()
    t0 = _t.perf_counter()
    tab.load_session_folder(DEMO_ROOT)
    elapsed = _t.perf_counter() - t0
    check(f"Open returns fast (<0.5s), labels don't wait on media", elapsed < 0.5,
          f"{elapsed*1000:.0f}ms")
    check("labels present immediately after Open",
          len(tab._store.labels) >= 3, str(len(tab._store.labels)))
    check("timeline has the label marks immediately",
          len(tab._strip.labels) >= 3, str(len(tab._strip.labels)))
    check("media still loading in the background (player not ready yet)",
          tab._player is None)
    ok = tab.wait_loaded()
    check("background media load completes", ok and tab._player is not None)
    tab.shutdown()


def test_vocabulary_seeded():
    # opening a recording seeds the shared label list in its recordings folder
    _fresh_tab().shutdown()
    recordings = os.path.dirname(os.path.abspath(DEMO_ROOT))
    check("label_list.json seeded in the recordings folder",
          os.path.isfile(label_vocab.path(recordings)))


def test_point_label_autosave_and_history():
    tab = _fresh_tab()
    before = len(_labels_on_disk())
    _key(tab, Qt.Key.Key_1)          # Jab = key 1 (point)
    labs = _labels_on_disk()
    check("point label placed + autosaved", len(labs) == before + 1, f"{len(labs)}")
    check("placed label is on t_session axis near the playhead",
          abs(labs[-1]["t_start"] - 2.0) < 0.05, str(labs[-1]["t_start"]))
    check("labels_added history event written",
          "labels_added" in _history_events())
    tab.shutdown()


def test_duration_flow_and_cancel():
    tab = _fresh_tab()
    start = len(tab._store.labels)
    # Clinch is key 6, a duration label: first press starts, second ends
    tab._seek(3.0); _key(tab, Qt.Key.Key_6)
    check("duration first press starts a pending label (nothing committed)",
          len(tab._store.labels) == start and tab._pending_duration is not None)
    tab._seek(5.0); _key(tab, Qt.Key.Key_6)
    labs = tab._store.labels
    check("duration second press commits ONE label with t_end",
          len(labs) == start + 1 and labs[-1]["t_end"] is not None
          and abs(labs[-1]["t_start"] - 3.0) < 0.05
          and abs(labs[-1]["t_end"] - 5.0) < 0.05, str(labs[-1]))
    # Esc cancels a pending duration
    tab._seek(6.0); _key(tab, Qt.Key.Key_6)
    check("another duration pending", tab._pending_duration is not None)
    _key(tab, Qt.Key.Key_Escape)
    check("Esc cancels the pending duration (no new label)",
          tab._pending_duration is None and len(tab._store.labels) == start + 1)
    tab.shutdown()


def test_undo_redo_through_tab():
    tab = _fresh_tab()
    n0 = len(tab._store.labels)
    _key(tab, Qt.Key.Key_2)          # Cross (point)
    check("label added", len(tab._store.labels) == n0 + 1)
    ctrl = Qt.KeyboardModifier.ControlModifier
    _key(tab, Qt.Key.Key_Z, ctrl)    # undo
    check("Ctrl+Z undoes the add", len(tab._store.labels) == n0)
    check("undo autosaved to disk", len(_labels_on_disk()) == n0)
    _key(tab, Qt.Key.Key_Z, ctrl | Qt.KeyboardModifier.ShiftModifier)  # redo
    check("Ctrl+Shift+Z redoes", len(tab._store.labels) == n0 + 1)
    tab.shutdown()


def test_tab_cycles_boxer():
    tab = _fresh_tab()
    tab._set_who("A")
    _key(tab, Qt.Key.Key_Tab); check("Tab A→B", tab._who == "B")
    _key(tab, Qt.Key.Key_Tab); check("Tab B→Both", tab._who == "Both")
    _key(tab, Qt.Key.Key_Tab); check("Tab Both→A", tab._who == "A")
    tab.shutdown()


def test_shortcuts_blocked_while_typing():
    tab = _fresh_tab()
    n0 = len(tab._store.labels)
    # simulate a text field having focus
    tab._focus_getter = lambda: QLineEdit()
    consumed = _key(tab, Qt.Key.Key_1)
    check("digit key NOT consumed while typing in a text field", consumed is False)
    check("no label placed while typing", len(tab._store.labels) == n0)
    # a non-text focus → shortcuts work again
    tab._focus_getter = lambda: QPushButton()
    consumed = _key(tab, Qt.Key.Key_1)
    check("digit key works when focus is not a text field", consumed is True
          and len(tab._store.labels) == n0 + 1)
    tab.shutdown()


def _click(button):
    """Click a real button with the mouse; return any exception its slot
    raised (unhandled, PyQt would abort the whole script and lose its output)."""
    from PyQt6.QtTest import QTest
    raised = []
    old_hook, sys.excepthook = sys.excepthook, lambda *a: raised.append(a[1])
    try:
        QTest.mouseClick(button, Qt.MouseButton.LeftButton)
    finally:
        sys.excepthook = old_hook
    return raised


def test_custom_note_through_the_real_button():
    """Custom note → type → click the real "Add note" button. (The button's
    clicked(bool) once reached add_note's ``text`` argument, so clicking it
    raised and added nothing; this drives the button, not the store.)"""
    from PyQt6.QtTest import QTest
    import brain_battle.data.labels_store as labels_store
    tab = _fresh_tab()
    tab._seek(4.0)
    t_play = tab._current_t()
    n0 = len(tab._store.labels)
    tab.start_note()
    check("Custom note mode is shown", tab._mode_stack.currentIndex() == 1)
    check("Add note disabled while the box is empty", not tab._note_save.isEnabled())
    QTest.keyClicks(tab._note_text, "B covers up on the ropes")
    check("Add note enabled once text is typed", tab._note_save.isEnabled())
    raised = _click(tab._note_save)
    check("clicking Add note raises nothing", not raised, repr(raised[:1]))
    notes = [l for l in tab._store.labels if labels_store.is_note(l)]
    check("clicking Add note adds exactly one note", len(tab._store.labels) == n0 + 1
          and len(notes) == 1, str(len(tab._store.labels) - n0))
    note = notes[-1] if notes else {}
    check("the note sits at the playhead time", abs(note.get("t_start", -9) - t_play) < 0.05,
          f"{note.get('t_start')} vs {t_play}")
    check("the panel goes back to labelling and the box is empty",
          tab._mode_stack.currentIndex() == 0 and not tab._note_text.toPlainText())
    # on the timeline's Notes row (painted, so it can be clicked)
    tab._strip.resize(900, 200); tab._strip.grab()
    check("the note is drawn on the timeline's Notes row",
          any(kind == "note" and key == note.get("id") for _r, kind, key in tab._strip._hits))
    # saved to this labeller's own file with type/who/time/text
    disk = [l for l in _labels_on_disk() if l.get("id") == note.get("id")]
    d = disk[0] if disk else {}
    check("saved to the labeller's labels file", bool(disk))
    check("saved with type note, who, time and text",
          d.get("type") == "note" and d.get("who") == tab._who
          and abs(d.get("t_start", -9) - t_play) < 0.05
          and d.get("text") == "B covers up on the ropes"
          and d.get("created_by") == _env.LABELLER, str(d))
    # Activity / history
    tab._on_panel_tab("activity")
    check("Activity shows the note", any("added a note" in line for line in tab.activity_lines()),
          str(tab.activity_lines()[:2]))
    tab.shutdown()
    # survives closing and reopening
    tab2 = _fresh_tab()
    again = tab2._store.by_id(note.get("id"))
    check("the note is still there after closing and reopening",
          again is not None and again.get("text") == "B covers up on the ropes")
    # undoable with the real Undo button
    tab2.start_note(); QTest.keyClicks(tab2._note_text, "second note")
    _click(tab2._note_save)
    n1 = len(tab2._store.labels)
    _click(tab2._undo_btn)
    check("Undo removes the note just added", len(tab2._store.labels) == n1 - 1
          and not any(l.get("text") == "second note" for l in tab2._store.labels))
    check("the undo is saved to disk",
          not any(l.get("text") == "second note" for l in _labels_on_disk()))
    tab2.shutdown()


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    tmp = tempfile.mkdtemp(prefix="bb_reviewtab_")
    DEMO_ROOT = generate_demo_session(out=tmp, rounds=1, round_sec=10,
                                      break_sec=3, baseline_sec=3, seed=5,
                                      boxer_a="K7Q2", boxer_b="M3XA")["root"]
    test_fast_open_labels_first()
    test_vocabulary_seeded()
    test_point_label_autosave_and_history()
    test_duration_flow_and_cancel()
    test_undo_redo_through_tab()
    test_tab_cycles_boxer()
    test_shortcuts_blocked_while_typing()
    test_custom_note_through_the_real_button()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
