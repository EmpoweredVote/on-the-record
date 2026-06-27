from __future__ import annotations

from src.bulk_relink import (
    DECISION_LINK,
    DECISION_REVIEW,
    DECISION_SKIP,
    UnlinkedSpeaker,
)


def test_decision_constants_have_expected_string_values():
    assert DECISION_LINK == "link"
    assert DECISION_REVIEW == "review"
    assert DECISION_SKIP == "skip"


def test_unlinked_speaker_defaults():
    s = UnlinkedSpeaker(display_name="Steve Hilton", normalized_name="steve hilton")
    assert s.appearances == []
    assert s.meeting_count == 0
    assert s.has_voice_profile is False
    assert s.known_id is None
    assert s.decision == DECISION_REVIEW
    assert s.candidates == []
