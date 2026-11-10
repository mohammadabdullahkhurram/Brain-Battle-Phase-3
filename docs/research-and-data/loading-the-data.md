# Loading the data

**What this page covers:** copy-paste examples for loading an export in Python
(pandas) and R (readr/dplyr) — loading the co-registered EEG and the events,
selecting one round, cutting windows (epochs) around labels for each boxer, and
joining labels to the brain data. The Python examples are run automatically
against a real export by the test suite (`tests/test_docs.py`), so they stay
correct.

These all work on an **unzipped export** (see
[the export zip](file-formats.md#the-export-zip)). The two files you'll use
most are `eeg_coregistered.csv` (both boxers' EEG on one clock) and
`events.csv` (round markers and labels on one timeline). Set `export_dir` to
your unzipped export folder; the examples build on each other.

## Python (pandas)

### Load the two main files

```python
import os
import pandas as pd

export_dir = "/path/to/your/unzipped/export"

eeg = pd.read_csv(os.path.join(export_dir, "eeg_coregistered.csv"))
events = pd.read_csv(os.path.join(export_dir, "events.csv"))

print("eeg:", eeg.shape, "columns:", list(eeg.columns)[:3], "…")
print(events.head())
```

`eeg` has one `t_session_seconds` column, then each boxer's channels suffixed
`_01` / `_02` (band order — cross-check names against the manifest), then
`phase_id`, `phase_label`, `marker`. `events` has `t_start`, `t_end`, `source`,
`who`, `label`.

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
boxer (or on `Both`). Band `_01` here is treated as Boxer A — confirm against
the manifest's `boxers` before trusting the mapping:

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
of `eeg_coregistered.csv`. The *relative* EEG alignment between the two boxers
is **Not yet verified on hardware**; see [Timing & accuracy](timing-and-accuracy.md)
for what that means for phase-based versus power/envelope analyses before you
read too much into tight between-brain timing.
