# tests/test_review_ux.py
from __future__ import annotations
from src.models import SpeakerMapping
from src.models import Segment
from src.review import identity_label
from src.review import ambiguous_speaker_surnames, duplicate_named_speakers, enrollment_warnings
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


def test_duplicate_named_speakers_groups_labels_by_shared_name():
    mappings = {
        "SPEAKER_19": SpeakerMapping("SPEAKER_19", "City Common Council - District 6 Zulich"),
        "SPEAKER_07": SpeakerMapping("SPEAKER_07", "City Common Council - District 6 Zulich"),
        "SPEAKER_02": SpeakerMapping("SPEAKER_02", "Mayor Johnson"),
    }
    dups = duplicate_named_speakers(mappings)
    assert dups == {"city common council - district 6 zulich": ["SPEAKER_07", "SPEAKER_19"]}


def test_duplicate_named_speakers_is_case_and_whitespace_insensitive():
    mappings = {
        "S0": SpeakerMapping("S0", "Jane Adams "),
        "S1": SpeakerMapping("S1", "jane adams"),
    }
    assert list(duplicate_named_speakers(mappings).values()) == [["S0", "S1"]]


def test_duplicate_named_speakers_excludes_placeholder_statuses():
    mappings = {
        "S0": SpeakerMapping("S0", "Unidentified Speaker", local_slug="unidentified-m-s0",
                             speaker_status="unidentified"),
        "S1": SpeakerMapping("S1", "Unidentified Speaker", local_slug="unidentified-m-s1",
                             speaker_status="unidentified"),
        "S2": SpeakerMapping("S2", "Non-speaker", speaker_status="non_speaker"),
        "S3": SpeakerMapping("S3", "Non-speaker", speaker_status="non_speaker"),
    }
    assert duplicate_named_speakers(mappings) == {}


def test_duplicate_named_speakers_empty_when_clean():
    mappings = {
        "S0": SpeakerMapping("S0", "Jane Adams"),
        "S1": SpeakerMapping("S1", None),
    }
    assert duplicate_named_speakers(mappings) == {}


def test_enrollment_warning_duplicate_carries_labels_list():
    mappings = {
        "S1": SpeakerMapping("S1", "Jane Adams"),
        "S0": SpeakerMapping("S0", "Jane Adams"),
    }
    w = next(w for w in enrollment_warnings(mappings, roster=None)
             if w["kind"] == "duplicate_name")
    assert w["labels"] == ["S0", "S1"]
    assert w["label"] == "S0,S1"  # joined form stays for existing callers


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


# --- surname collisions ------------------------------------------------------
# memo_reconcile.match_speaker resolves a memo last name by suffix-matching
# display_name, so two speakers can be mutually ambiguous WITHOUT sharing a full
# name — the collision duplicate_named_speakers is blind to.

def test_surname_pass_groups_different_names_sharing_a_last_name():
    mappings = {
        "S0": SpeakerMapping("S0", "Isak Nti Asare"),
        "S1": SpeakerMapping("S1", "Council President Asare"),
    }
    assert ambiguous_speaker_surnames(mappings) == {"asare": ["S0", "S1"]}


def test_surname_pass_ignores_exact_duplicates():
    """duplicate_named_speakers owns identical names; reporting them here too
    would double-warn about one problem."""
    mappings = {
        "S0": SpeakerMapping("S0", "Jane Adams"),
        "S1": SpeakerMapping("S1", "Jane Adams"),
    }
    assert ambiguous_speaker_surnames(mappings) == {}


def test_surname_pass_reports_all_labels_when_exact_and_variant_collide():
    """Every label in the group is ambiguous to the matcher, including the pair
    the exact-name detector already flags."""
    mappings = {
        "S0": SpeakerMapping("S0", "Isak Nti Asare"),
        "S1": SpeakerMapping("S1", "Isak Nti Asare"),
        "S2": SpeakerMapping("S2", "Council President Asare"),
    }
    assert ambiguous_speaker_surnames(mappings) == {"asare": ["S0", "S1", "S2"]}


def test_surname_pass_matches_a_bare_last_name_against_a_full_name():
    """match_speaker's equality branch: 'Asare' == target, 'Isak Nti Asare'
    ends with ' asare' — both hit, so both are ambiguous."""
    mappings = {
        "S0": SpeakerMapping("S0", "Asare"),
        "S1": SpeakerMapping("S1", "Isak Nti Asare"),
    }
    assert ambiguous_speaker_surnames(mappings) == {"asare": ["S0", "S1"]}


