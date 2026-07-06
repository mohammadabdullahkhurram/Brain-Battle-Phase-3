"""
sync_utils/video_sync.py  —  Brain Battle ffmpeg video helpers

Changes from original
─────────────────────
- generate_grid_command(): removed hardcoded Windows font path, replaced with
  _find_system_mono_font() that works on Windows, macOS, and Linux
- generate_grid_command(): removed time.sleep(5) blocking call
- generate_single_preview_optimized(): preserved but clearly marked as
  Windows/CUDA-only
- All other functions unchanged
"""

from __future__ import annotations

import ffmpeg
import logging
import numpy as np
import datetime
import subprocess
import tempfile
import sys
import os
from typing import Optional

log = logging.getLogger(__name__)


# --- Font helper (cross-platform) --------------------------------------------

def _find_system_mono_font() -> str:
    """
    Return a path to a monospace font that exists on this machine.
    Used for the drawtext overlay in generate_grid_command().
    Returns empty string if none found (drawtext will use a built-in font).
    """
    if sys.platform == "win32":
        candidates = [
            r"C:\Windows\Fonts\consola.ttf",    # Consolas (Win 7+)
            r"C:\Windows\Fonts\cour.ttf",        # Courier New
            r"C:\Windows\Fonts\lucon.ttf",       # Lucida Console
        ]
    elif sys.platform == "darwin":
        candidates = [
            "/System/Library/Fonts/Menlo.ttc",
            "/Library/Fonts/Courier New.ttf",
            "/System/Library/Fonts/Monaco.ttf",
        ]
    else:
        candidates = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",
            "/usr/share/fonts/truetype/liberation/LiberationMono-Regular.ttf",
            "/usr/share/fonts/TTF/DejaVuSansMono.ttf",
        ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return ""


# --- Single-clip preview helpers ---------------------------------------------

def generate_single_preview(video_path: str, delay: float, duration: float,
                             output_path: str, max_delay: float = 0):
    """
    Cuts a given video by its corresponding delay.

    Returns:
    - command (list): The ffmpeg command to run.
    - new_duration (float): New duration of the video.
    """
    video = ffmpeg.input(video_path)
    audio = video.audio
    new_duration = duration

    if delay < 0:
        video_cut = ffmpeg.trim(video, start=round(abs(delay), 3)).setpts("PTS-STARTPTS")
        audio_cut = ffmpeg.filter(audio, "atrim", start=round(abs(delay), 3)).filter(
            "asetpts", "PTS-STARTPTS"
        )
        new_duration -= round(delay)
    else:
        video_cut = ffmpeg.filter(video, "tpad", start_duration=round(delay, 3))
        audio_cut = ffmpeg.filter(
            audio, "adelay", delays=int(round(delay, 3) * 1000), all=1
        )

    out = ffmpeg.output(
        audio_cut, video_cut, output_path,
        acodec="aac", vcodec="libx264", pix_fmt="yuv420p",
        crf=23, preset="ultrafast", progress="pipe:1", ss=max_delay
    )
    return out.compile(), new_duration


def generate_single_preview_optimized(video_path: str, delay: float, duration: float,
                                      output_path: str, max_delay: float = 0):
    """
    NVIDIA/CUDA-accelerated version. Only works on Windows/Linux with an
    NVIDIA GPU and CUDA-enabled ffmpeg. DO NOT use on macOS or AMD hardware.
    """
    video = ffmpeg.input(video_path, hwaccel="cuda", hwaccel_output_format="cuda")
    audio = video.audio
    new_duration = duration
    video_cut_due_to_delay = 0

    if delay < 0:
        video_cut_due_to_delay = round(abs(delay), 2)
        new_duration -= video_cut_due_to_delay

    resulting_cut = max_delay + video_cut_due_to_delay
    out = ffmpeg.output(
        audio, video, output_path,
        acodec="copy", vcodec="h264_nvenc",
        progress="pipe:1", ss=resulting_cut
    )
    return out.compile(), new_duration


# --- Grid command (4-up, cross-platform) -------------------------------------

