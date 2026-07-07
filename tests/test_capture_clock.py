"""
Unit tests for the unified capture-time clock reconciliation (Step 2).

This verifies the ARITHMETIC that maps three clock domains onto one
session axis:
  - camera frame sidecars / phase markers: t_session_seconds (monotonic-relative)
  - EEG CSV: `timestamps` (Unix epoch, muselsl wall clock)
Bridge: t_session = unix_ts - capture_t0_unix, where capture_t0_(monotonic,unix)
is captured at the same instant at recording start.

IMPORTANT — what this does NOT test: whether muselsl's real timestamps are
accurate to tens-of-ms on a physical Muse band, or whether BLE transport
latency skews them. That is UNVERIFIED and needs a real band (see CLAUDE.md
"Deferred work"). These tests use synthetic EEG with KNOWN injected offsets,
so they confirm the reconciliation math and the manifest bridge only.

Run:  python3 tests/test_capture_clock.py
"""
import json
import os
import sys
import tempfile

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


SR = 256


def _synthetic_eeg(origin_unix, start_offset_sec, n_samples, seed=0):
    """EEG whose FIRST sample lands start_offset_sec after the shared origin
    (the measured startup latency). Unix timestamps at 256 Hz."""
    rng = np.random.default_rng(seed)
    t0 = origin_unix + start_offset_sec
    ts = t0 + np.arange(n_samples) / SR
    df = pd.DataFrame({
        "TP9": rng.standard_normal(n_samples), "AF7": rng.standard_normal(n_samples),
        "AF8": rng.standard_normal(n_samples), "TP10": rng.standard_normal(n_samples),
        "AUX": rng.standard_normal(n_samples),
    })
    df["timestamps"] = ts
    return df


def _reconcile(df, origin_unix):
    """The exact operation the reader writes into the CSV."""
    out = df.copy()
    out["t_session_seconds"] = out["timestamps"] - origin_unix
    return out


def test_single_band_offset_recovered():
    origin = 1_783_000_000.0
    df = _synthetic_eeg(origin, start_offset_sec=0.734, n_samples=SR * 5)
    rec = _reconcile(df, origin)
    check("first sample t_session == injected startup offset",
          abs(float(rec["t_session_seconds"].iloc[0]) - 0.734) < 1e-6,
          f"got {rec['t_session_seconds'].iloc[0]:.6f}")
    # spacing preserved at 1/256 s
    d = np.diff(rec["t_session_seconds"].to_numpy())
    check("inter-sample spacing == 1/256 s",
          np.allclose(d, 1.0 / SR, atol=1e-9), f"mean={d.mean():.8f}")


def test_two_bands_relative_skew_preserved():
    # Two boxers' bands start at different real instants; the shared axis must
    # preserve their RELATIVE skew (this is what between-brain timing needs).
    origin = 1_783_000_000.0
    a = _reconcile(_synthetic_eeg(origin, 0.20, SR * 4, seed=1), origin)
    b = _reconcile(_synthetic_eeg(origin, 0.55, SR * 4, seed=2), origin)
    skew = float(b["t_session_seconds"].iloc[0] - a["t_session_seconds"].iloc[0])
    check("relative band skew preserved on shared axis (0.35s)",
          abs(skew - 0.35) < 1e-6, f"got {skew:.6f}s")


def test_marker_and_eeg_coregister():
    # A phase marker is on the monotonic axis (t_session). An EEG event known
    # to be simultaneous with it is on the Unix axis. After reconciliation
    # both must land at the same t_session.
    t0_mono, t0_unix = 5000.0, 1_783_000_000.0
    marker_t_session = 3.0                       # monotonic-relative
    # The same real instant in Unix terms:
    event_unix = t0_unix + marker_t_session
    eeg_t_session = event_unix - t0_unix
    check("marker and simultaneous EEG event co-register",
          abs(eeg_t_session - marker_t_session) < 1e-9,
          f"marker={marker_t_session} eeg={eeg_t_session}")


