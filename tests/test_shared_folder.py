"""
A recordings folder that several computers share.

  1. atomic_write: a reader never sees half a file, even while many writers
     replace it; no temporary files are left behind.
  2. FolderLock: read-change-write edits from several processes all survive
     (the manifest, the team label list); a lock left by a crashed computer is
     taken over; a folder where no lock can be made still works.
  3. Labels: several PROCESSES labelling the same recording at once — each as
     a different labeller — lose nothing, and neither do several processes
     appending to the same history file.
  4. Two computers building / exporting recording.xdf at once don't collide.
  5. A folder that is not there (drive unplugged): folder_status says so, the
     Sessions screen shows a clear message with Try again instead of an empty
     list, Review & Label keeps unsaved labels and retries, and a recording
     cannot be started into it.
  6. The Sessions scan re-reads only recordings that changed.

Run:  python3 tests/test_shared_folder.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from PyQt6.QtWidgets import QApplication

import brain_battle.data.labels_store as ls
import brain_battle.data.label_vocab as lv
import brain_battle.data.safe_io as safe_io
import brain_battle.data.session_history as sh
import brain_battle.data.session_storage as ss

_app = QApplication.instance() or QApplication(sys.argv)
_failures = []
DEMO_ROOT = None
ROOT = _env.ROOT


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _spawn(code, *args):
    """Run a snippet in a separate Python process (a stand-in for another
    computer using the same folder)."""
    return subprocess.Popen([sys.executable, "-c", code, *map(str, args)], cwd=ROOT,
                            env=dict(os.environ, PYTHONPATH=ROOT),
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def _wait(procs):
    errs = []
    for p in procs:
        _out, err = p.communicate(timeout=120)
        if p.returncode != 0:
            errs.append(err.decode("utf-8", "replace")[-400:])
    return errs


def test_atomic_write():
    d = tempfile.mkdtemp(prefix="bb_atomic_")
    path = os.path.join(d, "data.json")
    safe_io.atomic_write_json(path, {"n": 0, "pad": "x" * 20000})
    stop = threading.Event()
    bad = []

    def reader():
        while not stop.is_set():
            got = safe_io.read_json(path, default="MISSING")
            if not (isinstance(got, dict) and len(got.get("pad", "")) == 20000):
                bad.append(got if got == "MISSING" else "partial")

    th = threading.Thread(target=reader); th.start()
    for i in range(200):
        safe_io.atomic_write_json(path, {"n": i, "pad": "x" * 20000})
    stop.set(); th.join()
    check("a reader never sees a missing or half-written file while it is being replaced",
          bad == [], f"{len(bad)} bad reads")
    check("no temporary files are left behind", os.listdir(d) == ["data.json"], str(os.listdir(d)))
    check("read_json: missing → default, garbage → default",
          safe_io.read_json(os.path.join(d, "nope.json"), 7) == 7
          and (open(os.path.join(d, "bad.json"), "w").write("{oops") and
               safe_io.read_json(os.path.join(d, "bad.json"), 8) == 8))
    try:
        safe_io.atomic_write_text(os.path.join(d, "no", "such", "dir", "f.txt"), "x")
        raised = False
    except OSError:
        raised = True
    check("writing into a folder that is not there raises OSError (so the UI can say so)", raised)


_BUMP = r"""
import sys
import brain_battle.data.safe_io as safe_io
path, n = sys.argv[1], int(sys.argv[2])
for _ in range(n):
    safe_io.update_json(path, lambda d: d.__setitem__("count", d["count"] + 1))
