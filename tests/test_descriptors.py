"""
Open files ("descriptors") — the regression test for "Too many open files".

macOS starts a program with room for 256 open files, sockets and pipes. Before
3.1.0-dev.3 every visit to New recording leaked 33 of them (the LSL library
keeps 3 per destroyed outlet, and 11 outlets were destroyed and re-made each
time), so a few recordings in a row ran out and manifest / marker writes failed.

This script puts ITSELF under a 256 limit (macOS / Linux) and checks:

  1. Several recordings in a row through one RecordFlow (simulated devices, the
     real capture paths, LSL on): the counts come back to the first visit's
     numbers every time, the peak stays well under 256, and the outlets are
     opened once.
  2. A second, fresh RecordFlow in the same process adds nothing either.
  3. The recording's own log file is closed once recording.xdf is built.
  4. A failed marker / history write is shown on the Recording screen and the
     Saved screen does not claim a clean save; a normal recording does.
  5. The start-up safety net raises a 256 soft limit (in a child process), and
     the counter itself counts files, sockets and pipes correctly.

On Windows there is no such limit to set and no descriptor table to list; the
limit and the exact counts are skipped there and the behaviour checks still run.

Run:  python3 tests/test_descriptors.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))

# The limit goes on BEFORE Qt, pylsl or the app are imported.
LIMIT = 256
try:
    import resource as _resource
    _soft, _hard = _resource.getrlimit(_resource.RLIMIT_NOFILE)
    _resource.setrlimit(_resource.RLIMIT_NOFILE, (LIMIT, _hard))
    HAVE_LIMIT = True
except Exception:                       # Windows
    HAVE_LIMIT = False

import _env  # noqa: F401,E402  (temp settings file + a labeller name; see tests/_env.py)
import logging      # noqa: E402
import os           # noqa: E402
import socket       # noqa: E402
import subprocess   # noqa: E402
import sys          # noqa: E402
import tempfile     # noqa: E402
import time         # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = tempfile.mkdtemp(prefix="bb_fd_cap_")
_env.set_setting("lsl_streams", True)     # the worst case: every live stream open

from PyQt6.QtWidgets import QApplication   # noqa: E402

_app = QApplication.instance() or QApplication(sys.argv)

from brain_battle.support import resource_limits as rl   # noqa: E402
import psutil  # noqa: E402,F401  (thread and child-process counts need it; a declared dependency)

_failures = []
# How far a count may sit from its reference and still be "back to baseline":
# room for a timer or a lazily opened library file, far below one leaked outlet
# set (33) or one leaked recording (20).
TOLERANCE = 6


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def pump(secs):
    t0 = time.time()
    while time.time() - t0 < secs:
        _app.processEvents(); time.sleep(0.02)


def _short_plan(flow):
    flow.goto_setup()
    su = flow._setup_step
    su._st_rest.set_value(2); su._st_round.set_value(10)
    su._st_break.set_value(2); su._st_rounds.set_value(1)
    su._name_a.setText("K7Q2"); su._name_b.setText("M3XA"); su._recompute()


def _record_once(flow, during=None):
    """One whole visit: open → record → Saved → XDF → leave. Returns the
    snapshots taken at each point."""
    snaps = {}
    flow.start(); pump(2.5)
    snaps["opened"] = rl.snapshot()
    _short_plan(flow)
    flow.begin_recording()
    pump(5.0)
    snaps["recording"] = rl.snapshot()
    if during:
        during(flow)
    t0 = time.time()
    while flow.is_recording() and time.time() - t0 < 40:
        pump(0.1)
    pump(0.6)
    flow.wait_for_xdf(30); pump(0.4)
    snaps["saved"] = rl.snapshot()
    snaps["head"] = flow._saved_step._head.text()
    snaps["problems"] = flow.write_problems()
    flow._leave_to_sessions(); pump(1.5)
    snaps["left"] = rl.snapshot()
    return snaps


def _fmt(s):
    return f"fds {s['fds']} threads {s['threads']} children {s['children']}"


# ─── 0. the counter and the limit themselves ──────────────────────────────────

def test_counter():
    if not HAVE_LIMIT:
        print("SKIP  descriptor counting (no descriptor table on this system)")
        return
    check("this script runs under the low limit", rl.open_file_limit()[0] == LIMIT,
          str(rl.open_file_limit()))
    before = rl.count_open_descriptors()
    f = open(os.path.join(tempfile.mkdtemp(), "x"), "w")
    s = socket.socket()
    r, w = os.pipe()
    after = rl.count_open_descriptors()
    check("the counter sees one new file, one socket and one pipe (2 ends)",
          (after["files"] - before["files"], after["sockets"] - before["sockets"],
           after["pipes"] - before["pipes"], after["fds"] - before["fds"]) == (1, 1, 2, 4),
          f"{before} -> {after}")
    f.close(); s.close(); os.close(r); os.close(w)
    check("…and sees them go", rl.count_open_descriptors()["fds"] == before["fds"])


def test_safety_net():
    if not HAVE_LIMIT:
        check("raise_open_file_limit does nothing, quietly, where there is no limit",
              rl.raise_open_file_limit() == (None, None))
        return
    code = ("import resource, sys; sys.path.insert(0, %r);"
            "from brain_battle.support.resource_limits import raise_open_file_limit as r;"
            "print(r(), resource.getrlimit(resource.RLIMIT_NOFILE)[0])" % ROOT)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         timeout=60).stdout.strip()
    # the child inherits this script's 256
    check("start-up safety net raises a 256 soft limit", out.startswith(f"({LIMIT}, ")
          and int(out.split()[-1]) > LIMIT, out)
    check("…and it is called from the app's main(), not on import",
          rl.open_file_limit()[0] == LIMIT
          and "raise_open_file_limit()" in open(os.path.join(ROOT, "brain_battle", "app.py"),
                                                encoding="utf-8").read())


# ─── 1–3. recordings in a row ─────────────────────────────────────────────────

def test_recordings_in_a_row():
    from brain_battle.ui.record_flow import RecordFlow
    from brain_battle.capture import lsl_outlets
    import brain_battle.support.log_setup as log_setup

    flow = RecordFlow()
    runs = []
    try:
        for n in range(4):
            runs.append(_record_once(flow))
            print(f"      visit {n + 1}: opened [{_fmt(runs[-1]['opened'])}]  recording "
                  f"[{_fmt(runs[-1]['recording'])}]  left [{_fmt(runs[-1]['left'])}]")
            if n == 0:
                outlets_first = lsl_outlets.open_outlet_count()
                check("the recording's own log file is closed once recording.xdf is built",
                      log_setup._session_handler is None
                      and not any(isinstance(h, logging.FileHandler)
                                  for h in logging.getLogger().handlers))
    finally:
        flow.shutdown(); pump(0.4)

    check("every one of the 4 recordings saved cleanly",
          all(r["head"] == "Recording saved" and not r["problems"] for r in runs),
          str([(r["head"], r["problems"]) for r in runs]))
    check("LSL outlets are opened once and reused (11 streams)",
          lsl_outlets.open_outlet_count() == outlets_first == 11,
          f"{outlets_first} -> {lsl_outlets.open_outlet_count()}")
    first = runs[0]
    for key, what in (("opened", "with the flow open"), ("recording", "while recording"),
                      ("left", "after leaving the flow")):
        worst = max(abs(r[key]["fds"] - first[key]["fds"]) for r in runs[1:])
        if HAVE_LIMIT:
            check(f"descriptors {what} are the same on every visit (±{TOLERANCE})",
                  worst <= TOLERANCE, str([r[key]["fds"] for r in runs]))
        # A leak grows on EVERY visit (before 3.1.0-dev.3: +4 a visit). The
        # operating system also starts and retires worker threads of its own
        # (Windows' thread pool), which can show as a one-off +4 in a single
        # snapshot. So: the typical later visit (the median) must be back at
        # the first visit's count, and no single visit may be far above it.
        later = sorted(r[key]["threads"] - first[key]["threads"] for r in runs[1:])
        check(f"threads {what} do not grow from visit to visit",
              later[len(later) // 2] <= 3 and later[-1] <= TOLERANCE,
              str([r[key]["threads"] for r in runs]))
    check("no helper program is left running after a recording",
          all(r["saved"]["children"] == 0 and r["left"]["children"] == 0 for r in runs),
          str([(r["saved"]["children"], r["left"]["children"]) for r in runs]))
    # (no upper bound: on Windows an installed ffmpeg can be a launcher that
    # starts the real one, which doubles the count)
    check("helper programs (the video encoders) run while recording",
          all(r["recording"]["children"] >= 1 for r in runs),
          str([r["recording"]["children"] for r in runs]))
    if HAVE_LIMIT:
        peak = max(r["recording"]["fds"] for r in runs)
        check("a recording with 4 cameras, 2 headbands and LSL peaks under 200 of the 256",
              peak < 200, f"peak {peak}")
        check("nothing is left open by a finished recording (Saved = flow open)",
              all(abs(r["saved"]["fds"] - r["opened"]["fds"]) <= TOLERANCE for r in runs),
              str([(r["opened"]["fds"], r["saved"]["fds"]) for r in runs]))

    # 2. a second, fresh flow (what each test in test_record_flow does)
    flow2 = RecordFlow()
    try:
        again = _record_once(flow2)
    finally:
        flow2.shutdown(); pump(0.4)
    check("a second RecordFlow in the same process opens no new LSL outlets",
          lsl_outlets.open_outlet_count() == 11, str(lsl_outlets.open_outlet_count()))
    if HAVE_LIMIT:
        check("…and holds no more descriptors than the first",
              abs(again["left"]["fds"] - first["left"]["fds"]) <= TOLERANCE
              and abs(again["recording"]["fds"] - first["recording"]["fds"]) <= TOLERANCE,
              f"{_fmt(first['left'])} vs {_fmt(again['left'])}")


# ─── 4. a failed write is visible ─────────────────────────────────────────────

def test_failed_writes_are_visible():
    from brain_battle.ui.record_flow import RecordFlow
    import brain_battle.data.session_history as session_history

    seen = {}

    def break_writes(flow):
        # From now on the marker file and the history cannot be written, as
        # when the process is out of descriptors (errno 24).
        def boom(*a, **k):
            raise OSError(24, "Too many open files")
        flow._layout.append_marker = boom
        seen["orig_log"] = session_history.log_event
        session_history.log_event = lambda *a, **k: False
        seen["banner_before"] = flow._recording_step._write_banner.isVisible()
        flow.stop_and_save(); pump(0.3)
        seen["banner_text"] = flow._recording_step._write_banner.text()
        seen["banner_shown"] = not flow._recording_step._write_banner.isHidden()

    flow = RecordFlow()
    try:
        run = _record_once(flow, during=break_writes)
        session_history.log_event = seen["orig_log"]
        check("no warning is shown while every write succeeds", seen["banner_before"] is False)
        check("a failed marker write shows a warning on the Recording screen",
              seen["banner_shown"] and "round and break times" in seen["banner_text"],
              seen["banner_text"])
        check("the warning is plain language (no error codes or file names)",
              not any(w in seen["banner_text"] for w in ("Errno", "csv", "json", "OSError")))
        check("the failed history write is reported too",
              "the recording's history" in run["problems"], str(run["problems"]))
        check("the Saved screen does not claim a clean save",
              run["head"] == flow._saved_step.HEAD_PROBLEM != "Recording saved", run["head"])

        clean = _record_once(flow)
        check("the next recording starts clean again (no stale warning)",
              clean["head"] == "Recording saved" and clean["problems"] == []
              and flow._recording_step._write_banner.isHidden(),
              str((clean["head"], clean["problems"])))
    finally:
        session_history.log_event = seen.get("orig_log", session_history.log_event)
        flow.shutdown(); pump(0.4)


if __name__ == "__main__":
    test_counter()
    test_safety_net()
    test_recordings_in_a_row()
    test_failed_writes_are_visible()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
