"""
camera_worker.py — one camera's capture loop, in a process of its own
─────────────────────────────────────────────────────────────────────
Why a process (ADR-0025): the app's main process has ~20 Python threads
(screens, headbands, timers, encoders) sharing one interpreter lock. When the
operating system pauses whichever thread holds that lock — which a busy Mac
does now and then for tens of milliseconds — every other thread waits too, and
a camera with a one-frame buffer loses a frame for every 17 ms its reader is
not run. Measured in the 2-minute soak: stalls of 230–290 ms and ~1 % of
frames lost, against ≤ 35 ms for the same reader in a process of its own.

So each camera's **read → timestamp → hand to ffmpeg → timestamp sidecar**
runs here, in a small process (a reader thread, a writer thread, a control
thread, a sender thread), at a raised thread priority. Nothing in the main
process — the screens, a slow save, a garbage collection — can delay it.
The capture clock (``clock.monotonic()`` = ``time.perf_counter()``) is the
same clock in every process, so the frame times line up exactly as before.

``CaptureLoop`` is plain Python (no Qt). ``usb_camera.USBCameraThread`` runs it
either in this process (``main``, the default) or in its own thread
(``isolated=False`` — tests that patch the encoder use that). Either way the
loop reports through one function ``out(kind, payload)``:

    state         "opening" / "live" / "reconnecting" / "error: …" / "stopped"
    fps           (actual fps, fell back to 30?)
    fps_note      "… can only record at 30 fps, not 60."
    frames        [(read time, stamp, energy, simulated delivery delay or None), …]
                  (stamp = the frame's time on the camera's fitted steady clock,
                  timing.FrameClock — what the sidecar and recording.xdf use)
    preview       a small BGR picture (≤ PREVIEW_FPS_CAP a second)
    stats         (frames in the last second, frames in total)
    dropped       (t_monotonic, dropped this recording)   encoder queue full
    queue         (deepest in the last second, capacity)
    written       [(frame index, t_monotonic), …]          frames ffmpeg got
    rec_started / rec_finalised / record_error (text)
    stalled (s) / recovered (s) / stall_event (t, "stalled"|"recovered", s)
    log           (level, logger name, message)            warnings and errors

and is controlled with ``start_recording`` / ``stop_recording`` /
``set_preview_width`` / ``set_taps`` / ``stop`` (in a process: the same, as
messages on the pipe). When the pipe to the app breaks (the app crashed), the
loop stops and **finalises the video** before the process exits.
"""
from __future__ import annotations

import collections
import logging
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Optional

import numpy as np

import brain_battle.capture.clock as _clock

log = logging.getLogger("brain_battle.capture.usb_camera")

# Batches sent to the app: a frame is reported within this long.
BATCH_SEC = 0.05


