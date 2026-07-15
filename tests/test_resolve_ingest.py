from __future__ import annotations

from pathlib import Path

import pytest

from src import ingest
from src.resolve import ResolvedSource


@pytest.fixture
def _stub_ffmpeg(monkeypatch, tmp_path):
    # Make ffmpeg checks/commands no-ops and give a fake duration.
    monkeypatch.setattr(ingest, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "get_audio_duration", lambda p: 123.0)


def test_normalize_audio_uses_resolved_metadata_and_saves_reference(
    monkeypatch, tmp_path, _stub_ffmpeg
):
    resolved = ResolvedSource(
        audio_url="https://cdn/ep.mp3",
        title="Ep 1",
        date="2026-06-03",
        outlet="What's Next LA",
        description="00:30 Intro\n02:10 Zoning\nGuest talks housing",
        image_url="https://cdn/art.jpg",
        transcript="Host: hi.\n\nGuest: hello.",
        resolver="podcast",
    )
    monkeypatch.setattr(ingest, "_resolve_source_safe", lambda url: resolved)

    downloaded = {}
    def _fake_download(url, out, cookies_file=None, progress=True):
        downloaded["url"] = url
        Path(out).write_bytes(b"x")
        return Path(out)
    # download_from_url is imported inside normalize_audio from .download
    import src.download as dl
    monkeypatch.setattr(dl, "download_from_url", _fake_download)

    out = tmp_path / "audio.wav"
    meta = ingest.normalize_audio("https://show/ep-1", out)

    assert downloaded["url"] == "https://cdn/ep.mp3"          # enclosure, not page
    assert meta["source_title"] == "Ep 1"
    assert meta["source_channel"] == "What's Next LA"
    assert meta["source_upload_date"] == "2026-06-03"
    assert meta["source_image_url"] == "https://cdn/art.jpg"
    assert meta["source_description"].startswith("00:30 Intro")
    # chapters parsed from the description
    assert any(c["title"] == "Zoning" for c in meta["source_chapters"])
    # reference transcript written next to the wav
    assert (out.parent / "reference_transcript.txt").read_text().startswith("Host: hi.")


def test_normalize_audio_drops_zero_intro_chapter_on_resolver_path(
    monkeypatch, tmp_path, _stub_ffmpeg
):
    from src.resolve import ResolvedSource
    resolved = ResolvedSource(
        audio_url="https://cdn/ep.mp3",
        description="0:00 Intro\n01:00 Real Topic\n02:30 Another",
        resolver="podcast",
    )
    monkeypatch.setattr(ingest, "_resolve_source_safe", lambda url: resolved)
    import src.download as dl
    monkeypatch.setattr(dl, "download_from_url",
                        lambda url, out, cookies_file=None, progress=True: (Path(out).write_bytes(b"x"), Path(out))[1])
    out = tmp_path / "audio.wav"
    meta = ingest.normalize_audio("https://show/ep", out)
    titles = [c["title"] for c in meta["source_chapters"]]
    assert "Intro" not in titles          # 0:00 entry dropped
    assert "Real Topic" in titles
    assert "Another" in titles


def test_normalize_audio_fallback_path_preserves_cookies_and_ytdlp_meta(
    monkeypatch, tmp_path, _stub_ffmpeg
):
    monkeypatch.setattr(ingest, "_resolve_source_safe", lambda url: None)

    captured = {}
    def _fake_download(url, out, cookies_file=None, progress=True):
        captured["url"] = url
        captured["cookies_file"] = cookies_file
        Path(out).write_bytes(b"x")
        return Path(out)
    import src.download as dl
    monkeypatch.setattr(dl, "download_from_url", _fake_download)
    monkeypatch.setattr(dl, "is_ytdlp_url", lambda u: True)
    monkeypatch.setattr(ingest, "fetch_source_metadata", lambda u: {
        "title": "YT Title", "channel": "YT Chan",
        "upload_date": "2026-01-02", "chapters": [], "duration": 10,
    })

    out = tmp_path / "audio.wav"
    meta = ingest.normalize_audio("https://youtube.com/watch?v=abc", out,
                                  cookies_file="/tmp/cookies.txt")

    assert captured["url"] == "https://youtube.com/watch?v=abc"   # original URL, not an enclosure
    assert captured["cookies_file"] == "/tmp/cookies.txt"          # cookies still passed
    assert meta["source_title"] == "YT Title"                      # yt-dlp metadata fetched
    assert meta["source_channel"] == "YT Chan"
    assert meta["source_upload_date"] == "2026-01-02"
    assert meta["source_image_url"] is None                        # resolver-only fields stay None
    assert meta["source_description"] is None


def test_normalize_audio_surfaces_resolved_enclosure_url(monkeypatch, tmp_path, _stub_ffmpeg):
    from src.resolve import ResolvedSource
    resolved = ResolvedSource(audio_url="https://cdn/ep.mp3", resolver="podcast")
    monkeypatch.setattr(ingest, "_resolve_source_safe", lambda url: resolved)
    import src.download as dl
    monkeypatch.setattr(dl, "download_from_url",
                        lambda url, out, cookies_file=None, progress=True: (Path(out).write_bytes(b"x"), Path(out))[1])
    meta = ingest.normalize_audio("https://show/ep", tmp_path / "audio.wav")
    assert meta["source_audio_url"] == "https://cdn/ep.mp3"


def test_normalize_audio_no_resolved_enclosure_for_plain_url(monkeypatch, tmp_path, _stub_ffmpeg):
    monkeypatch.setattr(ingest, "_resolve_source_safe", lambda url: None)
    import src.download as dl
    monkeypatch.setattr(dl, "download_from_url",
                        lambda url, out, cookies_file=None, progress=True: (Path(out).write_bytes(b"x"), Path(out))[1])
    monkeypatch.setattr(dl, "is_ytdlp_url", lambda u: False)
    meta = ingest.normalize_audio("https://example.com/x.mp4", tmp_path / "audio.wav")
    assert meta["source_audio_url"] is None
