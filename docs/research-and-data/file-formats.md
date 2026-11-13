# File format reference

**What this page covers:** the exact format of every file in a recording and
in an export — each field, column, unit, and what it means, with a short real
example. This is the lookup reference; for where these files sit on disk see
[Recording folder anatomy](recording-folder.md).

Every field name below is given exactly as it appears in the file. Numbers and
examples are from a real generated session, verified against the code that
writes each file (the module is cited in each section).

> **Clock values.** Several files carry timestamps. The one you almost always
> want is `t_session_seconds` — seconds since the session started, shared by
> the videos, the EEG, and the markers. The accuracy of that shared clock is
> covered in [Timing & accuracy](timing-and-accuracy.md); its EEG side is **Not
> yet verified on hardware**.

## `session_manifest.json`

The session's record of facts — written and kept up to date during recording
by `session_storage.SessionLayout._write_manifest`. One JSON object:

| Key | Type | Meaning |
|---|---|---|
| `app` | string | Always `"Brain Battle"` |
| `version` | string | App version that recorded it, e.g. `"3.0.0-dev"` |
| `started_at` | string | Local start time, ISO 8601 (seconds precision) |
| `started_unix` | number | Same instant as a Unix timestamp (seconds) |
| `boxers` | object | `A` and `B`, each `{name, headband}` (either may be `null`) |
| `demo` | bool | `true` only for generated demo data (shows the Demo badge) |
| `moments` | array | "Mark this moment" presses — each `{t_session_seconds, unix_time}` |
| `stopped_early` | bool | `true` if the operator pressed Stop and save before the plan finished |
| `stopped_t_session` | number \| null | Where it was stopped (session seconds), else `null` |
| `capture_clock` | object | The shared clock origin, one instant on three time scales: `{t0_monotonic, t0_unix, t0_lsl}` (`t0_lsl` is `null` in recordings made before version 3.1) |
| `source_starts` | object | Per source, its measured first sample: `{first_unix, first_t_session}` |
| `config` | object | The round plan: `{num_rounds, round_sec, break_sec, baseline_sec}` (seconds) |
| `phases` | array | One entry per baseline / round / break (see below) |

Inside `boxers.A` / `boxers.B`: `name` (string or `null`) and `headband`
(string or `null`). Each `phases` entry (a `session_storage.PhaseRecord`):

| Key | Type | Meaning |
|---|---|---|
| `phase` | string | `"baseline"`, `"round"`, or `"break"` |
| `label` | string | Human label, e.g. `"ROUND 1"` |
| `round_num` | int | Round number (`0` for baseline) |
| `folder` | string | Relative subfolder — `"continuous"` for every phase today |
| `planned_duration` | int | Configured length, seconds |
| `start_unix` | number | Phase start, Unix seconds |
| `start_monotonic` | number | Phase start on the monotonic clock |
| `end_unix` | number \| null | Phase end, Unix seconds (`null` until it closes) |
| `end_monotonic` | number \| null | Phase end on the monotonic clock |

Example (trimmed):

```json
{
  "app": "Brain Battle",
  "version": "3.0.0-dev",
  "started_at": "2026-11-09T17:26:10",
  "started_unix": 1794241570.96,
  "boxers": {
    "A": {"name": "Sam", "headband": "MuseS-7538"},
    "B": {"name": "Alex", "headband": "MuseS-7564"}
  },
  "demo": false,
  "moments": [{"t_session_seconds": 95.1, "unix_time": 1794241666.1}],
  "stopped_early": false,
  "stopped_t_session": null,
  "capture_clock": {
    "t0_monotonic": 1352412.95, "t0_unix": 1794241570.96,
    "t0_lsl": 1352412.95
  },
  "source_starts": {
    "EEG_01": {"first_unix": 1794241571.24, "first_t_session": 0.28}
  },
  "config": {
    "num_rounds": 2, "round_sec": 60,
    "break_sec": 20, "baseline_sec": 20
  },
  "phases": [{
    "phase": "baseline", "label": "BASELINE", "round_num": 0,
    "folder": "continuous", "planned_duration": 20,
    "start_unix": 1794241570.96, "start_monotonic": 1352412.95,
    "end_unix": 1794241590.96, "end_monotonic": 1352432.95
  }]
}
```

## `phase_markers.csv`

The instant each phase begins, on the shared session clock — written by
`session_storage.SessionLayout.append_marker`. One header row, then one row per
transition.

