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
