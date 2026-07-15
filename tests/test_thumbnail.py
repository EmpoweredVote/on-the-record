import shutil
import subprocess
from pathlib import Path

import pytest

from src.thumbnail import (
    thumbnail_seek_start,
    extract_thumbnail,
    find_video_file,
    attach_thumbnail,
)


class _FakeMeeting:
    def __init__(self, meeting_dir):
        self.audio_source = ""
        self.clip_start_seconds = None
        self.duration_seconds = 60.0
        self.meeting_id = "2026-02-04-council"
        self.thumbnail_url = None


def test_find_video_file_prefers_source_in_dir(tmp_path: Path):
    (tmp_path / "source.webm").write_bytes(b"x")
    assert find_video_file(tmp_path, "https://youtu.be/abc") == str(tmp_path / "source.webm")


def test_find_video_file_falls_back_to_local_input(tmp_path: Path):
    local = tmp_path / "clip.mp4"; local.write_bytes(b"x")
    assert find_video_file(tmp_path / "empty", str(local)) == str(local)


def test_find_video_file_none_when_absent(tmp_path: Path):
    assert find_video_file(tmp_path, "https://youtu.be/abc") is None


def test_attach_thumbnail_extracts_and_sets_url(tmp_path: Path, monkeypatch):
    (tmp_path / "source.webm").write_bytes(b"x")
    m = _FakeMeeting(tmp_path)
    import src.thumbnail as th
    monkeypatch.setattr(th, "extract_thumbnail",
                        lambda vp, cs, cd, out: out)          # pretend extraction worked
    monkeypatch.setattr("src.storage.upload_thumbnail",
                        lambda jpg, mid: "https://cdn/thumb.jpg")
    attach_thumbnail(m, tmp_path)
    assert m.thumbnail_url == "https://cdn/thumb.jpg"


def test_attach_thumbnail_no_video_is_noop(tmp_path: Path):
    m = _FakeMeeting(tmp_path)
    attach_thumbnail(m, tmp_path)                             # no source.* present
    assert m.thumbnail_url is None


def test_attach_thumbnail_never_raises(tmp_path: Path, monkeypatch):
    (tmp_path / "source.webm").write_bytes(b"x")
    m = _FakeMeeting(tmp_path)
    import src.thumbnail as th
    def boom(*a, **k):
        raise RuntimeError("ffmpeg exploded")
    monkeypatch.setattr(th, "extract_thumbnail", boom)
    attach_thumbnail(m, tmp_path)                             # must swallow the error
    assert m.thumbnail_url is None


def test_seek_no_clip_seeks_up_to_ten_percent():
    # No clip window: clip_start is 0, so seek ~10% in, capped at 10s.
    assert thumbnail_seek_start(None, 60.0) == pytest.approx(6.0)
    assert thumbnail_seek_start(0.0, 300.0) == pytest.approx(10.0)  # capped


def test_seek_with_clip_offsets_from_clip_start():
    # 40s kept section starting 120s into the source: 120 + min(10, 4) = 124.
    assert thumbnail_seek_start(120.0, 40.0) == pytest.approx(124.0)


def test_seek_zero_duration_is_clip_start():
    assert thumbnail_seek_start(90.0, 0.0) == pytest.approx(90.0)
    assert thumbnail_seek_start(None, None) == pytest.approx(0.0)


ffmpeg_missing = shutil.which("ffmpeg") is None


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not installed")
def test_extract_thumbnail_writes_a_jpeg(tmp_path: Path):
    # Synthesize a 3s test video so the test is self-contained.
    src = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=3:size=320x240:rate=10",
         str(src)],
        check=True, capture_output=True,
    )
    out = tmp_path / "thumbnail.jpg"
    result = extract_thumbnail(str(src), None, 3.0, out)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


def test_extract_thumbnail_missing_video_returns_none(tmp_path: Path):
    out = tmp_path / "thumbnail.jpg"
    result = extract_thumbnail(str(tmp_path / "nope.mp4"), None, 10.0, out)
    assert result is None
    assert not out.exists()


def test_extract_thumbnail_no_ffmpeg_returns_none(tmp_path: Path, monkeypatch):
    monkeypatch.setattr("src.thumbnail.shutil.which", lambda _: None)
    out = tmp_path / "thumbnail.jpg"
    assert extract_thumbnail("whatever.mp4", None, 10.0, out) is None


# add to tests/test_thumbnail.py
from src.thumbnail import download_image


def test_download_image_writes_file(tmp_path, monkeypatch):
    import src.thumbnail as th

    class _Resp:
        content = b"\xff\xd8jpegbytes"
        def raise_for_status(self): pass

    monkeypatch.setattr(th, "requests", type("R", (), {"get": staticmethod(lambda *a, **k: _Resp())}))
    out = tmp_path / "art.jpg"
    assert download_image("https://cdn/art.jpg", out) == out
    assert out.read_bytes() == b"\xff\xd8jpegbytes"


def test_attach_thumbnail_uses_artwork_when_no_video(tmp_path, monkeypatch):
    import src.thumbnail as th

    # No video file present.
    monkeypatch.setattr(th, "find_video_file", lambda d, s: None)
    monkeypatch.setattr(th, "download_image", lambda url, out: Path(out).write_bytes(b"x") or Path(out))
    monkeypatch.setattr("src.storage.upload_thumbnail", lambda path, mid: "https://bucket/thumb.jpg")

    class _M:
        audio_source = "https://show/ep"
        clip_start_seconds = None
        duration_seconds = 60.0
        meeting_id = "2026-07-15-podcast"
        thumbnail_url = None
        class processing_metadata:
            source_image_url = "https://cdn/art.jpg"

    m = _M()
    th.attach_thumbnail(m, tmp_path)
    assert m.thumbnail_url == "https://bucket/thumb.jpg"
