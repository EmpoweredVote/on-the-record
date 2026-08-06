"""Tests for the summary section-classification replay eval scoring
(src.summary_classify_eval)."""
from __future__ import annotations

from src.summary_classify_eval import (
    aggregate,
    gold_sections_valid,
    label_segments,
    score_meeting,
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
    ok, reason = gold_sections_valid(gold, valid_ids=set(range(10)))
    assert ok is True
    assert reason == ""


def test_gold_sections_valid_false_on_stale_segment_ids():
    # Simulates the real corpus hazard: summary.json boundaries reference
    # segment ids from before a merge/renumbering backfill.
    gold = [{"start_segment": 0, "end_segment": 292}]
    ok, reason = gold_sections_valid(gold, valid_ids=set(range(0, 195)))
    assert ok is False
    assert "stale" in reason


def test_gold_sections_valid_false_on_missing_fields():
    gold = [{"start_segment": 0}]
    ok, reason = gold_sections_valid(gold, valid_ids={0})
    assert ok is False


def test_gold_sections_valid_false_on_empty():
    ok, reason = gold_sections_valid([], valid_ids={0, 1})
    assert ok is False
    assert "no gold sections" in reason


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
