"""
xdf_writer.py — a small, dependency-free XDF file writer
────────────────────────────────────────────────────────
XDF (Extensible Data Format) is the file format of the Lab Streaming Layer
ecosystem — what LabRecorder writes and what EEGLAB, MNE-Python and pyxdf
read. This module writes it directly, so a Brain Battle recording can be
opened by those tools without LabRecorder having been running.

File layout (XDF 1.0, https://github.com/sccn/xdf/wiki/Specifications):

    "XDF:"  then a sequence of chunks, each
        [length-of-length: 1 byte = 1, 4 or 8][length][tag: uint16][content]

    tag 1  FileHeader    XML
    tag 2  StreamHeader  stream id (uint32) + XML describing the stream
    tag 3  Samples       stream id + sample count + per sample:
                         [8][float64 timestamp][values]
    tag 4  ClockOffset   stream id + collection time + offset (both float64)
    tag 6  StreamFooter  stream id + XML (first/last timestamp, sample count)

Everything is little-endian. Only what this app needs is implemented: numeric
streams of one type per stream, and string (marker) streams.

Pure Python + NumPy, no Qt. Verified by reading files back with pyxdf
(tests/test_xdf.py).
"""
from __future__ import annotations

import socket
import struct
import uuid
from xml.sax.saxutils import escape

import numpy as np

_TAG_FILE_HEADER, _TAG_STREAM_HEADER, _TAG_SAMPLES = 1, 2, 3
_TAG_CLOCK_OFFSET, _TAG_STREAM_FOOTER = 4, 6

# XDF channel_format name → NumPy dtype (None = variable-length strings)
FORMATS = {"float32": "<f4", "double64": "<f8", "int32": "<i4",
           "int16": "<i2", "int64": "<i8", "string": None}


def _varlen(n: int) -> bytes:
    """XDF's variable-length integer: a size byte (1, 4 or 8) then the value."""
    if n < 256:
        return struct.pack("<BB", 1, n)
    if n < 2 ** 32:
        return struct.pack("<BI", 4, n)
    return struct.pack("<BQ", 8, n)


def _xml(tag: str, value) -> str:
    return f"<{tag}>{escape(str(value))}</{tag}>"


def channels_xml(labels, unit: str = "", type_: str = "") -> str:
    """The standard ``<channels>`` description block (label / unit / type per
    channel) that EEGLAB and MNE use to name channels."""
    rows = []
    for lab in labels:
        row = _xml("label", lab)
        if unit:
            row += _xml("unit", unit)
        if type_:
            row += _xml("type", type_)
        rows.append(f"<channel>{row}</channel>")
    return "<channels>" + "".join(rows) + "</channels>"


class XdfWriter:
    """Write one XDF file.

        w = XdfWriter(path)
        sid = w.add_stream("EEG_A", "EEG", 5, 256, "float32", desc=channels_xml(...))
        w.write_samples(sid, timestamps, data)      # (N,), (N, 5)
        mid = w.add_stream("Markers", "Markers", 1, 0, "string")
        w.write_samples(mid, [12.5], ["ROUND_1_START"])
        w.close()                                   # writes the stream footers

    Timestamps are float seconds on whatever clock the caller uses; the file
    records one zero ClockOffset per stream to say "all streams were stamped by
    the same clock", which is what the readers' synchronisation step expects.
    """

    def __init__(self, path: str):
        self.path = path
        self._f = open(path, "wb")
        self._streams: dict = {}
        self._f.write(b"XDF:")
        self._chunk(_TAG_FILE_HEADER,
                    b'<?xml version="1.0"?><info><version>1.0</version></info>')

    def _chunk(self, tag: int, content: bytes) -> None:
        self._f.write(_varlen(len(content) + 2) + struct.pack("<H", tag) + content)

    def add_stream(self, name: str, type_: str, channel_count: int,
                   nominal_srate: float, channel_format: str,
                   source_id: str = "", desc: str = "") -> int:
        """Declare a stream; returns its id. ``desc`` is inner XML for the
        stream's ``<desc>`` element (see ``channels_xml``). ``nominal_srate``
        is 0 for irregular streams (markers, video frames)."""
        if channel_format not in FORMATS:
            raise ValueError(f"unsupported channel_format {channel_format!r}")
        sid = len(self._streams) + 1
        header = ('<?xml version="1.0"?><info>'
                  + _xml("name", name) + _xml("type", type_)
                  + _xml("channel_count", int(channel_count))
                  + _xml("nominal_srate", repr(float(nominal_srate)))
                  + _xml("channel_format", channel_format)
                  + _xml("source_id", source_id or name)
                  + _xml("version", "1.1")
                  + _xml("created_at", "0")
                  + _xml("uid", uuid.uuid4())
                  + _xml("session_id", "default")
                  + _xml("hostname", socket.gethostname())
                  + f"<desc>{desc}</desc></info>")
        self._chunk(_TAG_STREAM_HEADER, struct.pack("<I", sid) + header.encode("utf-8"))
        self._streams[sid] = {"format": channel_format, "channels": int(channel_count),
                              "first": None, "last": None, "count": 0}
        return sid

    def write_samples(self, stream_id: int, timestamps, data) -> None:
        """Append samples. Numeric streams take an (N, channels) array; string
        streams take a list of N strings. Every sample carries its own
        timestamp."""
        st = self._streams[stream_id]
        ts = np.asarray(timestamps, dtype="<f8").reshape(-1)
        n = int(ts.shape[0])
        if n == 0:
            return
        if FORMATS[st["format"]] is None:
            if len(data) != n:
                raise ValueError("one string per timestamp")
            parts = []
            for t, text in zip(ts, data):
                raw = str(text).encode("utf-8")
                parts.append(struct.pack("<Bd", 8, t) + _varlen(len(raw)) + raw)
            body = b"".join(parts)
        else:
            arr = np.asarray(data).reshape(n, st["channels"])
            rec = np.empty(n, dtype=[("tsbytes", "u1"), ("ts", "<f8"),
                                     ("v", FORMATS[st["format"]], (st["channels"],))])
            rec["tsbytes"], rec["ts"], rec["v"] = 8, ts, arr
            body = rec.tobytes()
        self._chunk(_TAG_SAMPLES, struct.pack("<I", stream_id) + _varlen(n) + body)
        if st["first"] is None:
            st["first"] = float(ts[0])
        st["last"] = float(ts[-1])
        st["count"] += n

    def close(self) -> None:
        """Write a zero clock offset and a footer per stream, then close."""
        if self._f is None:
            return
        for sid, st in self._streams.items():
            first = st["first"] if st["first"] is not None else 0.0
            last = st["last"] if st["last"] is not None else 0.0
            for t in sorted({first, last}):
                self._chunk(_TAG_CLOCK_OFFSET, struct.pack("<Idd", sid, t, 0.0))
            footer = ('<?xml version="1.0"?><info>'
                      + _xml("first_timestamp", repr(first))
                      + _xml("last_timestamp", repr(last))
                      + _xml("sample_count", st["count"]) + "</info>")
            self._chunk(_TAG_STREAM_FOOTER, struct.pack("<I", sid) + footer.encode("utf-8"))
        self._f.flush()
        self._f.close()
        self._f = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


__all__ = ["XdfWriter", "channels_xml", "FORMATS"]
