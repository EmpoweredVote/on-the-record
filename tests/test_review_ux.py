# tests/test_review_ux.py
from __future__ import annotations
from src.models import SpeakerMapping
from src.models import Segment
from src.review import identity_label
from src.review import enrollment_warnings
from src.review import snapshot_mapping, restore_mapping
from src.roster import Roster, RosterMember


def test_identity_label_for_each_status():
    assert identity_label(SpeakerMapping("S0", "Jane", politician_slug="jane-adams")) == "essentials:jane-adams"
    assert identity_label(SpeakerMapping("S0", "Bob", local_slug="bob-smith")) == "local:bob-smith"
    assert identity_label(SpeakerMapping("S0", "Unknown", local_slug="unidentified-m-s0",
                                         speaker_status="unidentified")) == "unidentified"
    assert identity_label(SpeakerMapping("S0", "Music", speaker_status="non_speaker")) == "non-speaker"
    # status wins over a (stale) slug — pins the precedence ordering
    assert identity_label(SpeakerMapping("S0", "X", politician_slug="p",
                                         speaker_status="unidentified")) == "unidentified"
    assert identity_label(SpeakerMapping("S0", "X", politician_slug="p",
                                         speaker_status="non_speaker")) == "non-speaker"
    assert identity_label(SpeakerMapping("S0", "Someone")) == "unlinked"
    assert identity_label(None) == "unlinked"


def _roster():
    return Roster(city="", body="B", members=[
        RosterMember(name="Hopi Stosberg", aliases=["Stosberg"],
                     politician_slug="hopi-h-stosberg", politician_id="u1"),
    ])


def test_warns_on_name_slug_mismatch():
    mappings = {"S0": SpeakerMapping("S0", "Isak Nti Asare", politician_slug="hopi-h-stosberg")}
    warns = enrollment_warnings(mappings, roster=None)
    assert any(w["kind"] == "name_slug_mismatch" and w["label"] == "S0" for w in warns)


def test_warns_on_duplicate_name_across_labels():
    mappings = {
        "S0": SpeakerMapping("S0", "Jane Adams", politician_slug="jane-adams"),
        "S1": SpeakerMapping("S1", "Jane Adams", politician_slug="jane-adams"),
    }
    warns = enrollment_warnings(mappings, roster=None)
    assert any(w["kind"] == "duplicate_name" for w in warns)


def test_warns_on_named_but_unlinked_roster_match():
    mappings = {"S0": SpeakerMapping("S0", "Hopi Stosberg")}  # matches roster, no link
    warns = enrollment_warnings(mappings, roster=_roster())
    assert any(w["kind"] == "unlinked_roster_match" and w["label"] == "S0" for w in warns)


def test_no_duplicate_warning_for_multiple_unidentified():
    mappings = {
        "S0": SpeakerMapping("S0", "Unidentified Speaker", local_slug="unidentified-m-s0", speaker_status="unidentified"),
        "S1": SpeakerMapping("S1", "Unidentified Speaker", local_slug="unidentified-m-s1", speaker_status="unidentified"),
    }
    assert not any(w["kind"] == "duplicate_name" for w in enrollment_warnings(mappings, roster=None))


def test_clean_mappings_have_no_warnings():
    mappings = {"S0": SpeakerMapping("S0", "Jane Adams", politician_slug="jane-adams")}
    assert enrollment_warnings(mappings, roster=None) == []


def test_snapshot_restore_round_trips_mapping_and_segments():
    segs = [Segment(0, 0, 5, "S0", "hi", speaker_name="Old")]
    mappings = {"S0": SpeakerMapping("S0", "Old", confidence=0.5, id_method="llm")}
    snap = snapshot_mapping(mappings, segs, "S0")

    # mutate (simulate a rename)
    mappings["S0"].speaker_name = "New"; mappings["S0"].id_method = "human_review"
    segs[0].speaker_name = "New"

    restore_mapping(mappings, segs, "S0", snap)
    assert mappings["S0"].speaker_name == "Old"
    assert mappings["S0"].id_method == "llm"
    assert segs[0].speaker_name == "Old"


def test_restore_removes_mapping_absent_at_snapshot_time():
    segs = [Segment(0, 0, 5, "S0", "hi")]
    mappings = {}
    snap = snapshot_mapping(mappings, segs, "S0")   # no mapping yet
    mappings["S0"] = SpeakerMapping("S0", "Added")
    restore_mapping(mappings, segs, "S0", snap)
    assert "S0" not in mappings   # reverted to absent


def test_restore_does_not_revert_relabeled_segments():
    # snapshot/restore is name-based, not label-based — it cannot undo a merge's
    # relabeling. This pins the limitation that justifies refusing merge-undo.
    from src.review import snapshot_mapping, restore_mapping
    segs = [Segment(0, 0, 5, "SRC", "hi", speaker_name="Bob")]
    mappings = {"SRC": SpeakerMapping("SRC", "Bob")}
    snap = snapshot_mapping(mappings, segs, "SRC")
    # simulate a merge relabeling SRC -> TGT
    segs[0].speaker_label = "TGT"
    restore_mapping(mappings, segs, "SRC", snap)
    assert segs[0].speaker_label == "TGT"   # label NOT reverted (by design)
