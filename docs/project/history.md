# Project history

**What this page covers:** the completed build phases and the historical
context behind the current architecture — the unified capture-time clock
build, the v1→v2 recording-model migration, and the grounding-fixes backlog.
`CLAUDE.md` keeps only a short pointer here; this is the long-form record.

> This page is not yet listed in `docs/nav.yml` — documentation step D3 adds
> the Project section that surfaces it in the in-app Documentation screen.
> Until then it lives here as a repository document.

Many file and class names below (`SyncReviewTab`, `LabellingTab`,
`vlc_module`, `sync_engine.py`, `live_session_tab.py`, `labels_config.py`,
`labels_data.json`, `EEGReviewWidget`, `MainEditorSyncWorker`) describe code
that has since been **removed or replaced**. They are kept here because the
reasoning that produced today's code refers to them. For what exists now, see
`CLAUDE.md`'s Module map.

---

## Unified capture-time clock (completed on `develop`)

Built on the long-lived `develop` branch (`main` stays the stable baseline;
develop merges to main only when a version is verified and ready to ship).
Goal: one monotonic capture-time reference stamped on every source so
alignment is MEASURED (tens-of-ms target — deliberately NOT sub-ms; no
genlock/LSL). See `docs/ARCHITECTURE_AND_EVALUATION.md` for the design.

**Versions: `develop` = `3.0.0-dev`, `main` = `2.1.0`.** 3.0.0 is a
DELIBERATE BREAKING CHANGE: the review/label path aligns by the capture-time
clock (`capture_clock` in the manifest + `t_session_seconds` in the camera
sidecars and EEG CSVs). Pre-3.0 sessions recorded without that data are NOT
supported — there is no legacy-fallback alignment path (one alignment path,
not two). Record fresh with 3.0. Becomes `3.0.0` when develop merges to main.

Step status:

- **STEP 1 — camera frame timestamps: DONE, verified.** Each recording writes
  a per-camera sidecar `<mp4-stem>.timestamps.csv`
  (`frame_index, t_session_seconds, t_monotonic, t_unix`) alongside the
  (unchanged) MP4. A shared origin `(t0_monotonic, t0_unix)` is captured once
  at recording-start and threaded through to every `USBCameraThread`, so all
  cameras stamp on one clock. Verified against the real `USBCameraThread.run()`
  loop (file-backed VideoCapture, no camera on this box): 13/13 — row-per-frame,
  count == MP4 frames, monotonic-increasing, unix sane, shared origin identical
  across cameras. NOT yet verified: real capture cadence (needs a physical
  camera). *(The origin was captured in the then-current
  `live_session_tab._on_session_started`; that responsibility now lives in the
  guided flow, `record_flow.py`.)*
- **STEP 2 — unified clock bridge + EEG-side stamping: DONE (EEG half
  UNVERIFIED).** The shared origin is persisted to the manifest as
  `capture_clock: {t0_monotonic, t0_unix}` (`session_storage.set_capture_origin`),
  and each band's measured first-sample time to `source_starts`
  (`record_source_start`) — startup skew is recorded, not assumed. The EEG
  recorder writes a `t_session_seconds` column (`= timestamps - origin_unix`)
  so EEG sits on the same axis as camera frames and markers. Reconciliation
  math + manifest bridge unit-tested in `tests/test_capture_clock.py` (incl.
  the real reader flush path). **UNVERIFIED**: the EEG *clock source* (muselsl
  wall-clock accuracy + BLE latency) is not confirmable without a real band —
  labeled in `eeg_module.py` and in Deferred work (`CLAUDE.md`). The arithmetic
  is verified; the EEG position on the axis is not.
