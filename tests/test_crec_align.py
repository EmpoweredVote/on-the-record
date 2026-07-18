# tests/test_crec_align.py
from __future__ import annotations

from src.crec_align import _content_tokens, _overlap
from src.models import Segment
from src.crec_align import DiarizedTurn, _build_diarized_turns


def test_content_tokens_drops_stopwords_punct_and_short():
    toks = _content_tokens("The Senator moved to proceed, on the BILL!")
    assert toks == {"senator", "moved", "proceed", "bill"}


def test_content_tokens_empty():
    assert _content_tokens("") == set()
    assert _content_tokens(None) == set()


def test_overlap_coefficient():
    assert _overlap({"a", "b", "c"}, {"a", "b"}) == 1.0        # containment of smaller
    assert _overlap({"a", "b", "c", "d"}, {"a", "b"}) == 1.0
    assert _overlap({"a", "b"}, {"b", "x"}) == 0.5
    assert _overlap(set(), {"a"}) == 0.0
    assert _overlap({"a"}, set()) == 0.0


def _seg(i, label, text):
    return Segment(segment_id=i, start_time=float(i), end_time=float(i + 1),
                   speaker_label=label, text=text)


def test_build_diarized_turns_groups_consecutive_same_label():
    segs = [
        _seg(0, "SPEAKER_00", "hello there"),
        _seg(1, "SPEAKER_00", "friends"),
        _seg(2, "SPEAKER_01", "hi"),
        _seg(3, "SPEAKER_00", "again"),
    ]
    turns = _build_diarized_turns(segs)
    assert [(t.speaker_label, t.text, t.index) for t in turns] == [
        ("SPEAKER_00", "hello there friends", 0),
        ("SPEAKER_01", "hi", 1),
        ("SPEAKER_00", "again", 2),
    ]


def test_build_diarized_turns_empty():
    assert _build_diarized_turns([]) == []
