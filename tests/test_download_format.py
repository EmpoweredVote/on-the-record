"""yt-dlp now requests a capped-resolution VIDEO stream so review clips exist."""
from __future__ import annotations

from src import download


def test_ytdlp_format_requests_video():
    fmt = download._ytdlp_format()
    assert "bestvideo" in fmt
    assert "height<=480" in fmt
    assert fmt != "bestaudio/best"


def test_direct_download_sends_user_agent(monkeypatch, tmp_path):
    """Direct (non-yt-dlp) downloads must send a browser UA; podcast CDNs
    (e.g. Buzzsprout enclosures) 403 header-less clients."""
    captured = {}

    class _Resp:
        headers = {"content-length": "3"}
        def raise_for_status(self): pass
        def iter_content(self, chunk_size=8192): yield b"abc"

    def _fake_get(url, stream=False, timeout=None, headers=None):
        captured["headers"] = headers
        return _Resp()

    monkeypatch.setattr(download.requests, "get", _fake_get)
    out = tmp_path / "f.mp3"
    download.download_from_url("https://cdn.example.com/ep.mp3", out, progress=False)
    assert captured["headers"].get("User-Agent")
