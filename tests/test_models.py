from src.models import MeetingSummary


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
