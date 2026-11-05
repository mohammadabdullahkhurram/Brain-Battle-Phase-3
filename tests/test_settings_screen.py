"""
The real Settings screen (settings_tab.SettingsTab, DESIGN §7) + its wiring
into app_settings/session_storage/usb_camera for Phase 3 step 7.

Covers:
  1. Video-quality bitrate: app_settings.gb_per_hour / session_storage's
     video_bytes_per_sec_for_mbps are COMPUTED from the chosen bitrate (not a
     hard-coded number), and usb_camera actually requests that bitrate.
  2. Known-headband bookkeeping (app_settings.remember_headband / known_headband_names).
  3. SettingsTab sections: recordings folder (name/path/free space, Change
     folder.../env-override note, Show in Finder), headbands (dropdowns +
     same-headband guard), video quality (radio rows persist), session
     defaults (steppers + bell + Test sound), label list (add/rename/remove/
     reorder/recolour persists to label_list.json with 1..n keys), footer.
  4. Headless end-to-end: change defaults/quality/label list in Settings ->
     start a new (simulated) recording -> the Set up step shows the new
     defaults, the recorder uses the chosen resolution, the disk estimate
     reflects it, and Review & Label shows the new label list.

Run:  python3 tests/test_settings_screen.py
"""
import glob
import json
import os
import shutil
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
    themselves) so nothing here ever touches the real repo's Captures/."""
    cap = tempfile.mkdtemp(prefix="bb_settings_cap_")
    os.environ.pop("BRAIN_BATTLE_CAPTURE_DIR", None)
    os.environ["BRAIN_BATTLE_CONFIG"] = os.path.join(tempfile.mkdtemp(), "settings.json")
    import app_settings as aps
    s = aps.load(); s.capture_dir = cap; s.save()
    return cap


# ─── 1. bitrate is computed, not hard-coded ────────────────────────────────────

def test_bitrate_computed():
    import app_settings as aps
    import session_storage as ss

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
    from session_controller import SessionConfig
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
    import usb_camera as uc
    args_std = uc._encoder_rate_args("libx264", 5.0)
    args_high = uc._encoder_rate_args("libx264", 8.0)
    check("5 Mbps -> -b:v 5M (unchanged default)", "-b:v" in args_std and "5M" in args_std)
    check("8 Mbps -> -b:v 8M", "-b:v" in args_high and "8M" in args_high)
    check("maxrate/bufsize scale with the bitrate (8M run has bigger caps than 5M run)",
          args_high[args_high.index("-maxrate") + 1] != args_std[args_std.index("-maxrate") + 1])


# ─── 2. known headbands ─────────────────────────────────────────────────────────

def test_known_headbands():
    cap = _fresh_env()
    import app_settings as aps
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


# ─── 3. the Settings screen's sections ──────────────────────────────────────────

def test_folder_section():
    cap = _fresh_env()
    from settings_tab import SettingsTab
    import app_settings as aps
    import label_vocab as lv

    st = SettingsTab()
    st.show(); pump()
    check("folder name shows the default Captures-root basename",
          st._folder_name.text() == os.path.basename(aps.load().capture_dir_resolved()))
    check("env-override note hidden + Change enabled with no env var set",
          not st._folder_env_note.isVisible() and st._change_folder_btn.isEnabled())

    got = []
    st.capture_dir_changed.connect(lambda p: got.append(p))
    new_dir = tempfile.mkdtemp(prefix="bb_settings_newcap_")
    st.apply_new_folder(new_dir)
    check("apply_new_folder updates the settings store",
          aps.load().capture_dir == new_dir)
    check("apply_new_folder emits capture_dir_changed", got == [new_dir])
    check("apply_new_folder seeds label_list.json in the new folder",
          os.path.isfile(lv.path(new_dir)))
    check("folder card reflects the new folder",
          st._folder_path.text() == new_dir)


def test_folder_env_override_disables_change():
    cap = _fresh_env()
    fixed = tempfile.mkdtemp(prefix="bb_settings_fixedcap_")
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = fixed
    try:
        from settings_tab import SettingsTab
        st = SettingsTab(); st.show(); pump()
        check("Change folder disabled when BRAIN_BATTLE_CAPTURE_DIR is set",
              not st._change_folder_btn.isEnabled())
        check("env-override note visible and plain-language",
              st._folder_env_note.isVisible()
              and "outside the app" in st._folder_env_note.text())
    finally:
        os.environ.pop("BRAIN_BATTLE_CAPTURE_DIR", None)