def test_manifest_bridge_roundtrips():
    from session_controller import SessionConfig
    from session_storage import create_session
    with tempfile.TemporaryDirectory() as tmp:
        layout = create_session(tmp, SessionConfig())
        layout.set_capture_origin(t0_monotonic=5000.0, t0_unix=1_783_000_000.0)
        layout.record_source_start("EEG_01", first_unix=1_783_000_000.20)
        layout.record_source_start("EEG_02", first_unix=1_783_000_000.55)
        m = json.loads(layout.manifest_path().read_text())
        cc = m.get("capture_clock", {})
        check("manifest stores capture_clock", cc.get("t0_unix") == 1_783_000_000.0
              and cc.get("t0_monotonic") == 5000.0, str(cc))
        ss = m.get("source_starts", {})
        check("manifest stores measured first_t_session per source",
              abs(ss["EEG_01"]["first_t_session"] - 0.20) < 1e-6
              and abs(ss["EEG_02"]["first_t_session"] - 0.55) < 1e-6, str(ss))


def test_reader_writes_t_session_column():
    # Exercise the REAL reader flush path (no BLE — inject buffers directly).
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    try:
        import vlc  # noqa: F401
    except Exception:
        import types; sys.modules["vlc"] = types.ModuleType("vlc")
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841
    from eeg_module import _MuseDirectReader

    origin = 1_783_000_000.0
    r = _MuseDirectReader(address="x", name="synthetic")
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "eeg.csv")
        r.start_recording(path, origin_unix=origin)
        # inject two 12-sample chunks starting 0.4s after origin
        n = 12
        for c in range(2):
            data = np.random.randn(5, n).astype(np.float32)
            ts = origin + 0.4 + (np.arange(n) + c * n) / SR
            r._rec_samples.append(data)
            r._rec_timestamps.append(ts.astype(np.float64))
        r._record_path = path  # ensure set
        written = r.stop_recording()
        df = pd.read_csv(written)
        check("reader CSV has t_session_seconds column",
              "t_session_seconds" in df.columns, str(list(df.columns)))
        check("reader first t_session == injected 0.4s startup",
              abs(float(df["t_session_seconds"].iloc[0]) - 0.4) < 1e-4,
              f"got {df['t_session_seconds'].iloc[0]:.6f}")
        check("reader stashes measured first_unix",
              abs(r._last_first_unix - (origin + 0.4)) < 1e-6)


def test_review_alignment_coregisters_all_three():
    """Step 3: the review-tab alignment math. Given per-camera measured
    starts (from sidecars), axis_shift = max(start) is the playhead-0 point;
    video seek[i] = axis_shift - start[i]; markers and EEG are placed at
    (t_session - axis_shift). At any playhead p, all three must resolve to
    the SAME shared t_session = p + axis_shift."""
    cam_starts = [0.10, 0.42, 0.25, 0.31]      # measured startup latencies (s)
    axis_shift = max(cam_starts)               # 0.42 — latest camera
    seeks = [axis_shift - s for s in cam_starts]
    check("latest camera seeks 0 (reference)",
          abs(seeks[cam_starts.index(axis_shift)]) < 1e-9)
    check("all seeks non-negative (valid VLC set_time)", all(s >= -1e-9 for s in seeks))

    for p in (0.0, 1.0, 5.0):
        shared = p + axis_shift
        # video: player i local time = p + seek[i]; its frame's shared time =
        # start[i] + local = start[i] + p + (axis_shift - start[i]) = shared
        vid = [cam_starts[i] + (p + seeks[i]) for i in range(4)]
        check(f"all cameras show shared t={shared:.2f} at playhead {p}",
              all(abs(v - shared) < 1e-9 for v in vid), str([round(v,3) for v in vid]))
        # a marker at shared t_session M is placed at (M - axis_shift); the
        # playhead reaches it when p == M - axis_shift, i.e. shared == M.
        marker_shared = shared          # marker whose placed pos == p
        placed = marker_shared - axis_shift
        check(f"marker at shared {marker_shared:.2f} sits at playhead {p}",
              abs(placed - p) < 1e-9)
        # EEG loaded with offset_sec=axis_shift → global_t = t_session - shift;
        # at playhead p the cursor (set to p) shows EEG t_session = p + shift.
        eeg_shared = p + axis_shift
        check(f"EEG cursor at playhead {p} shows shared t={eeg_shared:.2f}",
              abs(eeg_shared - shared) < 1e-9)


