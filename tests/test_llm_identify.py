"""Tests for the Layer-3 LLM speaker-identification step (src.llm_utils).

Regression guard: in an interview dominated by one enrolled person, the prompt
must tell the model the known names belong to other voices and permit abstaining
(null). Plus: a returned name must be anchored to the transcript (interviews) or
roster (civic) or it is rejected. See interview-chris-swanson-wdiv.
"""
from __future__ import annotations

from src.llm_utils import prompt_for_speaker_id
from src.models import Segment, SpeakerMapping


class _Provider:
    """Fake provider that records the prompt and returns a fixed JSON string."""

    def __init__(self, response='{"name": null, "reasoning": "cannot tell"}'):
        self.name = "fake"
        self.model = "fake"
        self.prompt = None
        self._response = response

    def complete(self, prompt, *, max_tokens=150, temperature=0.0):
        self.prompt = prompt
        return self._response


def _segments():
    return [
        Segment(segment_id=0, start_time=0.0, end_time=5.0,
                speaker_label="SPEAKER_00", text="So tell me about your campaign, Jane Smith."),
        Segment(segment_id=1, start_time=5.0, end_time=10.0,
                speaker_label="SPEAKER_01", text="Happy to. My focus is public safety."),
    ]


def _claimed():
    return {"SPEAKER_01": SpeakerMapping(
        speaker_label="SPEAKER_01", speaker_name="Chris Swanson", confidence=0.96)}


def test_prompt_marks_known_names_as_claimed_by_other_voices():
    p = _Provider()
    prompt_for_speaker_id(p, _segments(), _claimed(), "SPEAKER_00")
    assert "already" in p.prompt.lower()
    assert "different" in p.prompt.lower() or "another" in p.prompt.lower()
    assert "Chris Swanson" in p.prompt


def test_prompt_permits_abstaining_with_null():
    p = _Provider()
    prompt_for_speaker_id(p, _segments(), _claimed(), "SPEAKER_00")
    assert "null" in p.prompt.lower()


def test_prompt_uses_event_kind_framing():
    p = _Provider()
    prompt_for_speaker_id(p, _segments(), {}, "SPEAKER_00", event_kind="podcast")
    assert "interview" in p.prompt.lower() or "host" in p.prompt.lower()


def test_null_response_maps_to_no_mapping():
    p = _Provider('{"name": null, "reasoning": "cannot tell"}')
    assert prompt_for_speaker_id(p, _segments(), _claimed(), "SPEAKER_00") is None


def test_name_in_transcript_is_accepted():
    p = _Provider('{"name": "Jane Smith", "reasoning": "addressed by name"}')
    result = prompt_for_speaker_id(p, _segments(), {}, "SPEAKER_01")
    assert result is not None
    assert result.speaker_name == "Jane Smith"
    assert result.id_method == "llm"
    assert result.confidence == 0.75


def test_name_absent_from_transcript_is_rejected_as_hallucination():
    # "Mr. Bean" appears nowhere in the transcript -> guardrail returns None.
    p = _Provider('{"name": "Mr. Bean", "reasoning": "guess"}')
    assert prompt_for_speaker_id(p, _segments(), {}, "SPEAKER_01") is None


def test_roster_mode_accepts_roster_member_not_in_window():
    from src.roster import Roster, RosterMember
    roster = Roster(members=[RosterMember(name="President Asare", aliases=[])])
    p = _Provider('{"name": "President Asare", "reasoning": "chairs the meeting"}')
    result = prompt_for_speaker_id(
        p, _segments(), {}, "SPEAKER_00", event_kind="council", roster=roster)
    assert result is not None
    assert result.speaker_name == "President Asare"


def test_roster_mode_rejects_non_roster_name():
    from src.roster import Roster, RosterMember
    roster = Roster(members=[RosterMember(name="President Asare", aliases=[])])
    p = _Provider('{"name": "Councilmember Nonexistent", "reasoning": "guess"}')
    result = prompt_for_speaker_id(
        p, _segments(), {}, "SPEAKER_00", event_kind="council", roster=roster)
    assert result is None