def generate_grid_command(all_video_paths: list[str], delays: list[float],
                          durations: list[float],
                          output_file_name_with_extension: str,
                          video_end_time: Optional[float] = None):
    """
    Build an ffmpeg command that synchronises and composites all four videos
    into a 2x2 grid at 4K 60fps.

    Uses _find_system_mono_font() for the timecode overlay so the command
    works on Windows, macOS, and Linux without a hardcoded path.
    """
    videos = [ffmpeg.input(path) for path in all_video_paths]
    xstacked = ffmpeg.filter(videos, "xstack", inputs=4,
                             layout="0_0|0_h0|w0_0|w0_h0")

    max_delay_idx = np.argmax(delays)
    audio_stream = videos[max_delay_idx].audio

    video_scaled = (
        xstacked
        .filter("scale", 3840, 2160)
        .filter("fps", fps=60, round="up")
    )

    font_path = _find_system_mono_font()
    text_string = (
        "Time: %{eif:trunc(t/60):d}m"
        "%{eif:mod(t,60):d}s"
        "%{eif:mod(t*1000,1000):d}ms "
        "Frame: %{n}"
    )

    drawtext_args = dict(
        text=text_string,
        x="(w-tw)/2",
        y="h-(2*lh)",
        fontsize="72",
        fontcolor="white",
        box="1",
        boxcolor="black@0.4",
        expansion="normal",
    )
    if font_path:
        drawtext_args["fontfile"] = font_path

    video_with_timestamp = ffmpeg.filter(video_scaled, "drawtext", **drawtext_args)

    stop_duration = video_end_time if video_end_time else min(durations)

    out = ffmpeg.output(
        audio_stream,
        video_with_timestamp,
        output_file_name_with_extension,
        acodec="aac",
        vcodec="libx264",
        pix_fmt="yuv420p",
        crf=21,
        preset="superfast",
        progress="pipe:1",
        to=stop_duration,
    )

    command = out.compile()
    log.debug("compiled ffmpeg command: %s", command)

    return command, stop_duration


def generate_video_sync_command(all_video_paths: list[str], delays: list[float],
                                durations: list[float],
                                output_file_name_with_extension: str,
                                video_end_time: Optional[float] = None):
    """
    Generate the full synchronise-and-composite ffmpeg command (trim/tpad
    per clip, 2x2 xstack, 4K 60fps output).
    """
    videos = [ffmpeg.input(path) for path in all_video_paths]
    video_streams = [v.video for v in videos]
    audio_streams = [v.audio for v in videos]

    for index, video in enumerate(video_streams):
        if delays[index] < 0:
            video_streams[index] = ffmpeg.trim(
                video, start=round(abs(delays[index]), 2)
            ).setpts("PTS-STARTPTS")
            durations[index] -= round(delays[index])
        else:
            video_streams[index] = ffmpeg.filter(
                video, "tpad", start_duration=round(delays[index], 2)
            )
            audio_streams[index] = ffmpeg.filter(
                audio_streams[index], "apad", pad_dur=round(delays[index], 2)
            )

    xstacked = ffmpeg.filter(video_streams, "xstack",
                             inputs="4", layout="0_0|0_h0|w0_0|w0_h0")
    video_start_adjusted = ffmpeg.trim(xstacked, start=max(delays)).setpts(
        "PTS-STARTPTS"
    )

    maximum_delay = np.argmax(delays)
    audio_stream = audio_streams[maximum_delay]
    audios_start_adjusted = ffmpeg.filter(
        audio_stream, "atrim", start=max(delays)
    ).filter("asetpts", "PTS-STARTPTS")

    video_scaled = ffmpeg.filter(video_start_adjusted, "scale", 3840, 2160).filter(
        "fps", fps=60, round="up"
    )

    stop_duration = video_end_time if video_end_time else min(durations)

    out = ffmpeg.output(
        audio_stream, video_scaled,
        output_file_name_with_extension,
        acodec="aac", vcodec="libx264",
        pix_fmt="yuv420p", crf=21,
        preset="superfast", progress="pipe:1",
        to=stop_duration,
    )

    command = out.compile()
    return command, stop_duration - max(delays)


def run_ffmpeg_subprocess(ffmpeg_command: str, resulting_duration: float,
                          debug: bool = False):
    import subprocess, datetime
    try:
        ffmpeg_process = subprocess.Popen(
            ffmpeg_command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            universal_newlines=True,
        )
    except OSError:
        log.error("OSError running FFmpeg")
        return
    except ValueError as e:
        log.error("ValueError running FFmpeg: %s", e)
        return
    except Exception as e:
        log.error("FFmpeg error: %s", e)
        return

    end_time = str(datetime.timedelta(seconds=resulting_duration))
    log.info("FFmpeg starting...")
    for line in ffmpeg_process.stdout:
        if debug:
            log.debug("%s", line.rstrip())
        if line.startswith("out_time="):
            progress = line.split("=")[1].strip()
            log.debug("progress: %s / %s", progress, end_time)

    log.info("FFmpeg finished.")
    ffmpeg_process.terminate()


def get_first_frame_pts(video_path: str) -> float:
    """
    Return the PTS (presentation timestamp) of the first video frame in seconds.
    Falls back to 0.0 on any error.
    """
    import subprocess, json
    try:
        cmd = [
            "ffprobe", "-v", "quiet",
            "-select_streams", "v:0",
            "-show_entries", "packet=pts_time",
            "-of", "json",
            "-read_intervals", "%+#1",
            video_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        packets = data.get("packets", [])
        if packets:
            return float(packets[0].get("pts_time", 0.0))
    except Exception as e:
        log.warning("first-frame pts probe failed for %s: %r — using 0.0", video_path, e)
    return 0.0
