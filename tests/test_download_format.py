"""yt-dlp now requests a capped-resolution VIDEO stream so review clips exist."""
from __future__ import annotations

from src import download


def test_ytdlp_format_requests_video():
    fmt = download._ytdlp_format()
    assert "bestvideo" in fmt
    assert "height<=480" in fmt
    assert fmt != "bestaudio/best"
