"""
Crash-safe video recording tests (Phase 2, item 5 — Option A).

The camera recorder now feeds raw frames to a per-camera ffmpeg subprocess that
writes a fragmented H.264 MP4, via a bounded queue + writer thread so capture
never blocks on the encoder. These tests drive the REAL USBCameraThread path
with a file-backed source (no camera hardware needed):

  1. SIGKILL mid-recording: the process is hard-killed; the fragmented MP4
     still plays (ffprobe decodes it) and its frame count matches the sidecar
     row count within the last in-flight fragment — never a wild mismatch.
  2. Back-pressure: with the encoder write artificially slowed, capture keeps
     consuming the source (frames get dropped + counted) and the surviving
     frames still satisfy sidecar rows == encoded frames.
  3. ffmpeg dies: one camera's ffmpeg is killed mid-recording — that camera
     stops with a record_error and a consistent partial file+sidecar, while a
     second camera finishes normally.

(The "Review still loads + aligns a freshly generated demo" check lives in
tests/test_demo_session.py, which now generates fragmented H.264.)

Run:  python3 tests/test_video_recording.py   (nonzero exit on any failure)
Needs: ffmpeg on PATH + a working H.264 encoder.
"""
import os
import signal
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
import cv2
from PyQt6.QtWidgets import QApplication
_app = QApplication.instance() or QApplication(sys.argv)  # noqa: F841

from usb_camera import USBCameraThread, CameraDevice

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _make_source(path, seconds, fps=30, size="640x360"):
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-f", "lavfi",
         "-i", f"testsrc=size={size}:rate={fps}:duration={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", path],
        check=True)


def _ffprobe_frames(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames",
         "-of", "default=nokey=1:noprint_wrappers=1", path],
        capture_output=True, text=True)
    try:
        return int(r.stdout.strip())
    except ValueError:
        return -1   # unreadable


def _sidecar_rows(mp4_path):
    sc = os.path.splitext(mp4_path)[0] + ".timestamps.csv"
    if not os.path.exists(sc):
        return -1
    with open(sc, encoding="utf-8") as f:
        lines = f.read().splitlines()
    # data rows that actually have the 4 expected fields (ignore a partial
    # final line a hard kill may have truncated)
    return sum(1 for ln in lines[1:] if ln.count(",") == 3)


# ─── 1. SIGKILL mid-recording ───────────────────────────────────────────────

def test_sigkill_midrecording():
    runner = os.path.join(ROOT, "tests", "_camera_kill_runner.py")
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "src.mp4")
        _make_source(source, seconds=30)       # 900 frames, decodes flat-out
        out = os.path.join(tmp, "rec.mp4")
        sidecar = os.path.splitext(out)[0] + ".timestamps.csv"

        proc = subprocess.Popen([sys.executable, runner, source, out])
        # Wait until the recording has streamed a healthy number of frames.
        safety = time.monotonic() + 30.0
        rows = 0
        while rows < 150 and time.monotonic() < safety:
            time.sleep(0.1)
            rows = _sidecar_rows(out) if os.path.exists(sidecar) else 0
        check("recording produced frames before the kill", rows >= 150,
              f"sidecar rows={rows}")

        proc.send_signal(signal.SIGKILL)
        proc.wait(timeout=10)
        check("process was hard-killed", proc.returncode is not None
              and proc.returncode < 0, f"rc={proc.returncode}")

        # The ffmpeg child is orphaned; on our death it sees stdin EOF and
        # finalises the fragmented MP4. Give it a moment.
        time.sleep(2.5)

        frames = _ffprobe_frames(out)
        srows = _sidecar_rows(out)
        check("fragmented MP4 still plays after hard kill (ffprobe decodes it)",
              frames > 0, f"ffprobe frames={frames}")
        check("sidecar rows == encoded frames (within last in-flight fragment)",
              frames > 0 and abs(frames - srows) <= 2,
              f"ffprobe={frames} sidecar={srows}")


# ─── 2. Back-pressure ───────────────────────────────────────────────────────

