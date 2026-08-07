"""Tests for src.eval_meeting_sampling (deterministic meeting selection shared
by the summary-model eval scripts)."""
from __future__ import annotations

import json

from src.eval_meeting_sampling import discover_meetings, select_diverse_sample


# --- select_diverse_sample (pure) --------------------------------------

def test_select_diverse_sample_round_robins_across_kinds():
    meetings = [
        ("a-council-1", "council"), ("b-council-2", "council"), ("c-council-3", "council"),
        ("d-forum-1", "forum"),
    ]
    sample = select_diverse_sample(meetings, limit=2)
    # One from each kind (kinds visited in sorted order: council, forum),
    # not the first two alphabetically-within-council.
    assert sample == ["a-council-1", "d-forum-1"]


def test_select_diverse_sample_deterministic():
    meetings = [("m1", "council"), ("m2", "forum"), ("m3", "podcast")]
    assert select_diverse_sample(meetings, 3) == select_diverse_sample(meetings, 3)


def test_select_diverse_sample_limit_larger_than_pool():
    meetings = [("m1", "council"), ("m2", "forum")]
    sample = select_diverse_sample(meetings, limit=10)
    assert sorted(sample) == ["m1", "m2"]


def test_select_diverse_sample_zero_limit():
    assert select_diverse_sample([("m1", "council")], limit=0) == []


def test_select_diverse_sample_empty_pool():
    assert select_diverse_sample([], limit=5) == []


def test_select_diverse_sample_exhausts_smaller_bucket_gracefully():
    # forum has only 1 meeting; council has 3. Limit 4 should take all of both
    # without erroring on the now-empty forum bucket.
    meetings = [
        ("a-council-1", "council"), ("b-council-2", "council"), ("c-council-3", "council"),
        ("d-forum-1", "forum"),
    ]
    sample = select_diverse_sample(meetings, limit=4)
    assert sorted(sample) == ["a-council-1", "b-council-2", "c-council-3", "d-forum-1"]


# --- discover_meetings (thin I/O) --------------------------------------

def _write_meeting(base, meeting_id, event_kind, with_summary=True):
    mdir = base / meeting_id
    mdir.mkdir()
    (mdir / "transcript_named.json").write_text(json.dumps({"event_kind": event_kind}))
    if with_summary:
        (mdir / "summary.json").write_text(json.dumps({"sections": []}))


def test_discover_meetings_requires_both_files(tmp_path):
    _write_meeting(tmp_path, "has-both", "council")
    _write_meeting(tmp_path, "missing-summary", "council", with_summary=False)
    found = discover_meetings(tmp_path)
    assert found == [("has-both", "council")]


def test_discover_meetings_sorted_by_id(tmp_path):
    _write_meeting(tmp_path, "2026-02-01-b", "council")
    _write_meeting(tmp_path, "2026-01-01-a", "forum")
    found = discover_meetings(tmp_path)
    assert [m[0] for m in found] == ["2026-01-01-a", "2026-02-01-b"]


def test_discover_meetings_unparseable_transcript_is_unknown_kind(tmp_path):
    mdir = tmp_path / "broken"
    mdir.mkdir()
    (mdir / "transcript_named.json").write_text("{not json")
    (mdir / "summary.json").write_text("{}")
    found = discover_meetings(tmp_path)
    assert found == [("broken", "unknown")]
