"""Chapter-hint mapping and injection for the section classifier."""

from src.models import Segment
from src.summarize import chapters_to_segment_hints, _format_chapter_hint


def _segs(starts):
    return [
        Segment(
            segment_id=i,
            start_time=s,
            end_time=s + 10.0,
            speaker_label="SPEAKER_00",
            text=f"seg {i}",
        )
        for i, s in enumerate(starts)
    ]


def test_maps_chapter_start_to_containing_segment():
    segments = _segs([0.0, 30.0, 60.0, 90.0])
    chapters = [
        {"start_time": 0.0, "end_time": 60.0, "title": "Intro"},
        {"start_time": 60.0, "end_time": None, "title": "Housing"},
    ]
    hints = chapters_to_segment_hints(chapters, segments)
    assert hints == [
        {"start_segment": 0, "end_segment": 1, "title": "Intro"},
        {"start_segment": 2, "end_segment": 3, "title": "Housing"},
    ]


def test_snaps_to_nearest_when_between_segments():
    # Chapter at 35s: segment 1 starts 30s (contains it) — pick 1, not 2.
    segments = _segs([0.0, 30.0, 60.0])
    chapters = [
        {"start_time": 0.0, "end_time": 35.0, "title": "A"},
        {"start_time": 35.0, "end_time": None, "title": "B"},
    ]
    hints = chapters_to_segment_hints(chapters, segments)
    assert hints[1]["start_segment"] == 1


def test_empty_chapters_or_segments():
    assert chapters_to_segment_hints([], _segs([0.0])) == []
    assert chapters_to_segment_hints([{"start_time": 0.0, "title": "X"}], []) == []


def test_format_chapter_hint_includes_titles_and_guidance():
    hints = [{"start_segment": 0, "end_segment": 2, "title": "Housing"}]
    text = _format_chapter_hint(hints)
    assert "Housing" in text
    assert "verbatim" in text.lower()


def test_format_chapter_hint_empty_is_empty_string():
    assert _format_chapter_hint([]) == ""


from unittest.mock import MagicMock
from src.summarize import classify_sections, _classify_sections_interview


def _capture_client(response_json: str):
    client = MagicMock()
    captured = {}

    def create(**kwargs):
        captured["content"] = kwargs["messages"][0]["content"]
        msg = MagicMock()
        msg.content = [MagicMock(text=response_json)]
        return msg

    client.messages.create.side_effect = create
    return client, captured


def test_council_classifier_includes_hint():
    client, captured = _capture_client('{"sections": []}')
    classify_sections(client, _segs([0.0, 10.0]), chapter_hint="HINTMARKER housing")
    assert "HINTMARKER housing" in captured["content"]


def test_council_classifier_omits_hint_when_absent():
    client, captured = _capture_client('{"sections": []}')
    classify_sections(client, _segs([0.0, 10.0]))
    assert "HINTMARKER" not in captured["content"]


def test_interview_classifier_includes_hint():
    client, captured = _capture_client('{"sections": []}')
    _classify_sections_interview(client, _segs([0.0, 10.0]), chapter_hint="HINTMARKER tax")
    assert "HINTMARKER tax" in captured["content"]


from unittest.mock import patch
from src.models import Meeting
from src.summarize import generate_summary


def _meeting_with_chapters(chapters):
    segs = _segs([0.0, 30.0, 60.0])
    m = Meeting(
        meeting_id="t", city=None, date="2026-07-04",
        meeting_type="News Clip", event_kind="news_clip", segments=segs,
    )
    m.processing_metadata.source_chapters = chapters
    return m


def test_generate_summary_passes_chapter_hint_to_classifier():
    meeting = _meeting_with_chapters(
        [{"start_time": 0.0, "end_time": 60.0, "title": "UNIQUEHINT topic"},
         {"start_time": 60.0, "end_time": None, "title": "Second"}]
    )
    client, captured = _capture_client('{"sections": []}')
    with patch("src.summarize.anthropic") as mock_anthropic:
        mock_anthropic.Anthropic.return_value = client
        generate_summary(meeting)
    # First call is the classifier; its content should carry the hint.
    assert "UNIQUEHINT topic" in captured["content"]


def test_overlapping_chapters_same_segment_are_dropped():
    # Two chapters land in segment 0 (coarse segments); the earlier one has no
    # room of its own and is dropped — no overlapping/contradictory hints.
    segments = _segs([0.0, 100.0, 200.0])
    chapters = [
        {"start_time": 0.0, "title": "A"},
        {"start_time": 1.0, "title": "B"},
        {"start_time": 200.0, "title": "C"},
    ]
    hints = chapters_to_segment_hints(chapters, segments)
    assert [h["title"] for h in hints] == ["B", "C"]
    # Every emitted hint has a valid (non-inverted) range:
    for h in hints:
        assert h["start_segment"] <= h["end_segment"]
