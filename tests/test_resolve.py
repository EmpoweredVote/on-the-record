# tests/test_resolve.py
from __future__ import annotations

from src.resolve import ResolvedSource, resolve_source


def test_resolved_source_defaults():
    rs = ResolvedSource(audio_url="https://x/ep.mp3")
    assert rs.audio_url == "https://x/ep.mp3"
    assert rs.title is None
    assert rs.date is None
    assert rs.outlet is None
    assert rs.description is None
    assert rs.image_url is None
    assert rs.transcript is None
    assert rs.resolver == ""


def test_resolve_source_returns_none_for_unhandled_url():
    # No resolver applies to a plain YouTube URL — it stays on the yt-dlp path.
    assert resolve_source("https://youtube.com/watch?v=abc", fetch=lambda u: "") is None
