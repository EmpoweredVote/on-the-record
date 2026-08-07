"""Tests for the summary section-classification replay eval scoring
(src.summary_classify_eval)."""
from __future__ import annotations

from src.summary_classify_eval import (
    aggregate,
    boundary_counts,
    gold_sections_valid,
    label_segments,
    score_meeting,
    section_boundaries,
)


# --- label_segments ----------------------------------------------------

def test_label_segments_basic_mapping():
    sections = [
        {"section_type": "opening", "start_segment": 0, "end_segment": 1},
        {"section_type": "discussion", "start_segment": 2, "end_segment": 4},
    ]
    labels = label_segments(sections, valid_ids=set(range(5)), type_key="section_type")
    assert labels == {0: "opening", 1: "opening", 2: "discussion",
                       3: "discussion", 4: "discussion"}


def test_label_segments_uses_type_key_not_hardcoded():
    # Candidate sections (raw classify_sections() output) use "type", not
    # "section_type" — the caller must pass the right key.
    sections = [{"type": "vote", "start_segment": 0, "end_segment": 0}]
    labels = label_segments(sections, valid_ids={0}, type_key="type")
    assert labels == {0: "vote"}
    # Wrong key -> falls back to "unknown" rather than crashing.
    labels_wrong_key = label_segments(sections, valid_ids={0}, type_key="section_type")
    assert labels_wrong_key == {0: "unknown"}


def test_label_segments_overlap_last_section_wins():
    # Real summary.json data: adjacent sections sharing one boundary segment.
    sections = [
        {"section_type": "discussion", "start_segment": 0, "end_segment": 5},
        {"section_type": "topic_b", "start_segment": 5, "end_segment": 8},
    ]
    labels = label_segments(sections, valid_ids=set(range(9)), type_key="section_type")
    assert labels[5] == "topic_b"  # later section in list order wins
    assert labels[4] == "discussion"
    assert labels[8] == "topic_b"


def test_label_segments_filters_to_valid_ids():
    sections = [{"section_type": "opening", "start_segment": 0, "end_segment": 10}]
    labels = label_segments(sections, valid_ids={0, 2, 4}, type_key="section_type")
    assert set(labels) == {0, 2, 4}


def test_label_segments_none_valid_ids_means_no_filter():
    sections = [{"section_type": "opening", "start_segment": 0, "end_segment": 2}]
    labels = label_segments(sections, valid_ids=None, type_key="section_type")
    assert set(labels) == {0, 1, 2}


def test_label_segments_skips_malformed_sections():
    sections = [
        {"section_type": "opening"},  # missing start/end
        {"section_type": "bad_range", "start_segment": 5, "end_segment": 2},  # inverted
        {"section_type": "ok", "start_segment": 0, "end_segment": 0},
    ]
    labels = label_segments(sections, valid_ids={0}, type_key="section_type")
    assert labels == {0: "ok"}


def test_label_segments_empty_sections():
    assert label_segments([], valid_ids={0, 1}, type_key="section_type") == {}


# --- gold_sections_valid -------------------------------------------------

def test_gold_sections_valid_true_when_in_range():
    gold = [{"start_segment": 0, "end_segment": 3}, {"start_segment": 4, "end_segment": 9}]
    ok, reason = gold_sections_valid(gold, all_segment_ids=set(range(10)))
    assert ok is True
    assert reason == ""


def test_gold_sections_valid_false_on_stale_segment_ids():
    # Simulates the real corpus hazard: summary.json boundaries reference
    # segment ids from before a merge/renumbering backfill.
    gold = [{"start_segment": 0, "end_segment": 292}]
    ok, reason = gold_sections_valid(gold, all_segment_ids=set(range(0, 195)))
    assert ok is False
    assert "stale" in reason


def test_gold_sections_valid_false_on_missing_fields():
    gold = [{"start_segment": 0}]
    ok, reason = gold_sections_valid(gold, all_segment_ids={0})
    assert ok is False


def test_gold_sections_valid_false_on_empty():
    ok, reason = gold_sections_valid([], all_segment_ids={0, 1})
    assert ok is False
    assert "no gold sections" in reason