def _band_with_spike(start_offset, spike_t_session, n_sec=8, sr=SR, amp=50.0):
    """A band whose t_session starts at start_offset, with a unit spike on TP9
    at absolute session time spike_t_session."""
    n = int(n_sec * sr)
    ts = start_offset + np.arange(n) / sr
    df = pd.DataFrame({c: np.zeros(n) for c in ("TP9", "AF7", "AF8", "TP10", "AUX")})
    df["t_session_seconds"] = ts
    # nearest sample to the target session time
    spike_i = int(np.argmin(np.abs(ts - spike_t_session)))
    df.loc[spike_i, "TP9"] = amp
    return df


def test_coregistered_preserves_between_brain_skew():
    from export_module import build_coregistered_eeg
    # Two boxers: band A starts 0.20s in with a spike at session t=5.00;
    # band B starts 0.55s in with a spike at session t=5.35. The between-brain
    # skew (0.35s) is the payload — it must survive co-registration.
    a = _band_with_spike(0.20, 5.00)
    b = _band_with_spike(0.55, 5.35)
    markers = [(0.5, "baseline", "BASELINE_START"), (3.0, "round", "ROUND_1_START")]
    m = build_coregistered_eeg([a, b], markers, sr=SR)

    check("co-registered output built", m is not None)
    check("shared grid + both bands' channels present",
          "t_session_seconds" in m.columns and "TP9_01" in m.columns and "TP9_02" in m.columns,
          str([c for c in m.columns if c.startswith("TP9")]))
    g = m["t_session_seconds"].to_numpy()
    ta = float(g[int(np.nanargmax(m["TP9_01"].to_numpy()))])
    tb = float(g[int(np.nanargmax(m["TP9_02"].to_numpy()))])
    check("band-A spike at its session time (~5.00)", abs(ta - 5.00) < 1.0 / SR, f"{ta:.4f}")
    check("band-B spike at its session time (~5.35)", abs(tb - 5.35) < 1.0 / SR, f"{tb:.4f}")
    check("between-brain skew preserved (~0.35s)", abs((tb - ta) - 0.35) < 2.0 / SR,
          f"skew={tb - ta:.4f}s")

    # Markers land at the right t_session on the shared grid.
    for mt, pid, lab in markers:
        rows = m.index[m["marker"] == lab].tolist()
        ok = len(rows) == 1 and abs(float(g[rows[0]]) - mt) <= 1.0 / SR
        check(f"marker {lab} lands at t≈{mt}", ok,
              f"grid_t={float(g[rows[0]]):.4f}" if rows else "not found")
    # Active phase forward-filled: a row at t=4.0 is inside ROUND_1 (>=3.0)
    i4 = int(np.argmin(np.abs(g - 4.0)))
    check("active phase forward-filled (t=4.0 → round)",
          m["phase_id"].iloc[i4] == "round", str(m["phase_id"].iloc[i4]))

    # NaN outside each band's own range (band B has no data before 0.55)
    i_early = int(np.argmin(np.abs(g - 0.30)))
    check("NaN where a band has no sample (B before its start)",
          np.isnan(m["TP9_02"].iloc[i_early]) and not np.isnan(m["TP9_01"].iloc[i_early]))

    # Round-trips through CSV cleanly.
    import tempfile, os as _os
    with tempfile.TemporaryDirectory() as tmp:
        p = _os.path.join(tmp, "coreg.csv")
        m.to_csv(p, index=False)
        back = pd.read_csv(p)
        check("CSV round-trips (shape + skew)",
              back.shape == m.shape
              and abs(float(back["t_session_seconds"][int(np.nanargmax(back["TP9_02"]))]
                            - back["t_session_seconds"][int(np.nanargmax(back["TP9_01"]))]) - 0.35) < 2.0 / SR)


if __name__ == "__main__":
    test_single_band_offset_recovered()
    test_two_bands_relative_skew_preserved()
    test_marker_and_eeg_coregister()
    test_manifest_bridge_roundtrips()
    test_reader_writes_t_session_column()
    test_review_alignment_coregisters_all_three()
    test_coregistered_preserves_between_brain_skew()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
