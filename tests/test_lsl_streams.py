"""
Live LSL stream tests — what LabRecorder or an LSL viewer would see.

Opens the app's own outlets (capture/lsl_outlets.py), then subscribes to them
with ordinary pylsl inlets on the same computer and checks: the stream
descriptions (names, types, channel labels, units, rates), that samples arrive
intact, and that their timestamps are the capture clock converted to LSL time.
Also: a simulated headband publishes through the real path, and a disabled
publisher is silent.

Run:  python3 tests/test_lsl_streams.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import os
import sys
import tempfile
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")

import brain_battle.capture.clock as clock
from brain_battle.capture.lsl_outlets import LslPublisher

import pylsl

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _inlet(name, pub=None):
    """An open inlet on this script's own stream ``name`` (never another
    script's stream of the same name — see tests/_env.py). With the publisher,
    also wait until its outlet has seen the inlet subscribe."""
    outlet = pub._outlets.get(name) if pub is not None else None
    return _env.lsl_inlet(name, outlet=outlet)


def _pull_all(inlet, wait=1.0):
    time.sleep(wait)
    data, ts = inlet.pull_chunk(timeout=0.5)
    corr = inlet.time_correction(timeout=2.0)
    return data, np.asarray(ts) + corr


def test_publisher_round_trip():
    pub = LslPublisher()
    check("publisher is active (pylsl loaded)", pub.active)
    now_u = clock.unix_now()
    ts = now_u + np.arange(24) / 256
    data = np.arange(24 * 5, dtype=np.float32).reshape(24, 5)
    pub.push_eeg("A", data[:1], ts[:1], device="MuseS-7538")     # creates the outlet
    pub.open_markers(); pub.open_video("SIDE_01")
    pub.push_sensor("ACC", "A", np.zeros((1, 3), np.float32), ts[:1])

    eeg = _inlet("BrainBattle_EEG_A", pub)
    check("EEG stream is discoverable on the network", eeg is not None)
    info = eeg.info()
    labels, ch = [], info.desc().child("channels").child("channel")
    while not ch.empty():
        labels.append((ch.child_value("label"), ch.child_value("unit"), ch.child_value("type")))
        ch = ch.next_sibling()
    check("EEG stream: type EEG, 5 channels, 256 Hz, float32",
          info.type() == "EEG" and info.channel_count() == 5 and info.nominal_srate() == 256
          and info.channel_format() == pylsl.cf_float32)
    check("EEG stream: standard channel labels in microvolts",
          labels == [(c, "microvolts", "EEG") for c in ("TP9", "AF7", "AF8", "TP10", "AUX")],
          str(labels))
    check("EEG stream names its boxer and headband",
          info.desc().child_value("boxer") == "A"
          and info.desc().child_value("headband") == "MuseS-7538")

    pub.push_eeg("A", data, ts)
    got, got_ts = _pull_all(eeg)
    check("EEG samples arrive intact", np.array_equal(np.asarray(got, dtype=np.float32), data),
          str(np.shape(got)))
    want = np.array([clock.unix_to_lsl(t) for t in ts])
    check("EEG timestamps = capture clock converted to LSL time (< 1 ms)",
          len(got_ts) == 24 and np.abs(got_ts - want).max() < 1e-3,
          f"{np.abs(got_ts - want).max() * 1e3:.3f} ms" if len(got_ts) == 24 else "")

    mk, vid = _inlet("BrainBattle_Markers", pub), _inlet("BrainBattle_Video_SIDE_01", pub)
    check("marker + video streams are discoverable",
          mk is not None and vid is not None and mk.info().type() == "Markers"
          and vid.info().type() == "VideoFrames" and vid.info().nominal_srate() == 0)
    t_m = clock.monotonic()
    pub.push_marker("ROUND_1_START", t_m)
    for i in range(5):
        pub.push_frame("SIDE_01", i, t_m + i / 30)
    m, m_ts = _pull_all(mk)
    f, f_ts = _pull_all(vid, wait=0.2)
    check("marker arrives with its text at the right LSL time",
          [r[0] for r in m] == ["ROUND_1_START"] and abs(m_ts[0] - clock.mono_to_lsl(t_m)) < 1e-3)
    check("video frames arrive as frame numbers at their capture times",
          [r[0] for r in f] == [0, 1, 2, 3, 4]
          and np.abs(f_ts - (clock.mono_to_lsl(t_m) + np.arange(5) / 30)).max() < 1e-3)
    acc = _env.lsl_resolve("BrainBattle_ACC_A")
    check("movement stream: type ACC, 3 channels, 52 Hz",
          acc is not None and acc.type() == "ACC" and acc.channel_count() == 3
          and acc.nominal_srate() == 52)
    pub.close()
    check("closing the publisher stops it publishing", pub.stream_names() == [] and not pub.active)
    pub.push_marker("AFTER_CLOSE", clock.monotonic())        # must be a silent no-op
    check("a closed publisher pushes nothing", _pull_all(mk, wait=0.2)[0] == [])


def test_outlets_are_opened_once():
    """The LSL library keeps 3 descriptors for every outlet it destroys, so the
    app opens each outlet once per run and reuses it (ADR-0022)."""
    from brain_battle.capture import lsl_outlets
    from brain_battle.support import resource_limits as rl
    n0 = lsl_outlets.open_outlet_count()
    check("the outlets of a closed publisher stay open for the next one", n0 == 4, str(n0))
    ts = np.array([clock.unix_now()])
    before = rl.count_open_descriptors()["fds"]
    held = None
    for _ in range(5):
        pub = LslPublisher()
        pub.open_markers(); pub.open_video("SIDE_01")
        pub.push_eeg("A", np.zeros((1, 5), np.float32), ts, device="MuseS-7538")
        pub.push_sensor("ACC", "A", np.zeros((1, 3), np.float32), ts)
        same = held is None or held is pub._outlets["BrainBattle_EEG_A"]
        held = pub._outlets["BrainBattle_EEG_A"]
        pub.close()
    after = rl.count_open_descriptors()["fds"]
    check("five more publishers reuse the same four outlets",
          lsl_outlets.open_outlet_count() == n0 and same)
    check("…and use no more open files", after <= before, f"{before} -> {after}")
    pub = LslPublisher()
    pub.open_markers()
    mk = _inlet("BrainBattle_Markers", pub)
    t_m = clock.monotonic()
    pub.push_marker("REUSED", t_m)
    m, m_ts = _pull_all(mk)
    check("a reused outlet still delivers, at the right time",
          [r[0] for r in m] == ["REUSED"] and abs(m_ts[0] - clock.mono_to_lsl(t_m)) < 1e-3)
    # another headband on the same boxer: the stream's description must follow
    held = None; pub.close()    # nothing may keep the old outlet alive
    import gc; gc.collect()
    pub = LslPublisher()
    pub.push_eeg("A", np.zeros((1, 5), np.float32), ts, device="MuseS-0001")
    # The replaced stream may still be listed for a moment: wait until this
    # script's stream that names the new headband shows up.
    from brain_battle.capture.lsl_outlets import source_id
    names, deadline = [], time.monotonic() + 10
    while "MuseS-0001" not in names and time.monotonic() < deadline:
        names = []
        for found in pylsl.resolve_bypred(
                f"name='BrainBattle_EEG_A' and source_id='{source_id('BrainBattle_EEG_A')}'", 1, 2.0):
            try:
                names.append(pylsl.StreamInlet(found).info(timeout=4.0).desc().child_value("headband"))
            except RuntimeError:
                pass            # the old stream went away while we asked
    check("a different headband gets a stream that names it",
          "MuseS-0001" in names and lsl_outlets.open_outlet_count() == n0, str(names))
    pub.close()


def test_disabled_publisher_is_silent():
    pub = LslPublisher(enabled=False)
    pub.open_markers(); pub.open_video("SIDE_02")
    pub.push_eeg("B", np.zeros((2, 5), np.float32), np.array([1.0, 2.0]))
    pub.push_marker("X", clock.monotonic()); pub.push_frame("SIDE_02", 0, clock.monotonic())
    check("a disabled publisher opens no streams and never raises",
          not pub.active and pub.stream_names() == [])


def test_simulated_headband_publishes():
    """The simulated headband goes through the real reader → HeadbandSlot →
    LslPublisher path, so simulation mode gives real (synthetic) LSL streams."""
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)
    from brain_battle.capture.headband_source import HeadbandSlot
    pub = LslPublisher()
    slot = HeadbandSlot("B")
    slot.publisher = pub
    slot.assign({"name": "MuseS-7564", "address": "sim-B"})
    slot.start()
    t0 = time.time()
    while time.time() - t0 < 1.0:
        app.processEvents(); time.sleep(0.01)
    check("simulated headband connected and reports its battery",
          slot.connected and slot.battery is not None)
    eeg = _inlet("BrainBattle_EEG_B", pub)
    t0 = time.time()
    while time.time() - t0 < 2.0:
        app.processEvents(); time.sleep(0.01)
    got, got_ts = eeg.pull_chunk(timeout=0.5) if eeg else ([], [])
    rate = (len(got_ts) - 1) / (got_ts[-1] - got_ts[0]) if len(got_ts) > 2 else 0
    check("its EEG arrives over LSL at ~256 Hz with evenly spaced timestamps",
          abs(rate - 256) < 8, f"{rate:.1f} Hz from {len(got_ts)} samples")
    check("movement streams are published too (default setting)",
          {"BrainBattle_ACC_B", "BrainBattle_GYRO_B"} <= set(pub.stream_names()),
          str(pub.stream_names()))
    ts, chans = slot.trace()
    check("the live display buffer holds all five inputs on the capture clock",
          set(chans) == {"TP9", "AF7", "AF8", "TP10", "AUX"}
          and abs(ts[-1] - clock.unix_now()) < 3.0)
    slot.stop(); pub.close()


if __name__ == "__main__":
    test_publisher_round_trip()
    test_outlets_are_opened_once()
    test_disabled_publisher_is_silent()
    test_simulated_headband_publishes()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
