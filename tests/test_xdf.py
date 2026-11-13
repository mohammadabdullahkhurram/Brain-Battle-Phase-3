"""
recording.xdf tests — the XDF writer, the one capture clock, and the builder
that turns a recording folder into one XDF file. Everything is verified by
reading the file back with pyxdf (the reference XDF reader), with Python
warnings turned into errors so a sloppy file can't pass.

No cameras, no video encoding, no Qt event loop: the recording folder is built
by hand with the real writers (session_storage, the headband reader's CSV
path), so this runs in a couple of seconds.

Run:  python3 tests/test_xdf.py
"""
import json
import os
import sys
import tempfile
import time
import warnings

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")

import pyxdf

import brain_battle.capture.clock as clock
import brain_battle.data.labels_store as labels_store
import brain_battle.data.session_storage as ss
import brain_battle.data.session_xdf as session_xdf
from brain_battle.capture.session_controller import SessionConfig
from brain_battle.data.xdf_writer import XdfWriter, channels_xml

_failures = []
SR = 256


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _load(path, **kw):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        streams, _hdr = pyxdf.load_xdf(path, **kw)
    return {s["info"]["name"][0]: s for s in streams}


# ── 1. the writer ───────────────────────────────────────────────────────────

def test_writer_round_trip():
    path = os.path.join(tempfile.mkdtemp(), "t.xdf")
    t = 5000.3 + np.arange(SR * 10) / SR
    data = np.random.default_rng(0).normal(0, 20, (len(t), 5)).astype(np.float32)
    frames_t = 5000.1 + np.arange(300) / 29.5
    with XdfWriter(path) as w:
        e = w.add_stream("EEG", "EEG", 5, SR, "float32",
                         desc=channels_xml(["TP9", "AF7", "AF8", "TP10", "AUX"], "microvolts", "EEG"))
        m = w.add_stream("Markers", "Markers", 1, 0, "string")
        v = w.add_stream("Video", "VideoFrames", 1, 0, "int32")
        for i in range(0, len(t), 12):                       # chunked like live data
            w.write_samples(e, t[i:i + 12], data[i:i + 12])
        w.write_samples(m, [5000.0, 5004.0], ["BASELINE_START", "Jab/A & <B>"])
        w.write_samples(v, frames_t, np.arange(300).reshape(-1, 1))
    by = _load(path, dejitter_timestamps=False)
    check("writer: pyxdf reads all three streams", set(by) == {"EEG", "Markers", "Video"})
    check("writer: EEG samples are bit-exact", np.array_equal(by["EEG"]["time_series"], data))
    check("writer: EEG timestamps are exact", np.array_equal(by["EEG"]["time_stamps"], t))
    labels = [c["label"][0] for c in by["EEG"]["info"]["desc"][0]["channels"][0]["channel"]]
    check("writer: channel labels + unit survive",
          labels == ["TP9", "AF7", "AF8", "TP10", "AUX"]
          and by["EEG"]["info"]["desc"][0]["channels"][0]["channel"][0]["unit"][0] == "microvolts")
    check("writer: marker strings survive (incl. XML-special characters)",
          [r[0] for r in by["Markers"]["time_series"]] == ["BASELINE_START", "Jab/A & <B>"])
    check("writer: irregular (video-frame) timestamps are exact, values are the frame numbers",
          np.array_equal(by["Video"]["time_stamps"], frames_t)
          and by["Video"]["time_series"][:, 0].tolist() == list(range(300)))
    by2 = _load(path)                                        # pyxdf defaults (sync + dejitter)
    check("writer: default pyxdf loading keeps EEG within 0.1 ms and frame times exact",
          np.abs(by2["EEG"]["time_stamps"] - t).max() < 1e-4
          and np.array_equal(by2["Video"]["time_stamps"], frames_t))


# ── 2. the one capture clock ────────────────────────────────────────────────

def test_clock():
    import pylsl
    o = clock.session_origin()
    check("clock: one origin on three scales, mutually consistent",
          abs(clock.mono_to_unix(o["t0_monotonic"]) - o["t0_unix"]) < 1e-9
          and abs(clock.unix_to_lsl(o["t0_unix"]) - o["t0_lsl"]) < 1e-6)
    check("clock: unix_now tracks the wall clock", abs(clock.unix_now() - time.time()) < 0.5)
    a = clock.unix_now(); time.sleep(0.05); b = clock.unix_now()
    check("clock: unix_now advances with the monotonic clock", 0.04 < b - a < 0.2)
    check("clock: LSL is available", clock.lsl_available())
    d0 = pylsl.local_clock() - clock.mono_to_lsl(time.monotonic())
    time.sleep(1.0)
    d1 = pylsl.local_clock() - clock.mono_to_lsl(time.monotonic())
    check("clock: our LSL time matches pylsl.local_clock() (< 1 ms, no drift over 1 s)",
          abs(d0) < 1e-3 and abs(d1 - d0) < 2e-4, f"offset {d0 * 1e3:.3f} ms, drift {abs(d1 - d0) * 1e6:.0f} us")


# ── 3. a recording folder → recording.xdf ───────────────────────────────────

