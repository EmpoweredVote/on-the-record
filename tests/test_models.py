from src.models import Meeting, MeetingSummary


def test_meeting_summary_highlights_field():
    ms = MeetingSummary(highlights=["item 1"])
    assert ms.highlights == ["item 1"]


def test_meeting_summary_from_dict_key_decisions_compat():
    d = {"key_decisions": ["vote passed"], "executive_summary": "...", "model": "", "generated_at": ""}
    ms = MeetingSummary.from_dict(d)
    assert ms.highlights == ["vote passed"]


def test_meeting_summary_to_dict_uses_highlights():
    ms = MeetingSummary(highlights=["vote passed"])
    d = ms.to_dict()
    assert "highlights" in d
    assert "key_decisions" not in d


def test_meeting_summary_from_dict_highlights_takes_precedence():
    d = {"highlights": ["new"], "key_decisions": ["old"], "executive_summary": "", "model": "", "generated_at": ""}
    ms = MeetingSummary.from_dict(d)
    assert ms.highlights == ["new"]


def test_meeting_clip_window_roundtrip():
    m = Meeting(
        meeting_id="m1", city="X", date="2026-06-28",
        clip_start_seconds=1380.0, clip_end_seconds=2880.0,
    )
    d = m.to_dict()
    assert d["clip_start_seconds"] == 1380.0
    assert d["clip_end_seconds"] == 2880.0
    back = Meeting.from_dict(d)
    assert back.clip_start_seconds == 1380.0
    assert back.clip_end_seconds == 2880.0


def test_meeting_clip_window_defaults_none():
    m = Meeting(meeting_id="m1", city="X", date="2026-06-28")
    assert m.clip_start_seconds is None
    assert m.clip_end_seconds is None
    back = Meeting.from_dict({"meeting_id": "m1", "city": "X", "date": "2026-06-28"})
    assert back.clip_start_seconds is None