def test_gold_sections_valid_false_on_inverted_range():
    """A membership check alone passes this: 5 and 4 are both real segment ids.
    But end_segment < start_segment makes _full_section_transcript return "",
    so the section is unscoreable. Real case:
    2026-06-24-cd1-republican-primary-debate had a gold section at 5-4."""
    gold = [{"start_segment": 5, "end_segment": 4}]
    ok, reason = gold_sections_valid(gold, all_segment_ids=set(range(10)))
    assert ok is False
    assert "inverted" in reason
    assert "[5,4]" in reason


def test_gold_sections_valid_allows_single_segment_section():
    """start == end is a legitimate one-segment section, not an inversion."""
    ok, reason = gold_sections_valid([{"start_segment": 3, "end_segment": 3}],
                                     all_segment_ids=set(range(10)))
    assert ok is True
    assert reason == ""


def test_inverted_gold_section_would_otherwise_vanish_silently():
    """Why the gate has to catch this: label_segments already skips an inverted
    section, so without the gate the meeting scores against gold that is quietly
    missing a section — no error, just a wrong denominator."""
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 2},
            {"section_type": "procedural", "start_segment": 5, "end_segment": 4}]
    labels = label_segments(gold, valid_ids=set(range(10)), type_key="section_type")
    assert 5 not in labels                      # the section contributes nothing
    assert set(labels) == {0, 1, 2}
    assert gold_sections_valid(gold, all_segment_ids=set(range(10)))[0] is False


# --- score_meeting --------------------------------------------------------

def test_score_meeting_perfect_agreement():
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 2}]
    candidate = [{"type": "opening", "start_segment": 0, "end_segment": 2}]
    row = score_meeting(gold, candidate, valid_ids={0, 1, 2})
    assert row["agreement"] == 1.0
    assert row["n_segments"] == 3
    assert row["section_count_delta"] == 0
    assert row["parse_failures"] == 0


def test_score_meeting_partial_agreement():
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 3}]
    candidate = [
        {"type": "opening", "start_segment": 0, "end_segment": 1},
        {"type": "discussion", "start_segment": 2, "end_segment": 3},
    ]
    row = score_meeting(gold, candidate, valid_ids={0, 1, 2, 3})
    assert row["agree"] == 2
    assert row["n_segments"] == 4
    assert row["agreement"] == 0.5
    assert row["section_count_delta"] == 1  # 2 candidate sections vs 1 gold


def test_score_meeting_zero_agreement():
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 1}]
    candidate = [{"type": "discussion", "start_segment": 0, "end_segment": 1}]
    row = score_meeting(gold, candidate, valid_ids={0, 1})
    assert row["agreement"] == 0.0


def test_score_meeting_empty_candidate_counts_as_full_disagreement():
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 1}]
    row = score_meeting(gold, [], valid_ids={0, 1}, parse_failures=1)
    assert row["agreement"] == 0.0
    assert row["candidate_sections"] == 0
    assert row["section_count_delta"] == -1
    assert row["parse_failures"] == 1


def test_score_meeting_no_gold_labels_agreement_is_none():
    # Degenerate: valid_ids doesn't overlap the gold section's range at all.
    gold = [{"section_type": "opening", "start_segment": 5, "end_segment": 6}]
    row = score_meeting(gold, [], valid_ids={0, 1})
    assert row["n_segments"] == 0
    assert row["agreement"] is None


# --- aggregate --------------------------------------------------------

def test_aggregate_weights_by_segment_count_not_meeting_count():
    # Meeting A: 100% agreement over 2 segments. Meeting B: 0% over 8 segments.
    # A plain mean-of-means would give 50%; weighted-by-segments gives 20%.
    row_a = {"n_segments": 2, "agree": 2, "agreement": 1.0,
             "gold_sections": 1, "candidate_sections": 1,
             "section_count_delta": 0, "parse_failures": 0}
    row_b = {"n_segments": 8, "agree": 0, "agreement": 0.0,
             "gold_sections": 1, "candidate_sections": 1,
             "section_count_delta": 0, "parse_failures": 1}
    agg = aggregate("test-model", [row_a, row_b])
    assert agg["meetings"] == 2
    assert agg["segments"] == 10
    assert agg["label_agreement"] == 0.2
    assert agg["avg_section_count_delta"] == 0.0
    assert agg["parse_failures"] == 1


