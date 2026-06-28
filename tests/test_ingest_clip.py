"""ffmpeg command construction for clip windows."""

from src.ingest import _normalize_cmd


def test_normalize_cmd_no_clip_has_no_seek_flags():
    cmd = _normalize_cmd("in.mp4", "out.wav", clip_start=None, clip_end=None)
    assert "-ss" not in cmd and "-to" not in cmd
    assert cmd[:3] == ["ffmpeg", "-y", "-i"]
    assert cmd[-1] == "out.wav"


def test_normalize_cmd_clip_uses_accurate_seek_after_input():
    cmd = _normalize_cmd("in.mp4", "out.wav", clip_start=1380.0, clip_end=2880.0)
    i = cmd.index("-i")
    ss = cmd.index("-ss")
    to = cmd.index("-to")
    assert ss > i and to > i
    assert cmd[ss + 1] == "1380.0"
    assert cmd[to + 1] == "2880.0"


def test_normalize_cmd_start_only():
    cmd = _normalize_cmd("in.mp4", "out.wav", clip_start=1380.0, clip_end=None)
    assert "-ss" in cmd and "-to" not in cmd