| Column | Type | Unit | Meaning | Example |
|---|---|---|---|---|
| `t_session_seconds` | float | seconds | Time since session start | `20.000` |
| `phase_id` | string | — | `baseline` / `round` / `break` / `done` | `round` |
| `label` | string | — | The marker name (see below) | `ROUND_1_START` |
| `round_num` | int | — | Round number (`0` for baseline / end) | `1` |
| `unix_time` | float | seconds | Wall-clock time the row was written | `1794241570.962` |

The `label` values are: `BASELINE_START`; `ROUND_1_START`, `ROUND_2_START`, …
(`ROUND_n_START`); `BREAK_1_START`, `BREAK_2_START`, … (`BREAK_n_START`); and
`SESSION_END` at the very end. Example:

```csv
t_session_seconds,phase_id,label,round_num,unix_time
0.000,baseline,BASELINE_START,0,1794241570.962
20.000,round,ROUND_1_START,1,1794241570.962
80.000,break,BREAK_1_START,1,1794241570.962
100.000,round,ROUND_2_START,2,1794241570.962
160.000,done,SESSION_END,0,1794241570.962
```

## Camera videos

Four `.mp4` files per session (two under `Side Cams/`, two under `POV Cams/`),
written by `usb_camera.FrameEncoder`. Technical details, verified against that
encoder's ffmpeg command:

| Property | Value |
|---|---|
| Container | MP4, **fragmented** (`+frag_keyframe+empty_moov+default_base_moof`, `-frag_duration 500000` → a self-contained fragment ~every 0.5 s, so a crash loses at most the last fragment) |
| Codec | H.264 (hardware encoder where available, else `libx264`), pixel format `yuv420p` |
| Audio | **None** — recorded with `-an` |
| Keyframe interval | ~1 second (`-g` = the frame rate) |
| Frame rate | From the chosen video quality (30 fps for all presets today) |
| Resolution / bitrate | Per video-quality preset — see the table below |

The resolution and target bitrate come from the Settings video-quality preset
(`app_settings.VIDEO_QUALITY`), never hard-coded:

| Quality | Resolution | Frame rate | Target bitrate |
|---|---|---|---|
| Standard | 960 × 540 | 30 fps | 5.0 Mbps |
| High | 1280 × 720 | 30 fps | 8.0 Mbps |
| Light | 640 × 360 | 30 fps | 2.5 Mbps |

### Camera timestamp sidecars

Each video has a `.timestamps.csv` next to it (same base name) with one row per
recorded frame — written by `usb_camera.FrameEncoder._write_sidecar_row`. This
is what places a video frame on the session clock.

| Column | Type | Unit | Meaning | Example |
|---|---|---|---|---|
| `frame_index` | int | — | 0-based frame number in this video | `0` |
| `t_session_seconds` | float | seconds | Time since session start | `0.071412` |
| `t_monotonic` | float | seconds | The capture's monotonic-clock reading | `1352413.022679` |
| `t_unix` | float | seconds | The same frame as a Unix timestamp | `1794241571.032568` |

```csv
frame_index,t_session_seconds,t_monotonic,t_unix
0,0.071412,1352413.022679,1794241571.032568
1,0.104746,1352413.056013,1794241571.065901
```

> One camera in the generated demo deliberately drifts (declares 30 fps but
> captures at ~29.5 fps) so analysis code is tested against a real drift case.
> Prefer the **per-frame** sidecar times over multiplying `frame_index` by a
> nominal frame rate.

## EEG recordings (`continuous/Muse data/*.csv`)

One CSV per boxer's headband, streamed during recording by `eeg_module`
(`_MuseDirectReader.start_recording`). The Muse-S streams five channels at
**256 Hz**. Values are raw EEG amplitudes in **microvolts (µV)**.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `TP9` | float | µV | Left ear sensor |
| `AF7` | float | µV | Left forehead sensor |
| `AF8` | float | µV | Right forehead sensor |
| `TP10` | float | µV | Right ear sensor |
| `AUX` | float | µV | Spare/auxiliary channel (usually unused) |
| `timestamps` | float | seconds | The sample's time as Unix seconds, taken from the app's one capture clock (`capture/clock.py`) and smoothed per band by muselsl |
| `t_session_seconds` | float | seconds | The sample on the shared session clock (`timestamps − capture_clock.t0_unix`) |

