from __future__ import annotations

from src import ingest


class _FakeYDL:
    """Context-manager stand-in for yt_dlp.YoutubeDL."""

    def __init__(self, info):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _patch_ydl(monkeypatch, info):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda opts: _FakeYDL(info))


def test_fetch_source_metadata_maps_fields(monkeypatch):
    _patch_ydl(monkeypatch, {
        "title": "City Council Feb 10",
        "uploader": "CBS Evening News",
        "upload_date": "20260210",
        "duration": 3600,
        "chapters": [],
    })
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta["title"] == "City Council Feb 10"
    assert meta["channel"] == "CBS Evening News"
    assert meta["upload_date"] == "2026-02-10"
    assert meta["duration"] == 3600
    assert meta["chapters"] == []


def test_fetch_source_metadata_channel_fallback(monkeypatch):
    # No uploader → fall back to channel.
    _patch_ydl(monkeypatch, {"title": "t", "channel": "WFYI", "upload_date": ""})
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta["channel"] == "WFYI"


def test_fetch_source_metadata_missing_and_malformed(monkeypatch):
    _patch_ydl(monkeypatch, {"upload_date": "2026"})  # too short → None
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta["title"] is None
    assert meta["channel"] is None
    assert meta["upload_date"] is None
    assert meta["duration"] is None
    assert meta["chapters"] == []


def test_fetch_source_metadata_swallows_extractor_error(monkeypatch):
    _patch_ydl(monkeypatch, RuntimeError("private video"))
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta == {
        "title": None, "channel": None, "upload_date": None,
        "duration": None, "chapters": [],
    }


def test_fetch_source_metadata_none_info(monkeypatch):
    _patch_ydl(monkeypatch, None)
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta == {
        "title": None, "channel": None, "upload_date": None,
        "duration": None, "chapters": [],
    }
