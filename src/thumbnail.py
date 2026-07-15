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

import requests

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


def download_image(url: str, out_path: Path) -> Optional[Path]:
    """Download an image to out_path; return it, or None on failure."""
    try:
        resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        Path(out_path).write_bytes(resp.content)
    except Exception as exc:
        logger.warning("artwork download failed: %s", exc)
        return None
    return Path(out_path) if Path(out_path).exists() else None


VIDEO_EXTENSIONS = (".m4v", ".mp4", ".mkv", ".webm", ".avi", ".mov")


def find_video_file(meeting_dir: Path, original_input: str) -> Optional[str]:
    """Find the video file for a meeting, checking the meeting directory first.

    Returns path to the video file, or None if not found.
    """
    meeting_dir = Path(meeting_dir)
    # Downloaded source video in the meeting dir (source.m4v, source.mp4, ...).
    for ext in VIDEO_EXTENSIONS:
        candidate = meeting_dir / f"source{ext}"
        if candidate.exists():
            return str(candidate)

    # Otherwise the original local input, if it still exists.
    if original_input and not original_input.startswith(("http://", "https://")):
        p = Path(original_input)
        if p.exists() and p.suffix.lower() in VIDEO_EXTENSIONS:
            return str(p)

    return None


def attach_thumbnail(meeting, meeting_dir) -> None:
    """Best-effort: extract a frame from the kept section, upload it, and set
    ``meeting.thumbnail_url``. Never raises — a thumbnail must not break
    publishing. Called by both the terminal (run_local) and GUI publish paths.
    """
    try:
        from src.storage import upload_thumbnail

        video_path = find_video_file(meeting_dir, meeting.audio_source)
        out = Path(meeting_dir) / "thumbnail.jpg"
        if not video_path:
            # Audio-only source: use the resolver-provided artwork, if any.
            processing_metadata = getattr(meeting, "processing_metadata", None)
            image_url = getattr(processing_metadata, "source_image_url", None)
            if not image_url:
                return
            if download_image(image_url, out):
                url = upload_thumbnail(out, meeting.meeting_id)
                if url:
                    meeting.thumbnail_url = url
                    logger.info("Thumbnail (artwork): %s", url)
            return
        if extract_thumbnail(
            video_path, meeting.clip_start_seconds, meeting.duration_seconds, out
        ):
            url = upload_thumbnail(out, meeting.meeting_id)
            if url:
                meeting.thumbnail_url = url
                logger.info("Thumbnail: %s", url)
    except Exception as exc:  # absolutely non-fatal
        logger.warning("thumbnail step failed — %s", exc)
