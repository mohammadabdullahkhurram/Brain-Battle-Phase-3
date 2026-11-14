"""
More than one person labels the same recording.

  1. Each labeller's labels live in their own file (labels/<name>.json); two
     labellers never touch each other's file, and names that differ only in
     capitals / spacing are the same labeller.
  2. An old single labels.json is migrated automatically (original kept as
     labels.json.v2.bak) and can be claimed ("These are mine") — but another
     labeller's own work can not.
  3. The same labeller on two computers: a save merges what appeared on disk
     instead of overwriting it, and a deletion is not undone by the merge.
  4. Agreement: matching events within a tolerance, in plain language.
  5. Review & Label: you label as yourself; another labeller's labels are shown
     for comparison and never editable; the screen asks for a name when there
     is none and refuses to place a label until it has one.
  6. Marked moments stay in the recording so every labeller can name them.
  7. Sessions counts labels per labeller; history records who did what.

Run:  python3 tests/test_labellers.py
"""
import os as _os, sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.abspath(__file__)))
import _env  # noqa: F401

import json
import os
import sys
import tempfile

from PyQt6.QtWidgets import QApplication

import brain_battle.data.app_settings as app_settings
import brain_battle.data.label_agreement as la
import brain_battle.data.labels_store as ls
import brain_battle.data.session_history as sh
import brain_battle.data.session_storage as ss

_app = QApplication.instance() or QApplication(sys.argv)
_failures = []
DEMO_ROOT = None


def check(name, cond, detail=""):
    ok = bool(cond)
    if not ok:
        _failures.append(name)
    print(f"{'PASS' if ok else 'FAIL'}  {name}  {detail}")


def _lab(name, who, t, t_end=None, by="x"):
    return ls.new_label(name, who, ls.DURATION if t_end is not None else ls.POINT, t,
                        t_end=t_end, created_by=by)


# ── 1. one file per labeller ─────────────────────────────────────────────────

def test_separate_files():
    root = tempfile.mkdtemp(prefix="bb_lab_")
    maya = ls.open_for_labeller(root, "Maya K")
    jonas = ls.open_for_labeller(root, "Jonas R")
    maya.add(_lab("Jab", "A", 1.0)); maya.save()
    jonas.add(_lab("Jab", "A", 1.2)); jonas.add(_lab("Hook", "B", 4.0)); jonas.save()
    files = sorted(os.listdir(ls.labels_dir(root)))
    check("each labeller has their own file", files == ["jonas-r.json", "maya-k.json"], str(files))
    check("no single shared labels.json is written", not os.path.exists(os.path.join(root, "labels.json")))
    maya.add(_lab("Cross", "A", 2.0)); maya.save()
    again = ls.read_all(root)
    check("saving one labeller leaves the other's labels untouched",
          len(again["Jonas R"]) == 2 and len(again["Maya K"]) == 2, str({k: len(v) for k, v in again.items()}))
    check("every label read back says whose it is",
          all(l["labeller"] == who for who, labs in again.items() for l in labs))
    check("the file records the labeller's name as typed",
          json.load(open(ls.labeller_path(root, "Maya K"), encoding="utf-8"))["labeller"] == "Maya K")
    same = ls.open_for_labeller(root, "  maya   k ")
    check("capitals / spacing don't make a second labeller",
          len(same) == 2 and ls.same_labeller("Maya K", "maya-k") and not ls.same_labeller("Maya K", "Maya J"))
    check("a name with characters illegal in file names still gets a safe file",
          ls.labeller_slug('A/B: "C"?') == "a-b-c" and ls.labeller_slug("") == "labeller")
    check("labeller_counts", ls.labeller_counts(root) == {"Jonas R": 2, "Maya K": 2})
    check("all_labels is time-sorted across labellers",
          [l["t_start"] for l in ls.all_labels(root)] == [1.0, 1.2, 2.0, 4.0])


# ── 2. migration of the old single file ──────────────────────────────────────