"""


def test_lock_and_update():
    d = tempfile.mkdtemp(prefix="bb_lock_")
    path = os.path.join(d, "counter.json")
    safe_io.atomic_write_json(path, {"count": 0})
    errs = _wait([_spawn(_BUMP, path, 40) for _ in range(4)])
    got = safe_io.read_json(path)["count"]
    check("4 processes × 40 read-change-write edits: none lost", not errs and got == 160,
          f"{got} {errs[:1]}")
    check("the lock file is gone afterwards", not os.path.exists(path + ".lock"))

    # a lock left behind by a computer that crashed
    open(path + ".lock", "w").write("dead-pc pid 1\n")
    old = time.time() - 120
    os.utime(path + ".lock", (old, old))
    t0 = time.monotonic()
    with safe_io.FolderLock(path, timeout=3.0, stale_after=20.0) as lock:
        took = time.monotonic() - t0
        check("a stale lock is taken over promptly", lock.acquired and took < 1.0, f"{took:.2f}s")
    # a fresh lock held by someone else: wait, then carry on without it
    open(path + ".lock", "w").write("busy-pc pid 2\n")
    t0 = time.monotonic()
    with safe_io.FolderLock(path, timeout=0.4) as lock:
        check("a held lock is waited for, then the edit proceeds anyway (never hangs)",
              not lock.acquired and 0.3 < time.monotonic() - t0 < 2.0)
    check("…and someone else's lock is not removed", os.path.exists(path + ".lock"))
    os.unlink(path + ".lock")

    # the manifest and the team label list go through the same path
    root = str(ss.create_session(d, ss.SessionConfig() if hasattr(ss, "SessionConfig")
                                 else __import__("brain_battle.capture.session_controller",
                                                 fromlist=["SessionConfig"]).SessionConfig()).root)
    check("manifest edits are atomic + locked (dismiss, interrupted)",
          ss.dismiss_interrupted(root) and ss.mark_session_interrupted(root)
          and ss._read_manifest(root)["interrupted_dismissed"] is True
          and ss._read_manifest(root)["interrupted"] is True
          and sorted(os.listdir(root)) == sorted(set(os.listdir(root)))
          and not [f for f in os.listdir(root) if f.endswith((".tmp", ".lock"))])
    lv.save(d, lv.DEFAULT_LABELS)
    check("the team label list is written without leftovers",
          len(lv.load(d)) == len(lv.DEFAULT_LABELS)
          and not [f for f in os.listdir(d) if ".tmp" in f or f.endswith(".lock")], str(os.listdir(d)))


_LABELLER = r"""
import sys
import brain_battle.data.labels_store as ls
import brain_battle.data.session_history as sh
root, name, n = sys.argv[1], sys.argv[2], int(sys.argv[3])
store = ls.open_for_labeller(root, name)
for i in range(n):
    lab = ls.new_label("Jab", "A", ls.POINT, float(i), created_by=name)
    store.add(lab); store.save()
    sh.log_event(root, sh.EVENT_LABELS_ADDED, {"label": "Jab", "labeller": name, "i": i}, user=name)
"""


def test_concurrent_labellers():
    root = tempfile.mkdtemp(prefix="bb_conc_")
    # an old single-file labels.json is there too: every process migrates it at once
    json.dump({"schema_version": 2, "labels": [ls.new_label("Hook", "B", ls.POINT, 99.0,
                                                             created_by="old")]},
              open(os.path.join(root, "labels.json"), "w", encoding="utf-8"))
    names = ["Maya K", "Jonas R", "Noor", "Dana W", "Eli"]
    N = 25
    errs = _wait([_spawn(_LABELLER, root, n, N) for n in names])
    counts = ls.labeller_counts(root)
    check("5 processes labelling the same recording at once: every label of every labeller is there",
          not errs and all(counts.get(n) == N for n in names), f"{counts} {errs[:1]}")
    check("the legacy file was migrated exactly once, with nothing lost or doubled",
          counts.get("old") == 1 and not os.path.exists(os.path.join(root, "labels.json"))
          and os.path.isfile(os.path.join(root, ls.LEGACY_BACKUP)), str(counts))
    for n in names:
        data = json.load(open(ls.labeller_path(root, n), encoding="utf-8"))
        if data["labeller"] != n or len(data["labels"]) != N:
            check(f"{n}'s file is intact", False)
    events = [e for e in sh.read_history(root) if e["event"] == "labels_added"]
    raw = open(os.path.join(root, "history.jsonl"), encoding="utf-8").read().splitlines()
    check("the shared history has every line from every process, none torn",
          len(events) == len(names) * N and len(raw) == len(names) * N
          and all(l.startswith("{") and l.endswith("}") for l in raw),
          f"{len(events)} events / {len(raw)} lines")
    check("history says who did what",
          {e["user"] for e in events} == set(names)
          and all(e["details"]["labeller"] == e["user"] for e in events))
    check("no temporary or lock files left in the recording",
          not [f for dp, _d, fs in os.walk(root) for f in fs if ".tmp" in f or f.endswith(".lock")])


_XDF = r"""
import sys
import brain_battle.data.session_xdf as session_xdf
for _ in range(3):
    assert session_xdf.build_session_xdf(sys.argv[1])