def test_headbands_section():
    cap = _fresh_env()
    from settings_tab import SettingsTab
    import app_settings as aps

    st = SettingsTab(); st.show(); pump()
    check("Boxer A/B combos start on the saved mapping",
          st._hb_combo["A"].currentText() == "MuseS-7538"
          and st._hb_combo["B"].currentText() == "MuseS-7564")

    aps.remember_headband("MuseS-NEW")
    st.refresh(); pump()
    idx = st._hb_combo["B"].findText("MuseS-NEW")
    check("newly-known headband appears in the dropdown", idx >= 0)

    # same-headband guard: assigning B to A's current device is rejected
    before = dict(aps.load().headbands)
    a_name = st._hb_combo["A"].currentText()
    st._hb_combo["B"].setCurrentText(a_name)
    pump()
    check("conflict warning shown for a duplicate headband assignment",
          st._hb_warning.isVisible())
    check("conflicting assignment is NOT saved", aps.load().headbands == before)


def test_video_quality_section():
    cap = _fresh_env()
    from settings_tab import SettingsTab
    import app_settings as aps

    st = SettingsTab(); st.show(); pump()
    check("Standard selected by default", st._quality_rows["standard"].isChecked())
    st._quality_rows["high"].setChecked(True); pump()
    check("choosing High persists to settings", aps.load().video_quality == "high")
    st._quality_rows["light"].setChecked(True); pump()
    check("choosing Light persists to settings", aps.load().video_quality == "light")


def test_session_defaults_section():
    cap = _fresh_env()
    from settings_tab import SettingsTab
    import app_settings as aps

    st = SettingsTab(); st.show(); pump()
    st._st_rounds.set_value(7)
    st._st_round.set_value(90)
    st._on_defaults_changed()
    pump()
    d = aps.load().session_defaults
    check("stepper edits persist", (d["num_rounds"], d["round_sec"]) == (7, 90), str(d))

    st._bell.setChecked(False); pump()
    check("bell off persists", aps.load().round_bell is False)
    st._bell.setChecked(True); pump()
    check("bell on persists", aps.load().round_bell is True)

    # Test sound must not crash even headless/offscreen.
    try:
        st._on_test_sound()
        ok = True
    except Exception as e:
        ok = False
        print("    test-sound raised:", e)
    check("Test sound button doesn't crash headless", ok)


def test_label_list_section():
    cap = _fresh_env()
    from settings_tab import SettingsTab
    import label_vocab as lv

    st = SettingsTab(); st.show(); pump()
    check("label list starts from the 8 DESIGN defaults",
          [e["name"] for e in st._labels] == [d["name"] for d in lv.DEFAULT_LABELS])

    st._on_add_label()
    pump()
    check("add label appends with the next free key (9)",
          st._labels[-1]["key"] == 9 and st._labels[-1]["name"] == "New label")
    check("added label's colour is not already in use",
          st._labels[-1]["color"] not in [e["color"] for e in st._labels[:-1]])

    rows = st._current_rows()
    rows[-1]._name.setText("Slip"); rows[-1]._on_name_changed()
    check("rename persists in memory", st._labels[-1]["name"] == "Slip")

    st._move_label(rows[-1], -1)
    pump()
    check("reorder moves the entry and renumbers keys 1..n",
          [e["key"] for e in st._labels] == list(range(1, len(st._labels) + 1))
          and st._labels[-2]["name"] == "Slip")

    on_disk = lv.load(cap)
    check("label list round-trips through label_list.json",
          [e["name"] for e in on_disk] == [e["name"] for e in st._labels])

    rows = st._current_rows()
    before_n = len(st._labels)
    st._remove_label(rows[0])
    pump()
    check("remove shrinks the list by one", len(st._labels) == before_n - 1)
    check("keys stay contiguous 1..n after removal",
          [e["key"] for e in st._labels] == list(range(1, len(st._labels) + 1)))


def test_footer():
    cap = _fresh_env()
    from settings_tab import SettingsTab
    from version import __version__
    st = SettingsTab(); st.show(); pump()
    texts = [c.text() for c in st.findChildren(type(st._folder_name))]
    joined = "\n".join(texts)
    check("footer shows version + Constructor University",
          f"Brain Battle v{__version__}" in joined and "Constructor University" in joined)


