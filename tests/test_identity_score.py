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
