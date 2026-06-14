from src.models import Segment
from src.transcribe import remove_segment_overlaps


def test_remove_segment_overlaps_trims_the_later_speaker():
    segments = [
        Segment(0, 10.0, 15.0, "SPEAKER_00"),
        Segment(1, 14.0, 18.0, "SPEAKER_01"),
        Segment(2, 17.5, 20.0, "SPEAKER_02"),
    ]

    result = remove_segment_overlaps(segments)

    assert [(seg.start_time, seg.end_time) for seg in result] == [
        (10.0, 15.0),
        (15.0, 18.0),
        (18.0, 20.0),
    ]


def test_remove_segment_overlaps_collapses_fully_covered_segment():
    segments = [
        Segment(0, 10.0, 20.0, "SPEAKER_00"),
        Segment(1, 12.0, 14.0, "SPEAKER_01"),
    ]

    result = remove_segment_overlaps(segments)

    assert result[1].start_time == result[1].end_time == 14.0
