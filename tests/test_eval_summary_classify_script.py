"""Tests for scripts/eval_summary_classify.py's replay_one() gate: which
meetings the staleness check admits, and which segment population their
scores are computed over.

The distinction under test is the one the corpus actually exhibits:
backfill_segment_merge.py reindexes segments by time, which leaves valid
segment ids carrying empty text. A gold section boundary landing on such an
id is NOT stale — the id indexes into the current transcript — so the meeting
must still be scored, over its text-bearing segments.

scripts/ has no package __init__, so load the module by file path (matches
tests/test_generate_summary_ab_script.py).
"""
from __future__ import annotations

import importlib.util
import json
import pathlib
from unittest.mock import MagicMock

_spec = importlib.util.spec_from_file_location(
    "eval_summary_classify",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "eval_summary_classify.py")
eval_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(eval_mod)

from src.models import Meeting, Segment  # noqa: E402


def _fake_client(sections: list[dict]):
    """Client whose single messages.create returns `sections` as the classify
    stage's JSON payload — no network."""
    client = MagicMock()
    msg = MagicMock()
    msg.content = [MagicMock(text=json.dumps({"sections": sections}))]
    client.messages.create.return_value = msg
    return client


def _meeting_with_empty_text_segment() -> Meeting:
    """Segments 0-4 where segment 2 carries empty text — a valid id produced by
    backfill_segment_merge.py's reindex-by-time, not a stale one."""
    segments = [
        Segment(segment_id=i, start_time=float(i), end_time=float(i + 1),
                speaker_label="SPEAKER_00",
                text="" if i == 2 else f"Utterance {i}.")
        for i in range(5)
    ]
    return Meeting(meeting_id="m-empty-boundary", city="Testville",
                   date="2026-01-01", event_kind="council", segments=segments)


def test_gold_boundary_on_empty_text_segment_is_not_skipped_as_stale():
    meeting = _meeting_with_empty_text_segment()
    # Boundary lands exactly on segment 2, the empty-text one.
    gold = [
        {"section_type": "opening", "start_segment": 0, "end_segment": 2},
        {"section_type": "discussion", "start_segment": 2, "end_segment": 4},
    ]
    client = _fake_client([{"type": "opening", "start_segment": 0, "end_segment": 2},
                           {"type": "discussion", "start_segment": 2, "end_segment": 4}])

    row, skip_reason = eval_mod.replay_one(client, None, meeting, gold)

    assert skip_reason is None, f"meeting was wrongly skipped: {skip_reason}"
    assert row is not None


def test_scoring_still_restricted_to_text_bearing_segments():
    # Admitting the meeting must not widen the scored population: segment 2 has
    # no text, was never shown to the classifier, and must not be scored.
    meeting = _meeting_with_empty_text_segment()
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 4}]
    client = _fake_client([{"type": "opening", "start_segment": 0, "end_segment": 4}])

    row, skip_reason = eval_mod.replay_one(client, None, meeting, gold)

    assert skip_reason is None
    assert row["n_segments"] == 4  # ids 0,1,3,4 — not 5
    assert row["agreement"] == 1.0


def test_genuinely_out_of_range_gold_boundary_is_still_skipped():
    # The hazard the gate exists for: a boundary past the end of the current
    # transcript entirely (un-republished after renumbering).
    meeting = _meeting_with_empty_text_segment()
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 87}]
    client = _fake_client([])

    row, skip_reason = eval_mod.replay_one(client, None, meeting, gold)

    assert row is None
    assert "stale" in skip_reason


# --- the same gate in the synthesis A/B script ---------------------------
# generate_summary_ab.py reuses gold_sections_valid() for its own stale-gold
# guard, so it has to feed it the same id set; otherwise the two harnesses
# silently disagree about which third of the corpus is scoreable.

_ab_spec = importlib.util.spec_from_file_location(
    "generate_summary_ab",
    pathlib.Path(__file__).resolve().parent.parent / "scripts" / "generate_summary_ab.py")
ab_mod = importlib.util.module_from_spec(_ab_spec)
_ab_spec.loader.exec_module(ab_mod)


