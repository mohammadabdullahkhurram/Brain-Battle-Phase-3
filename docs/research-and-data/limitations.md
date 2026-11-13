# Known limitations & unverified items

**What this page covers:** one honest list of everything about the data that is
**not yet confirmed**, why it's open, and exactly what would close it. Brain
Battle's hardware is currently broken, so the whole system is built and tested
against a generated demo session and simulated devices — several things can
only be verified once real cameras and real Muse-S bands are available again.

Read this before drawing strong conclusions from a recording. Nothing here is a
bug to fix in code; each item needs a real capture session.

## The list

| # | Unverified item | Why it's open | What would verify it |
|---|---|---|---|
| 1 | **EEG clock source** — where each EEG sample sits on `t_session_seconds` | Samples are stamped when their Bluetooth packet arrives, then smoothed by muselsl's per-band correction; that's never been checked against a real band | A real 2-band session with a simultaneous physical event (see [the buzzer test](timing-and-accuracy.md#the-hardware-verification-procedure)): the EEG artifact should land at the same `t_session_seconds` as the camera frame |
| 2 | **Bluetooth latency** on the EEG stream | BLE transport adds an unknown delay before a sample is timestamped | The same buzzer test — a consistent offset between the EEG spike and the video frame is the latency to correct for |
| 3 | **Between-brain skew** in `recording.xdf` and `eeg_coregistered.csv` | The two boxers' relative alignment inherits items 1–2 | The same buzzer test, checked for **both** boxers |
| 4 | **Real-signal contact quality** | The flat/clipped/noisy/good heuristic (`contact_quality`) is unit-tested on synthetic signals, never on a real electrode on a moving boxer | A real session: compare the per-sensor log against the raw EEG and what the band actually reported |
| 5 | **Muse signal usability during boxing** | Nobody has inspected raw Muse-S EEG from a real sparring round — noise floor, motion artifact, whether the channels are even readable under impact | Record a real round and inspect the raw EEG; decide whether the signal is usable at all |
| 6 | **Eyes-on-glass playback** with real data | Playback, four-camera lockstep, and the brain-activity traces are verified headless (correct pixels, measured scrub, alignment arithmetic), but nobody has *watched* Review & Label render aligned playback on real signals | Open the first real capture in Review & Label and watch: four cameras in lockstep, both traces following a scrub, a marker landing on a real physical event |
| 7 | **Movement and pulse sensors** (accelerometer, gyroscope, PPG) | Recorded through muselsl's standard callbacks and exercised with simulated headbands, but never received from a real band by this project. The pulse sensor additionally relies on switching the headband to its "p50" mode, which is untested and is why it is off by default | Record a short real session with them switched on in Settings; check the files fill at about 52 Hz (movement) / 64 Hz (pulse) and that the EEG stays stable |
| 8 | **Live LSL streams with real headbands** | Verified against simulated headbands and real LSL receivers on one computer; never run alongside real Bluetooth traffic, or across a lab network | Open LabRecorder during a real session and confirm it lists and records the `BrainBattle_…` streams |
| 9 | **Synchrony analysis method** (undecided) | The project hasn't chosen a between-brain measure or an owner; this decides how much timing precision actually matters | A decision on the method — phase-based (PLV/coherence) needs a few-ms alignment; power/envelope tolerates tens of ms |

Items 1, 2, 3, and 6 are all discharged by the **same** real-hardware check —
the simultaneous tap/buzzer test described under
[Timing & accuracy](timing-and-accuracy.md#the-hardware-verification-procedure).
Run it first; it answers most of this list at once.

## What *is* settled

So the list above isn't mistaken for "nothing works": the file formats, the
clock arithmetic, the four-camera alignment in the demo, the labelling and
export pipeline, and the whole guided recording flow are all built and tested
(against simulated devices and the generated demo session). What's open is
specifically the handful of things that need **real signals from real
hardware** — not more code. See [Timing & accuracy](timing-and-accuracy.md) for
the numbers behind what's settled.