The four EEG sensors map to the plain-language names the app shows (per
`contact_quality.SENSOR_NAME`): `TP9` = **left ear**, `AF7` = **left
forehead**, `AF8` = **right forehead**, `TP10` = **right ear**.

```csv
TP9,AF7,AF8,TP10,AUX,timestamps,t_session_seconds
0.875,-5.660,6.332,-6.652,1.185,1794241571.236458,0.275303
-13.274,9.513,-6.448,10.994,-2.012,1794241571.240364,0.279209
```

> **Not yet verified on hardware.** A sample is stamped when its Bluetooth
> packet reaches the computer; whether that is accurate to within tens of
> milliseconds is unconfirmed without a real Muse band (`eeg_module` logs this
> warning at every EEG session start). The derived `t_session_seconds` inherits
> that. See [Timing & accuracy](timing-and-accuracy.md).

### Movement and pulse sensors (`continuous/Muse sensors/*.csv`)

Optional (Settings → *Extra signals and sharing*). One CSV per headband per
sensor, named after that headband's EEG file with `_ACC`, `_GYRO` or `_PPG`
added, written the same crash-safe way by the same reader. Every file ends with
the same two time columns as the EEG: `timestamps` and `t_session_seconds`.

| File | Columns before the time columns | Rate | Unit | What it is |
|---|---|---|---|---|
| `…_ACC.csv` | `X`, `Y`, `Z` | 52 Hz | g | Accelerometer — head movement and impacts |
| `…_GYRO.csv` | `X`, `Y`, `Z` | 52 Hz | degrees per second | Gyroscope — head rotation |
| `…_PPG.csv` | `PPG1`, `PPG2`, `PPG3` | 64 Hz | raw sensor counts | The optical pulse sensor's three light channels |

```csv
X,Y,Z,timestamps,t_session_seconds
0.021,0.048,0.979,1794241571.236458,0.275303
```

The headband delivers three movement samples per Bluetooth packet under one
arrival time; the app spreads them back over the three sample periods they
cover. **Not yet verified on hardware** — see [Known limitations](limitations.md).

## `recording.xdf`

Everything in the recording on **one clock**, in XDF — the file format of the
Lab Streaming Layer (LSL) ecosystem, which EEGLAB, MNE-Python and pyxdf open
directly. Built by `session_xdf.build_session_xdf` from the files described
above (it is a derived copy; see
[ADR-0015](../decisions/adr-0015-lsl-and-xdf.md)). How to load it is under
[Loading the data](loading-the-data.md).

| Stream name | Type | Channels | Rate | Contents |
|---|---|---|---|---|
| `BrainBattle_EEG_A`, `BrainBattle_EEG_B` | `EEG` | `TP9`, `AF7`, `AF8`, `TP10`, `AUX` | 256 Hz | Each boxer's EEG, float32, in microvolts |
| `BrainBattle_ACC_A` / `_B` | `ACC` | `X`, `Y`, `Z` | 52 Hz | Accelerometer (g), if recorded |
| `BrainBattle_GYRO_A` / `_B` | `GYRO` | `X`, `Y`, `Z` | 52 Hz | Gyroscope (degrees per second), if recorded |
| `BrainBattle_PPG_A` / `_B` | `PPG` | `PPG1`, `PPG2`, `PPG3` | 64 Hz | Pulse sensor, if recorded |
| `BrainBattle_Markers` | `Markers` | one string | irregular | The `phase_markers.csv` labels (`BASELINE_START` … `SESSION_END`) and `MOMENT` for each un-named "Mark this moment" |
| `BrainBattle_Labels` | `Markers` | one string | irregular | The placed labels (present once there are any) — see below |
| `BrainBattle_Video_SIDE_01`, `BrainBattle_Video_SIDE_02`, `BrainBattle_Video_POV_01`, `BrainBattle_Video_POV_02` | `VideoFrames` | `frame_index` (int32) | irregular | One sample per recorded video frame: the value is the frame's number in that camera's MP4, the timestamp is when it was captured |

**Timestamps** are seconds on the LSL clock: `capture_clock.t0_lsl +
t_session_seconds`. Every stream's description carries the session start as
`session_start` (inside a `brain_battle` element, next to `recording`,
`app_version`, and — per stream — `boxer` and `headband`, or `video_file` and
`measured_fps`), so

    t_session_seconds = timestamp − session_start

and the first marker, `BASELINE_START`, comes a few hundredths of a second
after session time 0 (the moment the devices were told to start). Recordings made
before version 3.1 have no `t0_lsl`; their XDF timestamps are simply
`t_session_seconds`.

