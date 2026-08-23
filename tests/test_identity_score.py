"""Tests for scoring diarization identity against a human-reviewed reference."""
import pytest

from bench.identity_score import identity_report, map_labels_to_reference

# A reviewed reference: ALICE speaks twice, BOB once.
REFERENCE = [
    (0.0, 30.0, "Alice"),
    (30.0, 60.0, "Bob"),
    (60.0, 90.0, "Alice"),
]


def test_labels_map_to_the_person_owning_most_of_their_speech():
    hypothesis = [(0.0, 29.0, "SPEAKER_00"), (31.0, 59.0, "SPEAKER_01")]
    mapping = map_labels_to_reference(hypothesis, REFERENCE)
    assert mapping["SPEAKER_00"].person == "Alice"
    assert mapping["SPEAKER_01"].person == "Bob"
    assert mapping["SPEAKER_00"].purity == pytest.approx(1.0)


def test_a_person_split_across_two_labels_is_fragmentation():
    """Alice's two appearances got different labels — exactly what chunked
    diarization does today, and what identify._dedupe_identities then demotes
    to unnamed+needs_review."""
    hypothesis = [(0.0, 30.0, "SPEAKER_00"), (30.0, 60.0, "SPEAKER_01"),
                  (60.0, 90.0, "SPEAKER_02")]
    report = identity_report(hypothesis, REFERENCE)
    assert report.speakers == 3
    assert report.reference_people == 2
    assert [f.person for f in report.fragmentation] == ["Alice"]
    assert sorted(report.fragmentation[0].labels) == ["SPEAKER_00", "SPEAKER_02"]
    assert report.conflation == []


def test_two_people_under_one_label_is_conflation():
    hypothesis = [(0.0, 60.0, "SPEAKER_00"), (60.0, 90.0, "SPEAKER_01")]
    report = identity_report(hypothesis, REFERENCE)
    assert [c.label for c in report.conflation] == ["SPEAKER_00"]
    assert sorted(report.conflation[0].people) == ["Alice", "Bob"]


def test_a_perfect_hypothesis_reports_neither_error():
    hypothesis = [(0.0, 30.0, "SPEAKER_00"), (30.0, 60.0, "SPEAKER_01"),
                  (60.0, 90.0, "SPEAKER_00")]
    report = identity_report(hypothesis, REFERENCE)
    assert report.fragmentation == []
    assert report.conflation == []
    assert report.speakers == 2


def test_sub_floor_slivers_are_not_counted_as_errors():
    """A 0.4s bleed across a boundary is diarization noise, not a second
    person; counting it would make every run look conflated."""
    hypothesis = [(0.0, 30.4, "SPEAKER_00"), (30.4, 60.0, "SPEAKER_01"),
                  (60.0, 90.0, "SPEAKER_00")]
    report = identity_report(hypothesis, REFERENCE, min_seconds=3.0)
    assert report.conflation == []
    assert report.fragmentation == []


# --- Proportional floor: a fixed 3s absolute floor is meaningless at
# meeting scale (a 10s bleed against a 1600s person is noise; a 3s floor
# alone can't tell that from a real merge). `min_fraction` (default 0.02)
# requires the overlap ALSO be at least 2% of the OTHER side's total
# attributed speech. These use realistic (hours-scale) magnitudes because
# the effect is invisible at the tens-of-seconds scale of the fixtures above.

def test_a_boundary_bleed_clearing_the_absolute_floor_is_not_conflation():
    """10s against a 1600s dominant speaker is 0.6% of the label's total --
    exactly the shape real June 10 output has (3-14s bleeds against
    500-1700s dominants) -- so it must not read as a second identity even
    though 10s alone clears the absolute 3.0s floor."""
    reference = [(0.0, 1600.0, "Alice"), (1600.0, 1610.0, "Bob")]
    hypothesis = [(0.0, 1610.0, "SPEAKER_00")]
    report = identity_report(hypothesis, reference)
    assert report.conflation == []
    assert report.conflation_summary == "no conflation"


def test_a_genuine_two_person_merge_is_still_conflation():
    """300s and 250s under one label is a real merge, not a bleed -- both
    clear the 2% proportional floor by a wide margin."""
    reference = [(0.0, 300.0, "Alice"), (300.0, 550.0, "Bob")]
    hypothesis = [(0.0, 550.0, "SPEAKER_00")]
    report = identity_report(hypothesis, reference)
    assert [c.label for c in report.conflation] == ["SPEAKER_00"]
    assert sorted(report.conflation[0].people) == ["Alice", "Bob"]
    assert "45.5%" in report.conflation_summary


def test_a_boundary_bleed_clearing_the_absolute_floor_is_not_fragmentation():
    """Symmetric to the conflation case: a label holding only 10s of a
    1600s person's total speech (0.6%) is a boundary bleed, not a second
    label for that person."""
    reference = [(0.0, 1600.0, "Alice")]
    hypothesis = [(0.0, 1590.0, "SPEAKER_00"), (1590.0, 1600.0, "SPEAKER_01")]
    report = identity_report(hypothesis, reference)
    assert report.fragmentation == []
    assert report.fragmentation_summary == "no fragmentation"


def test_a_genuine_fragmentation_across_two_labels_is_still_reported():
    """300s and 250s of one person's speech split across two labels is a
    real fragmentation, not a bleed."""
    reference = [(0.0, 550.0, "Alice")]
    hypothesis = [(0.0, 300.0, "SPEAKER_00"), (300.0, 550.0, "SPEAKER_01")]
    report = identity_report(hypothesis, reference)
    assert [f.person for f in report.fragmentation] == ["Alice"]
    assert sorted(report.fragmentation[0].labels) == ["SPEAKER_00", "SPEAKER_01"]
    assert "45.5%" in report.fragmentation_summary


def test_a_label_overlapping_no_reference_turn_does_not_crash():
    """A reference can have gaps — e.g. placeholder names like 'Unidentified
    Speaker' get excluded because they lump distinct unknown voices under one
    name, which would otherwise both punish keeping unknowns apart and reward
    merging them. A hypothesis label that speaks only inside such a gap overlaps
    nothing, and must be reported as unmapped rather than crashing the scorer."""
    hypothesis = [(0.0, 30.0, "SPEAKER_00"), (200.0, 260.0, "SPEAKER_99")]

    mapping = map_labels_to_reference(hypothesis, REFERENCE)
    assert "SPEAKER_00" in mapping
    assert "SPEAKER_99" not in mapping

    report = identity_report(hypothesis, REFERENCE)
    assert report.speakers == 2
    assert report.unmapped_labels == ["SPEAKER_99"]
    assert report.fragmentation == []
    assert report.conflation == []


def test_unmapped_labels_are_empty_when_the_reference_covers_everything():
    hypothesis = [(0.0, 30.0, "SPEAKER_00"), (30.0, 60.0, "SPEAKER_01")]
    assert identity_report(hypothesis, REFERENCE).unmapped_labels == []
