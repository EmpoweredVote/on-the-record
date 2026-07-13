from __future__ import annotations

import shutil
import wave
from pathlib import Path

import pytest


def _write_silent_wav(path: Path, seconds: float = 0.5, rate: int = 16000) -> None:
    """A tiny valid mono 16-bit PCM WAV, no ffmpeg needed to create it."""
    frames = int(seconds * rate)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(b"\x00\x00" * frames)


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_compress_audio_to_opus_produces_nonempty_file(tmp_path):
    from src.cleanup import compress_audio_to_opus

    wav = tmp_path / "audio.wav"
    _write_silent_wav(wav)
    opus = tmp_path / "audio.opus"

    result = compress_audio_to_opus(wav, opus)

    assert result == opus
    assert opus.exists() and opus.stat().st_size > 0


def test_compress_audio_to_opus_raises_without_ffmpeg(tmp_path, monkeypatch):
    from src import cleanup

    monkeypatch.setattr(cleanup.shutil, "which", lambda _: None)
    with pytest.raises(RuntimeError, match="ffmpeg"):
        cleanup.compress_audio_to_opus(tmp_path / "a.wav", tmp_path / "a.opus")


def test_compress_audio_to_opus_raises_on_ffmpeg_error(tmp_path, monkeypatch):
    import subprocess

    from src import cleanup

    monkeypatch.setattr(cleanup.shutil, "which", lambda _: "/usr/bin/ffmpeg")

    def boom(*args, **kwargs):
        raise subprocess.CalledProcessError(1, "ffmpeg", stderr=b"Invalid data found")

    monkeypatch.setattr(cleanup.subprocess, "run", boom)
    with pytest.raises(RuntimeError, match="Invalid data found"):
        cleanup.compress_audio_to_opus(tmp_path / "a.wav", tmp_path / "a.opus")


def test_pipeline_state_persists_media_cleaned(tmp_path):
    from src.checkpoint import PipelineState

    ps = PipelineState(tmp_path)
    assert ps.media_cleaned is False  # default
    ps.media_cleaned = True
    ps.save()

    reloaded = PipelineState(tmp_path)
    assert reloaded.media_cleaned is True


def _fake_compress(wav_path, opus_path, bitrate="32k"):
    Path(opus_path).write_bytes(b"OPUSDATA")
    return Path(opus_path)


def _finalized_meeting(mdir):
    (mdir / "transcript_named.json").write_text("{}")
    (mdir / "audio.wav").write_bytes(b"0" * 1000)
    (mdir / "source.mp4").write_bytes(b"0" * 5000)


def test_cleanup_meeting_compresses_and_deletes(tmp_meetings_dir, monkeypatch):
    from src import cleanup

    mdir = tmp_meetings_dir / "2026-02-04-council"
    mdir.mkdir()
    _finalized_meeting(mdir)
    monkeypatch.setattr(cleanup, "compress_audio_to_opus", _fake_compress)

    result = cleanup.cleanup_meeting("2026-02-04-council")

    assert result["status"] == "cleaned"
    assert result["reclaimed_bytes"] == 6000  # wav + video
    assert (mdir / "audio.opus").exists()
    assert not (mdir / "audio.wav").exists()
    assert not (mdir / "source.mp4").exists()


def test_cleanup_meeting_is_idempotent(tmp_meetings_dir, monkeypatch):
    from src import cleanup

    mdir = tmp_meetings_dir / "2026-02-04-council"
    mdir.mkdir()
    _finalized_meeting(mdir)
    monkeypatch.setattr(cleanup, "compress_audio_to_opus", _fake_compress)

    cleanup.cleanup_meeting("2026-02-04-council")
    second = cleanup.cleanup_meeting("2026-02-04-council")

    assert second["status"] == "already_clean"
    assert second["reclaimed_bytes"] == 0


def test_cleanup_meeting_refuses_unfinalized(tmp_meetings_dir, monkeypatch):
    from src import cleanup

    mdir = tmp_meetings_dir / "half-done"
    mdir.mkdir()
    (mdir / "audio.wav").write_bytes(b"0" * 1000)  # no transcript_named.json
    monkeypatch.setattr(cleanup, "compress_audio_to_opus", _fake_compress)

    result = cleanup.cleanup_meeting("half-done")

    assert result["status"] == "not_finalized"
    assert (mdir / "audio.wav").exists()  # nothing deleted


def test_cleanup_meeting_unknown_id(tmp_meetings_dir):
    from src import cleanup

    assert cleanup.cleanup_meeting("ghost")["status"] == "not_found"
    assert cleanup.cleanup_meeting("../escape")["status"] == "not_found"
    assert cleanup.cleanup_meeting(".")["status"] == "not_found"


def test_backfill_all_cleans_finalized_skips_others(tmp_meetings_dir, monkeypatch):
    from src import cleanup

    done = tmp_meetings_dir / "done"
    done.mkdir()
    (done / "transcript_named.json").write_text("{}")
    (done / "audio.wav").write_bytes(b"0" * 1000)
    (done / "source.mp4").write_bytes(b"0" * 4000)

    partial = tmp_meetings_dir / "partial"
    partial.mkdir()
    (partial / "audio.wav").write_bytes(b"0" * 1000)  # no transcript

    monkeypatch.setattr(cleanup, "compress_audio_to_opus", _fake_compress)

    results = cleanup.backfill_all()

    by_id = {r["meeting_id"]: r for r in results}
    assert by_id["done"]["status"] == "cleaned"
    assert by_id["done"]["reclaimed_bytes"] == 5000
    assert by_id["partial"]["status"] == "not_finalized"
    assert not (done / "source.mp4").exists()
    assert (partial / "audio.wav").exists()
