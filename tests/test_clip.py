"""Tests for clip-window time parsing and the source-absolute offset transform."""

import copy

import pytest

from src.clip import absolutize_meeting_times, parse_clip_time
from src.models import Meeting, MeetingSummary, Segment, SummarySection, Word


@pytest.mark.parametrize(
    "text,expected",
    [
        ("1380", 1380.0),
        ("1380.5", 1380.5),
        ("23:00", 1380.0),
        ("0:30", 30.0),
        ("1:05:00", 3900.0),
        ("01:05:00", 3900.0),
        ("00:00", 0.0),
    ],
)
def test_parse_clip_time_valid(text, expected):
    assert parse_clip_time(text) == expected


@pytest.mark.parametrize("text", ["", "abc", "1:2:3:4", "12:60", "-5", "1:-1", "  ", "nan", "inf", "-inf"])
def test_parse_clip_time_invalid(text):
    with pytest.raises(ValueError):
        parse_clip_time(text)


def _meeting_with_times(clip_start):
    return Meeting(
        meeting_id="m1", city="X", date="2026-06-28",
        duration_seconds=1500.0,
        clip_start_seconds=clip_start,
        clip_end_seconds=(clip_start + 1500.0) if clip_start else None,
        segments=[
            Segment(segment_id=0, start_time=0.0, end_time=10.0, speaker_label="S0", text="hi"),
            Segment(segment_id=1, start_time=10.0, end_time=20.0, speaker_label="S1", text="yo"),
        ],
        summary=MeetingSummary(sections=[
            SummarySection(section_type="discussion", title="T", content="c",
                           start_time=0.0, end_time=20.0, start_segment=0, end_segment=1),
        ]),
    )


def test_absolutize_shifts_segment_and_section_times():
    m = _meeting_with_times(1380.0)
    out = absolutize_meeting_times(m)
    assert [s.start_time for s in out.segments] == [1380.0, 1390.0]
    assert [s.end_time for s in out.segments] == [1390.0, 1400.0]
    assert out.summary.sections[0].start_time == 1380.0
    assert out.summary.sections[0].end_time == 1400.0


def test_absolutize_does_not_shift_duration_or_clip_fields():
    m = _meeting_with_times(1380.0)
    out = absolutize_meeting_times(m)
    assert out.duration_seconds == 1500.0
    assert out.clip_start_seconds == 1380.0
    assert out.clip_end_seconds == 2880.0


def test_absolutize_noop_when_no_clip():
    m = _meeting_with_times(None)
    out = absolutize_meeting_times(m)
    assert [s.start_time for s in out.segments] == [0.0, 10.0]


def test_absolutize_returns_copy_does_not_mutate_input():
    m = _meeting_with_times(1380.0)
    absolutize_meeting_times(m)
    assert m.segments[0].start_time == 0.0
    assert m.summary.sections[0].start_time == 0.0


def test_absolutize_shifts_word_timestamps():
    seg = Segment(segment_id=0, start_time=0.0, end_time=5.0, speaker_label="S0", text="hi there",
                  words=[Word(word="hi", start=0.0, end=1.0), Word(word="there", start=1.0, end=2.0)])
    m = Meeting(meeting_id="m1", city="X", date="2026-06-28", clip_start_seconds=1380.0, segments=[seg])
    out = absolutize_meeting_times(m)
    assert [(w.start, w.end) for w in out.segments[0].words] == [(1380.0, 1381.0), (1381.0, 1382.0)]
    # original untouched
    assert m.segments[0].words[0].start == 0.0


def test_absolutize_shifts_floor_votes():
    from src.clip import absolutize_meeting_times
    from src.models import Meeting, FloorVote
    m = Meeting(meeting_id="m", city=None, date="2019-07-11", clip_start_seconds=14600.0,
                floor_votes=[
                    FloorVote(438, "Q", 236, 193, 0, 9, 102.6, 0, True),
                    FloorVote(500, "Q2", 1, 1, 0, 0, None, None, False),
                ])
    out = absolutize_meeting_times(m)
    assert out.floor_votes[0].timestamp == 14702.6
    assert out.floor_votes[1].timestamp is None
    assert m.floor_votes[0].timestamp == 102.6


def test_absolutize_no_offset_leaves_floor_votes():
    from src.clip import absolutize_meeting_times
    from src.models import Meeting, FloorVote
    m = Meeting(meeting_id="m", city=None, date="d",
                floor_votes=[FloorVote(438, "Q", 236, 193, 0, 9, 102.6, 0, True)])
    assert absolutize_meeting_times(m).floor_votes[0].timestamp == 102.6