def test_migration_and_claim():
    root = tempfile.mkdtemp(prefix="bb_mig_")
    legacy = {"schema_version": 2, "time_axis": "t_session_seconds",
              "labels": [_lab("Jab", "A", 1.0, by="sam"), _lab("Clinch", "Both", 3.0, 5.0, by="sam")]}
    json.dump(legacy, open(os.path.join(root, "labels.json"), "w", encoding="utf-8"))
    check("an unmigrated labels.json is already visible to listings (read-only)",
          ls.labeller_counts(root) == {"sam": 2} and os.path.isfile(os.path.join(root, "labels.json")))
    owner = ls.migrate_legacy(root)
    check("migration moves the labels to labels/<created_by>.json",
          owner == "sam" and os.path.isfile(ls.labeller_path(root, "sam")), str(owner))
    check("the original is kept as labels.json.v2.bak and labels.json is gone",
          os.path.isfile(os.path.join(root, ls.LEGACY_BACKUP))
          and not os.path.exists(os.path.join(root, "labels.json")))
    check("nothing lost, ids kept",
          [l["id"] for l in ls.read_all(root)["sam"]] == [l["id"] for l in legacy["labels"]])
    check("migrating again is a no-op", ls.migrate_legacy(root) is None)
    check("the migrated set is offered for claiming", ls.legacy_sets(root) == ["sam"])

    me = ls.open_for_labeller(root, "Samira Okafor")
    moved = ls.claim_legacy(root, "sam", me)
    check("claiming moves the labels into my file and removes the legacy set",
          moved == 2 and ls.labeller_counts(root) == {"Samira Okafor": 2} and ls.legacy_sets(root) == [],
          f"{moved} {ls.labeller_counts(root)}")

    other = ls.open_for_labeller(root, "Jonas R"); other.add(_lab("Hook", "B", 9.0)); other.save()
    check("another labeller's own labels can NOT be claimed",
          ls.claim_legacy(root, "Jonas R", me) == 0 and ls.labeller_counts(root)["Jonas R"] == 1)

    # a v1 file (the oldest format) goes the same way, attributed to "Earlier labels"
    root1 = tempfile.mkdtemp(prefix="bb_mig1_")
    json.dump({"schema_version": 1, "labels": [{"t_seconds": 2.5, "label": "Jab", "boxer": "A"}]},
              open(os.path.join(root1, "labels.json"), "w", encoding="utf-8"))
    check("a version-1 file migrates too, as 'Earlier labels'",
          ls.migrate_legacy(root1) == ls.EARLIER_LABELS
          and ls.read_all(root1)[ls.EARLIER_LABELS][0]["t_start"] == 2.5)


# ── 3. the same labeller on two computers ────────────────────────────────────

def test_merge_on_save():
    root = tempfile.mkdtemp(prefix="bb_merge_")
    first = ls.open_for_labeller(root, "Maya K")
    a = first.add(_lab("Jab", "A", 1.0)); b = first.add(_lab("Cross", "A", 2.0)); first.save()
    pc1 = ls.open_for_labeller(root, "Maya K")
    pc2 = ls.open_for_labeller(root, "Maya K")
    pc1.add(_lab("Hook", "B", 3.0)); pc1.save()
    pc2.add(_lab("Block", "B", 4.0)); pc2.delete(a["id"]); pc2.save()     # pc2 never saw Hook
    names = sorted(l["label"] for l in ls.read_all(root)["Maya K"])
    check("a save keeps what the other computer added, and my deletion stays deleted",
          names == ["Block", "Cross", "Hook"], str(names))
    pc1.save()
    names = sorted(l["label"] for l in ls.read_all(root)["Maya K"])
    check("the other computer's next save takes my addition in, without bringing back "
          "a label it has not itself deleted", "Block" in names and "Hook" in names, str(names))
    check("undo/redo still work on a bound store",
          pc2.undo() is not None and pc2.by_id(a["id"]) is not None and pc2.redo() is not None)


# ── 4. agreement ─────────────────────────────────────────────────────────────