- **STEP 3 — align review/label by capture-time stamps: DONE (logic-verified,
  NOT eyes-verified).** Clean break, no legacy fallback (v3.0). The EEG review
  widget places EEG on the shared axis via its `t_session_seconds` column
  (the §2.4 EEG-vs-marker fix). The review path reads `capture_clock.t0_unix`
  (manifest) + each camera's measured start (frame sidecar); `axis_shift =
  max(start)` = playhead-0; alignment is READ, not computed from audio (video
  seek = `axis_shift − start`). Markers placed at `t_session − axis_shift`; EEG
  loaded with `offset_sec = axis_shift`. All three co-register (unit-tested).
  **UNVERIFIED / not-eyes-verified:** this is the review/label path that has
  never been watched on real data — logic + suites are green (necessary, NOT
  sufficient). *(This step predates the Phase 3 merge; its separate review and
  labelling tabs are now one screen, `review_label_tab.py` + `pyav_player.py`.)*
- **STEP 4 — retire audio xcorr: DONE.** All sessions come from the recording
  app (capture_clock present), so audio cross-correlation is obsolete. Deleted
  `MainEditorSyncWorker`, the manual-upload branch of the align path,
  `sync_utils/audio_analysis.py`, `sync_utils/eeg_video_sync.py`, and two
  now-dangling tests (`test_xcorr_confidence.py`, `test_seek_alignment.py` —
  the D′ seek-formula invariant is preserved in
  `test_capture_clock.py::test_review_alignment_coregisters_all_three`). Single
  alignment method: capture-time stamps. *(At the time this kept
  `sync_engine.SourceOffset`/`SyncResult` for export; the whole
  `sync_engine.py` module was later removed when the Review & Label screen
  replaced the old tabs.)*
- **STEP 5 — co-registered EEG export: DONE (alignment UNVERIFIED per Step 2).**
  `export_module.build_coregistered_eeg` merges both boxers' EEG onto ONE
  uniform 256 Hz `t_session_seconds` grid (union span): columns
  `t_session_seconds, <chan>_01, <chan>_02, phase_id, phase_label, marker`,
  NaN where a band has no sample. Written to the bundle as
  `eeg_coregistered.csv` alongside the raw per-band files. Verified against
  synthetic bands
  (`test_capture_clock.py::test_coregistered_preserves_between_brain_skew`): a
  known 0.35s between-brain skew survives, markers land at the right
  `t_session`, phase forward-fills, NaN outside range, CSV round-trips — plus
  an end-to-end export producing the zip. **INHERITED UNVERIFIED**: the
  between-brain alignment is only as accurate as the Step-2 EEG clock placement
  (Deferred work #0/#1).

What remained from this specific phase was the eyes-on-glass verification with
real capture data (Deferred work #0/#1 in `CLAUDE.md`). Everything planned
after it is tracked in `docs/ROADMAP.md`.

---

## Prior phase — functional fixes + cleanup (merged to main)

Two phases — functional fixes and cleanup — are complete and merged to main
(branch `fix/v2-migration-review-path`, 15 commits, merge `5c08a9b`; plus the
architecture report `2695f7d`). The migration gap that started that work (the
review/sync path still reading the v1 per-round layout) is closed. Coverage:

| Commits | Work |
|---|---|
| `8aaa14a` `faa8f38` `bfa0a36` `16f91a2` | Functional fixes A/B, D′, E, C |
| `0993721` | E follow-up (videotoolbox NameError, silently broken 4 commits) |
| `0de80f2` `e16aab3` | Cleanup G (ring buffer), I+F (storage API, `__version__`) |
| `bbc0769` `5ff48db` `7697642` `93888f4` | Smoke gate, J deletions, K logging, H pyqtgraph port |
| `43ece84` `fc13bd8` `d1ec67b` | CLAUDE.md + README documentation |

### The ten grounding items (commits on `fix/v2-migration-review-path`)

- **A/B [HIGH] — DONE in `8aaa14a`.** Live→Review handoff ported to the
  `continuous/` layout (was globbing v1 `Round {n}/` and returning silently);
  alignment was audio-aware at the time (<2 audio clips → simultaneous_start
  with offsets 0, 2+ → xcorr among the audio-bearing subset); `_clamp_ms`
  NaN/inf-guarded; `_xcorr_delay` returns 0 on empty input; OPEN SESSION FOLDER
  button and phase markers added to the Sync tab. *(Audio xcorr was later
  retired — see Step 4.)*
- **D′ [MED] — DONE in `faa8f38`.** Seek formula reverted to
  `max_norm_delay - d` (the old code seeked the latest-starting clip the most,
  misaligning by ~2× the true offset). The formula now lives in the
  capture-clock align path (`seek = axis_shift - start`); its invariant is
  guarded by `test_capture_clock.py::test_review_alignment_coregisters_all_three`.
- **E [MED] — DONE in `bfa0a36`.** Export failures surface the tail of ffmpeg's
  stderr + exit code (deque captured during the progress loop);
  `_detect_hw_encoder` requires a real test-encode probe, not just an
  `-encoders` listing, falling through to libx264.
- **C [MED] — DONE in `16f91a2`, SUPERSEDED in Step 4.** Real xcorr confidence
  was wired through the worker → `SourceOffset` → a `set_confidence` badge.
  Step 4 removed the xcorr source, so the computed confidence and the badge are
  gone. The discrimination test `test_xcorr_confidence.py` was deleted with the
  feature.
- **G [LOW] — DONE in `0de80f2`.** Ring buffer over-capacity ordering: the
  `n >= capacity` branch of `_RingBuffer.extend` left `_n_written` unaligned,
  so `to_array` mis-rotated. Latent (Muse chunks ≤12 into a 7680 buffer).
  Guarding test: `tests/test_ring_buffer.py`.
- **I + F [LOW] — DONE in `e16aab3`.** I: deleted the uncalled v1 per-phase
  path API (`phase_dir`/`side_cams_dir`/`pov_cams_dir`/`muse_dir`/
  `_phase_folder_name`) and rewrote `session_storage.py`'s stale docstrings to
  the continuous/ model. F: single `version.py` `__version__`, surfaced to the
  window title, the session manifest (was `"Phase 2"`), the README, and the
  CHANGELOG header. Guarded by `tests/test_smoke.py` (window-title check).
- **Smoke gate — ADDED in `bbc0769`.** `tests/test_smoke.py`: the
  import/construct floor and acceptance gate for deletions/refactors (the check
  that would have caught the videotoolbox bug, verified RED against the
  pre-`0993721` revision). E's follow-up fix itself is `0993721`.
- **J [CLEANUP] — DONE in `5ff48db`.** Deleted `sync_utils/eeg_module.py`
  (1207-line orphan, zero importers), a fully-dead `from sync_engine import …`
  line, and never-created `_drift_hb`/`_nudge_drift` machinery. Guarded by the
  smoke gate.
- **K [DIAGNOSTICS] — DONE in `7697642`.** Central logging in `log_setup.py`
  (console at INFO; `attach_session_log()` adds a DEBUG file handler in each
  session folder). All 56 `print()` calls → module loggers; 36 silent `except`
  blocks triaged (real-failure-hiding → warning/error, teardown races →
  debug/comment); control flow unchanged everywhere.
- **H [LOW] — DONE in `93888f4`.** `EEGReviewWidget` ported matplotlib →
  pyqtgraph: trace uploaded once at `load()`, playhead ticks move an
  `InfiniteLine` + shift the ViewBox; band-power welch recomputes per ≥0.2 s
  movement. Measured: waveform scrub 118→15 ms/tick, band-power 102→11 ms.
  Removed the repo's last matplotlib import. *(The widget itself was later
  folded into the merged Review & Label screen.)*

### Known limitations recorded at the time (now partly obsolete)

- **Sync confidence measured internal consistency, not correctness** — inherent
  to envelope xcorr. *(Moot: audio xcorr was retired in Step 4.)*
- **Encoder validation's "listed-but-no-hardware" fallback** (e.g. nvenc on a
  GPU-less box) is proven by forced-probe-failure unit tests only; it has never
  been exercised on real GPU-less hardware. *(Still true today.)*

---

## The v1.x → v2.x recording-model migration

The app was migrated from a v1.x **per-phase recording** model (separate
folders and clips per round: `baseline/`, `Round 1/`, each with `Side Cams/`,
`POV Cams/`, `Muse data/`) to a v2.x **one continuous take** model, which is
still the on-disk layout today:

```
<YYYY-MM-DD HH-MM A vs B>/
    continuous/
        Side Cams/*.mp4      # one MP4 per camera, whole session
        POV Cams/*.mp4
        Muse data/*.csv      # one CSV per Muse, whole session
    phase_markers.csv        # phase boundaries (t_session_seconds, phase_id, label, round_num, unix_time)
    session_manifest.json
```

- The **CAPTURE half** records one continuous take and writes
  `phase_markers.csv`; per-round folders are never created
  (`session_storage.py` documents this explicitly).
- The **REVIEW/SYNC half** was migrated to read `continuous/` +
  `phase_markers.csv` (fixed in `8aaa14a`). The historical migration gap — the
  Sync tab globbing nonexistent `Round {n}/` folders and returning silently —
  is where most of the original bugs lived.

A second structural consequence: all four in-app cameras (side AND POV) record
**no audio track** — originally via `cv2.VideoWriter`, now via a per-camera
ffmpeg subprocess (`usb_camera.FrameEncoder`, video-only). So audio-xcorr sync
could only ever work on externally recorded footage; in-app captures don't need
xcorr anyway — they start simultaneously and share `phase_markers.csv` time.
This is why audio xcorr was ultimately retired (Step 4).
