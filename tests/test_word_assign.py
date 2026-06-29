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


def test_hilton_clip_assigns_continuous_words_to_correct_speakers():
    # Diarization turns (from diarization.json, ~17:06-17:15).
    segs = [
        _seg(0, 1026.655, 1029.254, "SPEAKER_00"),  # Steve
        _seg(1, 1029.322, 1029.777, "SPEAKER_01"),  # Hailey (short)
        _seg(2, 1029.777, 1030.283, "SPEAKER_00"),  # Steve
        _seg(3, 1030.570, 1034.148, "SPEAKER_00"),  # Steve
        _seg(4, 1034.148, 1034.519, "SPEAKER_01"),  # Hailey (short)
        _seg(5, 1034.603, 1036.949, "SPEAKER_00"),  # Steve
    ]
    # Continuous-transcription word timings (from spike: drift-free, accurate).
    words = [
        Word("abortion", 1028.18, 1028.64),
        Word("tourism", 1028.64, 1029.20),
        Word("where", 1029.20, 1030.20),
        Word("other", 1033.44, 1033.70),
        Word("states", 1033.70, 1034.20),
    ]

    assign_words_to_segments(words, segs)

    def owner(token):
        for s in segs:
            if any(w.word == token for w in s.words):
                return s.speaker_label
        return None

    assert owner("abortion") == "SPEAKER_00"  # Steve's "abortion tourism"
    assert owner("tourism") == "SPEAKER_00"
    assert owner("other") == "SPEAKER_00"      # Steve's "ads in other states"
