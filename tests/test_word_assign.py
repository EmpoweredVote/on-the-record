from __future__ import annotations

from src.models import Segment, Word
from src.word_assign import assign_words_to_segments


def _seg(seg_id, start, end, label):
    return Segment(segment_id=seg_id, start_time=start, end_time=end, speaker_label=label)


def test_assigns_word_by_midpoint_to_containing_segment():
    segs = [_seg(0, 0.0, 2.0, "A"), _seg(1, 2.0, 4.0, "B")]
    words = [Word("hello", 0.2, 0.8), Word("there", 2.2, 2.8)]

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[0].words] == ["hello"]
    assert [w.word for w in segs[1].words] == ["there"]
    assert segs[0].text == "hello"
    assert segs[1].text == "there"