**Label events** are written as text: `<label>/<who>` for a moment label (for
example `Jab/A`), and two events — `<label>/<who>/start` and
`<label>/<who>/end` — for a label that lasts a while (`Clinch/Both/start`).

The same stream names are published **live** over LSL while the New recording
screens are open (`lsl_outlets.LslPublisher`), so a file LabRecorder makes from
them matches this one; the live set has no `BrainBattle_Labels` stream (labels
are placed later).

## `contact_quality.csv`

A per-sensor contact-quality log, sampled about once per second during
recording — written by `contact_log.ContactLogWriter`. One row per boxer per
tick.

| Column | Type | Meaning |
|---|---|---|
| `t_session_seconds` | float | Time since session start |
| `boxer` | string | `A` or `B` |
| `summary` | string | Plain-language summary for that tick (e.g. `Signal good`) |
| `TP9` | string | Left-ear status: `good` / `flat` / `clipped` / `noisy` |
| `AF7` | string | Left-forehead status |
| `AF8` | string | Right-forehead status |
| `TP10` | string | Right-ear status |

The four status values come from `contact_quality.channel_status`: `good`,
`flat` (electrode picking nothing up), `clipped` (amplifier railed), or `noisy`
(motion/looseness swamping the signal). Real-signal quality during boxing is
**Not yet verified on hardware**.

## `labels.json`

The labels placed in Review & Label — schema **version 2**, written by
`labels_store.LabelStore`. One JSON object:

| Key | Type | Meaning |
|---|---|---|
| `schema_version` | int | `2` |
| `time_axis` | string | Always `t_session_seconds` |
| `labels` | array | The placed labels (below) |

Each entry in `labels` (from `labels_store.new_label`):

| Field | Type | Unit | Meaning | Example |
|---|---|---|---|---|
| `id` | string | — | Unique id (hex) | `"a1b2c3…"` |
| `label` | string | — | The label text | `"Jab"` |
| `who` | string | — | `A`, `B`, or `Both` | `"A"` |
| `kind` | string | — | `point` (a moment) or `duration` (lasts a while) | `"point"` |
| `t_start` | float | seconds | When it starts (session clock) | `95.100` |
| `t_end` | float \| null | seconds | When it ends; `null` for a point label | `null` |
| `color` | string | — | `#RRGGBB` swatch | `"#3D4FD6"` |
| `created_by` | string | — | Who placed it (OS user, or `migrated`) | `"casey"` |
| `created_at` | string | — | When it was placed, ISO 8601 | `"2026-11-09T18:02:11"` |

```json
{"schema_version": 2, "time_axis": "t_session_seconds",
 "labels": [{"id": "a1b2c3", "label": "Jab", "who": "A",
             "kind": "point", "t_start": 95.1, "t_end": null,
             "color": "#3D4FD6", "created_by": "casey",
             "created_at": "2026-11-09T18:02:11"}]}
```

A version-1 `labels.json` is migrated to v2 on first open (the original is kept
as `labels.json.v1.bak`).

## `label_list.json`

The **shared** team label vocabulary — one file in the recordings folder (not
per session), written by `label_vocab`. An array of entries; each:

| Field | Type | Meaning |
|---|---|---|
| `key` | int | Number key `1`–`9` to place it (matches display order) |
| `name` | string | The label text, e.g. `"Jab"` |
| `color` | string | `#RRGGBB` swatch |
| `kind` | string | `point` or `duration` |

```json
[{"key": 1, "name": "Jab", "color": "#3D4FD6", "kind": "point"},
 {"key": 6, "name": "Clinch", "color": "#BE185D", "kind": "duration"}]
```

## `history.jsonl`

An append-only audit log — one JSON object **per line** (JSON Lines), written
by `session_history.log_event`. Each line:

| Field | Type | Meaning |
|---|---|---|
| `time` | string | Local time, ISO 8601 with timezone offset |
| `user` | string | OS login name at the time |
| `user_source` | string | How `user` was obtained (always `os`) |
| `app_version` | string | App version that wrote the line |
| `event` | string | The event name (see table) |
| `details` | object | Event-specific extra data (may be `{}`) |

The `event` values (from `session_history`):

