"""
The real Settings screen (settings_screen.SettingsScreen, DESIGN §7) + its wiring
into app_settings/session_storage/usb_camera for Phase 3 step 7.

Covers:
  1. Video-quality bitrate: app_settings.gb_per_hour / session_storage's
     video_bytes_per_sec_for_mbps are COMPUTED from the chosen bitrate (not a
     hard-coded number), and usb_camera actually requests that bitrate.
  2. Known-headband bookkeeping (app_settings.remember_headband / known_headband_names).
  3. SettingsScreen (design F): This computer (Your name, the recordings
     folder + Change, the env-override note), the shared Label list (add with
     Enter, remove, persists to label_list.json with 1..n keys), the shared
     Fighter colours (presets always there, add/remove, saved to
     fighter_colours.json, duplicates refused), Advanced (the four switches
     with their defaults; headbands with the same-headband guard; video
     quality persists).
  4. Headless end-to-end: change quality / the label list / a colour in
     Settings -> a new (simulated) recording uses the chosen resolution, Get
     ready offers the new colour, the plan set on Get ready is remembered for
     the next recording, and Review & Label shows the new label list.

Run:  python3 tests/test_settings_screen.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401  (temp settings file + a labeller name; see tests/_env.py)
import json
import os
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
try:
    import vlc  # noqa: F401
except Exception:
    import types
    sys.modules["vlc"] = types.ModuleType("vlc")

from PyQt6.QtWidgets import QApplication

_app = QApplication.instance() or QApplication(sys.argv)

_failures = []


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def pump(secs=0.05):
    t0 = time.time()
    while time.time() - t0 < secs:
        _app.processEvents(); time.sleep(0.02)


def _fresh_env():
    """Isolated settings file + an isolated recordings folder, saved as the
    settings store's OWN capture_dir (not BRAIN_BATTLE_CAPTURE_DIR — tests
    that specifically exercise the env-override behaviour set that
    themselves) so nothing here ever touches the real default recordings folder."""
    cap = tempfile.mkdtemp(prefix="bb_settings_cap_")
    os.environ.pop("BRAIN_BATTLE_CAPTURE_DIR", None)
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")
    import brain_battle.data.app_settings as aps
    s = aps.load(); s.capture_dir = cap; s.save()
    return cap


# ─── 1. bitrate is computed, not hard-coded ────────────────────────────────────

def test_bitrate_computed():
    import brain_battle.data.app_settings as aps
    import brain_battle.data.session_storage as ss

    for key in ("standard", "high", "light"):
        q = aps.VIDEO_QUALITY[key]
        expect_gb = q["mbps"] * 3600 / 8 / 1000
        check(f"gb_per_hour({key}) matches the mbps formula",
              abs(aps.gb_per_hour(q["mbps"]) - expect_gb) < 1e-9)
        expect_vbps = int(q["mbps"] * 1_000_000 / 8 * ss.VIDEO_RATE_HEADROOM)
        check(f"video_bytes_per_sec_for_mbps({key}) matches the mbps formula",
              ss.video_bytes_per_sec_for_mbps(q["mbps"]) == expect_vbps)

    check("high quality GB/hour > standard's (bigger bitrate -> bigger estimate)",
          aps.gb_per_hour(aps.VIDEO_QUALITY["high"]["mbps"]) >
          aps.gb_per_hour(aps.VIDEO_QUALITY["standard"]["mbps"]))

    # disk estimate scales with the chosen quality
    from brain_battle.capture.session_controller import SessionConfig
    cfg = SessionConfig(num_rounds=1, round_sec=60, break_sec=0, baseline_sec=0)
    vbps_std = ss.video_bytes_per_sec_for_mbps(aps.VIDEO_QUALITY["standard"]["mbps"])
    vbps_high = ss.video_bytes_per_sec_for_mbps(aps.VIDEO_QUALITY["high"]["mbps"])
    est_std = ss.estimate_session_bytes(cfg, 4, 2, vbps_std)
    est_high = ss.estimate_session_bytes(cfg, 4, 2, vbps_high)
    check("disk estimate at High quality > Standard", est_high > est_std,
          f"{est_std} vs {est_high}")
    # default (no explicit rate) must still equal the Standard-derived rate
    check("estimate_session_bytes default == Standard-quality rate",
          ss.estimate_session_bytes(cfg, 4, 2) == est_std)


def test_recorder_requests_chosen_bitrate():
    import brain_battle.capture.usb_camera as uc
    args_std = uc._encoder_rate_args("libx264", 5.0)
    args_high = uc._encoder_rate_args("libx264", 8.0)
    check("5 Mbps -> -b:v 5M (unchanged default)", "-b:v" in args_std and "5M" in args_std)
    check("8 Mbps -> -b:v 8M", "-b:v" in args_high and "8M" in args_high)
    check("maxrate/bufsize scale with the bitrate (8M run has bigger caps than 5M run)",
          args_high[args_high.index("-maxrate") + 1] != args_std[args_std.index("-maxrate") + 1])


# ─── 2. known headbands ─────────────────────────────────────────────────────────

def test_known_headbands():
    cap = _fresh_env()
    import brain_battle.data.app_settings as aps
    s = aps.load()
    check("known headbands seeded from the default A/B mapping",
          set(s.known_headband_names()) == {"MuseS-7538", "MuseS-7564"})
    aps.remember_headband("MuseS-NEW")
    s2 = aps.load()
    check("remember_headband persists a new device name",
          "MuseS-NEW" in s2.known_headband_names())
    aps.remember_headband("MuseS-NEW")   # idempotent
    check("remember_headband doesn't duplicate",
          aps.load().known_headbands.count("MuseS-NEW") == 1)
    aps.remember_headband("")
    check("remember_headband('') is a no-op", "" not in aps.load().known_headband_names())


# ─── 3. the Settings screen ──────────────────────────────────────────────────────

def test_this_computer_card():
    cap = _fresh_env()
    from brain_battle.ui.settings_screen import SettingsScreen
    import brain_battle.data.app_settings as aps
    st = SettingsScreen(); st.show(); pump()
    st._labeller_edit.setText("  Maya   K "); st._on_labeller_edited()
    check("Your name is saved tidied up", aps.load().labeller_name == "Maya K")
    st._labeller_edit.setText(""); st._on_labeller_edited()
    check("Your name is never cleared back to nobody", aps.load().labeller_name == "Maya K")
    check("the folder line names the folder and its free space",
          os.path.basename(cap) in st._folder_line.text() and "free" in st._folder_line.text(),
          st._folder_line.text())
    new = tempfile.mkdtemp(prefix="bb_settings_new_")
    got = []
    st.capture_dir_changed.connect(got.append)
    st.apply_new_folder(new)
    check("Change: the new folder is saved and announced",
          aps.load().capture_dir == new and got == [new])
    check("Change: the shared label list is seeded in the new folder",
          os.path.isfile(os.path.join(new, "label_list.json")))
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = new
    st.refresh(); pump()
    check("a folder set outside the app turns Change off and says why",
          not st._change_folder_btn.isEnabled() and "BRAIN_BATTLE_CAPTURE_DIR" in st._folder_env_note.text())
    os.environ.pop("BRAIN_BATTLE_CAPTURE_DIR", None)
    st.close()


def test_label_list_card():
    cap = _fresh_env()
    from brain_battle.ui.settings_screen import SettingsScreen
    import brain_battle.data.label_vocab as lv
    st = SettingsScreen(); st.show(); pump()
    check("the shared list starts with the 8 defaults", st.label_names()[:2] == ["Punch initiated", "Punch landed"]
          and len(st.label_names()) == 8, str(st.label_names()))
    st._on_add_label(); pump()
    check("+ Add opens the inline box", st._new_label.isVisible() and not st._add_label_btn.isVisible())
    st._new_label.setText("Slip"); st._commit_new_label(); pump()
    saved = lv.load(cap)
    check("Enter adds it to label_list.json as key 9",
          saved[-1]["name"] == "Slip" and saved[-1]["key"] == 9)
    check("a duplicate name is refused", st.add_label("slip") is False)
    st._remove_label(0); pump()
    saved = lv.load(cap)
    check("× removes it and the keys are renumbered 1..n",
          saved[0]["name"] == "Punch landed" and [x["key"] for x in saved] == list(range(1, 9)))
    st.close()


def test_colours_card():
    cap = _fresh_env()
    from brain_battle.ui.settings_screen import SettingsScreen
    import brain_battle.data.fighters as fighters
    st = SettingsScreen(); st.show(); pump()
    check("Red, Blue and Black are there", st.colour_names() == ["Red", "Blue", "Black"])
    check("adding a colour saves it to fighter_colours.json",
          st.add_colour("green", "#0F7A62") is None
          and [c["name"] for c in fighters.load_colours(cap)][-1] == "Green")
    check("a duplicate colour name is refused",
          st.add_colour("Green", "#000000") == "That colour name already exists")
    st.remove_colour("Red")
    check("a preset cannot be removed", "Red" in st.colour_names())
    st.remove_colour("Green")
    check("an added colour can be removed", "Green" not in [c["name"] for c in fighters.load_colours(cap)])
    st.close()


def test_advanced_card():
    _fresh_env()
    from brain_battle.ui.settings_screen import SettingsScreen
    import brain_battle.data.app_settings as aps
    st = SettingsScreen(); st.show(); pump()
    check("Advanced is folded until opened", not st._adv_body.isVisible())
    st.open_advanced(); pump()
    check("Advanced opens", st._adv_body.isVisible())
    b = st._signal_boxes
    check("defaults: head movement on, pulse off, 60 fps on, LSL off",
          b["record_motion"].isChecked() and not b["record_ppg"].isChecked()
          and b["record_60fps"].isChecked() and not b["lsl_streams"].isChecked())
    check("video sizes say 'at 60 fps' while 60 fps is on",
          all(r.text().endswith("at 60 fps") for r in st._quality_rows.values()),
          st._quality_rows["standard"].text())
    b["lsl_streams"].setChecked(True); b["record_60fps"].setChecked(False)
    check("…and follow the switch to 30 fps (smaller sizes)",
          all(r.text().endswith("at 30 fps") for r in st._quality_rows.values())
          and "2.2 GB" in st._quality_rows["standard"].text(), st._quality_rows["standard"].text())
    s = aps.load()
    check("switches save straight away", s.lsl_streams is True and s.record_60fps is False)
    st._quality_rows["high"].setChecked(True)
    check("video quality persists", aps.load().video_quality == "high")
    st._hb_combo["B"].setCurrentText(st._hb_combo["A"].currentText())
    check("the same headband for both boxers is refused, with a reason",
          aps.load().headbands["A"] != aps.load().headbands["B"]
          and "different headbands" in st._hb_warning.text())
    st.close()


# ─── 4. Settings drive a real recording ──────────────────────────────────────

def test_settings_drive_a_real_recording():
    cap = _fresh_env()
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    for k in ("BRAIN_BATTLE_SIMULATE_MISSING_CAMERA", "BRAIN_BATTLE_SIMULATE_DROP_HEADBAND"):
        os.environ.pop(k, None)
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = cap
    from brain_battle.ui.settings_screen import SettingsScreen
    import brain_battle.data.app_settings as aps
    from brain_battle.capture.session_controller import SessionConfig

    st = SettingsScreen(); st.show(); pump()
    st._quality_rows["high"].setChecked(True)
    st.add_label("Settings Special")
    st.add_colour("Gold", "#C9A227")
    pump()

    from brain_battle.ui.record_flow import RecordFlow
    flow = RecordFlow()
    try:
        flow.start(); pump(1.8)
        gr = flow._get_ready
        check("Get ready offers the new shared colour",
              "Gold" in [c["name"] for c in gr.card("A")._colours])
        live_cam = next((s for s in flow._cams if s.is_live), None)
        w, h = aps.VIDEO_QUALITY["high"]["w"], aps.VIDEO_QUALITY["high"]["h"]
        check("the recorder's camera thread requests the chosen resolution",
              live_cam is not None and (live_cam._thread._want_w, live_cam._thread._want_h) == (w, h))
        flow.set_plan(SessionConfig(num_rounds=1, round_sec=8, break_sec=0, baseline_sec=2))
        flow.set_fighters("K7Q2", "M3XA", "Gold", "Blue")
        flow.begin_recording()
        t0 = time.time()
        while flow.is_recording() and time.time() - t0 < 40:
            pump(0.1)
        pump(0.6)
        root = flow._saved_root
        check("a recording was saved", bool(root) and os.path.isdir(root))
        man = json.load(open(os.path.join(root, "session_manifest.json")))
        check("the chosen colour is in the manifest",
              man["boxers"]["A"]["colour"] == "Gold" and man["boxers"]["A"]["colour_hex"] == "#C9A227")
        check("the plan from Get ready is remembered for next time",
              aps.load().session_defaults["round_sec"] == 8)

        from brain_battle.ui.review_label_screen import ReviewLabelScreen
        rt = ReviewLabelScreen()
        try:
            rt.load_session_folder(root); rt.wait_loaded()
            check("Review & Label shows the Settings-edited label list",
                  "Settings Special" in [e["name"] for e in rt._vocab])
        finally:
            rt.shutdown()
    finally:
        flow.shutdown(); pump(0.3)
        st.close()


if __name__ == "__main__":
    test_bitrate_computed()
    test_recorder_requests_chosen_bitrate()
    test_known_headbands()
    test_this_computer_card()
    test_label_list_card()
    test_colours_card()
    test_advanced_card()
    test_settings_drive_a_real_recording()

    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