class CaptureLoop:
    def __init__(self, dev, width: int, height: int, fps: int, bitrate_mbps: float,
                 out: Callable[[str, object], None], batch_sec: float = 0.0):
        import brain_battle.capture.usb_camera as uc
        self.uc = uc
        self.dev = dev
        self.want_w, self.want_h, self.want_fps = int(width), int(height), int(fps)
        self.bitrate_mbps = float(bitrate_mbps)
        self.out = out
        self.batch_sec = float(batch_sec)
        self.preview_w = 0
        self.stop_flag = False
        self._rec_req: Optional[tuple] = None        # (path, clock)
        self._rec_encoder: Optional[str] = None      # told by the app with start_recording
        self._rec_stop_req = False
        self.recording = False
        self._encoder = None
        self._rec_queue: Optional[queue.Queue] = None
        self._rec_stop_event: Optional[threading.Event] = None
        self._writer_thread: Optional[threading.Thread] = None
        self._dropped = 0
        self._last_drop_log = 0.0
        self._prev_small = None
        self._last_frame_t: Optional[float] = None
        self._in_reopen = False
        self._cap = None
        self.queue_peak = 0
        self.queue_capacity = uc.REC_QUEUE_MAXSIZE
        self._pool: "collections.deque" = collections.deque()   # LIFO: see _take_buffer
        self._pool_n = 0
        self._pool_shape = None
        self._pool_lock = threading.Lock()
        self._frames: list = []
        self._fclock = None
        from brain_battle.capture.keep_awake import LatencyActivity
        self.activity = LatencyActivity("Brain Battle camera recording")
        self._written: list = []
        self._written_lock = threading.Lock()
        self._last_flush = 0.0
        self._trace = None                   # debug "trace": see _trace_dump

    # ── commands (any thread) ──
    def start_recording(self, path, clock, encoder: Optional[str] = None) -> None:
        self._rec_encoder = encoder or None
        self._rec_req = (Path(path), clock)

    def stop_recording(self) -> None:
        if self._rec_req is not None and not self.recording:
            self._rec_req = None                     # never started: nothing to finalise
            self.out("rec_finalised", None)
            return
        self._rec_stop_req = True

    def set_preview_width(self, w: int) -> None:
        self.preview_w = max(0, int(w))

    def stop(self) -> None:
        self.stop_flag = True

    # ── frame buffers ──
    def _set_pool_shape(self, shape) -> None:
        with self._pool_lock:
            if shape != self._pool_shape:
                self._pool_shape = shape
                self._pool = collections.deque(); self._pool_n = 0

    def _take_buffer(self, shape):
        """A frame buffer to read into. LAST in, first out: the buffer handed
        back most recently is used again, so normally the same two or three
        stay hot in memory and the rest of the pool is touched only during a
        backlog. (First in, first out cycled through all of them; on a
        computer short of memory the colder ones had been compressed away by
        the time they came round again, and every reuse stalled the reader on
        page faults — measured by the soak, 2026-11-24.)"""
        if shape != self._pool_shape:
            self._set_pool_shape(shape)
        try:
            return self._pool.pop()
        except IndexError:
            pass
        with self._pool_lock:
            if self._pool_n >= self.uc.POOL_MAX:
                return None
            self._pool_n += 1
        return np.empty(shape, dtype=np.uint8)

    def _give_buffer(self, buf) -> None:
        if buf is not None and buf.shape == self._pool_shape:
            self._pool.append(buf)

    def _energy(self, frame) -> float:
        try:
            s = self.uc.ENERGY_STRIDE
            g = frame[::s, ::s].sum(axis=2, dtype=np.int16) // 3
        except Exception:
            return 0.0
        e = 0.0 if self._prev_small is None else float(np.mean(np.abs(g - self._prev_small)))
        self._prev_small = g
        return e

    # ── reporting ──
    def _flush(self, now: float, force: bool = False) -> None:
        if not force and now - self._last_flush < self.batch_sec:
            return
        self._last_flush = now
        if self._frames:
            self.out("frames", self._frames); self._frames = []
        with self._written_lock:
            w, self._written = self._written, []
        if w:
            self.out("written", w)

    def _on_written(self, idx: int, t: float) -> None:
        with self._written_lock:
            self._written.append((idx, t))
        if self.batch_sec <= 0:
            self._flush(_clock.monotonic(), force=True)

    # ── device ──
    def _open_capture(self):
        import cv2
        try:
            import brain_battle.capture.simulation as simulation
            if simulation.enabled():
                return simulation.open_capture(self.dev, w=self.want_w, h=self.want_h,
                                               fps=self.want_fps)
        except Exception:
            pass
        return cv2.VideoCapture(self.dev.index, self.dev.backend or self.uc._preferred_backend())

    def _negotiate(self, cap) -> tuple[float, bool]:
        """MJPG, the size, the frame rate and a 1-frame buffer; fall back to
        30 fps (and say so) when 60 was asked for and the camera cannot."""
        import cv2
        try:
            cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        except Exception:
            pass
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.want_w)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.want_h)
        cap.set(cv2.CAP_PROP_FPS, self.want_fps)
        try:
            cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        except Exception:
            pass
        actual = cap.get(cv2.CAP_PROP_FPS) or float(self.want_fps)
        fallback = False
        if self.want_fps >= 59 and actual < 50:
            cap.set(cv2.CAP_PROP_FPS, 30)
            actual = cap.get(cv2.CAP_PROP_FPS) or 30.0
            fallback = True
            log.warning("cam idx=%s cannot record at %s fps (reports %.1f) — using 30 fps",
                        self.dev.index, self.want_fps, actual)
            self.out("fps_note", f"{self.dev.name} can only record at {actual:.0f} fps, "
                                 f"not {self.want_fps}.")
        return float(actual), fallback

    def _reopen(self, cap):
        try:
            cap.release()
        except Exception:
            pass
        attempt = 0
        while not self.stop_flag:
            attempt += 1
            new = self._open_capture()
            if new is not None and new.isOpened():
                self._negotiate(new)
                self._cap = new
                self._prev_small = None
                if self._fclock is not None:
                    self._fclock.reset()            # a reopened camera: a new frame clock
                return new
            time.sleep(min(2.0, 0.3 * attempt))
        return None

    def _watchdog(self) -> None:
        _same_priority_as_reader()
        stall = self.uc.STALL_SEC
        flagged = forced = False
        while not self.stop_flag:
            time.sleep(0.25)
            last = self._last_frame_t
            if last is None:
                continue
            since = _clock.monotonic() - last
            if since >= stall and not flagged:
                flagged = True
                self.out("stalled", float(since))
            if since >= 3 * stall and not forced and not self._in_reopen:
                forced = True
                cap = self._cap
                if cap is not None:
                    try:
                        cap.release()
                    except Exception:
                        pass
            if since < stall:
                flagged = forced = False

    # ── the loop ──
    def run(self) -> None:  # noqa: C901 — one loop, kept together on purpose
        import cv2
        uc = self.uc
        from brain_battle.support import platform_utils
        platform_utils.raise_thread_priority()
        debug = uc.capture_debug()
        self.out("state", "opening")
        cap = self._open_capture()
        if not cap.isOpened():
            self.out("state", f"error: could not open camera {self.dev.index}")
            return
        self._cap = cap
        actual_fps, fallback = self._negotiate(cap)
        self.out("fps", (actual_fps, fallback))
        from brain_battle.capture.timing import FrameClock
        self._fclock = FrameClock(actual_fps)
        if abs(actual_fps - self.want_fps) > 0.5 and not fallback:
            log.warning("cam idx=%s requested %s fps but reports %.2f fps — may drift "
                        "across long sessions vs other cameras", self.dev.index,
                        self.want_fps, actual_fps)
        self.out("state", "live")
        total = 0
        window: list = []
        last_stats = _clock.monotonic()
        last_preview = 0.0
        preview_every = 1e9 if "no-preview" in debug else uc.PREVIEW_INTERVAL_SEC
        if "trace" in debug:
            self._trace_start()
        tr = self._trace
        first_shape: Optional[tuple] = None
        fail_since: Optional[float] = None
        last_queue_reset = _clock.monotonic()
        wd = threading.Thread(target=self._watchdog, name="cam-watchdog", daemon=True)
        wd.start()
        try:
            while not self.stop_flag:
                if self._rec_req is not None and not self.recording and first_shape is not None:
                    path, clock = self._rec_req; self._rec_req = None
                    fh, fw = first_shape
                    fps = float(actual_fps) if actual_fps and actual_fps > 5 else 30.0
                    if fps > 120:
                        fps = 30.0
                    self._start_encoder(path, fw, fh, fps, clock)
                if self._rec_stop_req:
                    self._rec_stop_req = False
                    if self.recording:
                        self._flush(_clock.monotonic(), force=True)
                        self._stop_encoder()

                buf = (self._take_buffer((first_shape[0], first_shape[1], 3))
                       if first_shape is not None else None)
                t_call = _clock.monotonic() if tr is not None else 0.0
                if buf is not None:
                    ok, frame = cap.read(buf)
                    if frame is not buf:
                        self._give_buffer(buf)
                else:
                    ok, frame = cap.read()
                pooled = buf is not None and frame is buf
                if not ok or frame is None:
                    t_fail = _clock.monotonic()
                    fail_since = fail_since or t_fail
                    stalled_for = t_fail - (self._last_frame_t or fail_since)
                    if stalled_for >= uc.STALL_SEC and not self.stop_flag:
                        log.warning("cam idx=%s: no frame for %.1f s — reopening",
                                    self.dev.index, stalled_for)
                        self.out("stall_event", (t_fail, "stalled", stalled_for))
                        self.out("state", "reconnecting")
                        self._in_reopen = True
                        new = self._reopen(cap)
                        self._in_reopen = False
                        if new is None:
                            break
                        cap = new
                        fail_since = None
                        continue
                    time.sleep(0.03)
                    continue

                if first_shape is None:
                    first_shape = (frame.shape[0], frame.shape[1])
                elif (frame.shape[0], frame.shape[1]) != first_shape:
                    if pooled:
                        self._give_buffer(frame)
                    frame = cv2.resize(frame, (first_shape[1], first_shape[0]))
                    pooled = False

                total += 1
                now = _clock.monotonic()
                if fail_since is not None or (self._last_frame_t is not None
                                              and now - self._last_frame_t >= uc.STALL_SEC):
                    gap = now - (self._last_frame_t or now)
                    if gap >= uc.STALL_SEC:
                        self.out("stall_event", (now, "recovered", gap))
                        self.out("recovered", float(gap))
                        self.out("state", "live")
                    fail_since = None
                self._last_frame_t = now
                stamp = self._fclock.stamp(now)
                t_stamped = _clock.monotonic() if tr is not None else 0.0
                self._frames.append((now, stamp, self._energy(frame),
                                     getattr(cap, "last_delivered", None)))
                window.append(now)
                if window[0] < now - 1.0:
                    window = [t for t in window if t > now - 1.0]
                if now - last_stats > 1.0:
                    last_stats = now
                    self.out("stats", (float(len(window)), total))
                    self.out("queue", (self.queue_peak, self.queue_capacity))

                # to the encoder without ever waiting: a full queue drops the frame
                enqueued = False
                if self.recording and self._rec_queue is not None:
                    try:
                        self._rec_queue.put_nowait((frame, stamp, now))
                        enqueued = True
                        depth = self._rec_queue.qsize()
                        if now - last_queue_reset >= 1.0:
                            last_queue_reset = now
                            self.queue_peak = depth
                        else:
                            self.queue_peak = max(self.queue_peak, depth)
                    except queue.Full:
                        self._dropped += 1
                        self.out("dropped", (now, self._dropped))
                        if now - self._last_drop_log >= 1.0:
                            self._last_drop_log = now
                            log.warning("cam idx=%s: encoder queue full — dropping frames "
                                        "(total this run: %d)", self.dev.index, self._dropped)

                t_enq = _clock.monotonic() if tr is not None else 0.0
                if now - last_preview >= preview_every:
                    last_preview = now
                    pv = frame
                    if self.preview_w and frame.shape[1] > self.preview_w:
                        s = self.preview_w / float(frame.shape[1])
                        pv = cv2.resize(frame, (self.preview_w, max(1, int(frame.shape[0] * s))))
                    elif pooled:
                        pv = frame.copy()
                    self.out("preview", pv)
                if pooled and not enqueued:
                    self._give_buffer(frame)
                t_prev = _clock.monotonic() if tr is not None else 0.0
                self._flush(now)
                if tr is not None:
                    ru = _rusage()
                    tr["frames"].append((t_call, now, t_stamped, t_enq, t_prev, _clock.monotonic(),
                                         time.process_time(), time.thread_time(), ru[0], ru[1]))
        finally:
            if self._trace is not None:
                self._trace_dump(cap)
            try:
                self._flush(_clock.monotonic(), force=True)
                if self.recording or self._writer_thread is not None:
                    self._stop_encoder()
                elif self._rec_req is not None:
                    self._rec_req = None
                    self.out("rec_finalised", None)
            except Exception:
                pass
            self.stop_flag = True
            try:
                cap.release()
            except Exception:
                pass
            self.out("state", "stopped")

    # ── debug trace (BRAIN_BATTLE_CAPTURE_DEBUG=trace; tools/soak_test.py --trace) ──
    def _trace_start(self) -> None:
        """Per-frame timing of this camera process, written to
        ``$BRAIN_BATTLE_CAPTURE_TRACE_DIR/<camera>.trace.json`` when it stops:
        frames (read called, read returned, stamped, queued, preview done,
        reported, process CPU s, reader-thread CPU s, page faults: minor, major), writes (start, after the ffmpeg pipe, after the sidecar),
        sends (start, seconds, kind) over the pipe to the app, gcs (start,
        seconds, generation), and the simulated camera's own skips."""
        import gc
        self._trace = {"frames": [], "writes": [], "sends": [], "gcs": []}
        gcs = self._trace["gcs"]
        started = {}

        def on_gc(phase, info):
            if phase == "start":
                started["t"] = _clock.monotonic()
            elif "t" in started:
                gcs.append((started["t"], _clock.monotonic() - started.pop("t"),
                            info.get("generation")))
        gc.callbacks.append(on_gc)

    def _trace_dump(self, cap) -> None:
        import json
        import os
        d = os.environ.get("BRAIN_BATTLE_CAPTURE_TRACE_DIR")
        if not d:
            return
        self._trace["skips"] = list(getattr(cap, "skips", []) or [])
        self._trace["fps"] = float(self.want_fps)
        name = str(getattr(self.dev, "index", "cam"))
        try:
            with open(os.path.join(d, f"cam{name}.trace.json"), "w", encoding="utf-8") as f:
                json.dump(self._trace, f)
        except OSError:
            pass

    # ── the encoder (writer thread) ──
    def _start_encoder(self, path, fw, fh, fps, clock) -> None:
        """Start recording WITHOUT holding up the reader: the queue and the
        writer thread are set up at once and frames queue from this frame on;
        the writer thread launches ffmpeg (tens of ms — it held the reader up
        and lost frames at the start of every recording) and then empties the
        queue into it."""
        uc = self.uc
        enc = uc.FrameEncoder(path, fw, fh, fps, clock, encoder=self._rec_encoder,
                              bitrate_mbps=self.bitrate_mbps, on_frame=self._on_written)
        self._encoder = enc
        self.queue_capacity = max(uc.REC_QUEUE_MAXSIZE, int(round(2 * fps)))
        self._rec_queue = queue.Queue(maxsize=self.queue_capacity)
        self._rec_stop_event = threading.Event()
        self._dropped = 0
        self._last_drop_log = 0.0
        self._writer_thread = threading.Thread(
            target=self._writer_loop, args=(enc, self._rec_queue, self._rec_stop_event),
            name="cam-enc-writer", daemon=True)
        self.recording = True
        self.activity.begin()               # no App Nap / timer coalescing while recording
        self._writer_thread.start()

    def _encoder_failed_to_start(self, enc, q) -> None:
        """(writer thread) ffmpeg did not launch: nothing is recorded."""
        self.recording = False
        self.activity.end()
        if self._encoder is enc:
            self._encoder = None
        while True:                          # frames that queued meanwhile go back to the pool
            try:
                self._give_buffer(q.get_nowait()[0])
            except queue.Empty:
                break
        self._writer_thread = None
        self.out("state", "error: could not start video encoder")
        self.out("record_error", "Could not start recording for this camera (video "
                                 "encoder failed to launch).")
        self.out("rec_finalised", None)

    def _writer_loop(self, enc, q, stop_event) -> None:
        from brain_battle.support import platform_utils
        platform_utils.raise_thread_priority()
        if not enc.start():
            self._encoder_failed_to_start(enc, q)
            return
        self.out("rec_started", None)
        died = False
        while True:
            try:
                item = q.get(timeout=0.2)
            except queue.Empty:
                item = None
            if item is not None:
                frame, t_stamp, t_read = item
                tw0 = _clock.monotonic() if self._trace is not None else 0.0
                ok_w = enc.write_frame(frame, t_stamp, t_read)
                if self._trace is not None:
                    self._trace["writes"].append((tw0, getattr(enc, "_t_after_pipe", tw0),
                                                  _clock.monotonic()))
                self._give_buffer(frame)
                if not ok_w:
                    died = True
                    break
            elif not stop_event.is_set() and not enc.is_alive():
                died = True
                break
            if stop_event.is_set() and q.empty():
                break
        if died:
            log.error("cam idx=%s: ffmpeg encoder died mid-recording — keeping the partial "
                      "file. ffmpeg stderr tail:\n%s", self.dev.index, enc.stderr_tail())
            try:
                enc.close(timeout=2.0)
            except Exception:
                pass
            self.recording = False
            self.activity.end()
            if self._encoder is enc:
                self._encoder = None
            self.out("record_error", "Recording for this camera stopped because of a "
                                     "video-encoder error. The footage saved up to that "
                                     "point has been kept.")
        else:
            enc.close()

    def _stop_encoder(self) -> None:
        if self._rec_stop_event is not None:
            self._rec_stop_event.set()
        t = self._writer_thread
        if t is not None:
            t.join(timeout=15.0)
        self._writer_thread = None
        self._rec_queue = None
        self._rec_stop_event = None
        self._encoder = None
        self.recording = False
        self.activity.end()
        with self._written_lock:
            w, self._written = self._written, []
        if w:
            self.out("written", w)
        self.out("rec_finalised", None)


