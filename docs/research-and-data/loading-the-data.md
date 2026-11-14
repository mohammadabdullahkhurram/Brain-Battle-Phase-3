# Loading the data

**What this page covers:** copy-paste examples for loading a recording for
analysis. **Start with `recording.xdf`** — the one file the app makes for every
recording, with every signal, the round markers, every labeller's labels and
every video frame's time on one clock. It is in the standard Lab Streaming
Layer format, so EEGLAB, MNE-Python and pyxdf open it directly; nothing else
needs to be installed or run while recording. After that come the **tables** —
the CSV files, in Python (pandas) and R (readr/dplyr) — for people who prefer
plain spreadsheets. The Python examples (except the MNE one) are run
automatically against a real export by the test suite
(`tests/test_research_docs.py`), so they stay correct.

These all work on an **unzipped export** (see
[the export zip](file-formats.md#the-export-zip)). Set `export_dir` to your
unzipped export folder; the examples build on each other.

**Which should I use?** `recording.xdf` if you work in EEGLAB or MNE, need to
line up video frames with EEG, or want each labeller's labels as events. The
tables — `eeg_coregistered.csv` (both boxers' EEG on one clock), `events.csv`
and `labels.csv` (round markers and labels, with a `labeller` column) — if you
want pandas or R. Both describe the same recording on the same
`t_session_seconds` clock.

## The XDF file (`recording.xdf`)

`recording.xdf` holds every stream of the recording — both boxers' EEG, the
headbands' movement sensors, the round markers, each labeller's labels, and one
stream per camera giving each video frame's time — all on one clock. The app
builds it by itself when a recording finishes and again, with the labels, on
every export. The stream list is in
the [file format reference](file-formats.md#recordingxdf).

### Load it in Python (pyxdf)

`pip install pyxdf`. Every stream comes back as a dictionary with its samples
(`time_series`), their times (`time_stamps`) and its description (`info`):

```python
import os
import numpy as np
import pyxdf

export_dir = "/path/to/your/unzipped/export"

xdf_path = os.path.join(export_dir, "recording.xdf")
streams, header = pyxdf.load_xdf(xdf_path)
by_name = {s["info"]["name"][0]: s for s in streams}
print(sorted(by_name))


def session_start(stream):
    """The recording's start on the file's clock (same in every stream)."""
    ours = stream["info"]["desc"][0]["brain_battle"][0]
    return float(ours["session_start"][0])


def session_time(stream):
    """A stream's timestamps as t_session_seconds."""
    return stream["time_stamps"] - session_start(stream)


eeg_a = by_name["BrainBattle_EEG_A"]
channels = [c["label"][0]
            for c in eeg_a["info"]["desc"][0]["channels"][0]["channel"]]
print(channels, eeg_a["time_series"].shape, "microvolts at",
      eeg_a["info"]["nominal_srate"][0], "Hz")
```

Timestamps in the file are on the Lab Streaming Layer clock; subtracting the
session start turns them into the same `t_session_seconds` used by every CSV.

### Event markers

The round markers and the labels are marker streams — a time and a text each.
Each labeller has their own label stream, `BrainBattle_Labels_<labeller>`, and
its description says who they are:

```python
markers = by_name["BrainBattle_Markers"]
for t, text in zip(session_time(markers), markers["time_series"]):
    print(f"{t:8.3f} s  {text[0]}")

# the labels, one stream per labeller — e.g. "Jab/A", "Clinch/Both/start"
label_streams = {name: s for name, s in by_name.items()
                 if name.startswith("BrainBattle_Labels_")}
for name, placed in label_streams.items():
    who = placed["info"]["desc"][0]["brain_battle"][0]["labeller"][0]
    print("labelled by", who)
    for t, text in zip(session_time(placed), placed["time_series"]):
        print(f"{t:8.3f} s  {text[0]}")

# the boxers' names (absent from an export made with names replaced)
ours = eeg_a["info"]["desc"][0]["brain_battle"][0]
print("Boxer A:", ours.get("boxer_a_name", ["Boxer A"])[0])
```

### Line up video frames with EEG

Each camera has a stream with **one sample per video frame**: the value is the
frame's number in that camera's MP4 and the timestamp is when it was captured.
Because EEG is on the same clock, the EEG sample belonging to a frame — or the
frame belonging to an EEG sample — is a nearest-time lookup:

```python
# the camera whose file is videos/session_SIDE_01.mp4
video = by_name["BrainBattle_Video_SIDE_01"]
frame_numbers = video["time_series"][:, 0]
frame_times = video["time_stamps"]
eeg_times = eeg_a["time_stamps"]

# for every video frame: the index of the EEG sample at that instant
eeg_index_for_frame = np.clip(np.searchsorted(eeg_times, frame_times),
                              0, len(eeg_times) - 1)

# the other way round: which frame was on screen at ROUND_1_START?
texts = [m[0] for m in markers["time_series"]]
t_round1 = markers["time_stamps"][texts.index("ROUND_1_START")]
frame_i = np.searchsorted(frame_times, t_round1)
frame_at_round1 = int(frame_numbers[frame_i])
eeg_at_round1 = eeg_a["time_series"][np.searchsorted(eeg_times, t_round1)]
print("ROUND_1_START is frame", frame_at_round1,
      "of session_SIDE_01.mp4; EEG there:", eeg_at_round1)
```

Use the frame stream rather than "frame number ÷ 30": cameras do not hold their
nominal rate exactly, and each stream records the rate actually measured
(`measured_fps` in its description).

### MNE-Python

MNE has no built-in XDF reader; load with pyxdf and wrap the stream in a `Raw`
object. This example is **not run automatically** (MNE is not installed with
the app) but was checked against a generated recording:

```py
import mne
import numpy as np
import pyxdf

streams, _ = pyxdf.load_xdf("recording.xdf")
by_name = {s["info"]["name"][0]: s for s in streams}
eeg = by_name["BrainBattle_EEG_A"]
channel_list = eeg["info"]["desc"][0]["channels"][0]["channel"]
names = [c["label"][0] for c in channel_list]
sfreq = float(eeg["info"]["nominal_srate"][0])

# four EEG channels + AUX (kept as "misc"); MNE wants volts, not microvolts
info = mne.create_info(names, sfreq, ch_types=["eeg"] * 4 + ["misc"])
raw = mne.io.RawArray(eeg["time_series"].T * 1e-6, info)

# event markers → MNE annotations, timed from the first EEG sample
t_first = eeg["time_stamps"][0]
# (to use one labeller's labels only, keep just their stream)
for stream_name, m in by_name.items():
    if (stream_name == "BrainBattle_Markers"
            or stream_name.startswith("BrainBattle_Labels_")):
        raw.annotations.append(m["time_stamps"] - t_first, 0.0,
                               [x[0] for x in m["time_series"]])

raw.filter(1.0, 45.0)
raw.plot()          # the traces, with the round markers and labels
events, event_id = mne.events_from_annotations(raw)
```

For a two-brain analysis build a second `Raw` from `BrainBattle_EEG_B` the same
way; `raw.crop` both to their common time span first, since each headband starts
a fraction of a second apart. The [MNELAB](https://github.com/cbrnr/mnelab)
application can also open the file with a few clicks.

### EEGLAB (MATLAB)

Install the **xdfimport** plugin (*File → Manage EEGLAB extensions*), then
either use *File → Import data → Using EEGLAB functions and plugins → From .XDF
file*, or from the command line (**not run automatically**):

```matlab
% One boxer's EEG, with the marker streams turned into EEGLAB events
EEG = pop_loadxdf('recording.xdf', 'streamname', 'BrainBattle_EEG_A');
EEG = eeg_checkset(EEG);
unique({EEG.event.type})      % BASELINE_START, ROUND_1_START, Jab/A, ...
pop_eegplot(EEG, 1, 1, 1);        % scroll the data with its events

% Everything, incl. the video-frame streams (the xdf library's reader)
streams = load_xdf('recording.xdf');
names = cellfun(@(s) s.info.name, streams, 'UniformOutput', false);
video = streams{strcmp(names, 'BrainBattle_Video_SIDE_01')};
% video.time_series = frame numbers; video.time_stamps = their times
```

`pop_loadxdf` imports one EEG stream at a time; load `BrainBattle_EEG_B` the
same way for the other boxer. Channel names (TP9, AF7, AF8, TP10, AUX) come from
the file; assign locations with *Edit → Channel locations* (AUX has none).

### Optional, advanced: recording with LabRecorder as well

You do not need this. The app's own `recording.xdf` is the file to use.

While the New recording screens are open, the app also publishes the same
streams live on the network (switchable in Settings). If another Lab Streaming
Layer device — a different sensor, a stimulus program — has to go into the same
file, start [LabRecorder](https://github.com/labstreaminglayer/App-LabRecorder),
tick the `BrainBattle_…` streams and that device's stream, and press Start
before you begin the recording. The examples above work on a LabRecorder-made
file unchanged, except `session_start`, which only the app's own file carries
(use the time of the `BASELINE_START` marker instead), and labels, which are
placed later and so are only in the app's file.

## The tables, in Python (pandas)

### Load the two main files

```python
import pandas as pd

eeg = pd.read_csv(os.path.join(export_dir, "eeg_coregistered.csv"))
events = pd.read_csv(os.path.join(export_dir, "events.csv"))

print("eeg:", eeg.shape, "columns:", list(eeg.columns)[:3], "…")
print(events.head())
```

`eeg` has one `t_session_seconds` column, then each boxer's channels suffixed
`_01` (Boxer A) / `_02` (Boxer B), then
`phase_id`, `phase_label`, `marker`. `events` has `t_start`, `t_end`, `source`,
`who`, `label`, `labeller`. When more than one person labelled the recording,
pick one before analysing — `events[events["labeller"].isin(["", "Maya K"])]`
keeps the round markers and Maya's labels — or you will count events twice.

### Select one round

Round boundaries are the phase markers. To pull the EEG for round 1, find
`ROUND_1_START` and the next marker after it:

```python
phases = events[events["source"] == "phase"]
is_round1 = phases["label"] == "ROUND_1_START"
round1_start = phases.loc[is_round1, "t_start"].iloc[0]
later = phases[phases["t_start"] > round1_start]["t_start"]
round1_end = (later.iloc[0] if len(later)
              else eeg["t_session_seconds"].iloc[-1])

round1 = eeg[(eeg["t_session_seconds"] >= round1_start) &
             (eeg["t_session_seconds"] < round1_end)]
print(f"round 1: {round1_start:.1f}-{round1_end:.1f}s,"
      f" {len(round1)} EEG rows")
```

(You could also filter on the forward-filled `phase_id == "round"` column, but
that selects *every* round at once; the markers let you pick a specific one.)

### Cut epochs around each boxer's labels

A ±2-second window of a boxer's four channels around every label placed on that
boxer (or on `Both`). Band `_01` is Boxer A:

```python
WINDOW = 2.0
boxer_a_channels = ["TP9_01", "AF7_01", "AF8_01", "TP10_01"]

labels = events[events["source"] == "label"]
epochs = []
for _, lab in labels.iterrows():
    if lab["who"] not in ("A", "Both"):
        continue
    t = lab["t_start"]
    window = eeg[(eeg["t_session_seconds"] >= t - WINDOW) &
                 (eeg["t_session_seconds"] <= t + WINDOW)]
    epochs.append((lab["label"], t, window[boxer_a_channels]))

print(f"cut {len(epochs)} epochs around Boxer A labels")
```

### Join labels to the brain data

Attach, to each label, the nearest co-registered EEG row — a `merge_asof` on
time gives you both boxers' channels at each label instant:

```python
labels_sorted = labels.sort_values("t_start")
eeg_sorted = eeg.sort_values("t_session_seconds")

joined = pd.merge_asof(
    labels_sorted, eeg_sorted,
    left_on="t_start", right_on="t_session_seconds",
    direction="nearest")

print(joined[["label", "who", "t_start", "TP9_01", "TP9_02"]].head())
```

## R (readr / dplyr)

The same four steps in R. These are **not automatically tested** (they run only
where `Rscript` and the `readr`/`dplyr` packages are installed), but mirror the
Python above:

```r
library(readr)
library(dplyr)

export_dir <- "/path/to/your/unzipped/export"

eeg <- read_csv(file.path(export_dir, "eeg_coregistered.csv"))
events <- read_csv(file.path(export_dir, "events.csv"))

# ── select one round ──
phases <- events %>% filter(source == "phase")
round1_start <- phases %>%
  filter(label == "ROUND_1_START") %>% pull(t_start) %>% first()
round1_end <- phases %>%
  filter(t_start > round1_start) %>% pull(t_start) %>% first()
round1 <- eeg %>% filter(t_session_seconds >= round1_start,
                         t_session_seconds < round1_end)

# ── epochs around Boxer A labels (band _01), ±2 s ──
labels <- events %>% filter(source == "label", who %in% c("A", "Both"))
window_of <- function(t) {
  eeg %>%
    filter(t_session_seconds >= t - 2, t_session_seconds <= t + 2) %>%
    select(t_session_seconds, TP9_01, AF7_01, AF8_01, TP10_01)
}

# ── join each label to the nearest EEG row ──
idx <- sapply(labels$t_start,
              function(t) which.min(abs(eeg$t_session_seconds - t)))
joined <- bind_cols(labels, eeg[idx, c("TP9_01", "TP9_02")])
```

## A reminder on timing

Everything above treats both boxers as sharing one clock — which is the point
of `recording.xdf` and `eeg_coregistered.csv`. The *relative* EEG alignment between the two boxers
is **Not yet verified on hardware**; see [Timing & accuracy](timing-and-accuracy.md)
for what that means for phase-based versus power/envelope analyses before you
read too much into tight between-brain timing.
