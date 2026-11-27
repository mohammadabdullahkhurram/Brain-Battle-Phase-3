# ADR-0024 — Timing goal under 10 ms: live timing check and buzzer calibration

**What this page covers:** how the app measures and corrects the delay of every
camera and headband so that all devices agree to **under 10 ms**. It replaces
the earlier project note "don't invest in tighter sync precision".

- **Status:** Accepted
- **Date:** 2026-11-17

## Context

Until now the project said: the synchrony analysis method is undecided, so
don't invest in sync precision beyond the tens of milliseconds the one capture
clock already gives ([ADR-0017](adr-0017-one-capture-clock.md)). The professor
supervising the study has now set the requirement: **under 10 ms between all
devices after correction**.

The one capture clock stamps each camera frame and each headband packet when it
*reaches the computer*. Every device delivers late: cameras by their exposure,
USB transfer and decoding (typically 30–100 ms); headbands by Bluetooth (tens of
ms, with a packet of 12 samples every ~47 ms). The delays differ per device, so
two devices stamp the same physical instant tens of ms apart. Arrival times are
also uneven (jitter), and devices lose frames or packets.

## Decision

**1. Cameras give their best timing.** Ask for MJPG and a 1-frame driver buffer
(the newest frame, never a queued old one), and record at **60 fps** when
Settings › Advanced › "Record cameras at 60 fps" is on (the default) — that
halves the frame period, so a camera's own uncertainty is ±8 ms instead of
±17 ms. A camera that cannot do 60 falls back to 30 and the app says so (Get
ready, Saved, `camera_fps` in the manifest, `timing_quality.csv`). Frames are
still stamped with `clock.monotonic()` as they arrive.

**2. Headband times are de-jittered.** Each Bluetooth packet's arrival is logged
(`timing.PacketTimer`). Packets that never arrived are counted from the gaps in
the Muse library's timestamps. Every sample gets the time of a straight line
through (sample number, arrival time): least squares over the last ~90 s,
outliers (packets far off the line) dropped and refitted, intercept by median —
so the arrival jitter does not reach the files, a late packet does not drag the
line, and slow crystal drift is followed. The constant Bluetooth delay stays;
that is what step 4 measures.

**3. Live timing per device.** Rate, jitter (spread of arrival intervals) and
lost frames / missing packets, per device, once a second
(`timing.RateMonitor`): shown in the **Test timing** dialog (Get ready, and
from the timing line on Recording — the dialog never pauses recording) and
written to **`timing_quality.csv`** in every recording, one row per device per
second, with the applied correction (`offset_ms`) and events (camera stalled /
reopened, a camera that could only do 30 fps).

**4. Buzzer calibration ("Run buzzer test").** Five beeps ~1.5 s apart; at each,
both boxers tap their headband once, in view of the cameras.

- Each **headband**: the tap is a sharp artifact on the skin channels at
  256 Hz; its onset is the rising edge back-extrapolated to the baseline
  (sub-sample), and the accelerometer must show a jolt within ±0.15 s. The tap
  is *found* on the summed absolute deviation of the channels, but its onset
  is *fitted* on their signed sum (each channel times the sign of its
  artifact), through the unbroken rise before the first local maximum, and
  kept between the last sample at the baseline and the first on the rise.
  (2026-11-25: fitting the rectified sum read the onset 0.4–0.9 ms early on
  average and up to 2.9 ms early depending on where the tap fell between two
  samples — the cause of an occasional end-to-end error over 2 ms. Detector
  alone, simulator noise level: bias −0.42 → +0.07 ms, 99th percentile
  |error| 1.73 → 0.68 ms. The real buzzer test end to end, 200 runs on the
  GitHub Windows runner (`tools/buzzer_study.py`, heavy-checks): median 0.77 →
  0.33 ms, 99th percentile 1.66 → 1.25 ms, worst 1.70 → 1.36 ms.)
- Each **camera**: frame-difference energy of every frame (a 64×36 grey copy).
  A camera integrates light over the frame, so the frame in which the tap
  starts shows only part of the change; the split of the change between that
  frame and the next gives a sub-frame onset. It is taken as the centre of the
  change over the first frame above the threshold and the frames either side
  of it (minus one period), because a tap that starts just before a frame ends
  leaves too little in that frame to pass the threshold. (Using only the first
  frame above the threshold and the next put such taps up to ~20 % of a
  period late — the 2–3 ms of the 2026-11-26 staging study; fixed 2026-11-27.)
  The frame stamps around the tap
  are first replaced by the camera's steady frame clock (each stamp locked to
  the lattice φ + n·T, fitted robustly), because delivery jitter is not
  capture jitter.
- For each beep, the **consensus** is the median tap time over all devices.
  A device's per-tap value is its tap time minus the consensus, which cancels
  everything the devices share for that tap — the sound card's delay and the
  boxers' reaction time, which also varies tap to tap. The device's
  **correction** is the median of its per-tap values, re-centred so the
  corrections' median is 0 (the data stays where the devices on average put
  it). Raw median (tap − beep) is kept as `offset_ms` for reference.
- The result is saved in the manifest as **`timing_calibration`** and applied
  — subtracted from each device's times — when `recording.xdf`,
  `eeg_coregistered.csv` and Review & Label's alignment are built. **Raw files
  are never changed.** The Saved screen shows the result, or "Timing not
  measured".

**5. Tested against known answers.** The simulated devices deliver late by a
known delay + jitter, capture on a steady clock with a 1-frame buffer like real
cameras, and render taps physically (partial exposure; an 8 ms EEG rise; an
accelerometer jolt). `tests/test_timing.py` runs the real buzzer test through
the real capture paths and requires every device to be recovered **within 2 ms**
of what it actually delivered (measured: about 0.3–1.2 ms); on synthetic data
over 40 random runs, 95 % are within 2 ms.

## Consequences

- With 60 fps cameras and the buzzer test, the expected largest difference
  between devices after correction is a few ms — under the 10 ms goal — in
  simulation. **It has not been tried on real hardware** (AGENTS.md deferred
  work): real tap artifacts, real cameras' exposure and real Bluetooth may
  behave differently, and the first real session must check it.
- The corrections are **relative between devices**. The common delay of all
  devices relative to the computer's own events (the system markers, which are
  computer-clock instants) is not measured; markers may sit a few tens of ms
  off all devices together. That does not affect between-device synchrony.
- Reaction-time differences **between the two boxers** are not separable from
  a difference between their headbands (each headband sees only its own boxer's
  tap; the cameras see both at once). The median over five taps limits random
  differences; a systematic one (one boxer always slower) would remain. The
  operator can ask the boxers to tap together, or have one person tap both
  headbands.
- `recording.xdf` streams carry `timing_correction_ms` in their description.
- A recording without a buzzer test is recorded exactly as before; only the
  timing report and "Timing not measured" differ.

## Alternatives considered

- **Keep "don't invest in sync precision".** Overruled: the professor needs
  < 10 ms.
- **Correct against the beep time directly.** Rejected: it builds the sound
  card's delay and the boxers' reaction time (~150–250 ms, varying) into every
  correction.
- **A hardware sync box (TTL pulses / an LED in view + a trigger into the
  EEG).** The Muse-S has no trigger input; an LED would only cover the cameras.
- **Rewrite the raw files with corrected times.** Rejected: raw files are the
  source of truth and must stay as captured; the correction is applied when
  derived files are built.
