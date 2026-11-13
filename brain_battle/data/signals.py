"""
signals.py — the signals a recording contains, and what they are called
───────────────────────────────────────────────────────────────────────
One small, import-free place for the facts several modules must agree on: the
headband's channels and sampling rates, and the stream names used both by the
live LSL outlets (``capture/lsl_outlets.py``) and inside ``recording.xdf``
(``data/session_xdf.py``). Keeping the names here means a LabRecorder file made
from the live streams and the app's own XDF use identical stream names.
"""
from __future__ import annotations

SAMPLE_RATE = 256                                   # EEG, Hz
EEG_CHANNELS = ("TP9", "AF7", "AF8", "TP10", "AUX")
EEG_UNIT = "microvolts"

# Where each electrode sits, in plain language (TP9 … are the standard 10-20
# system names; AUX is the headband's spare input and usually carries nothing).
CHANNEL_PLACES = {"TP9": "left ear", "AF7": "left forehead", "AF8": "right forehead",
                  "TP10": "right ear", "AUX": "spare input"}

# The headband's other sensors: kind → (rate Hz, channel names, unit, LSL channel type).
SENSORS = {
    "ACC":  (52, ("X", "Y", "Z"), "g", "accelerometer"),
    "GYRO": (52, ("X", "Y", "Z"), "dps", "gyroscope"),
    "PPG":  (64, ("PPG1", "PPG2", "PPG3"), "raw", "PPG"),
}

EEG_DIR_NAME = "Muse data"        # continuous/<this>/session_<band>.csv
SENSOR_DIR_NAME = "Muse sensors"  # continuous/<this>/session_<band>_<KIND>.csv

MARKER_STREAM = "BrainBattle_Markers"
LABEL_STREAM = "BrainBattle_Labels"
XDF_NAME = "recording.xdf"


def eeg_stream_name(which: str) -> str:
    return f"BrainBattle_EEG_{str(which).upper()}"


def sensor_stream_name(kind: str, which: str) -> str:
    return f"BrainBattle_{kind}_{str(which).upper()}"


def video_stream_name(stem: str) -> str:
    """``stem`` is the camera file stem, e.g. ``SIDE_01`` for session_SIDE_01.mp4."""
    return f"BrainBattle_Video_{stem}"


__all__ = ["SAMPLE_RATE", "EEG_CHANNELS", "EEG_UNIT", "CHANNEL_PLACES", "SENSORS",
           "EEG_DIR_NAME", "SENSOR_DIR_NAME", "MARKER_STREAM", "LABEL_STREAM",
           "XDF_NAME", "eeg_stream_name", "sensor_stream_name", "video_stream_name"]