def test_ab_gate_admits_gold_boundary_on_empty_text_segment():
    meeting = _meeting_with_empty_text_segment()
    gold = [
        {"section_type": "opening", "start_segment": 0, "end_segment": 2},
        {"section_type": "discussion", "start_segment": 2, "end_segment": 4},
    ]
    ok, reason = ab_mod.gold_gate(meeting, gold)
    assert ok is True, f"A/B harness wrongly skipped: {reason}"


def test_ab_gate_still_rejects_out_of_range_gold_boundary():
    meeting = _meeting_with_empty_text_segment()
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 87}]
    ok, reason = ab_mod.gold_gate(meeting, gold)
    assert ok is False
    assert "stale" in reason


# --- split reporting -----------------------------------------------------
# label_agreement is a constant on interview-kind meetings (single-label
# "topic" vocabulary), so the report has to separate them from the meetings
# where it actually varies. replay_one tags each row with which it is.

def _meeting_of_kind(kind: str) -> Meeting:
    segments = [
        Segment(segment_id=i, start_time=float(i), end_time=float(i + 1),
                speaker_label="SPEAKER_00", text=f"Utterance {i}.")
        for i in range(6)
    ]
    return Meeting(meeting_id=f"m-{kind}", city="Testville", date="2026-01-01",
                   event_kind=kind, segments=segments)


def test_replay_one_tags_interview_kind_rows():
    gold = [{"section_type": "topic", "start_segment": 0, "end_segment": 5}]
    client = _fake_client([{"type": "topic", "start_segment": 0, "end_segment": 5}])

    podcast_row, _ = eval_mod.replay_one(client, None, _meeting_of_kind("podcast"), gold)
    council_row, _ = eval_mod.replay_one(client, None, _meeting_of_kind("council"), gold)

    assert podcast_row["is_interview"] is True
    assert council_row["is_interview"] is False


def test_report_groups_partition_rows_into_all_multilabel_and_interview():
    rows = [{"is_interview": True}, {"is_interview": False}, {"is_interview": False}]
    got = {name: [r for r in rows if keep(r)] for name, keep in eval_mod.REPORT_GROUPS}
    assert len(got["all"]) == 3
    assert len(got["multi-label"]) == 2
    assert len(got["interview"]) == 1
    # "all" must stay a superset — it's the number a reader compares across runs.
    assert len(got["all"]) == len(got["multi-label"]) + len(got["interview"])


def test_boundary_metric_separates_models_that_label_agreement_cannot():
    """End-to-end through replay_one on a single-label (interview) meeting:
    two candidates that score identically on label_agreement must be
    distinguished by boundary_f1."""
    gold = [{"section_type": "topic", "start_segment": 0, "end_segment": 2},
            {"section_type": "topic", "start_segment": 3, "end_segment": 5}]
    meeting = _meeting_of_kind("podcast")

    faithful = _fake_client([{"type": "topic", "start_segment": 0, "end_segment": 2},
                             {"type": "topic", "start_segment": 3, "end_segment": 5}])
    collapsed = _fake_client([{"type": "topic", "start_segment": 0, "end_segment": 5}])

    good_row, _ = eval_mod.replay_one(faithful, None, meeting, gold)
    bad_row, _ = eval_mod.replay_one(collapsed, None, meeting, gold)

    assert good_row["agreement"] == bad_row["agreement"] == 1.0  # indistinguishable
    assert good_row["boundary_f1"] == 1.0                        # now separated
    assert bad_row["boundary_f1"] == 0.0


def test_both_harnesses_agree_on_admitting_the_same_meeting():
    # The regression that motivated this: the two scripts must not disagree.
    meeting = _meeting_with_empty_text_segment()
    gold = [{"section_type": "opening", "start_segment": 0, "end_segment": 2},
            {"section_type": "discussion", "start_segment": 2, "end_segment": 4}]
    client = _fake_client([{"type": "opening", "start_segment": 0, "end_segment": 4}])

    row, skip_reason = eval_mod.replay_one(client, None, meeting, gold)
    ok, _ = ab_mod.gold_gate(meeting, gold)

    assert (skip_reason is None) is ok is True