def test_agreement():
    mine = [_lab("Jab", "A", 1.00), _lab("Cross", "A", 2.00), _lab("Hook", "B", 5.00),
            _lab("Clinch", "Both", 10.0, 14.0)]
    theirs = [_lab("Jab", "A", 1.30), _lab("Cross", "B", 2.10), _lab("Hook", "B", 6.00),
              _lab("Clinch", "Both", 11.5, 13.0), _lab("Jab", "A", 20.0)]
    ag = la.compare(mine, theirs, "Maya", "Jonas", tolerance=0.5)
    check("same label + same boxer + within tolerance is the same event; an overlapping "
          "lasting label counts too", len(ag.matched) == 2, str(len(ag.matched)))
    check("a different boxer, or too far apart, is not the same event",
          len(ag.only_a) == 2 and len(ag.only_b) == 3, f"{len(ag.only_a)} {len(ag.only_b)}")
    check("percent = same events / all distinct events", ag.total == 7 and ag.percent == 29,
          f"{ag.total} {ag.percent}")
    s = ag.sentence(a_is_you=True)
    check("plain-language sentence", s.startswith("You and Jonas marked the same event 2 times out of 7")
          and "(29% agreement)" in s and "only you marked" in s and "only Jonas marked" in s, s)
    wide = la.compare(mine, theirs, "Maya", "Jonas", tolerance=1.0)
    check("a wider tolerance matches more", len(wide.matched) == 3, str(len(wide.matched)))
    check("each label is matched at most once (closest first)",
          len(la.compare([_lab("Jab", "A", 1.0)],
                         [_lab("Jab", "A", 1.1), _lab("Jab", "A", 1.2)], tolerance=0.5).matched) == 1)
    check("nothing labelled → 0% and a sentence that says so",
          la.compare([], [], "A", "B").percent == 0 and "yet" in la.compare([], [], "A", "B").sentence())
    pairs = la.pairwise({"A": mine, "B": theirs, "C": []})
    check("pairwise covers every pair", [(p.name_a, p.name_b) for p in pairs] ==
          [("A", "B"), ("A", "C"), ("B", "C")])


# ── 5 + 6. Review & Label ────────────────────────────────────────────────────

def test_review_screen():
    import brain_battle.ui.review_label_screen as rl
    root = DEMO_ROOT
    other = ls.open_for_labeller(root, "Jonas R")
    other.add(_lab("Jab", "A", 4.2, by="Jonas R")); other.add(_lab("Hook", "B", 8.0, by="Jonas R"))
    other.save()
    _env.set_labeller("Maya K")
    tab = rl.ReviewLabelScreen()
    try:
        tab.load_session_folder(root); tab.wait_loaded()
        jab = next(e for e in tab._vocab if e["name"] == "Jab")
        tab._set_who("A"); tab._seek(4.0); tab._place_from_vocab(jab)
        mine = json.load(open(ls.labeller_path(root, "Maya K"), encoding="utf-8"))
        check("my label is saved in MY file, under my name",
              mine["labeller"] == "Maya K" and len(mine["labels"]) == 1
              and mine["labels"][0]["created_by"] == "Maya K")
        check("the other labeller's file is untouched",
              len(json.load(open(ls.labeller_path(root, "Jonas R"), encoding="utf-8"))["labels"]) == 2)
        check("others' labels are offered for comparison, mine are not among them",
              list(tab._others) == ["Jonas R"] and tab._compare_with == "Jonas R")
        check("comparison is off until asked for (the timeline shows only my labels)",
              tab._strip.others == [])
        tab._tabs.set_value("compare", emit=True)
        check("Compare shows the other labeller on the timeline, separately from mine",
              [n for n, _ in tab._strip.others] == ["Jonas R"] and len(tab._strip.others[0][1]) == 2
              and len(tab._strip.labels) == 1)
        ag = tab.agreement()
        check("agreement between me and them", len(ag.matched) == 1 and len(ag.only_b) == 1
              and "You and Jonas R marked the same event 1 time out of 2 (50% agreement)"
              in tab._agree_text.text(), tab._agree_text.text())
        check("another labeller's labels cannot be deleted from here",
              tab._delete_label(tab._others["Jonas R"][0]["id"]) is None
              and len(ls.read_all(root)["Jonas R"]) == 2)
        # a third person starts labelling on another computer: it shows up by itself
        third = ls.open_for_labeller(root, "Noor"); third.add(_lab("Jab", "A", 4.1)); third.save()
        tab._reload_others()
        check("a labeller who appears later is picked up without reopening",
              sorted(tab._others) == ["Jonas R", "Noor"] and tab._compare_combo.count() == 2)
        events = [e for e in sh.read_history(root) if e["event"] == "labels_added"]
        check("history says who placed the label",
              events and events[-1]["user"] == "Maya K"
              and events[-1]["details"].get("labeller") == "Maya K"
              and events[-1]["details"].get("who") == "A", str(events[-1:]))

        # switching who is labelling switches files — nothing is carried over
        tab.set_labeller("Noor")
        check("changing the labeller opens THEIR labels",
              tab._store.labeller == "Noor" and len(tab._store) == 1 and "Maya K" in tab._others)
    finally:
        tab.shutdown()

    # no name yet: the screen asks, and will not place a label until it has one
    s = app_settings.load(); s.labeller_name = ""; s.save()
    tab = rl.ReviewLabelScreen()
    try:
        tab.load_session_folder(root); tab.wait_loaded()
        before = ls.labeller_counts(root)
        tab._place_from_vocab(tab._vocab[0])
        check("with no name, nothing is placed and the screen asks who is labelling",
              ls.labeller_counts(root) == before and not tab._name_ask.isHidden()
              and len(tab._store) == 0)
        got = []
        tab.labeller_changed.connect(got.append)
        tab._name_edit.setText("  Dana   W "); tab._confirm_name()
        check("giving a name saves it once, tidied up, and tells the app",
              app_settings.labeller_name() == "Dana W" and got == ["Dana W"] and tab._name_ask.isHidden())
        tab._place_from_vocab(tab._vocab[0])
        check("…and labelling then works", ls.labeller_counts(root).get("Dana W") == 1)
    finally:
        tab.shutdown()
        _env.set_labeller()


