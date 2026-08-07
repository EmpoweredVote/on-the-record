"""Repairing section order / inverted ranges in already-generated summaries."""
from __future__ import annotations

import json

from src.models import (
    Meeting,
    MeetingSummary,
    Segment,
    SpeakerMapping,
    SummarySection,
)
from backfill_summary_section_order import backfill, repair_meeting


def _sec(title, start_seg, end_seg, start_t, end_t, content="…"):
    return SummarySection(section_type="topic", title=title, content=content,
                          start_time=start_t, end_time=end_t,
                          start_segment=start_seg, end_segment=end_seg)


def _meeting(sections, meeting_id="2025-10-06-interview"):
    segs = [Segment(segment_id=i, start_time=i * 10.0, end_time=i * 10.0 + 9.0,
                    speaker_label="S0", speaker_name="Host", text=f"t{i}")
            for i in range(8)]
    return Meeting(meeting_id=meeting_id, city=None, date="2025-10-06",
                   meeting_type="Interview", event_kind="news_clip",
                   segments=segs,
                   speakers={"S0": SpeakerMapping(speaker_label="S0", speaker_name="Host")},
                   summary=MeetingSummary(executive_summary="", sections=sections))


def _out_of_order():
    return _meeting([
        _sec("First", 0, 1, 0.0, 19.0),
        _sec("Last", 6, 7, 60.0, 79.0),
        _sec("Middle", 2, 3, 20.0, 39.0),   # emitted last, covers the middle
    ])


def test_repair_reorders_sections_chronologically():
    m = _out_of_order()
    clamped, moved = repair_meeting(m)
    assert (clamped, moved) == (0, True)
    assert [s.title for s in m.summary.sections] == ["First", "Middle", "Last"]


def test_repair_clamps_inverted_range():
    m = _meeting([_sec("Phantom", 5, 4, 50.0, 49.0)])
    clamped, moved = repair_meeting(m)
    assert clamped == 1
    assert (m.summary.sections[0].start_segment,
            m.summary.sections[0].end_segment) == (5, 5)


def test_repair_leaves_content_and_times_untouched():
    """Only boundaries and order change — a repair must not alter what a summary
    says, and it cannot backfill content an inverted section never got."""
    m = _meeting([_sec("Phantom", 5, 4, 50.0, 49.0, content="")])
    repair_meeting(m)
    sec = m.summary.sections[0]
    assert sec.content == ""
    assert (sec.start_time, sec.end_time) == (50.0, 49.0)


def test_repair_is_a_noop_on_well_formed_sections():
    m = _meeting([_sec("A", 0, 1, 0.0, 19.0), _sec("B", 2, 3, 20.0, 39.0)])
    assert repair_meeting(m) == (0, False)


def test_repair_keeps_legitimate_overlap():
    """Two sections sharing a straddling merged segment must not be 'repaired'."""
    m = _meeting([_sec("A", 0, 3, 0.0, 35.0), _sec("B", 3, 5, 36.0, 59.0)])
    assert repair_meeting(m) == (0, False)
    assert [s.title for s in m.summary.sections] == ["A", "B"]


def test_backfill_rewrites_both_copies(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2025-10-06-interview", completed_stage=7)
    m = _out_of_order()
    (mdir / "transcript_named.json").write_text(json.dumps(m.to_dict()))
    standalone = m.summary.to_dict()
    standalone["sections"][0]["extra_key"] = "preserve me"
    (mdir / "summary.json").write_text(json.dumps(standalone))

    assert backfill(dry_run=False) == 1

    embedded = json.loads((mdir / "transcript_named.json").read_text())["summary"]["sections"]
    assert [s["title"] for s in embedded] == ["First", "Middle", "Last"]
    raw = json.loads((mdir / "summary.json").read_text())["sections"]
    assert [s["title"] for s in raw] == ["First", "Middle", "Last"]
    assert next(s for s in raw if s["title"] == "First")["extra_key"] == "preserve me"


def test_backfill_dry_run_writes_nothing(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2025-10-06-interview", completed_stage=7)
    original = json.dumps(_out_of_order().to_dict())
    (mdir / "transcript_named.json").write_text(original)

    assert backfill(dry_run=True) == 1
    assert (mdir / "transcript_named.json").read_text() == original


def test_backfill_skips_well_formed_meetings(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2025-10-06-interview", completed_stage=7)
    m = _meeting([_sec("A", 0, 1, 0.0, 19.0), _sec("B", 2, 3, 20.0, 39.0)])
    (mdir / "transcript_named.json").write_text(json.dumps(m.to_dict()))
    assert backfill(dry_run=False) == 0


def test_backfill_is_idempotent(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2025-10-06-interview", completed_stage=7)
    (mdir / "transcript_named.json").write_text(json.dumps(_out_of_order().to_dict()))
    assert backfill(dry_run=False) == 1
    assert backfill(dry_run=False) == 0
