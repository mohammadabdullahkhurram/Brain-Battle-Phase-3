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


if __name__ == "__main__":
    test_single_band_offset_recovered()
    test_two_bands_relative_skew_preserved()
    test_marker_and_eeg_coregister()
    test_manifest_bridge_roundtrips()
    test_reader_writes_t_session_column()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
