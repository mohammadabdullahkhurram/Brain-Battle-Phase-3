# Glossary

**What this page covers:** every term used across these docs and the app, in one
place — the plain-language word the UI uses on one side, the internal/technical
name on the other. The UI-vocabulary rows match `DESIGN.md`'s Vocabulary section;
the rest are terms that only show up in the code and developer docs.

## UI vocabulary (what the app says ← what it is internally)

The app shows the left column; the right column is the implementation term kept
out of the UI (see [design system](../developer-guide/design-system.md#vocabulary-plain-language-no-jargon)).

| UI word | Internal / technical name |
|---|---|
| Side camera 1 / Side camera 2 | Side cams (DSLR/capture-card feeds) |
| Boxer A view / Boxer B view | POV 01 / POV 02 |
| Headband | Muse-S EEG band |
| Brain activity | EEG |
| Rest before round 1 | baseline |
| Round N · Break | phase markers |
| Recording | session capture |
| Recordings folder | capture dir (`BRAIN_BATTLE_CAPTURE_DIR`) |
| Moment | a `mark_moment` timestamp, shown later as an unnamed label |
| "Weak contact: left ear" etc. | per-channel signal quality (`TP9` = left ear, `AF7` = left forehead, `AF8` = right forehead, `TP10` = right ear) |
| Worth checking | a contact-log weak period over 30 s |
| Your name / labeller | the typed name labels are saved under (`app_settings.labeller_name`); one labels file per labeller |
| Compare · "the same event" | another labeller's labels beside yours; two labels matched by `label_agreement` (same label, same boxer, start within the tolerance) |
| Hide names | `hide_names_on_screen` — show "Boxer A / Boxer B" instead of names |
| Replace boxer names with Boxer A / Boxer B | a name-free export (`hide_names`) |
| Get ready | the first New-recording screen (devices + plan); formerly the Connect devices and Set up session steps |
| Show in Finder / Show in Explorer | reveal in the file manager (`platform_utils`) |

## Technical terms (developer docs)

| Term | Meaning |
|---|---|
| **Capture-time clock** | The shared origin `(t0_monotonic, t0_unix, t0_lsl)` captured at record start and threaded into every source, so all frames and EEG samples share one axis ([ADR-0001](../decisions/adr-0001-capture-time-clock.md), [ADR-0017](../decisions/adr-0017-one-capture-clock.md)). |
| **`recording.xdf`** | One file per recording holding every signal, the markers, the labels and each video frame's time on one clock, in the XDF format ([ADR-0015](../decisions/adr-0015-lsl-and-xdf.md)). |
| **Stream** | One named signal in LSL / XDF terms — for example `BrainBattle_EEG_A` — with its own samples and timestamps. |
| **Timebase / scale** | The EEG display's two settings: how many seconds fit across, and how many microvolts the scale bar stands for. |
| **TP9, AF7, AF8, TP10, AUX** | The headband's inputs by their standard electrode names: left ear, left forehead, right forehead, right ear, and a spare input that normally carries nothing. |
| **`t_session_seconds`** | Seconds since the capture origin — the one axis video, EEG, markers, and labels all live on. |
| **Sidecar** | A camera's `<stem>.timestamps.csv` with one row per encoded frame (`frame_index`, `t_session_seconds`, `t_monotonic`, `t_unix`). |
| **Phase markers** | Rows in `phase_markers.csv` marking `BASELINE_START`, `ROUND_n_START`, `BREAK_n_START`, `SESSION_END`. |
| **Manifest** | `session_manifest.json` — boxers, config, `capture_clock`, `source_starts`, `demo`, moments, stopped-early. |
| **Co-registered EEG** | Both bands resampled onto one uniform 256 Hz `t_session_seconds` grid (`eeg_coregistered.csv`). |
| **Drift camera** | A camera whose true capture rate differs from its declared container rate (the demo's 30-vs-29.5 fps camera) — the case that drove [ADR-0005](../decisions/adr-0005-pyav-playback.md). |
| **Seek coalescing** | The decode worker keeping only the latest requested playhead, so scrubbing costs a couple of decodes, not one per move. |
| **Point / duration label** | A label kind: an instant (`point`, `t_end` null) or a span (`duration`, start + end) — [ADR-0006](../decisions/adr-0006-labels-v2.md). |
| **who (A / B / Both)** | The boxer a label belongs to. |
| **Interrupted session** | A recording with phases but no `SESSION_END` — kept, flagged, and surfaced once. |
| **Moment** | A timestamp marked during recording (M key), surfaced in Review as an unnamed label to name. |
| **Capture clock origin** | See *capture-time clock*. |
| **Analysis bundle** | The export zip (labels.csv, events.csv, co-registered EEG, raw media + sidecars, manifest, history, README). |
| **Simulation mode** | Fake cameras/headbands through the real capture paths, gated by `BRAIN_BATTLE_SIMULATE` ([ADR-0011](../decisions/adr-0011-simulated-devices.md)). |
| **Demo session** | A real v3 session folder written by `tools/generate_demo_session.py` for testing without hardware. |
| **History log** | The append-only `history.jsonl` per recording (who recorded/opened/labelled/exported). |
| **Capture pre-flight** | The checks before recording (ffmpeg present, disk space) that can block a start. |

## Acronyms

| Acronym | Expansion |
|---|---|
| **EEG** | Electroencephalography — the brain-activity signal from the headband. |
| **BLE** | Bluetooth Low Energy — how the Muse-S bands connect. |
| **UVC** | USB Video Class — the standard the cameras speak. |
| **ADR** | Architecture Decision Record — see [Decisions](../decisions/index.md). |
| **CIP** | Community Impact Project (Constructor University) — see [goals](goals.md). |
| **fps** | Frames per second. |
| **PPG** | Photoplethysmography — the Muse-S optical pulse sensor (optional, off by default, untested on real bands). |
| **LSL** | Lab Streaming Layer — the research standard for sending signals between programs on a network with a shared clock. |
| **XDF** | Extensible Data Format — the file format LSL recordings are saved in; read by EEGLAB, MNE-Python and pyxdf. |
| **µV** | Microvolt — a millionth of a volt, the unit of EEG amplitude. |
