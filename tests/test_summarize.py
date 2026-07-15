"""Tests for event-kind-aware summarize behavior."""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.models import Meeting, MeetingSummary, Segment
from src.summarize import (
    _INTERVIEW_CLASSIFY_SYSTEM,
    _INTERVIEW_EXECUTIVE_SYSTEM,
    _INTERVIEW_SUMMARIZE_SYSTEM,
    classify_sections,
    generate_summary,
)


def make_meeting(event_kind: str, event_orgs: list | None = None) -> Meeting:
    seg = Segment(
        segment_id=0, start_time=0.0, end_time=10.0,
        speaker_label="SPEAKER_00", text="Hello, welcome to the interview.",
    )
    return Meeting(
        meeting_id="test",
        city=None,
        date="2026-06-16",
        meeting_type="News Clip",
        event_kind=event_kind,
        event_orgs=event_orgs or [],
        segments=[seg],
    )


def _mock_client(section_json: str, summary_json: str):
    """Build a mock Anthropic client whose messages.create returns different JSON per call."""
    client = MagicMock()
    responses = [section_json, summary_json, summary_json]
    call_count = [0]

    def create_message(**kwargs):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        msg = MagicMock()
        msg.content = [MagicMock(text=responses[idx])]
        return msg

    client.messages.create.side_effect = create_message
    return client


def test_interview_classify_uses_topic_sections():
    meeting = make_meeting("news_clip")
    sections_json = '{"sections": [{"type": "topic", "start_segment": 0, "end_segment": 0, "title": "Tax Policy"}]}'
    exec_json = '{"executive_summary": "Interview summary.", "highlights": ["Claim 1"]}'
    section_summary_json = "Summary of topic."

    client = MagicMock()
    responses = [sections_json, section_summary_json, exec_json]
    call_count = [0]

    def side_effect(**kwargs):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        msg = MagicMock()
        msg.content = [MagicMock(text=responses[idx])]
        # Record the system prompt on first call (classify)
        if idx == 0:
            side_effect.classify_system = kwargs.get("system", "")
        return msg

    client.messages.create.side_effect = side_effect

    with patch("src.summarize.anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        result = generate_summary(meeting)

    assert "topic" in side_effect.classify_system.lower() or "interview" in side_effect.classify_system.lower()
    assert isinstance(result, MeetingSummary)


def test_deliberative_classify_uses_council_sections():
    meeting = make_meeting("council")
    meeting.city = "Bloomington"
    sections_json = '{"sections": [{"type": "roll_call", "start_segment": 0, "end_segment": 0, "title": "Roll Call"}]}'
    exec_json = '{"executive_summary": "Council summary.", "highlights": ["Vote passed"]}'

    client = MagicMock()
    responses = [sections_json, "Roll call content.", exec_json]
    call_count = [0]

    def side_effect(**kwargs):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        msg = MagicMock()
        msg.content = [MagicMock(text=responses[idx])]
        if idx == 0:
            side_effect.classify_system = kwargs.get("system", "")
        return msg

    client.messages.create.side_effect = side_effect

    with patch("src.summarize.anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        result = generate_summary(meeting)

    assert "council" in side_effect.classify_system.lower()


def test_interview_executive_uses_source_attribution():
    meeting = make_meeting("news_clip", event_orgs=["California Courier"])
    sections_json = '{"sections": [{"type": "topic", "start_segment": 0, "end_segment": 0, "title": "Immigration"}]}'
    section_summary_json = "Subject discussed immigration policy."
    exec_json = '{"executive_summary": "In an interview with California Courier, John Smith discussed...", "highlights": ["Committed to reform"]}'

    client = MagicMock()
    responses = [sections_json, section_summary_json, exec_json]
    call_count = [0]

    def side_effect(**kwargs):
        idx = min(call_count[0], len(responses) - 1)
        call_count[0] += 1
        msg = MagicMock()
        msg.content = [MagicMock(text=responses[idx])]
        if idx == 2:
            side_effect.exec_prompt = kwargs.get("messages", [{}])[0].get("content", "")
        return msg

    client.messages.create.side_effect = side_effect

    with patch("src.summarize.anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        result = generate_summary(meeting)

    assert "California Courier" in side_effect.exec_prompt


def test_interview_constants_defined():
    assert "topic" in _INTERVIEW_CLASSIFY_SYSTEM.lower()
    assert "interview" in _INTERVIEW_SUMMARIZE_SYSTEM.lower()
    assert "interview" in _INTERVIEW_EXECUTIVE_SYSTEM.lower()


def test_show_notes_hint_present():
    from src.summarize import _show_notes_hint
    from src.models import ProcessingMetadata

    m = Meeting(meeting_id="x", city=None, date="2026-06-16")
    m.processing_metadata = ProcessingMetadata(
        source_description="Guest: Mayor Kerry Thomson on housing."
    )
    hint = _show_notes_hint(m)
    assert "Show notes" in hint
    assert "Kerry Thomson" in hint


def test_show_notes_hint_empty_when_absent():
    from src.summarize import _show_notes_hint

    m = Meeting(meeting_id="x", city=None, date="2026-06-16")
    assert _show_notes_hint(m) == ""
