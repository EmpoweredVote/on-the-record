"""Re-deriving summary section boundaries from their (stable) times.

The cases here are drawn from the three meetings whose embedded summary
boundaries outran their segment count after the segment-merge backfill:
2026-04-01-ca-courier-stevehiltoninterview, 2026-04-14-pod-save-america-
nithya-raman and 2026-06-27-interview.
"""
from __future__ import annotations

from src.models import MeetingSummary, SummarySection, Segment
from src.summary_sections import (
    reindex_sections_from_times,
    sections_index_into,
    stale_sections,
)


def _seg(sid, start, end, label="A", text="x"):
    return Segment(segment_id=sid, start_time=start, end_time=end,
                   speaker_label=label, text=text)


def _sec(start_t, end_t, start_seg=0, end_seg=0, title="T"):
    return SummarySection(section_type="discussion", title=title, content="…",
                          start_time=start_t, end_time=end_t,
                          start_segment=start_seg, end_segment=end_seg)


# --- Tier 1: the exact inverse of how summarize.py derived the times ---------

def test_end_segment_comes_from_segment_end_time_not_start_time():
    """summarize.py sets end_time = seg_end_map[end_seg], so the inverse must
    match a segment's *end*. Matching `start_time <= end_time` instead walks the
    boundary onto the following segment."""
    segs = [_seg(0, 0.0, 10.0), _seg(1, 10.0, 20.0), _seg(2, 20.0, 30.0)]
    sec = _sec(0.0, 10.0, start_seg=99, end_seg=99)
    reindex_sections_from_times([sec], segs)
    assert (sec.start_segment, sec.end_segment) == (0, 0)


def test_abutting_boundary_does_not_overlap_the_next_section():
    """pod-save section 0 ends at 44.615 and section 1 starts at 44.615: the
    same instant is one segment's end and the next one's start."""
    segs = [_seg(0, 0.0, 20.0), _seg(6, 30.0, 44.615), _seg(7, 44.615, 60.0)]
    a, b = _sec(0.0, 44.615), _sec(44.615, 60.0)
    reindex_sections_from_times([a, b], segs)
    assert (a.start_segment, a.end_segment) == (0, 6)
    assert (b.start_segment, b.end_segment) == (7, 7)


def test_zero_length_trailing_segment_wins_the_final_boundary():
    """The last segment can be an empty 0-length turn sharing the previous
    segment's end time (pod-save seg 86 ends 2093.965, seg 87 is 2093.965 →
    2093.965). The section ran through the later of the two."""
    segs = [_seg(85, 2080.0, 2088.43), _seg(86, 2088.582, 2093.965),
            _seg(87, 2093.965, 2093.965, label="B", text="")]
    sec = _sec(2080.0, 2093.965)
    reindex_sections_from_times([sec], segs)
    assert sec.end_segment == 87


# --- Tier 2: a merged segment straddling a topic boundary -------------------

def test_boundary_inside_a_merged_segment_maps_to_that_segment():
    """2026-06-27 seg 53 (885.985-989.952) finishes one topic and opens the
    next, so section 3 ends inside it and section 4 starts inside it. Both must
    land on 53 — the single-segment overlap is the honest answer, not a bug."""
    segs = [_seg(52, 800.0, 885.0), _seg(53, 885.985, 989.952),
            _seg(54, 990.5, 1050.0)]
    a, b = _sec(800.0, 962.024), _sec(962.767, 1050.0)
    reindex_sections_from_times([a, b], segs)
    assert (a.start_segment, a.end_segment) == (52, 53)
    assert (b.start_segment, b.end_segment) == (53, 54)


def test_containment_beats_a_nearer_non_containing_segment():
    """A section starting inside a long segment must not skip to the next
    segment merely because that segment's start is closer in absolute time."""
    segs = [_seg(50, 1139.431, 1162.297), _seg(51, 1162.4, 1200.0)]
    sec = _sec(1156.154, 1200.0)
    reindex_sections_from_times([sec], segs)
    assert sec.start_segment == 50  # not 51, though |1162.4-1156.154| is smaller


# --- Tier 3: boundaries in silence / outside the transcript -----------------

def test_boundary_in_a_gap_between_segments():
    segs = [_seg(0, 0.0, 10.0), _seg(1, 30.0, 40.0)]
    sec = _sec(15.0, 25.0)
    reindex_sections_from_times([sec], segs)
    assert (sec.start_segment, sec.end_segment) == (0, 0)


def test_end_is_never_before_start():
    segs = [_seg(0, 0.0, 10.0), _seg(1, 10.0, 20.0)]
    sec = _sec(10.0, 5.0)  # nonsense times; must still yield a usable range
    reindex_sections_from_times([sec], segs)
    assert sec.end_segment >= sec.start_segment


def test_every_boundary_is_a_real_segment_id():
    segs = [_seg(0, 0.0, 10.0), _seg(1, 10.0, 20.0), _seg(2, 20.0, 30.0)]
    secs = [_sec(0.0, 10.0), _sec(10.0, 20.0), _sec(20.0, 30.0),
            _sec(-5.0, 999.0), _sec(999.0, 1000.0)]
    reindex_sections_from_times(secs, segs)
    ids = {s.segment_id for s in segs}
    assert all(s.start_segment in ids and s.end_segment in ids for s in secs)


# --- Bookkeeping -------------------------------------------------------------

def test_returns_number_of_sections_changed():
    segs = [_seg(0, 0.0, 10.0), _seg(1, 10.0, 20.0)]
    already_right = _sec(0.0, 10.0, start_seg=0, end_seg=0)
    stale = _sec(10.0, 20.0, start_seg=88, end_seg=99)
    assert reindex_sections_from_times([already_right, stale], segs) == 1


def test_noop_without_sections_or_segments():
    assert reindex_sections_from_times([], [_seg(0, 0.0, 1.0)]) == 0
    assert reindex_sections_from_times([_sec(0.0, 1.0)], []) == 0


def test_sections_index_into_current_segments():
    segs = [_seg(0, 0.0, 10.0), _seg(1, 10.0, 20.0)]
    assert sections_index_into([_sec(0.0, 10.0, 0, 1)], segs)
    assert not sections_index_into([_sec(0.0, 10.0, 0, 139)], segs)


def test_stale_sections_reports_the_overrun():
    """Detection compares against the transcript's segment ids — publish drops
    empty-text segments, so the DB's max segment_index is not a valid yardstick."""
    segs = [_seg(0, 0.0, 10.0), _seg(1, 10.0, 20.0, text="")]
    summary = MeetingSummary(executive_summary="",
                             sections=[_sec(0.0, 20.0, 0, 421)])
    reason = stale_sections(summary, segs)
    assert reason and "421" in reason
    assert stale_sections(MeetingSummary(executive_summary="",
                                         sections=[_sec(0.0, 20.0, 0, 1)]), segs) is None