| Event | When it's logged | Typical `details` |
|---|---|---|
| `recording_started` | A recording begins | boxers, config |
| `recording_finished` | A recording ends cleanly | duration, whether stopped early |
| `recording_interrupted` | A recording didn't close cleanly | where it stopped |
| `recording_recovered` | An interrupted recording was detected/recovered | — |
| `opened_for_review` | The session is opened in Review & Label | — |
| `moment_marked` | "Mark this moment" was pressed | the moment time |
| `labels_added` | One or more labels placed | count / label info |
| `labels_removed` | One or more labels deleted | count / label info |
| `labels_migrated` | A v1 `labels.json` was upgraded to v2 | counts |
| `labels_exported` | Labels were written out | — |
| `export_created` | An analysis bundle was built | the zip name |

A twelfth event, `interrupted_dismissed`, is logged when you dismiss the
interrupted-recording banner in Sessions.

```json
{"time": "2026-11-09T17:26:10.9+00:00", "user": "casey",
 "user_source": "os", "app_version": "3.0.0-dev",
 "event": "recording_started", "details": {}}
```

## The export zip

"Export for analysis" builds a self-contained zip — written by
`export_module.SessionExportWorker`. It's a copy of the recording's key files
plus three derived CSVs and the XDF. Contents:

| Entry | What it is |
|---|---|
| `recording.xdf` | [Everything on one clock](#recordingxdf), rebuilt at export time so it includes the labels |
| `labels.csv` | The placed labels, one row each (columns below) |
| `events.csv` | Phase markers + labels merged onto one timeline |
| `eeg_coregistered.csv` | Both boxers' EEG resampled onto one shared clock |
| `eeg/` | The raw per-boxer EEG CSVs, unchanged |
| `sensors/` | The movement / pulse CSVs, unchanged (only if they were recorded) |
| `videos/` | The camera `.mp4` files and their `.timestamps.csv` sidecars |
| `session_manifest.json` | A copy of the manifest |
| `phase_markers.csv` | A copy of the markers |
| `history.jsonl` | A copy of the history log |
| `README.txt` | A plain-text description of the bundle |
| `combined_2x2.mp4` | *Optional* — a 2×2 montage of the four cameras (off by default) |

### `labels.csv`

The `labels.json` entries flattened to CSV (`export_module._LABELS_V2_COLUMNS`).
Columns: `id`, `label`, `who`, `kind`, `t_start`, `t_end`, `color`,
`created_by`, `created_at` — same meanings as [`labels.json`](#labelsjson). A
`null` `t_end` is written as an empty cell.

### `events.csv`

Phase markers and labels on **one** timeline, sorted by start time (phases
before labels at the same instant) — from `labels_store.events_timeline`.

| Column | Type | Unit | Meaning | Example |
|---|---|---|---|---|
| `t_start` | float | seconds | When the event starts | `20.000` |
| `t_end` | float | seconds | When it ends (empty for a marker or a point label) | `` |
| `source` | string | — | `phase` (a round marker) or `label` (a placed label) | `phase` |
| `who` | string | — | `A` / `B` / `Both` for labels; empty for markers | `A` |
| `label` | string | — | The marker or label text | `ROUND_1_START` |

### `eeg_coregistered.csv`

Both boxers' EEG linearly interpolated onto **one** uniform 256 Hz
`t_session_seconds` grid — from `export_module.build_coregistered_eeg`. This is
usually the file you want for a between-brain comparison, because both boxers
share one clock and one set of rows.

| Column | Type | Unit | Meaning |
|---|---|---|---|
| `t_session_seconds` | float | seconds | The shared time grid |
| `TP9_01`, `AF7_01`, `AF8_01`, `TP10_01`, `AUX_01` | float | µV | Boxer A's channels (the `_01` band) |
| `TP9_02`, `AF7_02`, `AF8_02`, `TP10_02`, `AUX_02` | float | µV | Boxer B's channels (the `_02` band) |
| `phase_id` | string | — | The active phase at that row (forward-filled) |
| `phase_label` | string | — | The active phase's label |
| `marker` | string | — | Non-empty only on the exact row of a phase boundary |

Each band's channels are `NaN` outside that band's own recorded range (an
honest "no sample here"). `_01` is Boxer A's headband and `_02` is Boxer B's,
as recorded in that session's manifest (`boxers.A.headband` /
`boxers.B.headband`).

> **Not yet verified on hardware.** The *relative* alignment between the two
> boxers in `eeg_coregistered.csv` depends on the EEG clock discussed above and
> is measured-but-hardware-unverified. For what this means for different
> analysis methods, see [Timing & accuracy](timing-and-accuracy.md).