def _make_session(base):
    """A small but complete recording folder written with the real writers."""
    from brain_battle.capture.eeg_module import _MuseDirectReader
    cfg = SessionConfig(num_rounds=1, round_sec=10, break_sec=0, baseline_sec=4)
    layout = ss.create_session(base, cfg, boxer_a={"name": "Sam", "headband": "MuseS-1111"},
                               boxer_b={"name": "Alex", "headband": "MuseS-2222"})
    origin = clock.session_origin()
    layout.set_capture_origin(**origin)
    t0u = origin["t0_unix"]
    for t, pid, lab, rn in ((0.0, "baseline", "BASELINE_START", 0), (4.0, "round", "ROUND_1_START", 1),
                            (14.0, "done", "SESSION_END", 0)):
        layout.append_marker(t, pid, lab, rn)
    layout.add_moment(6.5)
    muse = layout.continuous_dir() / "Muse data"
    truth = {}
    # File-name order would give 2222 → A; the manifest says 1111 is A.
    for who, dev, start in (("B", "MuseS-2222", 0.30), ("A", "MuseS-1111", 0.55)):
        r = _MuseDirectReader(address="x", name=dev, motion=True)
        path = str(muse / f"session_{dev.replace('MuseS-', 'Muse S ')}.csv")
        r.start_recording(path, origin_unix=t0u)
        n = int((14.0 - start) * SR)
        rng = np.random.default_rng(ord(who))
        data = rng.normal(0, 15, (5, n)).astype(np.float32)
        ts = t0u + start + np.arange(n) / SR
        for i in range(0, n, 12):
            r._on_eeg(data[:, i:i + 12], ts[i:i + 12])
        acc = rng.normal(0, 0.1, (3, 60)).astype(np.float32)
        r._on_sensor("ACC", acc, t0u + start + np.arange(60) / 52)
        r.stop_recording()
        truth[who] = (start + np.arange(n) / SR, data.T)
    side = layout.continuous_dir() / "Side Cams"
    side.mkdir(parents=True)
    (side / "session_SIDE_01.mp4").write_bytes(b"")          # the builder only needs the sidecar
    ft = 0.07 + np.arange(400) / 29.5                         # a camera running slow (29.5 fps)
    with open(side / "session_SIDE_01.timestamps.csv", "w") as f:
        f.write("frame_index,t_session_seconds,t_monotonic,t_unix\n")
        for i, t in enumerate(ft):
            f.write(f"{i},{t:.6f},{origin['t0_monotonic'] + t:.6f},{t0u + t:.6f}\n")
        f.write("400,13.6")                                    # a crash-truncated last row
    return str(layout.root), origin, truth, ft


