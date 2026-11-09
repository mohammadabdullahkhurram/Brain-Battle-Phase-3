# Threading model

**What this page covers:** every thread and subprocess the app uses, how they
talk to each other, the one rule that makes it all safe (the UI thread never
blocks), and how to tear a thread down without crashing the process — including
a real bug that taught us the teardown rule.

## The rule: the UI thread never blocks

Everything the user sees runs on the **main (UI) thread**. Anything that could
take more than a few milliseconds — camera capture, video encoding, BLE reads,
video decoding, folder scanning, export — runs on its own thread or subprocess
and talks back to the UI thread with **Qt queued signals**. If the UI thread
ever waited on a camera, a Bluetooth read, or a decode, the whole app would
freeze. It doesn't, by design.

![Threading diagram: the UI thread starts and stops camera threads (which queue frames to ffmpeg subprocesses) and headband reader threads during a recording, and a DecodeWorker and media-load worker during review; all workers report back to the UI thread via Qt signals.](../diagrams/threads.png)

## The threads

| Thread / process | Count | Lives | Job | Module |
|---|---|---|---|---|
| **UI thread** | 1 | always | every widget and screen | all UI modules |
| **`USBCameraThread`** | up to 4 | recording | grab frames, queue them to the encoder | `usb_camera.py` |
| **encoder-writer thread** | 1 per camera | recording | drain the frame queue into ffmpeg's stdin | `usb_camera.py` |
| **`ffmpeg` subprocess** | 1 per camera | recording | encode fragmented H.264 + nothing else | spawned by `FrameEncoder` |
| **`_MuseDirectReader`** | 1 per band | recording/live | BLE read loop (muselsl `Muse` via `bleak`) | `eeg_module.py` |
| **EEG CSV-writer thread** | 1 per band | recording | append EEG chunks to disk (flush+fsync) | `eeg_module.py` |
| **`DecodeWorker`** | 1 | review | decode the frame-at-`t` for all cameras | `pyav_player.py` |
| **`_MediaLoadWorker`** | 1 | review (brief) | open videos + EEG when a session opens | `review_label_tab.py` |
| **`_ScanWorker` / `_SizeWorker`** | 1 each | Sessions | scan folders + compute sizes off-UI | `sessions_tab.py` |
| **`SessionExportWorker` / `VideoExportWorker`** | 1 | export | build the zip / merge the video | `export_module.py` |

## How they communicate

- **UI → worker:** a direct method call that only sets state and returns
  immediately — e.g. `DecodeWorker.request(t)` stores the latest `t` and
  notifies a condition variable; `USBCameraThread.stop()` sets a flag.
- **Worker → UI:** a Qt **signal**, delivered on the UI thread's event loop
  (queued connection). Examples: `frames_ready(t, images)`, `media_loaded(...)`,
  `scanned(sessions)`, `phase_changed(...)`.
- **Producer buffer reuse:** where a producer reuses a buffer (the EEG BLE
  callback), the hand-off **copies** the array before emitting — `_on_eeg`
  documents exactly this. Never pass a reused ndarray across a thread boundary
  without copying.
- **Backpressure, never blocking:** the camera frame queue is bounded. A full
  queue **drops a frame and increments a counter**; it never blocks the capture
  loop. A dead ffmpeg stops only that one camera (its tile goes red) while the
  others keep recording.

## Seek coalescing (why scrubbing stays cheap)

`DecodeWorker` keeps only the **latest** requested playhead. Requests that
arrive while a decode is running overwrite the pending one, so dragging the
scrubber across a long session costs a couple of decodes, not one per pixel.
`PlayerController` paces playback against the **real monotonic clock**, so it
skips frames under load rather than falling behind in time — 1× stays honest 1×.
`tests/test_player.py` asserts both.

## Safe teardown (and the bug that taught us how)

A `QThread` that is destroyed while still running aborts the whole process
(`SIGABRT: "QThread destroyed while running"`). So every thread owner must stop
and **wait** for its thread before dropping the reference:

- `CameraSlot.shutdown()` → `self._thread.wait(2500)`.
- `ReviewLabelTab.shutdown()` → waits on `_MediaLoadWorker`, then
  `PlayerController.shutdown()` stops the timer and `DecodeWorker` (`wait(2000)`).
- `SessionsTab.shutdown()` → waits on its scan/size workers.

**The lesson — late signals can orphan a live thread.** A camera thread's
`finished` signal can be delivered *late* — after the slot has already stopped it
and started a replacement (e.g. during rapid re-scans, or navigating in and out
of New recording). The original `CameraSlot._on_finished` nulled `self._thread`
whenever any thread finished; a late signal from the **old** thread then nulled
the reference to the **new, live** thread, orphaning it so it aborted the process
at teardown (commit `9874a6f`). The fix is a **sender-identity guard**:

```python
def _on_finished(self):
    t = self.sender()
    if t is not None and t is not self._thread:
        return   # a late signal from a thread we've already replaced
    ...
```

The general rule: when a slot can replace its worker, every late callback from a
worker must check it's still the *current* worker before mutating shared state.

## Headless testability

The pure-logic pieces deliberately avoid Qt so they can be tested without an
event loop: `session_controller` (the phase state machine), `labels_store` and
`label_vocab`, `docs_nav`, and `pyav_player`'s `PyAVCamera`/`MultiCamDecoder`
(the Qt `DecodeWorker`/`PlayerController` are defined inside a
`try: import PyQt6` block). Keep new timing/alignment/parsing logic in a Qt-free
function so it stays unit-testable — see [testing strategy](testing-strategy.md).