def test_moments_for_every_labeller():
    import brain_battle.ui.review_label_screen as rl
    root = DEMO_ROOT
    ss._update_manifest(root, lambda m: m.update(moments=[{"t_session_seconds": 6.5}]))
    _env.set_labeller("Maya K")
    tab = rl.ReviewLabelScreen()
    try:
        tab.load_session_folder(root); tab.wait_loaded()
        check("a marked moment is waiting to be named", len(tab._moments) == 1)
        tab._apply_moment_name(6.5, tab._vocab[0], "B")
        check("naming it makes a normal label of mine and it stops asking me",
              len(tab._moments) == 0 and tab._store.moment_named(6.5)
              and any(abs(l["t_start"] - 6.5) < 1e-6 for l in tab._store.labels))
        check("the mark itself stays in the recording", len(ss.read_moments(root)) == 1)
        tab.set_labeller("Jonas R")
        check("…so another labeller is still asked to name it", len(tab._moments) == 1)
    finally:
        tab.shutdown()
        _env.set_labeller()


# ── 7. Sessions ──────────────────────────────────────────────────────────────

def test_sessions_counts():
    info = ss.session_info(DEMO_ROOT)
    check("Sessions counts every labeller's labels",
          info.labels_count == sum(info.labellers.values()) and info.labels_count >= 4
          and {"Maya K", "Jonas R"} <= set(info.labellers), str(info.labellers))
    check("labels_by(name) is per labeller, however the name is typed",
          info.labels_by("maya k") == info.labellers["Maya K"] and info.labels_by("Nobody") == 0)
    import brain_battle.ui.sessions_screen as st
    check("the Labels column says how many and from whom",
          st.labels_text(info)[0] == f"{info.labels_count} labels" and st.labels_text(info)[1] != "")
    cached = ss.session_info_cached(DEMO_ROOT)
    extra = ls.open_for_labeller(DEMO_ROOT, "Late Labeller"); extra.add(_lab("Jab", "A", 1.0)); extra.save()
    check("the scan cache notices a new labeller's file",
          ss.session_info_cached(DEMO_ROOT).labels_count == cached.labels_count + 1)


if __name__ == "__main__":
    from tools.generate_demo_session import generate_demo_session
    DEMO_ROOT = generate_demo_session(out=tempfile.mkdtemp(prefix="bb_labellers_"), rounds=1,
                                      round_sec=8, break_sec=2, baseline_sec=2, seed=11,
                                      boxer_a="Ivy", boxer_b="Zoe")["root"]
    test_separate_files()
    test_migration_and_claim()
    test_merge_on_save()
    test_agreement()
    test_review_screen()
    test_moments_for_every_labeller()
    test_sessions_counts()
    print(f"\n{'RED' if _failures else 'GREEN'}: {len(_failures)} failure(s)")
    for f in _failures:
        print("  FAIL:", f)
    sys.exit(1 if _failures else 0)