def test_aggregate_empty_rows():
    agg = aggregate("test-model", [])
    assert agg["meetings"] == 0
    assert agg["segments"] == 0
    assert agg["label_agreement"] is None
    assert agg["avg_section_count_delta"] is None
    assert agg["parse_failures"] == 0


# --- section_boundaries ---------------------------------------------------

def test_section_boundaries_are_section_starts_excluding_document_start():
    # 3 sections -> 2 boundaries. Position 0 is not a decision the model made:
    # every segmentation starts at the beginning, so counting it would hand
    # every model a free correct boundary.
    sections = [
        {"start_segment": 0, "end_segment": 4},
        {"start_segment": 5, "end_segment": 9},
        {"start_segment": 10, "end_segment": 14},
    ]
    assert section_boundaries(sections, sorted(range(15))) == [5, 10]


def test_section_boundaries_are_positions_not_raw_segment_ids():
    # Scoring runs over text-bearing ids only, so a boundary's *position* in
    # that population is what matters — id 10 is the 3rd scored segment here.
    ordered = [0, 5, 10, 20]
    sections = [{"start_segment": 0, "end_segment": 5},
                {"start_segment": 10, "end_segment": 20}]
    assert section_boundaries(sections, ordered) == [2]


def test_section_boundaries_snaps_boundary_on_empty_text_segment_forward():
    # A gold boundary can land on an empty-text id (valid, but never scored).
    # It snaps to the next scored segment rather than being dropped.
    ordered = [0, 1, 3, 4]          # id 2 carries empty text
    sections = [{"start_segment": 0, "end_segment": 1},
                {"start_segment": 2, "end_segment": 4}]
    assert section_boundaries(sections, ordered) == [2]  # position of id 3


def test_section_boundaries_ignores_out_of_range_and_malformed():
    ordered = [0, 1, 2]
    sections = [{"start_segment": 0, "end_segment": 2},
                {"start_segment": 99, "end_segment": 120},   # past the end
                {"start_segment": None, "end_segment": 1},   # malformed
                {"end_segment": 1}]                          # missing start
    assert section_boundaries(sections, ordered) == []


def test_section_boundaries_deduplicates():
    sections = [{"start_segment": 0, "end_segment": 3},
                {"start_segment": 2, "end_segment": 3},
                {"start_segment": 2, "end_segment": 5}]
    assert section_boundaries(sections, sorted(range(6))) == [2]


# --- boundary_counts ------------------------------------------------------

def test_boundary_counts_exact_match():
    assert boundary_counts([5, 10], [5, 10], tolerance=0) == (2, 2, 2)


def test_boundary_counts_off_by_one_within_tolerance():
    # A boundary one scored segment early is not a real segmentation error.
    assert boundary_counts([5, 10], [4, 11], tolerance=1) == (2, 2, 2)


def test_boundary_counts_outside_tolerance_does_not_match():
    assert boundary_counts([5], [8], tolerance=1) == (0, 1, 1)


def test_boundary_counts_matching_is_one_to_one():
    # Three candidate boundaries crowded around one gold boundary must not
    # each claim it — otherwise spamming boundaries would inflate recall.
    matched, n_gold, n_cand = boundary_counts([5], [4, 5, 6], tolerance=1)
    assert (matched, n_gold, n_cand) == (1, 1, 3)


def test_boundary_counts_both_empty_is_a_match_not_a_failure():
    # A single-section meeting genuinely has no interior boundaries.
    assert boundary_counts([], [], tolerance=1) == (0, 0, 0)


# --- boundary metric on score_meeting -------------------------------------

