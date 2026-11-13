# Troubleshooting

**What this page covers:** common problems, what causes them, and what to
do — organized as a symptom → cause → fix table per topic. See the
[FAQ](faq.md) for questions that aren't really "something's wrong."

## Cameras

| Symptom | Cause | Fix |
|---|---|---|
| A tile says "Not found. Check the USB cable and the video receiver, then press Search again." | The app couldn't open that camera/receiver | Check the USB cable and the video receiver are connected, then press **Search again** on the Connect devices step |
| A tile has a red border | Same as above | Same as above — a red border always means that camera is missing |
| "N frames lost" shown during recording | The video encoder briefly couldn't keep up with that camera | Usually harmless for a few frames; if it keeps climbing, close other heavy apps and check the laptop isn't thermal-throttling |
| Recording won't start at all, with an error mentioning ffmpeg or an encoder | `ffmpeg` isn't installed or isn't on `PATH`, or no working video encoder was found | Re-check [Installation](../getting-started/installation.md)'s ffmpeg step; run `ffmpeg -version` in a terminal to confirm it's found |

## Headbands

| Symptom | Cause | Fix |
|---|---|---|
| A headband shows "Not switched on" | The band is off, or Bluetooth hasn't found it yet | Switch it on, keep it nearby, press **Search again** |
| A headband shows "Connecting…" and stays there | Bluetooth pairing is slow or being interrupted by another scan | Wait a few seconds; avoid scanning for a second headband while the first is mid-connect |
| "✕ Lost connection" appears during recording | The Bluetooth link dropped (out of range, interference, low battery) | Press **Reconnect** on that boxer's row — the SAME recording resumes, it doesn't restart |
| A sensor chip says "Weak contact" | That part of the headband isn't making good skin contact | Follow the tip shown under the chip (e.g. move the band so the sensor sits flat, brush hair out of the way) |

## Disk space

| Symptom | Cause | Fix |
|---|---|---|
| "✕ Not enough space" on Set up session, Start recording disabled | Free space is below what this session is estimated to need | Free up space, shorten the session (fewer/shorter rounds), or pick a smaller [video quality](settings.md) |
| "! Low space" warning, but you can still start | Free space is tight but above the hard minimum | You can continue, but consider freeing up space before a long session |
| The sidebar's free-space bar turns amber or red | Same idea, shown continuously rather than just at Set up | Free up space, or change the [recordings folder](settings.md) to a drive with more room |

## Interrupted recordings

| Symptom | Cause | Fix |
|---|---|---|
| Sessions shows "A recording was interrupted" | The app didn't shut that recording down cleanly last time (crash, power loss, forced quit) | Press **Open it** to review what was captured — everything up to the interruption is kept intact — or **Dismiss** if you've already handled it |
| A recording's Length shows "Stopped at m:ss of m:ss" but you don't remember stopping it | Likely the interrupted case above, or you pressed **Stop and save** deliberately | Open the recording and check its "Worth checking" notes on the Saved screen, or history, for what happened |

## Playback (Review & Label)

| Symptom | Cause | Fix |
|---|---|---|
| Video tiles stay blank after opening a recording | Media loads in the background after labels/timeline appear — give it a moment | Wait a second or two; labels and the timeline are intentionally fast even before video finishes loading |
| Playback stutters or lags | Four simultaneous video decodes is real work for a laptop | Lower the playback speed (0.25× / 0.5×), or close other heavy apps |
| A brain-activity trace says "Weak contact" during review | The same per-sensor quality heuristic as during recording, computed from the saved signal | Informational — it tells you which stretches of that recording's signal may be less reliable, nothing to fix after the fact |

## Other lab software (LabRecorder, EEG viewers)

| Symptom | Cause | Fix |
|---|---|---|
| The computer asks whether to "allow incoming network connections" when you open New recording | Brain Battle is offering its live signals to other lab software on the network (Lab Streaming Layer) | Either answer is fine for recording. Choose Allow if you want LabRecorder or an EEG viewer on *another* computer to see the signals. To stop it asking, switch off **Share live signals with other lab software** in [Settings](settings.md) |
| LabRecorder doesn't list any `BrainBattle_…` streams | The streams exist only while the New recording screens are open, and only if sharing is switched on | Open **New recording** first, then press LabRecorder's **Update**; check the switch in [Settings](settings.md); on another computer, check both are on the same network and the firewall allowed the connection |
| LabRecorder lists the camera streams but they stay empty | Camera streams carry one sample per *recorded* frame, so they only fill while a recording is running | Start the recording |

## The app on quit

| Symptom | Cause | Fix |
|---|---|---|
| Quitting while recording asks "Quit while recording?" | A safety check — quitting mid-recording would cut it off | Choose **Stop and save** from the Recording screen first if you want a clean stop, or confirm the quit if you're sure (everything recorded so far is kept either way) |
| The app seems to hang briefly on quit | EEG streams and camera threads are being shut down cleanly before the window closes (avoids a "QThread destroyed while still running" crash) | Wait a moment rather than force-quitting — it's normal and brief |

## Still stuck?

Check the [FAQ](faq.md), or see `AGENTS.md`'s "Project situation" in the
repository for what's currently known-unverified about hardware behaviour.
