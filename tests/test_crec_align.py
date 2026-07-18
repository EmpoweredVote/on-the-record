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


# add to tests/test_crec_align.py
from src.crec_align import LabelResolution, _confidence, _aggregate
from src.congress_roster import CongressMember
from src.crec_normalize import ResolvedSpeaker


def _member(bio, last):
    return CongressMember(bio, f"First {last}", last, "XX", None, "senate", "Democrat")


def _rs_member(bio, last):
    return ResolvedSpeaker(member=_member(bio, last), method="surname", confidence=1.0)


def _rs_role(role):
    return ResolvedSpeaker(role=role, method="role", confidence=1.0)


def _dturn(label, idx):
    return DiarizedTurn(speaker_label=label, text="", index=idx)


def test_confidence_is_product_of_factors():
    assert _confidence(1.0, 1.0, 1.0) == 1.0
    assert _confidence(0.5, 1.0, 0.8) == 0.4
    assert _confidence(1.0, 0.5, 0.5) == 0.25


def test_aggregate_confident_member():
    d_turns = [_dturn("S0", 0), _dturn("S1", 1)]
    matches = [
        ("S0", _rs_member("B1", "Baldwin"), 0.9),
        ("S1", _rs_member("M1", "McConnell"), 0.8),
    ]
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member.bioguide == "B1"
    assert out["S0"].method == "congressional_record"
    assert out["S0"].needs_review is False
    assert out["S1"].member.bioguide == "M1"


def test_aggregate_split_vote_is_ambiguous():
    # one label's two runs match two different members -> tie -> ambiguous
    d_turns = [_dturn("S0", 0), _dturn("S0", 1)]
    matches = [
        ("S0", _rs_member("B1", "Baldwin"), 0.9),
        ("S0", _rs_member("M1", "McConnell"), 0.9),
    ]
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member is None
    assert out["S0"].method == "ambiguous"
    assert out["S0"].needs_review is True


def test_aggregate_below_gate_is_ambiguous_member_none():
    # a member surfaced but confidence below the gate
    d_turns = [_dturn("S0", 0), _dturn("S0", 1), _dturn("S0", 2), _dturn("S0", 3)]
    matches = [("S0", _rs_member("B1", "Baldwin"), 0.6)]   # match_fraction 1/4 -> conf 0.15
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member is None
    assert out["S0"].method == "ambiguous"
    assert out["S0"].needs_review is True
    assert out["S0"].matched_turns == 1
    assert out["S0"].total_turns == 4


def test_aggregate_role_dominant():
    d_turns = [_dturn("S0", 0)]
    matches = [("S0", _rs_role("presiding_officer"), 0.4)]
    out = _aggregate(d_turns, matches, min_confidence=0.5)
    assert out["S0"].member is None
    assert out["S0"].role == "presiding_officer"
    assert out["S0"].method == "congressional_record"
    assert out["S0"].needs_review is False


def test_aggregate_unresolved_when_no_matches():
    d_turns = [_dturn("S0", 0)]
    out = _aggregate(d_turns, [], min_confidence=0.5)
    assert out["S0"].method == "unresolved"
    assert out["S0"].member is None
    assert out["S0"].total_turns == 1


# add to tests/test_crec_align.py
import json
from pathlib import Path

from src.congress_roster import build_roster
from src.govinfo import CrecTurn
from src.crec_normalize import annotate_turns
from src.crec_align import align_crec_to_diarization

_FIX = Path(__file__).parent / "fixtures" / "congress" / "legislators-current.sample.json"


def _senate_roster():
    return build_roster(json.loads(_FIX.read_text(encoding="utf-8")), "senate")


def test_align_clean_two_members():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "I move to proceed to the healthcare funding bill", "g", 0),
        CrecTurn("Ms. BALDWIN of Wisconsin", "I rise in strong support of the healthcare measure", "g", 1),
    ], _senate_roster())
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill today"),
        _seg(1, "SPEAKER_01", "I rise in strong support of this healthcare measure"),
    ]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    assert out["SPEAKER_00"].member.last_name == "McConnell"
    assert out["SPEAKER_00"].method == "congressional_record"
    assert out["SPEAKER_01"].member.last_name == "Baldwin"


def test_align_role_interjection():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "I move to proceed to the healthcare funding bill", "g", 0),
        CrecTurn("The PRESIDING OFFICER", "Without objection it is so ordered", "g", 1),
        CrecTurn("Ms. BALDWIN of Wisconsin", "I rise in strong support of the healthcare measure", "g", 2),
    ], _senate_roster())
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill"),
        _seg(1, "SPEAKER_09", "without objection it is so ordered"),
        _seg(2, "SPEAKER_01", "I rise in strong support of the healthcare measure"),
    ]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    assert out["SPEAKER_00"].member.last_name == "McConnell"
    assert out["SPEAKER_09"].role == "presiding_officer"
    assert out["SPEAKER_01"].member.last_name == "Baldwin"


def test_align_revise_and_extend_gap_does_not_break_others():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "I move to proceed to the healthcare funding bill", "g", 0),
        CrecTurn("Ms. BALDWIN of Wisconsin", "submitted remarks about unrelated agriculture policy subsidies", "g", 1),
        CrecTurn("Mr. McCONNELL", "I yield the floor on the healthcare funding bill", "g", 2),
    ], _senate_roster())
    segs = [
        _seg(0, "SPEAKER_00", "I move to proceed to the healthcare funding bill"),
        _seg(1, "SPEAKER_00", "I yield the floor on the healthcare funding bill"),
    ]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    # the Baldwin "revise and extend" CREC turn was never spoken -> free gap;
    # SPEAKER_00 still resolves to McConnell.
    assert out["SPEAKER_00"].member.last_name == "McConnell"


def test_align_unresolved_when_no_overlap():
    annotated = annotate_turns([
        CrecTurn("Mr. McCONNELL", "healthcare funding appropriations markup", "g", 0),
    ], _senate_roster())
    segs = [_seg(0, "SPEAKER_00", "completely unrelated words about weather sports music")]
    out = align_crec_to_diarization(segs, annotated, min_confidence=0.4)
    assert out["SPEAKER_00"].method == "unresolved"


def test_align_empty_inputs():
    assert align_crec_to_diarization([], [], min_confidence=0.4) == {}
