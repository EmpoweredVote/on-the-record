"""Tests for the LLM speaker-identification prompt (src.llm_utils).

Regression guard: in an interview transcript dominated by one enrolled person,
the LLM used to guess that name for the unenrolled interviewer too, because the
prompt neither told it those names were already claimed by other voices nor gave
it permission to abstain. See interview-chris-swanson-wdiv.
"""
from __future__ import annotations

from src.models import Segment, SpeakerMapping
from src.llm_utils import prompt_for_speaker_id


class _CapturingLLM:
    """Fake llama callable that records the prompt and returns a null id."""

    def __init__(self):
        self.prompt = None

    def __call__(self, prompt, **kwargs):
        self.prompt = prompt
        return {"choices": [{"text": '{"name": null, "reasoning": "cannot tell"}'}]}


def _segments():
    return [
        Segment(segment_id=0, start_time=0.0, end_time=5.0,
                speaker_label="SPEAKER_00", text="So tell me about your campaign."),
        Segment(segment_id=1, start_time=5.0, end_time=10.0,
                speaker_label="SPEAKER_01", text="Happy to. My focus is public safety."),
    ]


def test_prompt_marks_known_names_as_claimed_by_other_voices():
    llm = _CapturingLLM()
    current = {"SPEAKER_01": SpeakerMapping(
        speaker_label="SPEAKER_01", speaker_name="Chris Swanson", confidence=0.96)}
    prompt_for_speaker_id(llm, _segments(), current, "SPEAKER_00")
    # The prompt must tell the model these names belong to *different* voices and
    # must not be reused for the unknown speaker.
    assert "already" in llm.prompt.lower()
    assert "different" in llm.prompt.lower() or "another" in llm.prompt.lower()
    assert "Chris Swanson" in llm.prompt


def test_prompt_permits_abstaining_with_null():
    llm = _CapturingLLM()
    current = {"SPEAKER_01": SpeakerMapping(
        speaker_label="SPEAKER_01", speaker_name="Chris Swanson", confidence=0.96)}
    prompt_for_speaker_id(llm, _segments(), current, "SPEAKER_00")
    assert "null" in llm.prompt.lower()


def test_prompt_returns_null_maps_to_no_mapping():
    llm = _CapturingLLM()
    current = {"SPEAKER_01": SpeakerMapping(
        speaker_label="SPEAKER_01", speaker_name="Chris Swanson", confidence=0.96)}
    result = prompt_for_speaker_id(llm, _segments(), current, "SPEAKER_00")
    assert result is None
