"""Extract a representative thumbnail frame from a meeting's source video.

Best-effort: every function returns None / no-ops on failure so the pipeline
never breaks over a missing thumbnail.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

EXTRACT_TIMEOUT_SECONDS = 120


def thumbnail_seek_start(
    clip_start: Optional[float], clip_duration: Optional[float]
) -> float:
    """Seconds into the FULL source video to start scanning for a frame.

    Skips a short way into the kept section (past the intro), capped at 10s.
    ``clip_duration`` is the kept-section length (the clipped audio duration).
    """
    base = clip_start or 0.0
    dur = clip_duration or 0.0
    return base + min(10.0, 0.10 * dur)


def extract_thumbnail(
    video_path: str,
    clip_start: Optional[float],
    clip_duration: Optional[float],
    out_path: Path,
) -> Optional[Path]:
    """Write a JPEG thumbnail to ``out_path``; return it, or None on failure.

    Uses ffmpeg's ``thumbnail`` filter to auto-pick a representative frame
    (skips black/fade/flat frames) from a batch starting at the seek point.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not on PATH; skipping thumbnail extraction")
        return None

    seek = thumbnail_seek_start(clip_start, clip_duration)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(seek),
        "-i", str(video_path),
        "-vf", "thumbnail=n=300,scale=640:-2",
        "-frames:v", "1",
        "-q:v", "3",
        str(out_path),
    ]
    try:
        subprocess.run(
            cmd, check=True, capture_output=True, timeout=EXTRACT_TIMEOUT_SECONDS
        )
    except (subprocess.CalledProcessError, OSError, subprocess.TimeoutExpired) as exc:
        stderr = getattr(exc, "stderr", None)
        detail = stderr.decode("utf-8", "ignore")[:500] if stderr else str(exc)
        logger.warning("thumbnail extraction failed: %s", detail)
        return None

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    return None
