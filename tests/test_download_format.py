"""yt-dlp now requests a capped-resolution VIDEO stream so review clips exist."""
from __future__ import annotations

import pytest

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


def _captured_ydl_opts(monkeypatch, call) -> dict:
    """Run *call* with yt_dlp.YoutubeDL stubbed out; return the opts it was handed."""
    yt_dlp = pytest.importorskip("yt_dlp")
    captured: dict = {}

    class _FakeYDL:
        def __init__(self, opts):
            captured.update(opts)
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def extract_info(self, url, download=True): return {"ext": "mp4"}
        def download(self, urls): return 0

    monkeypatch.setattr(yt_dlp, "YoutubeDL", _FakeYDL)
    call()
    monkeypatch.undo()  # restore the real class for the probe below
    return captured


def _enabled_js_runtimes(opts: dict) -> dict:
    """What a REAL YoutubeDL ends up enabling for these opts.

    Asserting through yt-dlp itself (rather than on our literal dict) is what
    catches the option being silently ignored.
    """
    yt_dlp = pytest.importorskip("yt_dlp")
    probe = {k: v for k, v in opts.items() if k not in ("outtmpl", "progress_hooks")}
    with yt_dlp.YoutubeDL({**probe, "simulate": True}) as ydl:
        return ydl.params.get("js_runtimes") or {}


def test_ytdlp_video_download_enables_node_runtime(monkeypatch, tmp_path):
    """`js_runtimes` is a top-level YoutubeDL param, NOT a youtube extractor arg.

    Passing it under extractor_args is silently dropped, leaving yt-dlp on its
    deno-only default -> "No supported JavaScript runtime could be found",
    fewer player clients, and missing/403 formats.
    """
    out = tmp_path / "video.mp4"
    out.write_bytes(b"x")  # the downloader asserts the produced file exists

    opts = _captured_ydl_opts(
        monkeypatch,
        lambda: download.download_via_ytdlp(
            "https://www.youtube.com/watch?v=abc", out, progress=False
        ),
    )
    assert "node" in _enabled_js_runtimes(opts)


def test_ytdlp_caption_download_enables_node_runtime(monkeypatch, tmp_path):
    opts = _captured_ydl_opts(
        monkeypatch,
        lambda: download.download_captions_via_ytdlp(
            "https://www.youtube.com/watch?v=abc", tmp_path / "c.vtt"
        ),
    )
    assert "node" in _enabled_js_runtimes(opts)
