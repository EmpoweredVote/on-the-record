"""Direct unit tests for the Layer-3 anchoring guardrail helpers.

Guards the short-surname bug: `_significant_tokens` must keep 2-char surnames
(Wu, Ng, Vo, Xu, Oh) so `tokens[-1]` is the real surname and a hallucinated
"Eric Ng" cannot borrow "Eric Wu"'s anchor.
"""
from __future__ import annotations

from src.llm_utils import _significant_tokens, prompt_for_speaker_id
from src.models import Segment
from src.roster import Roster, RosterMember


class _Provider:
    def __init__(self, response):
        self.name = "fake"
        self.model = "fake"
        self.prompt = None
        self._response = response

    def complete(self, prompt, *, max_tokens=150, temperature=0.0):
        self.prompt = prompt
        return self._response


def _segs(text0, text1):
    return [
        Segment(segment_id=0, start_time=0.0, end_time=5.0,
                speaker_label="SPEAKER_00", text=text0),
        Segment(segment_id=1, start_time=5.0, end_time=10.0,
                speaker_label="SPEAKER_01", text=text1),
    ]


# --- _significant_tokens -----------------------------------------------------

def test_significant_tokens_retains_two_char_surname():
    assert _significant_tokens("Eric Wu") == ["eric", "wu"]


def test_significant_tokens_honorific_only_is_empty():
    assert _significant_tokens("Speaker") == []


def test_significant_tokens_drops_honorific_keeps_surname():
    assert _significant_tokens("Mr. Bean") == ["bean"]


# --- short-surname rejection end to end (roster mode) ------------------------

def test_roster_rejects_different_short_surname():
    roster = Roster(members=[RosterMember(name="Eric Wu", aliases=[])])
    p = _Provider('{"name": "Eric Ng", "reasoning": "guess"}')
    result = prompt_for_speaker_id(
        p, _segs("Thanks, Eric Wu, for joining.", "Glad to be here."),
        {}, "SPEAKER_00", event_kind="council", roster=roster)
    assert result is None


def test_roster_accepts_matching_short_surname():
    roster = Roster(members=[RosterMember(name="Eric Wu", aliases=[])])
    p = _Provider('{"name": "Eric Wu", "reasoning": "chairs"}')
    result = prompt_for_speaker_id(
        p, _segs("Thanks, Eric Wu, for joining.", "Glad to be here."),
        {}, "SPEAKER_00", event_kind="council", roster=roster)
    assert result is not None
    assert result.speaker_name == "Eric Wu"


# --- fuzzy branch (interview, no roster) -------------------------------------

def test_interview_fuzzy_accepts_near_match():
    p = _Provider('{"name": "Fitzpatric", "reasoning": "addressed"}')
    result = prompt_for_speaker_id(
        p, _segs("Welcome, Fitzpatrick.", "Thank you."),
        {}, "SPEAKER_00")
    assert result is not None
    assert result.speaker_name == "Fitzpatric"


def test_interview_rejects_absent_low_ratio_name():
    p = _Provider('{"name": "Bilirakis", "reasoning": "guess"}')
    result = prompt_for_speaker_id(
        p, _segs("Welcome, Fitzpatrick.", "Thank you."),
        {}, "SPEAKER_00")
    assert result is None


# --- empty-roster fallthrough ------------------------------------------------

def test_empty_roster_falls_through_to_transcript():
    roster = Roster(members=[])
    p = _Provider('{"name": "Fitzpatrick", "reasoning": "addressed"}')
    result = prompt_for_speaker_id(
        p, _segs("Welcome, Fitzpatrick.", "Thank you."),
        {}, "SPEAKER_00", event_kind="council", roster=roster)
    assert result is not None
    assert result.speaker_name == "Fitzpatrick"