def _rusage() -> tuple:
    """(minor, major) page faults of this process so far (debug trace)."""
    try:
        import resource
        r = resource.getrusage(resource.RUSAGE_SELF)
        return r.ru_minflt, r.ru_majflt
    except Exception:
        return 0, 0


def _same_priority_as_reader() -> None:
    """Every thread of a camera process runs at the reader's priority.

    The threads share one interpreter lock (GIL). A thread left at the default
    priority can be paused by a busy computer WHILE it holds that lock (on an
    M1, on an efficiency core), and the GIL is a condition variable, so macOS
    does not lend it the waiting reader's priority: the reader would wait
    although it is itself at the highest priority. A precaution — the soak on
    a Mac that was paging heavily could not separate its effect from the
    machine's (2026-11-24, ADR-0025)."""
    try:
        from brain_battle.support import platform_utils
        platform_utils.raise_thread_priority()
    except Exception:
        pass


# ─── the process ──────────────────────────────────────────────────────────────

class _PipeLogHandler(logging.Handler):
    """Warnings and errors of the camera process go to the app's log."""

    def __init__(self, send):
        super().__init__(logging.WARNING)
        self._send = send

    def emit(self, record):
        try:
            self._send(("log", (record.levelno, record.name, record.getMessage())))
        except Exception:
            pass


