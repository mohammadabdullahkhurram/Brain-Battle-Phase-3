"""
Subprocess runner for tests/test_video_recording.py — NOT a test itself.

Records a file-backed USBCameraThread (the REAL capture → queue → writer →
FrameEncoder path) to a given MP4. The parent test lets it stream, then
SIGKILLs the whole process to simulate a hard app crash mid-recording, and
checks the fragmented MP4 is still playable and consistent with its sidecar.

Usage (parent test only):
    python3 tests/_camera_kill_runner.py <source_video> <out_mp4>
"""
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt6.QtWidgets import QApplication

import cv2
from usb_camera import USBCameraThread, CameraDevice


def main():
    source, out = sys.argv[1], sys.argv[2]
    _app = QApplication.instance() or QApplication([])  # noqa: F841

    dev = CameraDevice(index=source, name="file", backend=cv2.CAP_FFMPEG)
    th = USBCameraThread(dev)
    # Queue the recording BEFORE start so it's picked up at the first frame
    # (the file decodes flat-out, so there's no warm-up window to miss).
    th.start_recording(out, clock=(1000.0, 1_700_000_000.0))
    th.start()
    # Stay alive until the parent kills us.
    while True:
        time.sleep(0.2)


if __name__ == "__main__":
    main()
