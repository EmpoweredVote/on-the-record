"""Event-kind framing and truncation logging in summarize.py prompts."""

import logging
from unittest.mock import MagicMock

import pytest

from src.event_kinds import EVENT_KINDS, INTERVIEW_KINDS, summary_subject
from src.models import Segment
from src.summarize import (
    _CLASSIFY_SYSTEM,
    _EXECUTIVE_SYSTEM,
    _EXTRACT_ROLL_CALL_SYSTEM,
    _EXTRACT_VOTE_SYSTEM,
    _SUMMARIZE_DISCUSSION_SYSTEM,
    _extract_votes,
    _framed,
    _summarize_discussion,
    classify_sections,
)

_TEMPLATES = (
    _CLASSIFY_SYSTEM,
    _SUMMARIZE_DISCUSSION_SYSTEM,
    _EXTRACT_ROLL_CALL_SYSTEM,
    _EXTRACT_VOTE_SYSTEM,
    _EXECUTIVE_SYSTEM,
)


def _segs(n=2):
    return [
        Segment(segment_id=i, start_time=float(i), end_time=float(i + 1),
                speaker_label="SPEAKER_00", text=f"line {i}")
        for i in range(n)
    ]


def _capture_client(text='{"sections": []}', stop_reason="end_turn"):
    client = MagicMock()
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        msg = MagicMock()
        msg.content = [MagicMock(text=text)]
        msg.stop_reason = stop_reason
        return msg

    client.messages.create.side_effect = create
    return client, calls


@pytest.mark.parametrize("kind", ["council", None])
def test_council_and_legacy_kinds_keep_council_wording(kind):
    assert _framed(_CLASSIFY_SYSTEM, kind).startswith(
        "You are an expert at analyzing city council meeting transcripts."
    )
    assert "a section of a city council meeting for" in _framed(
        _SUMMARIZE_DISCUSSION_SYSTEM, kind
    )
    assert "executive summary of a city council meeting for citizens" in _framed(
        _EXECUTIVE_SYSTEM, kind
    )


@pytest.mark.parametrize(
    "kind, noun",
    [
        ("debate", "candidate debate"),
        ("forum", "candidate forum"),
        ("school_board", "school board meeting"),
        ("floor", "legislative floor session"),
        ("community_meeting", "community meeting"),
        ("other", "public meeting"),
    ],
)
def test_non_council_kinds_drop_council_framing(kind, noun):
    for template in _TEMPLATES:
        framed = _framed(template, kind)
        assert "council meeting" not in framed
        assert noun in framed
        assert "{subject}" not in framed


def test_every_kind_fills_every_placeholder():
    for kind in (*EVENT_KINDS, None):
        for template in _TEMPLATES:
            assert "{subject}" not in _framed(template, kind)
            assert "{short_subject}" not in _framed(template, kind)


def test_json_braces_survive_framing():
    assert '"sections": [' in _framed(_CLASSIFY_SYSTEM, "debate")


def test_summary_subject_covers_every_non_interview_kind_or_defaults():
    for kind in EVENT_KINDS:
        if kind in INTERVIEW_KINDS:
            continue
        full, short = summary_subject(kind)
        assert full and short


def test_classify_sections_frames_system_and_user_prompt():
    client, calls = _capture_client()
    classify_sections(client, _segs(), event_kind="debate")
    assert "candidate debate transcripts" in calls[0]["system"]
    assert calls[0]["messages"][0]["content"].startswith(
        "Classify this debate transcript into sections:"
    )


def test_classify_sections_default_user_prompt_unchanged():
    client, calls = _capture_client()
    classify_sections(client, _segs())
    assert calls[0]["messages"][0]["content"].startswith(
        "Classify this council meeting transcript into sections:"
    )


def test_chunked_classify_frames_every_chunk(monkeypatch):
    from src import config
    monkeypatch.setattr(config, "SUMMARY_CHUNK_SIZE", 2)
    client, calls = _capture_client()
    classify_sections(client, _segs(5), event_kind="forum")
    assert len(calls) == 3
    assert all("candidate forum transcripts" in c["system"] for c in calls)


def test_max_tokens_stop_reason_logs_warning(caplog):
    client, _ = _capture_client(text='{"votes": [', stop_reason="max_tokens")
    with caplog.at_level(logging.WARNING, logger="src.summarize"):
        md, votes = _extract_votes(client, "transcript")
    assert votes == []
    assert any("max_tokens" in r.getMessage() and "votes" in r.getMessage()
               for r in caplog.records)


def test_normal_stop_reason_logs_nothing(caplog):
    client, _ = _capture_client(text="Summary.", stop_reason="end_turn")
    with caplog.at_level(logging.WARNING, logger="src.summarize"):
        _summarize_discussion(client, "transcript", "Title", event_kind="debate")
    assert not caplog.records