"""


def test_concurrent_xdf():
    errs = _wait([_spawn(_XDF, DEMO_ROOT) for _ in range(3)])
    import pyxdf
    streams, _ = pyxdf.load_xdf(os.path.join(DEMO_ROOT, "recording.xdf"))
    check("3 processes rebuilding recording.xdf at once leave one valid file and no leftovers",
          not errs and len(streams) >= 4
          and not [f for f in os.listdir(DEMO_ROOT) if ".tmp" in f], f"{errs[:1]}")


def test_folder_missing():
    gone = os.path.join(tempfile.mkdtemp(prefix="bb_gone_"), "Shared Drive", "Recordings")
    check("folder_status: missing / ok",
          safe_io.folder_status(gone) == safe_io.FOLDER_MISSING
          and safe_io.folder_status(tempfile.mkdtemp()) == safe_io.FOLDER_OK
          and safe_io.folder_status("") == safe_io.FOLDER_MISSING)
    text = safe_io.folder_problem_text(gone, safe_io.FOLDER_MISSING)
    check("the message says what happened and what to do",
          "can't be reached" in text and "Try again" in text and "Settings" in text and gone in text)
    check("is_network_path never raises and is False for a local temp folder",
          safe_io.is_network_path(tempfile.gettempdir()) is False
          and safe_io.is_network_path(gone) in (False, True))

    import brain_battle.ui.sessions_screen as st
    page = st.SessionsScreen(gone)
    page.refresh()
    deadline = time.time() + 10
    while not page._scanned_once and time.time() < deadline:
        _app.processEvents(); time.sleep(0.01)
    check("Sessions: an unreachable folder shows a clear message with Try again, not just "
          "an empty list",
          not page._folder_banner.isHidden() and "can't be reached" in page._folder_banner.text()
          and "can't be reached" in page._empty_title.text() and page._empty_btn.isHidden(),
          page._folder_banner.text()[:80])
    os.makedirs(gone)
    page.refresh()
    deadline = time.time() + 10
    while page._folder_state != safe_io.FOLDER_OK and time.time() < deadline:
        _app.processEvents(); time.sleep(0.01)
    check("…and it clears when the folder is back", page._folder_banner.isHidden()
          and page._empty_title.text() == "No recordings yet")
    page.shutdown()

    from brain_battle.ui.sidebar_shell import SidebarShell
    shell = SidebarShell(capture_dir=os.path.join(gone, "nope"))
    check("the sidebar's folder card says the folder can't be reached",
          not shell.folder_reachable() and "Can't be reached" in shell._free_label.text())


def test_review_keeps_unsaved_labels():
    import brain_battle.ui.review_label_screen as rl
    base = tempfile.mkdtemp(prefix="bb_drive_")
    root = os.path.join(base, os.path.basename(DEMO_ROOT))
    shutil.copytree(DEMO_ROOT, root)
    tab = rl.ReviewLabelScreen()
    try:
        tab.load_session_folder(root); tab.wait_loaded()
        tab._place_from_vocab(tab._vocab[0])
        check("labels save normally", not tab.has_unsaved_labels() and tab._autosave.kind() == "ok")
        parked = root + ".unplugged"
        tab.shutdown()                       # release the video files (Windows locks open files)
        os.rename(root, parked)              # the drive "goes away"
        tab._root = root
        tab._seek(3.0); tab._place_from_vocab(tab._vocab[1])
        check("when the folder disappears the label is kept in memory and the screen says "
              "it is not saved",
              tab.has_unsaved_labels() and len(tab._store) == 2 and tab._autosave.kind() == "bad"
              and "Not saved" in tab._autosave.text() and tab._retry_timer.isActive(),
              tab._autosave.text()[:60])
        os.rename(parked, root)              # … and comes back
        tab._retry_save()
        on_disk = ls.read_all(root)[_env.LABELLER]
        check("when it comes back the retry saves everything",
              not tab.has_unsaved_labels() and len(on_disk) == 2 and tab._autosave.kind() == "ok"
              and not tab._retry_timer.isActive())
    finally:
        tab.shutdown()


def test_cannot_record_into_missing_folder():
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    gone_parent = tempfile.mkdtemp(prefix="bb_ro_")
    blocker = os.path.join(gone_parent, "not-a-folder")
    open(blocker, "w").write("x")                       # a FILE where the folder should be
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = os.path.join(blocker, "Recordings")
    try:
        import brain_battle.ui.record_flow as rf
        shown = []
        real = rf.QMessageBox.critical
        rf.QMessageBox.critical = staticmethod(lambda _p, title, text, *a, **k: shown.append((title, text)))
        flow = rf.RecordFlow()
        try:
            flow.start()
            check("Get ready says the folder can't be used and Start is off",
                  not flow._setup_step._start.isEnabled()
                  and flow._setup_step._disk.kind() == "bad", flow._setup_step._disk.text()[:70])
            flow.begin_recording()
            check("pressing Start anyway explains instead of recording",
                  not flow.is_recording() and shown and "recordings folder" in shown[0][0].lower(),
                  str(shown[:1])[:120])
        finally:
            flow.shutdown()
            rf.QMessageBox.critical = real
    finally:
        os.environ.pop("BRAIN_BATTLE_CAPTURE_DIR", None)
        os.environ.pop("BRAIN_BATTLE_SIMULATE", None)


def test_scan_cache():
    base = tempfile.mkdtemp(prefix="bb_scan_")
    from brain_battle.capture.session_controller import SessionConfig
    import datetime as dt
    for i in range(40):
        ss.create_session(base, SessionConfig(), started_at=dt.datetime(2026, 1, 1, 9, i),
                          boxer_a=f"A{i}", boxer_b=f"B{i}")
    calls = {"n": 0}
    real = ss.session_info

    def counting(root):
        calls["n"] += 1
        return real(root)

    ss.session_info = counting
    try:
        first = ss.list_sessions(base); n_first = calls["n"]
        second = ss.list_sessions(base); n_second = calls["n"] - n_first
        check("the first scan reads every recording, a repeat scan reads none",
              len(first) == 40 and n_first == 40 and n_second == 0 and len(second) == 40,
              f"{n_first} then {n_second}")
        store = ls.open_for_labeller(first[0].root, "Maya K")
        store.add(ls.new_label("Jab", "A", ls.POINT, 1.0)); store.save()
        third = ls.labeller_counts(first[0].root)
        rows = ss.list_sessions(base)
        check("…and after one recording changes, only that one is re-read",
              calls["n"] - n_first - n_second == 1 and third == {"Maya K": 1}
              and next(r for r in rows if r.root == first[0].root).labels_count == 1,
              str(calls["n"] - n_first - n_second))
    finally:
        ss.session_info = real


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    DEMO_ROOT = generate_demo_session(out=tempfile.mkdtemp(prefix="bb_shared_"), rounds=1,
                                      round_sec=6, break_sec=2, baseline_sec=2, seed=3,
                                      boxer_a="Ivy", boxer_b="Zoe")["root"]
    test_atomic_write()
    test_lock_and_update()
    test_concurrent_labellers()
    test_concurrent_xdf()
    test_folder_missing()
    test_review_keeps_unsaved_labels()
    test_cannot_record_into_missing_folder()
    test_scan_cache()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