def test_collapsing_topics_scores_perfect_agreement_but_zero_boundary_recall():
    """The motivating case, measured on real corpus data 2026-08-07.

    Interview-kind meetings are classified with a single-label vocabulary
    ("topic"), so per-segment label agreement is a constant — deepseek
    collapsed 2025-10-06-interview's 6 gold topics into 1 section and scored
    agreement=1.00. The boundary metric is what has to catch that.
    """
    gold = [
        {"section_type": "topic", "start_segment": 0, "end_segment": 9},
        {"section_type": "topic", "start_segment": 10, "end_segment": 19},
        {"section_type": "topic", "start_segment": 20, "end_segment": 29},
    ]
    collapsed = [{"type": "topic", "start_segment": 0, "end_segment": 29}]
    row = score_meeting(gold, collapsed, valid_ids=set(range(30)))

    assert row["agreement"] == 1.0            # the blind spot
    assert row["boundary_recall"] == 0.0      # what now catches it
    assert row["boundary_f1"] == 0.0
    assert row["n_gold_boundaries"] == 2


def test_score_meeting_perfect_boundaries():
    gold = [{"section_type": "topic", "start_segment": 0, "end_segment": 4},
            {"section_type": "topic", "start_segment": 5, "end_segment": 9}]
    cand = [{"type": "topic", "start_segment": 0, "end_segment": 4},
            {"type": "topic", "start_segment": 5, "end_segment": 9}]
    row = score_meeting(gold, cand, valid_ids=set(range(10)))
    assert row["boundary_f1"] == 1.0
    assert row["boundary_precision"] == 1.0
    assert row["boundary_recall"] == 1.0


def test_score_meeting_over_segmentation_hurts_precision_not_recall():
    gold = [{"section_type": "topic", "start_segment": 0, "end_segment": 9},
            {"section_type": "topic", "start_segment": 10, "end_segment": 19}]
    cand = [{"type": "topic", "start_segment": 0, "end_segment": 4},
            {"type": "topic", "start_segment": 5, "end_segment": 9},
            {"type": "topic", "start_segment": 10, "end_segment": 14},
            {"type": "topic", "start_segment": 15, "end_segment": 19}]
    row = score_meeting(gold, cand, valid_ids=set(range(20)))
    assert row["boundary_recall"] == 1.0        # found the real boundary
    assert row["boundary_precision"] == 1 / 3   # plus two invented ones
    assert row["n_candidate_boundaries"] == 3


def test_score_meeting_single_section_gold_and_candidate_is_perfect():
    gold = [{"section_type": "topic", "start_segment": 0, "end_segment": 5}]
    cand = [{"type": "topic", "start_segment": 0, "end_segment": 5}]
    row = score_meeting(gold, cand, valid_ids=set(range(6)))
    assert row["boundary_f1"] == 1.0


# --- aggregate: boundary micro-averaging ----------------------------------

def test_aggregate_micro_averages_boundaries_not_mean_of_f1():
    # Meeting A: 1 of 1 boundary found. Meeting B: 0 of 9 found.
    # Mean-of-per-meeting-F1 would say 0.50; micro-average says 1/10 recall.
    row_a = {"n_segments": 10, "agree": 10, "agreement": 1.0,
             "gold_sections": 2, "candidate_sections": 2, "section_count_delta": 0,
             "parse_failures": 0, "boundary_matched": 1,
             "n_gold_boundaries": 1, "n_candidate_boundaries": 1}
    row_b = {"n_segments": 10, "agree": 10, "agreement": 1.0,
             "gold_sections": 10, "candidate_sections": 1, "section_count_delta": -9,
             "parse_failures": 0, "boundary_matched": 0,
             "n_gold_boundaries": 9, "n_candidate_boundaries": 0}
    agg = aggregate("test-model", [row_a, row_b])
    assert agg["boundary_recall"] == 0.1
    assert agg["boundary_precision"] == 1.0
    assert abs(agg["boundary_f1"] - 2 * 1.0 * 0.1 / 1.1) < 1e-9


def test_aggregate_tolerates_rows_without_boundary_keys():
    # Backward compatibility: score rows predating the boundary metric.
    row = {"n_segments": 2, "agree": 2, "agreement": 1.0, "gold_sections": 1,
           "candidate_sections": 1, "section_count_delta": 0, "parse_failures": 0}
    agg = aggregate("test-model", [row])
    assert agg["boundary_f1"] is None


def test_aggregate_boundary_f1_is_none_when_nothing_to_score():
    agg = aggregate("test-model", [])
    assert agg["boundary_f1"] is None
    assert agg["boundary_precision"] is None
    assert agg["boundary_recall"] is None