# ─── 4. headless end-to-end: Settings -> new recording -> Review & Label ──────

def test_settings_drive_a_real_recording():
    cap = _fresh_env()
    os.environ["BRAIN_BATTLE_SIMULATE"] = "1"
    for k in ("BRAIN_BATTLE_SIMULATE_MISSING_CAMERA", "BRAIN_BATTLE_SIMULATE_DROP_HEADBAND"):
        os.environ.pop(k, None)
    os.environ["BRAIN_BATTLE_CAPTURE_DIR"] = cap

    from settings_tab import SettingsTab
    import app_settings as aps
    import label_vocab as lv
    import session_storage as ss

    # 1. Change defaults, video quality, and the label list in Settings.
    st = SettingsTab(); st.show(); pump()
    st._st_rounds.set_value(2); st._st_round.set_value(10)
    st._st_break.set_value(2); st._st_rest.set_value(2)
    st._on_defaults_changed()
    st._quality_rows["high"].setChecked(True)
    pump()
    st._on_add_label(); pump()
    rows = st._current_rows()
    rows[-1]._name.setText("Settings Special"); rows[-1]._on_name_changed()
    pump()

    settings = aps.load()
    check("settings landed: 2 rounds / 10s / high quality",
          (settings.session_defaults["num_rounds"], settings.session_defaults["round_sec"],
           settings.video_quality) == (2, 10, "high"))

    # 2. Start a new (simulated) recording — it must reload these settings.
    from record_flow import RecordFlow
    flow = RecordFlow()
    try:
        flow.start(); pump(1.8)
        flow.goto_setup()   # populates the Set up step from app_settings.load()
        su = flow._setup_step
        check("Set up step shows the new defaults from Settings",
              (su._st_rounds.value(), su._st_round.value()) == (2, 10))

        w, h, _fps = aps.VIDEO_QUALITY["high"]["w"], aps.VIDEO_QUALITY["high"]["h"], 30
        live_cam = next((s for s in flow._cams if s.is_live), None)
        check("a live camera slot exists before recording", live_cam is not None)
        if live_cam is not None and live_cam._thread is not None:
            check("the recorder's camera thread requests the chosen resolution",
                  (live_cam._thread._want_w, live_cam._thread._want_h) == (w, h),
                  f"{live_cam._thread._want_w}x{live_cam._thread._want_h}")

        # disk estimate on the Set up screen reflects the High-quality rate
        su._recompute(); pump()
        vbps = ss.video_bytes_per_sec_for_mbps(aps.VIDEO_QUALITY["high"]["mbps"])
        est_high = ss.estimate_session_bytes(su.config(), 4, 2, vbps)
        est_std = ss.estimate_session_bytes(
            su.config(), 4, 2, ss.video_bytes_per_sec_for_mbps(aps.VIDEO_QUALITY["standard"]["mbps"]))
        check("High-quality estimate is bigger than Standard's would be",
              est_high > est_std)

        flow.begin_recording()
        t0 = time.time()
        while flow.is_recording() and time.time() - t0 < 40:
            pump(0.1)
        pump(0.6)
        check("flow landed on the Saved step", flow._stack.currentIndex() == flow.STEP_SAVED)

        root = flow._saved_root
        check("a recording root was saved", bool(root) and os.path.isdir(root))

        # 3. Review & Label shows the new label list (shared label_list.json).
        from review_label_tab import ReviewLabelTab
        rt = ReviewLabelTab()
        try:
            rt.load_session_folder(root); rt.wait_loaded()
            names = [e["name"] for e in rt._vocab]
            check("Review & Label shows the Settings-edited label list",
                  "Settings Special" in names, str(names))
        finally:
            rt.shutdown()
    finally:
        flow.shutdown(); pump(0.3)


if __name__ == "__main__":
    test_bitrate_computed()
    test_recorder_requests_chosen_bitrate()
    test_known_headbands()
    test_folder_section()
    test_folder_env_override_disables_change()
    test_headbands_section()
    test_video_quality_section()
    test_session_defaults_section()
    test_label_list_section()
    test_footer()
    test_settings_drive_a_real_recording()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
