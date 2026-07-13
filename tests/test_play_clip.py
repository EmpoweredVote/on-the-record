"""play_speaker_clip launches a non-blocking, looping player and returns the handle."""
from __future__ import annotations

import run_local


class _FakeProc:
    def __init__(self):
        self.terminated = False
    def poll(self):
        return None  # still running
    def terminate(self):
        self.terminated = True
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self.terminated = True


def test_play_speaker_clip_uses_video_nonblocking(monkeypatch):
    captured = {}
    fake = _FakeProc()
    monkeypatch.setattr(run_local.subprocess, "Popen", lambda cmd, **kw: captured.__setitem__("cmd", cmd) or fake)
    handle = run_local.play_speaker_clip("/m/source.mp4", "/m/audio.wav", 30.0, duration=10.0, title="x")
    assert handle is fake                       # returns the Popen handle (non-blocking)
    assert captured["cmd"][-1] == "/m/source.mp4"
    assert "-loop" in captured["cmd"] and "0" in captured["cmd"]
    assert "-nodisp" not in captured["cmd"]     # video → display on


def test_play_speaker_clip_audio_fallback_nodisp(monkeypatch):
    captured = {}
    fake = _FakeProc()
    monkeypatch.setattr(run_local.subprocess, "Popen", lambda cmd, **kw: captured.__setitem__("cmd", cmd) or fake)
    handle = run_local.play_speaker_clip(None, "/m/audio.wav", 30.0, duration=10.0)
    assert handle is fake
    assert captured["cmd"][-1] == "/m/audio.wav"
    assert "-nodisp" in captured["cmd"]


def test_play_speaker_clip_no_media_returns_none(monkeypatch, capsys):
    spawned = {"n": 0}
    monkeypatch.setattr(run_local.subprocess, "Popen", lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1))
    handle = run_local.play_speaker_clip(None, None, 30.0)
    assert handle is None
    assert spawned["n"] == 0
    assert "no media" in capsys.readouterr().out.lower()


def test_play_speaker_clip_ffplay_missing_returns_none(monkeypatch, capsys):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(run_local.subprocess, "Popen", boom)
    handle = run_local.play_speaker_clip("/m/source.mp4", None, 30.0)
    assert handle is None
    assert "ffplay not found" in capsys.readouterr().out.lower()


def test_stop_player_terminates_running(monkeypatch):
    fake = _FakeProc()
    run_local._stop_player(fake)
    assert fake.terminated is True
    # tolerant of None
    run_local._stop_player(None)


def test_review_audio_path_prefers_wav_then_opus(tmp_path):
    import run_local

    assert run_local._review_audio_path(tmp_path) is None

    (tmp_path / "audio.opus").write_bytes(b"OPUS")
    assert run_local._review_audio_path(tmp_path) == str(tmp_path / "audio.opus")

    (tmp_path / "audio.wav").write_bytes(b"RIFF")
    assert run_local._review_audio_path(tmp_path) == str(tmp_path / "audio.wav")
