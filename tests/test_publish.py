"""Tests for the meetings.* publisher: playback resolution helpers.

The row-building logic was refactored in commit dd9b26e into cursor-bound
upserts (``_upsert_meeting`` / ``_upsert_speakers`` / ``_replace_segments``)
that need a live Postgres connection, so the old pure ``build_*_row`` helpers
no longer exist. Their unit tests were removed with them. The pure URL
helpers below survived the refactor unchanged and remain worth covering.
"""

import pytest

from src.models import Meeting
from src.publish import _upsert_meeting
from src.publish import extract_youtube_id, resolve_playback


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


class RecordingCursor:
    def __init__(self, select_row):
        self.select_row = select_row
        self.calls = []
        self._fetchone = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT id FROM meetings.meetings" in sql:
            self._fetchone = self.select_row
        elif "RETURNING id" in sql:
            self._fetchone = ("new-uuid",)

    def fetchone(self):
        return self._fetchone


@pytest.mark.parametrize("existing_row", [("existing-uuid",), None])
def test_upsert_meeting_writes_title_and_event_kind(existing_row):
    cur = RecordingCursor(existing_row)
    meeting = Meeting(
        meeting_id="ca-governor-debate",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        title="California Governor Debate",
        event_kind="debate",
    )

    _upsert_meeting(cur, meeting, None)

    write_sql, write_params = cur.calls[1]
    assert "title" in write_sql
    assert "event_kind" in write_sql
    assert "California Governor Debate" in write_params
    assert "debate" in write_params
