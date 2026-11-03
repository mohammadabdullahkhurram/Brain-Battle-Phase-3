"""
app_settings.py
───────────────
Small application-settings store (a plain JSON file in the user's config
directory) for everything that used to be hard-coded in source: the
headband → Boxer A/B mapping (replacing `eeg_module.BOXER_LABELS`), the
default session plan, the round bell on/off, and the record video quality.

Pure stdlib + filesystem (no Qt), so it is trivially testable and usable from
any thread. The Settings screen edits this store in a later step; for now the
guided recording flow reads it and the defaults match today's hard-coded
values exactly, so behaviour is unchanged until the user edits anything.

Config file location (first that applies):
  • $BRAIN_BATTLE_CONFIG                      (explicit file path — tests use this)
  • macOS:   ~/Library/Application Support/Brain Battle/settings.json
  • Windows: %APPDATA%/Brain Battle/settings.json
  • else:    ~/.config/brain-battle/settings.json
"""
from __future__ import annotations

import json
import os
import platform
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Optional

from session_controller import SessionConfig

SCHEMA_VERSION = 1

# Record video-quality presets (DESIGN §7). (width, height, fps, ~GB per hour
# per camera for the Settings copy). "standard" is today's hard-coded default.
VIDEO_QUALITY = {
    "standard": {"label": "Standard", "w": 960,  "h": 540, "fps": 30, "gb_per_hour": 2.2},
    "high":     {"label": "High",     "w": 1280, "h": 720, "fps": 30, "gb_per_hour": 4.0},
    "light":    {"label": "Light",    "w": 640,  "h": 360, "fps": 30, "gb_per_hour": 1.0},
}

# Default headband → Boxer mapping (the former eeg_module.BOXER_LABELS values).
_DEFAULT_HEADBANDS = {"A": "MuseS-7538", "B": "MuseS-7564"}


# ─── config-file location ─────────────────────────────────────────────────────

def _config_dir() -> str:
    env = os.environ.get("BRAIN_BATTLE_CONFIG")
    if env:
        return os.path.dirname(os.path.abspath(env)) or "."
    sysname = platform.system()
    home = os.path.expanduser("~")
    if sysname == "Darwin":
        return os.path.join(home, "Library", "Application Support", "Brain Battle")
    if sysname == "Windows":
        base = os.environ.get("APPDATA") or os.path.join(home, "AppData", "Roaming")
        return os.path.join(base, "Brain Battle")
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(home, ".config")
    return os.path.join(base, "brain-battle")


def config_path() -> str:
    env = os.environ.get("BRAIN_BATTLE_CONFIG")
    if env:
        return os.path.abspath(env)
    return os.path.join(_config_dir(), "settings.json")


def default_capture_root() -> str:
    """Where recordings are saved when the user hasn't chosen a folder: the
    BRAIN_BATTLE_CAPTURE_DIR override, else a `Captures/` folder next to the
    source. (Historically this lived in live_session_tab._default_capture_root.)"""
    env = os.environ.get("BRAIN_BATTLE_CAPTURE_DIR")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "Captures")


# ─── the settings object ──────────────────────────────────────────────────────

@dataclass
class AppSettings:
    capture_dir:      Optional[str] = None          # None → default_capture_root()
    headbands:        dict = field(default_factory=lambda: dict(_DEFAULT_HEADBANDS))
    session_defaults: dict = field(default_factory=lambda: {
        "num_rounds": 3, "round_sec": 180, "break_sec": 60, "baseline_sec": 60})
    round_bell:       bool = True
    video_quality:    str = "standard"

    # ---- derived accessors ----
    def capture_dir_resolved(self) -> str:
        return self.capture_dir or default_capture_root()

    def session_config(self) -> SessionConfig:
        d = self.session_defaults or {}
        base = SessionConfig()
        return SessionConfig(
            num_rounds=int(d.get("num_rounds", base.num_rounds)),
            round_sec=int(d.get("round_sec", base.round_sec)),
            break_sec=int(d.get("break_sec", base.break_sec)),
            baseline_sec=int(d.get("baseline_sec", base.baseline_sec)),
        )

    def headband_for(self, who: str) -> Optional[str]:
        return (self.headbands or {}).get(str(who).upper())

    def boxer_labels(self) -> dict:
        """{device_name: "BOXER A"/"BOXER B"} — the former hard-coded
        BOXER_LABELS, now derived from the headband mapping."""
        out = {}
        for who in ("A", "B"):
            dev = self.headband_for(who)
            if dev:
                out[dev] = f"BOXER {who}"
        return out

    def video_dims(self) -> tuple[int, int, int]:
        q = VIDEO_QUALITY.get(self.video_quality, VIDEO_QUALITY["standard"])
        return int(q["w"]), int(q["h"]), int(q["fps"])

    # ---- persistence ----
    def to_json(self) -> dict:
        return {"schema_version": SCHEMA_VERSION, **asdict(self)}

    def save(self, path: Optional[str] = None) -> None:
        p = Path(path or config_path())
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2, ensure_ascii=False),
                       encoding="utf-8")
        tmp.replace(p)


def load(path: Optional[str] = None) -> AppSettings:
    """Load settings, falling back to defaults for anything missing or
    unreadable. Never raises — a corrupt file yields defaults."""
    p = Path(path or config_path())
    if not p.is_file():
        return AppSettings()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AppSettings()
    if not isinstance(data, dict):
        return AppSettings()
    s = AppSettings()
    if isinstance(data.get("capture_dir"), str) and data["capture_dir"]:
        s.capture_dir = data["capture_dir"]
    hb = data.get("headbands")
    if isinstance(hb, dict):
        s.headbands = {"A": hb.get("A") or _DEFAULT_HEADBANDS["A"],
                       "B": hb.get("B") or _DEFAULT_HEADBANDS["B"]}
    sd = data.get("session_defaults")
    if isinstance(sd, dict):
        s.session_defaults.update({k: sd[k] for k in
            ("num_rounds", "round_sec", "break_sec", "baseline_sec") if k in sd})
    if isinstance(data.get("round_bell"), bool):
        s.round_bell = data["round_bell"]
    if data.get("video_quality") in VIDEO_QUALITY:
        s.video_quality = data["video_quality"]
    return s


# ─── module-level convenience (the live app uses one shared settings file) ─────

def boxer_labels() -> dict:
    """{device_name: "BOXER A"/"BOXER B"} from the saved settings — the
    drop-in replacement for the old eeg_module.BOXER_LABELS dict."""
    return load().boxer_labels()


__all__ = [
    "AppSettings", "load", "config_path", "default_capture_root",
    "boxer_labels", "VIDEO_QUALITY", "SCHEMA_VERSION",
]
