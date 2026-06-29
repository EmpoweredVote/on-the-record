import shutil
import subprocess
from pathlib import Path

import pytest

from src.thumbnail import thumbnail_seek_start, extract_thumbnail


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
