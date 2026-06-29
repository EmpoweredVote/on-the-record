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


def test_short_turn_does_not_steal_boundary_word_from_dominant():
    # Steve speaks continuously; a 0.37s Hailey turn sits at a word boundary.
    segs = [
        _seg(0, 1030.570, 1034.148, "SPEAKER_00"),  # Steve (long)
        _seg(1, 1034.148, 1034.519, "SPEAKER_01"),  # Hailey (0.371s, short)
        _seg(2, 1034.603, 1036.949, "SPEAKER_00"),  # Steve (long)
    ]
    # "saying" spans the boundary; midpoint 1034.56 lies in the gap, not inside
    # the short Hailey turn. It must NOT be handed to Hailey.
    words = [Word("saying", 1034.20, 1034.92)]

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[1].words] == []        # Hailey gets nothing
    assert "saying" in [w.word for w in segs[0].words] or \
           "saying" in [w.word for w in segs[2].words]  # stays with Steve


def test_short_turn_keeps_word_that_mostly_fits_inside_it():
    # Roll-call shape: clerk calls a name, member answers "Here." in a brief
    # turn, clerk continues. The member's word mostly fills their short turn, so
    # it must stay with the member (protects council vote/roll-call records).
    segs = [
        _seg(0, 0.0, 5.0, "CLERK"),       # long
        _seg(1, 5.0, 5.5, "MEMBER"),      # 0.5s short turn
        _seg(2, 5.6, 10.0, "CLERK"),      # long
    ]
    words = [Word("Here.", 5.05, 5.45)]   # 0.4s word, ~100% inside MEMBER turn

    assign_words_to_segments(words, segs)

    assert [w.word for w in segs[1].words] == ["Here."]  # member keeps it
    assert segs[0].words == [] and segs[2].words == []
