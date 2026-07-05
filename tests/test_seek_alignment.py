"""
Regression test for the MainEditorSyncWorker seek formula.

History: the seek computation in MainEditorSyncWorker.run() has flip-flopped
twice between `seek = norm_delay` and `seek = max_norm_delay - norm_delay`,
each time with a confident comment. This test pins the ground truth with
real media instead of reasoning:

  Three clips share one audio track but start recording at KNOWN offsets
  (A at +0.0s, B at +1.5s, C at +0.8s — i.e. B is missing the first 1.5s
  of the master timeline). A loud transient is injected at a known master
  time. After the worker's computed seeks are applied, that transient must
  land at the same playhead position in all three clips.

  Convention (verified empirically against _xcorr_delay): a POSITIVE delay
  means the clip STARTED LATER, so it is missing head footage and needs
  the SMALLEST seek. The latest-starting clip seeks 0; earlier-starting
  clips seek forward past their extra head footage:
      seek_i = max_norm_delay - norm_delay_i

Also asserts the simultaneous-start (in-app, no-audio) path yields all-zero
seeks, so the formula is inert there.

Run:  python3 tests/test_seek_alignment.py
Requires ffmpeg on PATH. Stubs python-vlc only if libVLC is unavailable.
"""
import os
import subprocess
import sys
import tempfile
import types

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── vlc stub (only when libVLC is missing — the worker never touches vlc,
#    but importing brain_battle_main does) ────────────────────────────────────
try:
    import vlc  # noqa: F401
except Exception:
    _vlc = types.ModuleType("vlc")

    class _State:
        NothingSpecial, Opening, Buffering, Playing, Paused, Stopped, Ended, Error = range(8)

    class _EventType:
        MediaPlayerTimeChanged = "t"; MediaPlayerLengthChanged = "l"

    class _CBDec:
        VideoLockCb = staticmethod(lambda f: f)
        VideoUnlockCb = staticmethod(lambda f: f)
        VideoDisplayCb = staticmethod(lambda f: f)

    class _Media:
        def __init__(self, *a, **k): pass

    class _Instance:
        def __init__(self, *a, **k): pass
        def media_player_new(self): raise RuntimeError("stub: no playback in tests")
        def media_new(self, uri): return _Media()

    _vlc.State, _vlc.EventType, _vlc.CallbackDecorators = _State, _EventType, _CBDec
    _vlc.Media, _vlc.Instance = _Media, _Instance
    sys.modules["vlc"] = _vlc

from PyQt6.QtCore import QCoreApplication  # noqa: E402

_app = QCoreApplication.instance() or QCoreApplication(sys.argv)

import brain_battle_main as bbm  # noqa: E402
from sync_utils.audio_analysis import process_audio  # noqa: E402

SR = 44100
MASTER_DUR = 12.0
TRANSIENT_T = 6.0          # master time of the loud burst
START_OFFSETS = [0.0, 1.5, 0.8]   # clip i starts this many seconds LATE
TOLERANCE_SEC = 0.06       # xcorr grid is 0.25ms; AAC priming jitter is <10ms


def _write_master_wav(path: str) -> None:
    """Quiet pink-ish noise with one loud 50ms white-noise burst at TRANSIENT_T."""
    rng = np.random.default_rng(42)
    n = int(SR * MASTER_DUR)
    # Low-amplitude noise, low-passed a little so it still correlates well
    noise = rng.standard_normal(n).astype(np.float64)
    noise = np.convolve(noise, np.ones(8) / 8, mode="same") * 0.02
    b0 = int(TRANSIENT_T * SR)
    burst = rng.standard_normal(int(0.05 * SR)) * 0.9
    noise[b0:b0 + len(burst)] += burst
    pcm = np.clip(noise, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype(np.int16)
    import wave
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(pcm16.tobytes())


def _make_clip(master_wav: str, start_offset: float, out_path: str) -> None:
    """Video+audio MP4 whose recording begins `start_offset` s into the
    master timeline (a camera that started late misses that much head)."""
    dur = MASTER_DUR - start_offset
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=160x120:rate=15",
        "-ss", f"{start_offset:.3f}", "-i", master_wav,
        "-t", f"{dur:.3f}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True)


def _make_silent_clip(out_path: str, dur: float = 3.0) -> None:
    """Audio-less MP4 — the in-app cv2.VideoWriter case."""
    cmd = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i", f"testsrc=duration={dur}:size=160x120:rate=15",
        "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", out_path,
    ]
    subprocess.run(cmd, check=True)


def _run_worker(video_paths):
    """Run the REAL MainEditorSyncWorker synchronously; return
    (norm_delays, seek_ms, sync_mode)."""
    result = {}
    worker = bbm.MainEditorSyncWorker(video_paths)
    worker.finished.connect(lambda paths, payload: result.update(payload=payload))
    worker.error.connect(lambda msg: result.update(error=msg))
    worker.run()  # synchronous — same code path as worker.start()
    assert "error" not in result, f"worker errored: {result.get('error')}"
    assert "payload" in result, "worker emitted neither finished nor error"
    payload = result["payload"]
    n = len(video_paths)
    norm_delays = [float(d) for d in payload[:n]]
    seek_ms = [int(payload[n + 1 + i]) for i in range(n)]
    return norm_delays, seek_ms, worker.sync_mode


def _transient_local_sec(video_path: str) -> float:
    """Locate the burst in a clip's audio (seconds from clip start)."""
    audio, sr = process_audio(video_path)
    assert len(audio) > 0, f"no audio extracted from {video_path}"
    return float(np.argmax(np.abs(audio))) / sr


def test_external_seek_alignment(tmp_dir: str) -> None:
    master = os.path.join(tmp_dir, "master.wav")
    _write_master_wav(master)
    clips = []
    for i, off in enumerate(START_OFFSETS):
        p = os.path.join(tmp_dir, f"clip_{chr(65 + i)}.mp4")
        _make_clip(master, off, p)
        clips.append(p)

    norm_delays, seek_ms, mode = _run_worker(clips)
    assert mode == "audio_xcorr", f"expected audio path, got {mode}"

    # Sanity, formula-independent: xcorr must recover the injected offsets.
    for d, expected in zip(norm_delays, START_OFFSETS):
        assert abs(d - expected) < 0.05, (
            f"xcorr broke: norm_delays={norm_delays}, expected≈{START_OFFSETS}")

    # THE regression assertion: after each clip seeks by its computed
    # seek_ms, the shared transient must sit at the same playhead time.
    playhead_positions = []
    for path, seek in zip(clips, seek_ms):
        local = _transient_local_sec(path)
        playhead_positions.append(local - seek / 1000.0)
    spread = max(playhead_positions) - min(playhead_positions)
    print(f"  norm_delays = {[round(d, 3) for d in norm_delays]}")
    print(f"  seek_ms     = {seek_ms}")
    print(f"  transient at playhead t = {[round(t, 3) for t in playhead_positions]}"
          f"  (spread {spread * 1000:.1f} ms)")
    assert spread <= TOLERANCE_SEC, (
        f"SEEK FORMULA MISALIGNS PLAYBACK: transient lands {spread:.3f}s apart "
        f"across clips (seeks={seek_ms} ms for start offsets {START_OFFSETS}s). "
        f"Correct seeks skip the EARLY clips' head footage: "
        f"seek_i = max_norm_delay - norm_delay_i.")


def test_simultaneous_start_inert(tmp_dir: str) -> None:
    clips = []
    for i in range(3):
        p = os.path.join(tmp_dir, f"silent_{i}.mp4")
        _make_silent_clip(p)
        clips.append(p)
    norm_delays, seek_ms, mode = _run_worker(clips)
    print(f"  mode={mode}  norm_delays={norm_delays}  seek_ms={seek_ms}")
    assert mode == "simultaneous_start", f"expected simultaneous_start, got {mode}"
    assert norm_delays == [0.0, 0.0, 0.0], norm_delays
    assert seek_ms == [0, 0, 0], f"seeks must all be 0 on the in-app path: {seek_ms}"


if __name__ == "__main__":
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for test in (test_external_seek_alignment, test_simultaneous_start_inert):
            print(f"\n=== {test.__name__} ===")
            try:
                test(tmp)
                print(f"PASS  {test.__name__}")
            except AssertionError as e:
                failures += 1
                print(f"FAIL  {test.__name__}\n  {e}")
    print(f"\n{'RED' if failures else 'GREEN'}: {failures} failure(s)")
    sys.exit(1 if failures else 0)
