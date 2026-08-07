"""Re-deriving summary section boundaries from their (stable) times.

The cases here are drawn from the three meetings whose embedded summary
boundaries outran their segment count after the segment-merge backfill:
2026-04-01-ca-courier-stevehiltoninterview, 2026-04-14-pod-save-america-
nithya-raman and 2026-06-27-interview.
"""
from __future__ import annotations

from src.models import MeetingSummary, SummarySection, Segment
from src.summary_sections import (
    normalize_raw_sections,
    normalize_sections,
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


# --- Structural guards on classifier output ------------------------------------
#
# The classifier's JSON is consumed as-is, and the prompt never requires sections
# to be ordered or well-formed. Two real defects reached production:
# 2025-10-06-interview had a section emitted after the final one but covering the
# middle of the transcript, and 2026-06-24-cd1-republican-primary-debate had a
# section whose end_segment was below its start_segment.

def test_raw_sections_sorted_chronologically():
    raw = [{"type": "topic", "title": "Late",  "start_segment": 110, "end_segment": 127},
           {"type": "topic", "title": "Early", "start_segment": 46,  "end_segment": 80}]
    ordered, clamped, moved = normalize_raw_sections(raw)
    assert [s["title"] for s in ordered] == ["Early", "Late"]
    assert (clamped, moved) == (0, True)


def test_raw_sections_tie_break_on_end_segment():
    raw = [{"title": "Wide",   "start_segment": 5, "end_segment": 11},
           {"title": "Narrow", "start_segment": 5, "end_segment": 6}]
    ordered, _, _ = normalize_raw_sections(raw)
    assert [s["title"] for s in ordered] == ["Narrow", "Wide"]


def test_raw_sections_inverted_range_is_clamped():
    """An inverted range makes _full_section_transcript return '', so the section
    is summarised from nothing and comes out with empty content. Clamping before
    Pass 2 means it gets a real transcript instead."""
    raw = [{"type": "procedural", "title": "Candidate Introduction",
            "start_segment": 5, "end_segment": 4}]
    ordered, clamped, _ = normalize_raw_sections(raw)
    assert (ordered[0]["start_segment"], ordered[0]["end_segment"]) == (5, 5)
    assert clamped == 1


def test_raw_sections_preserves_other_keys_and_does_not_mutate_input():
    raw = [{"type": "topic", "title": "T", "start_segment": 5, "end_segment": 4, "extra": "keep"}]
    ordered, _, _ = normalize_raw_sections(raw)
    assert ordered[0]["extra"] == "keep"
    assert raw[0]["end_segment"] == 4          # caller's dict untouched


def test_raw_sections_already_well_formed_is_unchanged():
    raw = [{"title": "A", "start_segment": 0, "end_segment": 4},
           {"title": "B", "start_segment": 5, "end_segment": 9}]
    ordered, clamped, moved = normalize_raw_sections(raw)
    assert ordered == raw and (clamped, moved) == (0, False)


def test_raw_sections_missing_end_defaults_to_start():
    raw = [{"title": "A", "start_segment": 7}]
    ordered, clamped, _ = normalize_raw_sections(raw)
    assert (ordered[0]["start_segment"], ordered[0]["end_segment"]) == (7, 7)
    assert clamped == 0                        # absent, not inverted


def test_raw_sections_empty_input():
    assert normalize_raw_sections([]) == ([], 0, False)


def test_normalize_sections_repairs_built_sections():
    """The same two repairs applied to already-built SummarySection objects, for
    summaries that were generated before the guards existed."""
    late = _sec(1537.2, 1731.1, 110, 127, title="Late")
    middle = _sec(646.7, 997.1, 46, 80, title="Middle")
    inverted = _sec(126.0, 126.0, 5, 4, title="Inverted")
    ordered, clamped, moved = normalize_sections([late, middle, inverted])
    assert [s.title for s in ordered] == ["Inverted", "Middle", "Late"]
    assert (inverted.start_segment, inverted.end_segment) == (5, 5)
    assert (clamped, moved) == (1, True)


def test_normalize_sections_leaves_good_order_alone():
    a, b = _sec(0.0, 10.0, 0, 4), _sec(10.0, 20.0, 5, 9)
    ordered, clamped, moved = normalize_sections([a, b])
    assert ordered == [a, b] and (clamped, moved) == (0, False)


def test_normalize_sections_permits_legitimate_overlap():
    """A merged segment straddling a topic boundary makes two sections share it.
    Ordering must not treat that as something to repair."""
    a, b = _sec(586.0, 962.0, 26, 53), _sec(962.8, 1229.3, 53, 64)
    ordered, clamped, moved = normalize_sections([a, b])
    assert ordered == [a, b] and (clamped, moved) == (0, False)


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