def test_surname_pass_is_case_and_whitespace_insensitive():
    mappings = {
        "S0": SpeakerMapping("S0", "  Isak Nti ASARE "),
        "S1": SpeakerMapping("S1", "Council President Asare"),
    }
    assert ambiguous_speaker_surnames(mappings) == {"asare": ["S0", "S1"]}


def test_surname_pass_excludes_placeholders():
    """Unidentified/non-speaker names are placeholders, not identities — same
    exclusion duplicate_named_speakers makes."""
    mappings = {
        "S0": SpeakerMapping("S0", "Unidentified Speaker", speaker_status="unidentified"),
        "S1": SpeakerMapping("S1", "Another Speaker", speaker_status="unidentified"),
        "S2": SpeakerMapping("S2", "Some Speaker", speaker_status="non_speaker"),
    }
    assert ambiguous_speaker_surnames(mappings) == {}


def test_surname_pass_clean_when_last_names_differ():
    mappings = {
        "S0": SpeakerMapping("S0", "Isak Nti Asare"),
        "S1": SpeakerMapping("S1", "Hopi Stosberg"),
    }
    assert ambiguous_speaker_surnames(mappings) == {}


def test_surname_pass_agrees_with_the_matcher_it_protects():
    """The detector's whole claim is 'match_speaker would call these ambiguous'.
    Assert that against the real matcher rather than trusting the restatement."""
    from src.memo_reconcile import SpeakerRow, match_speaker

    grouped = {
        "S0": SpeakerMapping("S0", "Isak Nti Asare"),
        "S1": SpeakerMapping("S1", "Council President Asare"),
    }
    surname, labels = next(iter(ambiguous_speaker_surnames(grouped).items()))
    rows = [SpeakerRow(l, grouped[l].speaker_name) for l in labels]
    speaker_id, note = match_speaker(surname, rows)
    assert speaker_id is None and "ambiguous" in note

    # ...and a pair it does NOT group resolves cleanly.
    distinct = {
        "S0": SpeakerMapping("S0", "Isak Nti Asare"),
        "S1": SpeakerMapping("S1", "Hopi Stosberg"),
    }
    assert ambiguous_speaker_surnames(distinct) == {}
    rows = [SpeakerRow(l, m.speaker_name) for l, m in distinct.items()]
    assert match_speaker("asare", rows) == ("S0", None)


def test_enrollment_warnings_emits_ambiguous_surname_with_labels():
    mappings = {
        "S0": SpeakerMapping("S0", "Isak Nti Asare"),
        "S1": SpeakerMapping("S1", "Council President Asare"),
    }
    warns = enrollment_warnings(mappings, roster=None)
    hit = [w for w in warns if w["kind"] == "ambiguous_surname"]
    assert len(hit) == 1
    assert hit[0]["labels"] == ["S0", "S1"]
    assert hit[0]["label"] == "S0,S1"
    assert "Asare" in hit[0]["detail"]


def test_enrollment_warnings_does_not_double_warn_on_exact_duplicates():
    mappings = {
        "S0": SpeakerMapping("S0", "Jane Adams"),
        "S1": SpeakerMapping("S1", "Jane Adams"),
    }
    kinds = [w["kind"] for w in enrollment_warnings(mappings, roster=None)]
    assert "duplicate_name" in kinds
    assert "ambiguous_surname" not in kinds


def test_surname_pass_ignores_role_annotations():
    """A memo member name is always a real last name, so a trailing '(Moderator)'
    can never be the target that makes two speakers ambiguous — grouping on it
    is pure noise, and noisy warnings are the reason this hole stayed open."""
    mappings = {
        "S0": SpeakerMapping("S0", "Pearl Vinard (Moderator)"),
        "S1": SpeakerMapping("S1", "Steve Hinnefeld (Moderator)"),
    }
    assert ambiguous_speaker_surnames(mappings) == {}


def test_surname_pass_keeps_hyphens_apostrophes_and_initials():
    """Real last names carry punctuation — the noise filter must not eat them."""
    for surname, a, b in [
        ("piedmont-smith", "Councilmember Piedmont-Smith", "Kate Piedmont-Smith"),
        ("o'brien", "Dan O'Brien", "Chair O'Brien"),
    ]:
        mappings = {"S0": SpeakerMapping("S0", a), "S1": SpeakerMapping("S1", b)}
        assert ambiguous_speaker_surnames(mappings) == {surname: ["S0", "S1"]}
