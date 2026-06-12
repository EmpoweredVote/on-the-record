"""Tests for the Supabase publisher: playback resolution and payload builders."""

import pytest

from src.models import Meeting, ProcessingMetadata, Segment, SpeakerMapping, Word
from src.publish import (
    build_meeting_row,
    build_people_rows,
    build_segment_rows,
    build_speaker_rows,
    extract_youtube_id,
    resolve_playback,
)


# ---------------------------------------------------------------------------
# resolve_playback / extract_youtube_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://www.youtube.com/watch?v=AbC12345xyz", "AbC12345xyz"),
        ("https://youtube.com/watch?v=AbC12345xyz&t=120", "AbC12345xyz"),
        ("https://youtu.be/AbC12345xyz", "AbC12345xyz"),
        ("https://youtu.be/AbC12345xyz?si=share", "AbC12345xyz"),
        ("https://www.youtube.com/embed/AbC12345xyz", "AbC12345xyz"),
        ("https://www.youtube.com/shorts/AbC12345xyz", "AbC12345xyz"),
        ("https://www.youtube.com/live/AbC12345xyz", "AbC12345xyz"),
        ("https://m.youtube.com/watch?v=AbC12345xyz", "AbC12345xyz"),
        ("https://vimeo.com/12345", None),
        ("https://example.com/watch?v=nope", None),
        ("not a url", None),
    ],
)
def test_extract_youtube_id(url, expected):
    assert extract_youtube_id(url) == expected


def test_resolve_playback_youtube():
    assert resolve_playback("https://www.youtube.com/watch?v=AbC12345xyz") == (
        "youtube",
        "AbC12345xyz",
    )


def test_resolve_playback_catstv_blob_is_direct_file():
    url = "https://catstv.blob.core.windows.net/videoarchive/B_CC_260218.m4v"
    assert resolve_playback(url) == ("file", url)


def test_resolve_playback_direct_mp4():
    url = "https://example.gov/meetings/2026-02-10.mp4"
    assert resolve_playback(url) == ("file", url)


def test_resolve_playback_hls():
    url = "https://stream.example.gov/live/playlist.m3u8"
    assert resolve_playback(url) == ("hls", url)


def test_resolve_playback_unknown_provider():
    assert resolve_playback("https://www.facebook.com/video/123") == (None, None)


def test_resolve_playback_local_path():
    assert resolve_playback("/Users/operator/meeting.mp4") == (None, None)
    assert resolve_playback("") == (None, None)


def test_resolve_playback_catstv_page_falls_back_on_error(monkeypatch):
    """A catstv.net page URL that can't be scraped degrades to (None, None)."""
    import src.download as download

    def boom(url):
        raise ValueError("no video found")

    monkeypatch.setattr(download, "_extract_blob_url_from_page", boom)
    assert resolve_playback("https://catstv.net/government.php?id=99") == (None, None)


# ---------------------------------------------------------------------------
# Payload builders
# ---------------------------------------------------------------------------

def _meeting() -> Meeting:
    return Meeting(
        meeting_id="2026-02-18-council",
        city="Bloomington",
        date="2026-02-18",
        meeting_type="Regular Session",
        audio_source="https://catstv.blob.core.windows.net/videoarchive/B_CC_260218.m4v",
        duration_seconds=11000.0,
        segments=[
            Segment(
                segment_id=0,
                start_time=0.0,
                end_time=5.0,
                speaker_label="SPEAKER_00",
                speaker_name="Council President Asare",
                text="Good evening.",
                words=[Word(word="Good", start=0.0, end=0.4)],
                confidence=0.95,
            ),
            Segment(
                segment_id=1,
                start_time=5.0,
                end_time=6.0,
                speaker_label="SPEAKER_01",
                text="",  # empty segments are skipped
            ),
        ],
        speakers={
            "SPEAKER_00": SpeakerMapping(
                speaker_label="SPEAKER_00",
                speaker_name="Council President Asare",
                confidence=0.95,
                id_method="voice_profile",
                politician_slug="asare-isabel",
            ),
            "SPEAKER_01": SpeakerMapping(
                speaker_label="SPEAKER_01",
                speaker_name="Public Commenter",
            ),
        },
        processing_metadata=ProcessingMetadata(),
    )


def test_meeting_row_url_source():
    row = build_meeting_row(_meeting(), body_slug="bloomington")
    assert row["source_url"].startswith("https://catstv.blob")
    assert row["playback_kind"] == "file"
    assert row["playback_url"] == row["source_url"]
    assert row["meeting_date"] == "2026-02-18"
    assert row["body_slug"] == "bloomington"


def test_meeting_row_local_path_source():
    meeting = _meeting()
    meeting.audio_source = "/Users/operator/meeting.mp4"
    row = build_meeting_row(meeting, body_slug=None)
    assert row["source_url"] is None
    assert row["playback_kind"] is None
    assert row["playback_url"] is None


def test_meeting_row_rejects_bad_date():
    meeting = _meeting()
    meeting.date = "Feb 18, 2026"
    with pytest.raises(RuntimeError, match="not YYYY-MM-DD"):
        build_meeting_row(meeting, body_slug=None)


def test_segment_rows_exclude_words_and_empty_text():
    rows = build_segment_rows(_meeting())
    assert len(rows) == 1  # empty-text segment dropped
    assert "words" not in rows[0]
    assert rows[0]["politician_slug"] == "asare-isabel"  # denormalized from mapping
    assert rows[0]["text"] == "Good evening."


def test_speaker_rows_include_unidentified():
    rows = build_speaker_rows(_meeting())
    assert len(rows) == 2
    by_label = {r["speaker_label"]: r for r in rows}
    assert by_label["SPEAKER_01"]["politician_slug"] is None


def test_people_rows_only_for_slugged_speakers():
    rows = build_people_rows(_meeting(), body_slug=None)
    assert len(rows) == 1
    assert rows[0]["politician_slug"] == "asare-isabel"
    assert rows[0]["display_name"] == "Council President Asare"
