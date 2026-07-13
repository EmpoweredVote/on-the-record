"""Manual post-finalization media cleanup.

Shrinks a processed meeting's on-disk footprint: compress audio.wav -> audio.opus
(small, kept as durable provenance evidence) and delete the source video + WAV.
Never touches the download/ingest hot path. Triggered only manually (CLI + GUI).
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path

from src import config

logger = logging.getLogger(__name__)

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
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError(
            f"ffmpeg failed to compress {wav_path} -> {opus_path}: {stderr[-500:]}"
        ) from exc
    return opus_path


def _is_safe_meeting_id(meeting_id: str) -> bool:
    return (
        bool(meeting_id)
        and meeting_id not in (".", "..")
        and "/" not in meeting_id
        and "\\" not in meeting_id
        and ".." not in meeting_id
    )


def cleanup_meeting(meeting_id: str) -> dict:
    """Compress audio and delete the source video + WAV for one finalized meeting.

    Returns {"meeting_id", "status", "reclaimed_bytes"}. Statuses:
      not_found | not_finalized | no_audio | compress_failed | cleaned | already_clean
    Fail-safe: never deletes anything unless audio.opus exists and is non-empty.
    Idempotent: re-running a clean meeting is a no-op ("already_clean").
    """
    base = {"meeting_id": meeting_id, "reclaimed_bytes": 0}
    if not _is_safe_meeting_id(meeting_id):
        return {**base, "status": "not_found"}

    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not meeting_dir.is_dir():
        return {**base, "status": "not_found"}
    if not (meeting_dir / "transcript_named.json").exists():
        return {**base, "status": "not_finalized"}

    wav = meeting_dir / "audio.wav"
    opus = meeting_dir / "audio.opus"

    opus_ready = opus.exists() and opus.stat().st_size > 0
    if not opus_ready:
        if not wav.exists():
            return {**base, "status": "no_audio"}
        compress_audio_to_opus(wav, opus)
        if not (opus.exists() and opus.stat().st_size > 0):
            return {**base, "status": "compress_failed"}

    reclaimed = 0
    for ext in _VIDEO_EXTS:
        video = meeting_dir / f"source{ext}"
        if video.exists():
            reclaimed += video.stat().st_size
            video.unlink()
    if wav.exists():
        reclaimed += wav.stat().st_size
        wav.unlink()

    _mark_cleaned(meeting_dir)
    status = "cleaned" if reclaimed > 0 else "already_clean"
    return {**base, "status": status, "reclaimed_bytes": reclaimed}


def _mark_cleaned(meeting_dir: Path) -> None:
    """Best-effort persist of media_cleaned=True; never blocks the deletion result."""
    try:
        from src.checkpoint import PipelineState

        ps = PipelineState(meeting_dir)
        ps.media_cleaned = True
        ps.save()
    except Exception as exc:  # best-effort: never block the deletion result
        logger.warning("failed to persist media_cleaned for %s: %s", meeting_dir, exc)
