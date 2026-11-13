"""
session_xdf.py — one XDF file per recording
───────────────────────────────────────────
Builds ``recording.xdf`` inside a recording folder: every signal of the session
on ONE clock, in the standard Lab Streaming Layer file format that EEGLAB,
MNE-Python and pyxdf open directly.

Streams written (names shared with the live LSL outlets, see data/signals.py):

    BrainBattle_EEG_A / _B        EEG          5 ch · 256 Hz · microvolts
    BrainBattle_ACC|GYRO|PPG_A/_B headband movement / pulse sensors (if recorded)
    BrainBattle_Markers           Markers      BASELINE_START, ROUND_1_START, …,
                                               SESSION_END, and MOMENT
    BrainBattle_Labels            Markers      the labels placed in Review & Label
                                               ("Jab/A", "Clinch/Both/start", …)
    BrainBattle_Video_<camera>    VideoFrames  one sample per video frame; the
                                               value is the frame number in the
                                               MP4, the timestamp is when that
                                               frame was captured

Timestamps are LSL-clock seconds: ``t0_lsl + t_session_seconds``, where
``t0_lsl`` is the session start stored in the manifest's ``capture_clock`` (and
repeated in every stream's description as ``session_start``). So

    t_session_seconds = xdf_timestamp − session_start

and the first marker, ``BASELINE_START``, sits at session time 0.

The file is derived from the recording's crash-safe source files (EEG CSVs,
camera sidecars, phase_markers.csv, the manifest, labels.json), not written
live. That is deliberate: it can be rebuilt at any time — after a crash, after
labelling, or for recordings made before this feature existed — and can never
be the thing that loses data. (docs/decisions/adr-0015-lsl-and-xdf.md)

Qt-free. Command line:

    python3 -m brain_battle.data.session_xdf "<recording folder>"
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import numpy as np

import brain_battle.data.session_storage as session_storage
from brain_battle.data.signals import (
    EEG_CHANNELS, EEG_UNIT, SAMPLE_RATE, SENSORS, MARKER_STREAM, LABEL_STREAM,
    XDF_NAME, eeg_stream_name, sensor_stream_name, video_stream_name)
from brain_battle.data.xdf_writer import XdfWriter, channels_xml, _xml

log = logging.getLogger(__name__)

MOMENT_MARKER = "MOMENT"
_CHUNK = 4096          # samples per XDF chunk for continuous signals


def xdf_path(session_root) -> str:
    return os.path.join(str(session_root), XDF_NAME)


def _read_signal_csv(path: str, channels, t0_unix: Optional[float]):
    """(t_session, data[N, C]) from one of the app's streamed CSVs. Tolerates
    the truncated last row a crash can leave, and files written before the
    ``t_session_seconds`` column existed."""
    import pandas as pd
    try:
        df = session_storage.read_csv_tolerant(path)
    except Exception as e:
        log.warning("could not read %s: %r", path, e)
        return None
    if "t_session_seconds" in df.columns:
        t = pd.to_numeric(df["t_session_seconds"], errors="coerce")
    elif "timestamps" in df.columns and t0_unix is not None:
        t = pd.to_numeric(df["timestamps"], errors="coerce") - float(t0_unix)
    else:
        return None
    cols = []
    for c in channels:
        cols.append(pd.to_numeric(df[c], errors="coerce") if c in df.columns
                    else pd.Series(np.nan, index=df.index))
    data = np.column_stack([c.to_numpy(dtype=float) for c in cols]) if len(df) else \
        np.zeros((0, len(channels)))
    t = t.to_numpy(dtype=float)
    keep = np.isfinite(t) & np.all(np.isfinite(data), axis=1)
    return t[keep], data[keep]


def _read_sidecar(path: str):
    """(frame_index, t_session) arrays from a camera's .timestamps.csv."""
    import pandas as pd
    try:
        df = session_storage.read_csv_tolerant(path)
        t = pd.to_numeric(df["t_session_seconds"], errors="coerce").to_numpy(dtype=float)
        idx = pd.to_numeric(df["frame_index"], errors="coerce").to_numpy(dtype=float)
    except Exception as e:
        log.warning("could not read %s: %r", path, e)
        return None
    keep = np.isfinite(t) & np.isfinite(idx)
    return idx[keep].astype(np.int32), t[keep]


def _read_labels(session_root) -> list:
    try:
        with open(os.path.join(str(session_root), "labels.json"), encoding="utf-8") as f:
            data = json.load(f)
        return [x for x in data.get("labels", []) if isinstance(x, dict) and "t_start" in x]
    except (OSError, ValueError, AttributeError):
        return []


def label_events(labels) -> list[tuple]:
    """Placed labels as time-sorted ``(t_session, text)`` marker events. A
    moment label is ``"<label>/<who>"``; a label that lasts a while becomes two
    events, ``"<label>/<who>/start"`` and ``"<label>/<who>/end"``."""
    out = []
    for lab in labels or []:
        try:
            name, who, t0 = str(lab["label"]), str(lab.get("who") or "Both"), float(lab["t_start"])
        except (KeyError, TypeError, ValueError):
            continue
        if lab.get("t_end") is None:
            out.append((t0, f"{name}/{who}"))
        else:
            out.append((t0, f"{name}/{who}/start"))
            out.append((float(lab["t_end"]), f"{name}/{who}/end"))
    out.sort(key=lambda e: e[0])
    return out