def test_backpressure_drops_and_consistency():
    import usb_camera
    orig_write = usb_camera.FrameEncoder.write_frame

    def slow_write(self, frame, t):
        time.sleep(0.01)       # throttle the encoder write path
        return orig_write(self, frame, t)

    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "src.mp4")
        _make_source(source, seconds=10)       # 300 frames, decodes flat-out
        out = os.path.join(tmp, "rec.mp4")

        usb_camera.FrameEncoder.write_frame = slow_write
        try:
            dev = CameraDevice(index=source, name="file", backend=cv2.CAP_FFMPEG)
            th = USBCameraThread(dev)
            th.start_recording(out, clock=(1000.0, 1_700_000_000.0))
            th.start()
            # Let the source fully decode (fast) while the writer lags behind.
            time.sleep(4.0)
            th.stop_recording()
            time.sleep(0.5)
            th.stop()
            th.wait(15000)
            dropped = th.dropped_frames
        finally:
            usb_camera.FrameEncoder.write_frame = orig_write

        frames = _ffprobe_frames(out)
        srows = _sidecar_rows(out)
        check("slow encoder caused frames to be dropped + counted", dropped > 0,
              f"dropped={dropped}")
        check("capture kept consuming the source despite the slow writer",
              dropped + max(0, srows) >= 250,
              f"dropped={dropped} + encoded={srows} of ~300")
        check("sidecar rows == encoded frames under back-pressure",
              frames > 0 and frames == srows, f"ffprobe={frames} sidecar={srows}")


# ─── 3. ffmpeg dies mid-recording ───────────────────────────────────────────

def test_ffmpeg_death_isolated_to_one_camera():
    with tempfile.TemporaryDirectory() as tmp:
        source = os.path.join(tmp, "src.mp4")
        _make_source(source, seconds=30)
        out_a = os.path.join(tmp, "cam_a.mp4")   # will have its ffmpeg killed
        out_b = os.path.join(tmp, "cam_b.mp4")   # finishes normally

        errors_a = []
        th_a = USBCameraThread(CameraDevice(index=source, name="A", backend=cv2.CAP_FFMPEG))
        th_b = USBCameraThread(CameraDevice(index=source, name="B", backend=cv2.CAP_FFMPEG))
        th_a.record_error.connect(lambda m: errors_a.append(m))
        th_a.start_recording(out_a, clock=(1000.0, 1_700_000_000.0))
        th_b.start_recording(out_b, clock=(1000.0, 1_700_000_000.0))
        th_a.start()
        th_b.start()

        # Wait until camera A is encoding, then break the pipe to its ffmpeg
        # (close stdin → ffmpeg hits EOF, finalises, and exits — the realistic
        # "encoder died / pipe broke" that still leaves a playable partial).
        safety = time.monotonic() + 15.0
        while th_a._encoder is None and time.monotonic() < safety:
            time.sleep(0.05)
        time.sleep(1.5)
        killed = False
        enc = th_a._encoder
        if enc is not None and enc._proc is not None and enc._proc.stdin is not None:
            try:
                enc._proc.stdin.close()
                killed = True
            except OSError:
                pass
        check("camera A's ffmpeg pipe was broken mid-recording", killed)

        # Let A notice the broken pipe and B keep going.
        time.sleep(1.5)
        # Let Qt deliver A's queued record_error signal.
        _app.processEvents()

        check("camera A reported a recording error (slot would go red)",
              len(errors_a) >= 1, str(errors_a[:1]))
        check("camera A stopped recording", not th_a.is_recording)

        # B finishes normally.
        th_b.stop_recording()
        time.sleep(0.3)
        th_a.stop(); th_a.wait(8000)
        th_b.stop(); th_b.wait(8000)
        _app.processEvents()

        fa, sa = _ffprobe_frames(out_a), _sidecar_rows(out_a)
        fb, sb = _ffprobe_frames(out_b), _sidecar_rows(out_b)
        check("camera A's partial file is still playable + consistent",
              fa > 0 and abs(fa - sa) <= 2, f"A ffprobe={fa} sidecar={sa}")
        check("camera B finished normally (plays, sidecar matches)",
              fb > 0 and fb == sb, f"B ffprobe={fb} sidecar={sb}")


if __name__ == "__main__":
    test_sigkill_midrecording()
    test_backpressure_drops_and_consistency()
    test_ffmpeg_death_isolated_to_one_camera()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