def main(conn, dev, width, height, fps, bitrate_mbps, env) -> None:
    """Entry point of a camera process (multiprocessing, "spawn")."""
    import os
    os.environ.update(env or {})
    outbox: "queue.SimpleQueue" = queue.SimpleQueue()
    alive = threading.Event(); alive.set()

    def send(msg):
        outbox.put(msg)

    def sender():
        # one thread owns the pipe's sending end
        _same_priority_as_reader()
        while True:
            msg = outbox.get()
            if msg is None:
                break
            try:
                t0 = _clock.monotonic()
                conn.send(msg)
                dt_send = _clock.monotonic() - t0
                if loop._trace is not None and dt_send > 0.002:
                    loop._trace["sends"].append((t0, dt_send, msg[0]))
            except (OSError, EOFError, BrokenPipeError):
                alive.clear()
                loop.stop()

    root = logging.getLogger()
    root.handlers[:] = [_PipeLogHandler(send)]
    root.setLevel(logging.WARNING)
    try:
        import cv2
        cv2.setNumThreads(1)
    except Exception:
        pass
    loop = CaptureLoop(dev, width, height, fps, bitrate_mbps,
                       out=lambda kind, payload: send((kind, payload)), batch_sec=BATCH_SEC)
    snd = threading.Thread(target=sender, name="cam-proc-send", daemon=True)
    snd.start()

    def control():
        import brain_battle.capture.simulation as simulation
        _same_priority_as_reader()
        while True:
            try:
                msg = conn.recv()
            except (OSError, EOFError):
                loop.stop()                 # the app has gone: stop, finalise, exit
                return
            kind, payload = msg
            if kind == "stop":
                loop.stop(); return
            if kind == "start_recording":
                loop.start_recording(*payload)
            elif kind == "stop_recording":
                loop.stop_recording()
            elif kind == "preview_width":
                loop.set_preview_width(payload)
            elif kind == "taps":
                simulation.schedule_taps(payload, notify=False)
            elif kind == "clear_taps":
                simulation.clear_taps(notify=False)

    ctl = threading.Thread(target=control, name="cam-proc-control", daemon=True)
    ctl.start()
    try:
        loop.run()
    finally:
        send(("exit", None))
        send(None)
        snd.join(timeout=2.0)
        try:
            conn.close()
        except Exception:
            pass


__all__ = ["CaptureLoop", "main", "BATCH_SEC"]