def build_session_xdf(session_root, labels=None, out_path: Optional[str] = None) -> Optional[str]:
    """Write ``recording.xdf`` for a recording folder and return its path, or
    None if the folder has nothing to put in it. ``labels`` defaults to the
    folder's own labels.json. The file is replaced atomically, so a reader
    never sees a half-written one."""
    root = Path(session_root)
    manifest = session_storage._read_manifest(root) or {}
    cc = manifest.get("capture_clock") or {}
    t0 = float(cc.get("t0_lsl") or 0.0)
    t0_unix = cc.get("t0_unix")
    boxers = manifest.get("boxers") or {}
    common = (_xml("recording", root.name) + _xml("session_start", repr(t0))
              + _xml("app_version", manifest.get("version", "")))

    def desc(extra: str = "", chans: str = "") -> str:
        return chans + "<brain_battle>" + common + extra + "</brain_battle>"

    out_path = out_path or xdf_path(root)
    tmp = out_path + ".tmp"
    n_streams = 0
    w = XdfWriter(tmp)
    try:
        def continuous(name, type_, channels, rate, unit, ch_type, path, who):
            nonlocal n_streams
            got = _read_signal_csv(path, channels, t0_unix)
            if got is None or len(got[0]) == 0:
                return
            t, data = got
            extra = _xml("boxer", who) + _xml(
                "headband", (boxers.get(who) or {}).get("headband") or "")
            sid = w.add_stream(name, type_, len(channels), rate, "float32",
                               source_id=f"brainbattle-{name}",
                               desc=desc(extra, channels_xml(channels, unit, ch_type)))
            for i in range(0, len(t), _CHUNK):
                w.write_samples(sid, t0 + t[i:i + _CHUNK], data[i:i + _CHUNK])
            n_streams += 1

        for who, path in sorted(session_storage.eeg_files(root).items()):
            continuous(eeg_stream_name(who), "EEG", EEG_CHANNELS, SAMPLE_RATE,
                       EEG_UNIT, "EEG", path, who)
        for (who, kind), path in sorted(session_storage.sensor_files(root).items()):
            rate, channels, unit, ch_type = SENSORS[kind]
            continuous(sensor_stream_name(kind, who), kind, channels, rate,
                       unit, ch_type, path, who)

        def markers(name, events):
            nonlocal n_streams
            if not events:
                return
            sid = w.add_stream(name, "Markers", 1, 0, "string",
                               source_id=f"brainbattle-{name}",
                               desc=desc(chans=channels_xml(["marker"], type_="Marker")))
            w.write_samples(sid, [t0 + t for t, _ in events], [text for _, text in events])
            n_streams += 1

        events = [(t, label) for t, _pid, label in session_storage.read_phase_markers(root)]
        events += [(m["t_session_seconds"], MOMENT_MARKER)
                   for m in session_storage.read_moments(root)]
        markers(MARKER_STREAM, sorted(events, key=lambda e: e[0]))
        markers(LABEL_STREAM, label_events(_read_labels(root) if labels is None else labels))

        for stem, mp4, sidecar in session_storage.camera_files(root):
            got = _read_sidecar(sidecar) if os.path.isfile(sidecar) else None
            if got is None or len(got[0]) == 0:
                continue
            idx, t = got
            span = float(t[-1] - t[0])
            fps = (len(t) - 1) / span if span > 0 else 0.0
            sid = w.add_stream(
                video_stream_name(stem), "VideoFrames", 1, 0, "int32",
                source_id=f"brainbattle-video-{stem}",
                desc=desc(_xml("video_file", os.path.relpath(mp4, root).replace(os.sep, "/"))
                          + _xml("measured_fps", f"{fps:.4f}"),
                          channels_xml(["frame_index"], type_="FrameIndex")))
            for i in range(0, len(t), _CHUNK):
                w.write_samples(sid, t0 + t[i:i + _CHUNK], idx[i:i + _CHUNK].reshape(-1, 1))
            n_streams += 1
    except Exception:
        w.close()
        _unlink(tmp)
        raise
    w.close()
    if n_streams == 0:
        _unlink(tmp)
        return None
    os.replace(tmp, out_path)
    return out_path


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def ensure_session_xdf(session_root) -> Optional[str]:
    """Build recording.xdf only if the recording doesn't have one yet (used for
    recordings that crashed, or were made before the app wrote XDF). Never
    raises."""
    path = xdf_path(session_root)
    if os.path.isfile(path):
        return path
    try:
        return build_session_xdf(session_root)
    except Exception as e:
        log.warning("could not build %s: %r", path, e)
        return None


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in ("-h", "--help"):
        print('usage: python3 -m brain_battle.data.session_xdf "<recording folder>"')
        return 2
    if not os.path.isfile(os.path.join(args[0], "session_manifest.json")):
        print(f"Not a recording folder (no session_manifest.json): {args[0]}", file=sys.stderr)
        return 1
    path = build_session_xdf(args[0])
    print(f"Wrote {path}" if path else "Nothing to write: no signals found in that folder.")
    return 0 if path else 1


if __name__ == "__main__":
    sys.exit(main())


__all__ = ["build_session_xdf", "ensure_session_xdf", "xdf_path", "label_events",
           "MOMENT_MARKER"]
