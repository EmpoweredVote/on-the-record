from unittest import mock
from pathlib import Path
from src import download


def test_is_hls_url():
    assert download.is_hls_url("https://x.azurefd.net/east/T/manifest.m3u8")
    assert not download.is_hls_url("https://x/video.mp4")
    assert not download.is_hls_url("https://youtube.com/watch?v=abc")


def test_download_from_url_routes_m3u8_to_ffmpeg(tmp_path, monkeypatch):
    calls = {}
    def fake_run(cmd, *a, **k):
        calls["cmd"] = cmd
        out = cmd[-1]  # implemented as: ffmpeg ... <out.wav> (out is the last arg)
        Path(out).write_bytes(b"RIFF")  # simulate ffmpeg producing the wav
        class R:
            returncode = 0
            stderr = ""
        return R()
    monkeypatch.setattr(download.subprocess, "run", fake_run)
    out = tmp_path / "audio.wav"
    url = "https://houseliveprod.azurefd.net/east/T/manifest.m3u8"
    res = download.download_from_url(url, str(out))
    cmd = calls["cmd"]
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd and url in cmd
    assert "-vn" in cmd  # audio only
    assert str(res).endswith(".wav")
