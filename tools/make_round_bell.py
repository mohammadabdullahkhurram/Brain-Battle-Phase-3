"""
make_round_bell.py — turn the raw Freesound boxing-bell recording into
assets/sounds/round_bell.wav, the WAV `sync_beep.py` plays for the round bell.

Source
──────
assets/sounds/source/boxing_bell_benboncan_66951_preview-hq.mp3 — "Boxing
Bell.wav" by Benboncan (Freesound #66951), licensed CC-BY 4.0. See
docs/project/credits.md for the full credit. This is Freesound's own
high-quality preview encode (the original .wav requires a Freesound login to
download); it is already close to mono and clean enough that re-encoding to
mp3 introduces no audible artefacts at this short, percussive length.

Processing (every number here is a named constant below, so re-running after
changing one re-derives everything else):
  1. Downmix to mono BEFORE any level analysis — loudnorm has to measure the
     signal it will actually ship, and averaging L+R first avoids the ~3 dB
     level mismatch a post-loudnorm downmix would introduce.
  2. `silenceremove` trims any lead-in silence so playback starts right on
     the strike, not before it.
  3. `highpass` at HIGHPASS_HZ removes handling rumble/room noise below the
     bell's fundamental.
  4. `treble` (a shelving filter despite the name) pulls down everything
     above SHELF_FREQ_HZ by SHELF_GAIN_DB — the raw recording's upper
     harmonics are harsh/piercing at playback volume; this softens them
     without dulling the strike.
  5. `atrim` keeps RING_DUR_SEC of the strike + natural ring, `afade` fades
     the last FADE_DUR_SEC of that out smoothly instead of cutting it off.
  6. Two-pass `loudnorm`: measure the processed-but-not-yet-normalised mono
     signal, then apply with the measured stats so the output lands on
     TARGET_LUFS integrated loudness exactly (a single-pass loudnorm only
     approximates the target).
  7. Final format: SAMPLE_RATE Hz, 16-bit PCM, mono.

Run: python3 tools/make_round_bell.py
Requires ffmpeg/ffprobe on PATH (same requirement as the app itself).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "assets" / "sounds" / "source" / "boxing_bell_benboncan_66951_preview-hq.mp3"
OUTPUT = ROOT / "assets" / "sounds" / "round_bell.wav"

HIGHPASS_HZ    = 120      # rumble/handling noise below this is cut
SHELF_FREQ_HZ  = 4000     # high-shelf corner: everything above softened
SHELF_GAIN_DB  = -6       # how much the shelf pulls the top end down
RING_DUR_SEC   = 2.0      # strike + natural ring kept, within the 1.5-2.5s target
FADE_DUR_SEC   = 0.5      # gentle fade-out over the tail of RING_DUR_SEC
TARGET_LUFS    = -20.0    # integrated loudness target (comfortable, not jarring)
TARGET_TP_DBTP = -3.0     # true-peak ceiling loudnorm must not exceed
TARGET_LRA     = 7.0      # loudness range target (ffmpeg's default)
SAMPLE_RATE    = 44100


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed ({' '.join(cmd)}):\n{result.stderr}")
    return result.stderr   # ffmpeg writes filter output (and loudnorm's JSON) to stderr


def _measure_loudnorm(wav_path: Path) -> dict:
    stderr = _run([
        "ffmpeg", "-y", "-i", str(wav_path),
        "-af", f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP_DBTP}:LRA={TARGET_LRA}:print_format=json",
        "-f", "null", "-",
    ])
    m = re.search(r"\{[^{}]*\}", stderr, flags=re.DOTALL)
    if not m:
        sys.exit(f"could not parse loudnorm measurement:\n{stderr}")
    return json.loads(m.group(0))


def main() -> None:
    if not SOURCE.is_file():
        sys.exit(f"source file missing: {SOURCE}")

    with tempfile.TemporaryDirectory() as tmp:
        pre = Path(tmp) / "pre.wav"

        # Pass 1: mono downmix, trim-to-onset, EQ, trim-to-ring-length, fade-out.
        # Kept as float32 so the loudnorm measurement below sees full precision.
        _run([
            "ffmpeg", "-y", "-i", str(SOURCE),
            "-af", (
                "pan=mono|c0=0.5*c0+0.5*c1,"
                "silenceremove=start_periods=1:start_duration=0.01:"
                "start_threshold=-45dB:detection=peak,"
                f"highpass=f={HIGHPASS_HZ},"
                f"treble=g={SHELF_GAIN_DB}:f={SHELF_FREQ_HZ},"
                f"atrim=0:{RING_DUR_SEC},"
                f"afade=t=out:st={RING_DUR_SEC - FADE_DUR_SEC}:d={FADE_DUR_SEC}"
            ),
            "-c:a", "pcm_f32le", str(pre),
        ])

        # Pass 2: measure, then apply loudnorm with the measured stats (accurate
        # two-pass normalisation — a single pass only approximates the target).
        measured = _measure_loudnorm(pre)
        OUTPUT.parent.mkdir(parents=True, exist_ok=True)
        _run([
            "ffmpeg", "-y", "-i", str(pre),
            "-af", (
                f"loudnorm=I={TARGET_LUFS}:TP={TARGET_TP_DBTP}:LRA={TARGET_LRA}:"
                f"measured_I={measured['input_i']}:measured_TP={measured['input_tp']}:"
                f"measured_LRA={measured['input_lra']}:measured_thresh={measured['input_thresh']}:"
                f"offset={measured['target_offset']}:linear=true"
            ),
            "-ar", str(SAMPLE_RATE), "-sample_fmt", "s16", str(OUTPUT),
        ])

    verify = _measure_loudnorm(OUTPUT)
    print(f"wrote {OUTPUT.relative_to(ROOT)}")
    print(f"  integrated loudness: {verify['input_i']} LUFS (target {TARGET_LUFS})")
    print(f"  true peak:           {verify['input_tp']} dBTP (ceiling {TARGET_TP_DBTP})")


if __name__ == "__main__":
    main()