def test_session_xdf():
    base = tempfile.mkdtemp(prefix="bb_xdf_")
    root, origin, truth, ft = _make_session(base)
    files = ss.eeg_files(root)
    check("eeg_files: boxer ↔ file comes from the recording's own manifest",
          "1111" in files["A"] and "2222" in files["B"], str(files))

    labels = [labels_store.new_label("Jab", "A", labels_store.POINT, 5.25, created_by="t"),
              labels_store.new_label("Clinch", "Both", labels_store.DURATION, 8.0, t_end=9.5,
                                     created_by="t")]
    path = session_xdf.build_session_xdf(root, labels=labels)
    check("recording.xdf is written into the recording folder, no temp file left",
          path == os.path.join(root, "recording.xdf") and os.path.isfile(path)
          and not os.path.exists(path + ".tmp"))
    by = _load(path, dejitter_timestamps=False)
    t0 = origin["t0_lsl"]
    check("XDF: EEG per boxer, movement, markers, labels and the camera's frames",
          set(by) == {"BrainBattle_EEG_A", "BrainBattle_EEG_B", "BrainBattle_ACC_A",
                      "BrainBattle_ACC_B", "BrainBattle_GYRO_A", "BrainBattle_GYRO_B",
                      "BrainBattle_Markers", "BrainBattle_Labels", "BrainBattle_Video_SIDE_01"}
          - ({"BrainBattle_GYRO_A", "BrainBattle_GYRO_B"} - set(by)), str(sorted(by)))
    for who in ("A", "B"):
        s = by[f"BrainBattle_EEG_{who}"]
        t_true, y_true = truth[who]
        check(f"XDF: Boxer {who}'s EEG is the right band, sample-exact, on the session clock",
              s["time_series"].shape == y_true.shape and np.allclose(s["time_series"], y_true, atol=1e-4)
              and np.abs((s["time_stamps"] - t0) - t_true).max() < 1e-5,
              f"max dt {np.abs((s['time_stamps'] - t0) - t_true).max():.2e}")
        bb = s["info"]["desc"][0]["brain_battle"][0]
        check(f"XDF: Boxer {who}'s stream names its boxer, headband and the session start",
              bb["boxer"][0] == who and float(bb["session_start"][0]) == t0
              and bb["headband"][0].endswith("1111" if who == "A" else "2222"))
    mk = by["BrainBattle_Markers"]
    check("XDF: phase markers + the marked moment, in time order",
          [r[0] for r in mk["time_series"]] == ["BASELINE_START", "ROUND_1_START", "MOMENT", "SESSION_END"]
          and np.allclose(mk["time_stamps"] - t0, [0.0, 4.0, 6.5, 14.0], atol=1e-6))
    lb = by["BrainBattle_Labels"]
    check("XDF: labels become marker events (a lasting label = start + end)",
          [r[0] for r in lb["time_series"]] == ["Jab/A", "Clinch/Both/start", "Clinch/Both/end"]
          and np.allclose(lb["time_stamps"] - t0, [5.25, 8.0, 9.5], atol=1e-6))
    vid = by["BrainBattle_Video_SIDE_01"]
    check("XDF: one sample per video frame at its true capture time (truncated last row ignored)",
          len(vid["time_stamps"]) == 400 and np.abs((vid["time_stamps"] - t0) - ft).max() < 1e-5
          and vid["time_series"][:, 0].tolist() == list(range(400)))
    vb = vid["info"]["desc"][0]["brain_battle"][0]
    check("XDF: the video stream points at its MP4 and reports the measured frame rate",
          vb["video_file"][0] == "continuous/Side Cams/session_SIDE_01.mp4"
          and abs(float(vb["measured_fps"][0]) - 29.5) < 0.01, vb["measured_fps"][0])

    rates = ss.camera_frame_rates(root)
    check("camera_frame_rates reports the rate the camera really achieved",
          len(rates) == 1 and rates[0][0] == "SIDE_01" and rates[0][1] == 400
          and abs(rates[0][2] - 29.5) < 0.01, str(rates))

    # frame-by-frame alignment the way an analyst would do it
    eeg = by["BrainBattle_EEG_A"]
    frame = 200
    i = int(np.searchsorted(eeg["time_stamps"], vid["time_stamps"][frame]))
    check("XDF: the EEG sample for a given video frame is within one sample period",
          abs(eeg["time_stamps"][i] - vid["time_stamps"][frame]) <= 1 / SR + 1e-9)

    # default labels come from labels.json; rebuilding replaces the file
    store = labels_store.LabelsStore(); store.add(labels[0])
    store.save(os.path.join(root, "labels.json"))
    session_xdf.build_session_xdf(root)
    check("XDF: with no labels given, the folder's labels.json is used",
          [r[0] for r in _load(path)["BrainBattle_Labels"]["time_series"]] == ["Jab/A"])
    mtime = os.path.getmtime(path)
    check("ensure_session_xdf leaves an existing file alone",
          session_xdf.ensure_session_xdf(root) == path and os.path.getmtime(path) == mtime)
    check("command line rebuilds it", session_xdf.main([root]) == 0)


def test_edge_cases():
    base = tempfile.mkdtemp(prefix="bb_xdf_empty_")
    layout = ss.create_session(base, SessionConfig())
    check("a folder with no signals yields no file (and no temp file)",
          session_xdf.build_session_xdf(layout.root) is None
          and not os.path.exists(os.path.join(layout.root, "recording.xdf"))
          and not os.path.exists(os.path.join(layout.root, "recording.xdf.tmp")))
    check("command line refuses a non-recording folder", session_xdf.main([tempfile.mkdtemp()]) == 1)

    # a recording made before the LSL origin / t_session column existed
    old = ss.create_session(base, SessionConfig(), boxer_a="Old")
    ss._update_manifest(old.root, lambda m: m.update(capture_clock={"t0_unix": 1000.0}))
    muse = old.continuous_dir() / "Muse data"
    with open(muse / "session_Muse S 7538.csv", "w") as f:
        f.write("TP9,AF7,AF8,TP10,AUX,timestamps\n")
        for i in range(SR):
            f.write(f"1,2,3,4,5,{1000.5 + i / SR}\n")
    by = _load(session_xdf.build_session_xdf(old.root), dejitter_timestamps=False)
    s = by["BrainBattle_EEG_A"]
    check("an older recording still converts (session time from timestamps − t0_unix)",
          len(s["time_stamps"]) == SR and abs(s["time_stamps"][0] - 0.5) < 1e-6)

    # a crashed recording is given its XDF when it is found again
    crash = ss.create_session(base, SessionConfig(), boxer_a="Crash")
    crash.set_capture_origin(**clock.session_origin())
    crash.append_marker(0.0, "baseline", "BASELINE_START", 0)
    from brain_battle.data.session_storage import PhaseRecord
    crash.add_phase(PhaseRecord("baseline", "BASELINE", 0, "continuous", 60, time.time(), time.monotonic()))
    n = ss.flag_new_interrupted(base)
    check("an interrupted recording gets recording.xdf when it is flagged",
          n == 1 and os.path.isfile(os.path.join(crash.root, "recording.xdf")))
    man = json.load(open(os.path.join(crash.root, "session_manifest.json")))
    check("…and is flagged interrupted exactly once", man.get("interrupted") is True
          and ss.flag_new_interrupted(base) == 0)


if __name__ == "__main__":
    test_writer_round_trip()
    test_clock()
    test_session_xdf()
    test_edge_cases()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
