"""Manual post-finalization media cleanup.

Shrinks a processed meeting's on-disk footprint: compress audio.wav -> audio.opus
(small, kept as durable provenance evidence) and delete the source video + WAV.
Never touches the download/ingest hot path. Triggered only manually (CLI + GUI).
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from src import config

# Mirror the video container set used by review/thumbnail lookups.
_VIDEO_EXTS = (".m4v", ".mp4", ".mkv", ".webm", ".avi", ".mov")


def compress_audio_to_opus(wav_path: Path, opus_path: Path, bitrate: str = "32k") -> Path:
    """Compress a WAV to mono Opus via ffmpeg. Returns opus_path on success.

    32 kbps mono libopus is transparent for speech (incl. overlapping voices) and
    yields ~10-14 MB/hr vs ~115 MB/hr for the 16 kHz WAV. Raises on failure so the
    caller never deletes the WAV when compression did not produce output.
    """
    if shutil.which("ffmpeg") is None:
        raise RuntimeError("ffmpeg is not installed or not on PATH")
    cmd = [
        "ffmpeg", "-y",
        "-i", str(wav_path),
        "-c:a", "libopus",
        "-b:a", bitrate,
        "-ac", str(config.CHANNELS),
        str(opus_path),
    ]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return opus_path
