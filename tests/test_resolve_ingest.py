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
