"""play_speaker_clip uses video when present, else audio.wav (no afplay lie)."""
from __future__ import annotations

import run_local


def test_play_speaker_clip_uses_video(monkeypatch):
    captured = {}
    monkeypatch.setattr(run_local.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd))
    run_local.play_speaker_clip("/m/source.mp4", "/m/audio.wav", 30.0, duration=10.0, title="x")
    assert captured["cmd"][-1] == "/m/source.mp4"
    assert "-nodisp" not in captured["cmd"]  # video → display on


def test_play_speaker_clip_falls_back_to_audio(monkeypatch):
    captured = {}
    monkeypatch.setattr(run_local.subprocess, "run", lambda cmd, **kw: captured.setdefault("cmd", cmd))
    run_local.play_speaker_clip(None, "/m/audio.wav", 30.0, duration=10.0, title="x")
    assert captured["cmd"][-1] == "/m/audio.wav"
    assert "-nodisp" in captured["cmd"]  # audio-only → no display window


def test_play_speaker_clip_no_media(monkeypatch, capsys):
    called = {"ran": False}
    monkeypatch.setattr(run_local.subprocess, "run", lambda *a, **k: called.__setitem__("ran", True))
    run_local.play_speaker_clip(None, None, 30.0)
    assert called["ran"] is False
    assert "no media" in capsys.readouterr().out.lower()
