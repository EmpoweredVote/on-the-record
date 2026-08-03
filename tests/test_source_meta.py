from __future__ import annotations

from src import ingest


class _FakeYDL:
    """Context-manager stand-in for yt_dlp.YoutubeDL."""

    captured_opts = None

    def __init__(self, info, opts=None):
        self._info = info
        _FakeYDL.captured_opts = opts

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

    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda opts: _FakeYDL(info, opts))


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
        "description": None, "channel_id": None, "channel_url": None,
    }


def test_fetch_source_metadata_none_info(monkeypatch):
    _patch_ydl(monkeypatch, None)
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta == {
        "title": None, "channel": None, "upload_date": None,
        "duration": None, "chapters": [],
        "description": None, "channel_id": None, "channel_url": None,
    }


def test_fetch_source_metadata_includes_description_and_channel_identity(monkeypatch):
    _patch_ydl(monkeypatch, {
        "title": "T", "uploader": "KXAN", "upload_date": "20260801",
        "duration": 3480, "description": "Full debate.",
        "channel_id": "UCabc", "channel_url": "https://www.youtube.com/channel/UCabc",
    })
    meta = ingest.fetch_source_metadata("https://www.youtube.com/watch?v=x")
    assert meta["description"] == "Full debate."
    assert meta["channel_id"] == "UCabc"
    assert meta["channel_url"] == "https://www.youtube.com/channel/UCabc"


def test_fetch_source_metadata_opts_enable_node_js_runtime(monkeypatch):
    _patch_ydl(monkeypatch, {})
    ingest.fetch_source_metadata("https://www.youtube.com/watch?v=x")
    assert _FakeYDL.captured_opts.get("js_runtimes") == {"node": {}}


def test_fetch_source_metadata_empty_dict_still_has_new_keys(monkeypatch):
    _patch_ydl(monkeypatch, {})
    meta = ingest.fetch_source_metadata("https://www.youtube.com/watch?v=x")
    for key in ("description", "channel_id", "channel_url"):
        assert key in meta and meta[key] is None
