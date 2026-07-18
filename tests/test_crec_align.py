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


# add to tests/test_crec_align.py
from src.crec_align import _align


def test_align_clean_one_to_one():
    d = [{"apple", "pear"}, {"river", "bank"}]
    c = [{"apple", "pear"}, {"river", "bank"}]
    assert _align(d, c) == [(0, 0), (1, 1)]


def test_align_skips_unmatched_crec_turn_as_gap():
    # a CREC turn with no diarized counterpart is a free gap (revise-and-extend)
    d = [{"apple", "pear"}, {"river", "bank"}]
    c = [{"apple", "pear"}, {"zzz", "qqq"}, {"river", "bank"}]
    assert _align(d, c) == [(0, 0), (1, 2)]


def test_align_skips_unmatched_diarized_turn_as_gap():
    d = [{"apple", "pear"}, {"noise", "cough"}, {"river", "bank"}]
    c = [{"apple", "pear"}, {"river", "bank"}]
    assert _align(d, c) == [(0, 0), (2, 1)]


def test_align_below_floor_not_matched():
    # near-zero overlap must not produce a pair
    d = [{"apple", "pear", "cat", "dog", "fish"}]
    c = [{"apple", "zzz", "qqq", "www", "eee"}]   # overlap 1/5 = 0.2 > floor -> matched
    assert _align(d, c) == [(0, 0)]
    d2 = [{"a1", "a2", "a3", "a4", "a5", "a6", "a7", "a8", "a9", "a10"}]
    c2 = [{"a1", "z2", "z3", "z4", "z5", "z6", "z7", "z8", "z9", "z10"}]  # 1/10 = 0.1, not > floor
    assert _align(d2, c2) == []


def test_align_empty():
    assert _align([], [{"a"}]) == []
    assert _align([{"a"}], []) == []
