from __future__ import annotations

from gui.models import MeetingSummary, stage_label


def test_stage_label_maps_each_stage_to_friendly_text():
    assert stage_label(0) == "Not started"
    assert stage_label(1) == "Audio ingested"
    assert stage_label(2) == "Speakers separated"
    assert stage_label(3) == "Transcribed"
    assert stage_label(4) == "Identified — ready to review"
    assert stage_label(5) == "Summarized"
    assert stage_label(6) == "Voices enrolled"
    assert stage_label(7) == "Published"


def test_stage_label_tolerates_unknown_stage():
    assert stage_label(99) == "Unknown (99)"


def test_meeting_summary_display_name_prefers_title():
    s = MeetingSummary(
        meeting_id="2026-02-04-regular-session",
        title="Budget Hearing",
        city="Bloomington",
        meeting_type="Regular Session",
        date="2026-02-04",
        event_kind="council",
        completed_stage=4,
    )
    assert s.display_name == "Budget Hearing"
    assert s.stage_label == "Identified — ready to review"


def test_meeting_summary_display_name_falls_back_to_city_and_type():
    s = MeetingSummary(
        meeting_id="2026-02-04-regular-session",
        title=None,
        city="Bloomington",
        meeting_type="Regular Session",
        date="2026-02-04",
        event_kind="council",
        completed_stage=2,
    )
    assert s.display_name == "Bloomington Regular Session"
