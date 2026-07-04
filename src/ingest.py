"""Stage 1: Audio ingestion and normalization."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from . import config
from .audio_utils import (
    apply_noise_reduction,
    check_ffmpeg_installed,
    get_audio_duration,
    load_wav,
)

# A line counts as a chapter only if its first non-whitespace token is a
# timestamp (MM:SS or HH:MM:SS), followed by whitespace and a non-empty title.
_TIMESTAMP_LINE_RE = re.compile(r"^\s*((?:\d{1,2}:)?\d{1,2}:\d{2})\s+(\S.*?)\s*$")


def _timestamp_to_seconds(ts: str) -> float:
    """Parse 'MM:SS' or 'HH:MM:SS' into float seconds."""
    parts = [int(p) for p in ts.split(":")]
    if len(parts) == 3:
        h, m, s = parts
        return float(h * 3600 + m * 60 + s)
    m, s = parts
    return float(m * 60 + s)


def parse_description_chapters(description: str | None) -> list[dict]:
    """Extract timestamped agenda lines from a video description.

    Only lines whose first non-whitespace token is a timestamp are treated as
    chapters. Requires at least 2 such lines, otherwise returns []. Each result
    is {start_time, end_time, title}; end_time is the next entry's start_time
    (None for the last entry).
    """
    if not description:
        return []

    parsed: list[dict] = []
    for line in description.splitlines():
        match = _TIMESTAMP_LINE_RE.match(line)
        if match:
            parsed.append({
                "start_time": _timestamp_to_seconds(match.group(1)),
                "title": match.group(2).strip(),
            })

    if len(parsed) < 2:
        return []

    for i, chap in enumerate(parsed):
        chap["end_time"] = parsed[i + 1]["start_time"] if i + 1 < len(parsed) else None

    return parsed


def _drop_intro_chapters(chapters: list[dict]) -> list[dict]:
    """Remove intro-type entries — a chapter starting at 0:00 is almost always a
    cold open / branding card, not an agenda item."""
    return [c for c in chapters if c.get("start_time") != 0.0]


def normalize_chapters(info: dict) -> list[dict]:
    """Build a normalized chapter list from a yt-dlp info dict.

    Prefers the creator's formal chapters (info["chapters"]); when absent/empty,
    falls back to timestamped lines in the description. Intro-type entries
    (start_time 0:00) are dropped from either source. Each result is
    {start_time: float, end_time: float | None, title: str}.
    """
    raw = info.get("chapters") or []
    normalized: list[dict] = []
    if raw:
        for c in raw:
            start = c.get("start_time")
            if start is None:
                continue
            normalized.append({
                "start_time": float(start),
                "end_time": float(c["end_time"]) if c.get("end_time") is not None else None,
                "title": (c.get("title") or "").strip(),
            })

    if not normalized:
        normalized = parse_description_chapters(info.get("description"))

    return _drop_intro_chapters(normalized)


def _is_url(path: str) -> bool:
    """Check if a string looks like a URL."""
    try:
        parsed = urlparse(str(path))
        return parsed.scheme in ("http", "https")
    except Exception:
        return False


def _normalize_cmd(
    ffmpeg_input: str,
    output_path: str,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> list[str]:
    """Build the ffmpeg arg list for normalizing (and optionally clipping) audio.

    `-ss`/`-to` are placed AFTER `-i` (output-side, decode-accurate seek) so the
    cut is frame-exact and the persisted clip_start is authoritative — never a
    keyframe-rounded approximation.
    """
    cmd = ["ffmpeg", "-y", "-i", ffmpeg_input]
    if clip_start is not None:
        cmd += ["-ss", str(clip_start)]
    if clip_end is not None:
        cmd += ["-to", str(clip_end)]
    cmd += [
        "-ac", str(config.CHANNELS),
        "-ar", str(config.SAMPLE_RATE),
        "-vn",
        str(output_path),
    ]
    return cmd


def normalize_audio(
    input_path: str | Path,
    output_path: str | Path,
    noise_reduce: bool = False,
    cookies_file: str | None = None,
    clip_start: float | None = None,
    clip_end: float | None = None,
) -> dict:
    """Normalize audio to 16kHz mono WAV via ffmpeg.

    Accepts a local file path or a URL. If a URL is provided, the video is
    downloaded first, then normalized. Supports YouTube, Facebook (via yt-dlp),
    CATS TV page URLs, and any direct video URL.

    Args:
        input_path: Source audio/video file path or URL.
        output_path: Destination WAV file path.
        noise_reduce: If True, apply spectral-gating noise reduction after conversion.
        cookies_file: Path to a Netscape-format cookies file for authenticated
            yt-dlp downloads (e.g. private Facebook videos).
        clip_start: Optional start time in seconds for output-side accurate seek.
        clip_end: Optional end time in seconds for output-side accurate seek.

    Returns:
        Metadata dict with source, output path, duration, and whether noise reduction was applied.
    """
    if not check_ffmpeg_installed():
        raise RuntimeError("ffmpeg is not installed or not on PATH")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source_str = str(input_path)

    # Download from URL if needed
    source_title = None
    if _is_url(source_str):
        from .download import download_from_url, is_ytdlp_url

        # Use a placeholder stem; yt-dlp may change the extension
        download_path = output_path.parent / "source.mp4"
        print(f"  Downloading from URL...")
        actual_path = download_from_url(source_str, download_path, cookies_file=cookies_file)
        ffmpeg_input = str(actual_path)

        if is_ytdlp_url(source_str):
            try:
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                    info = ydl.extract_info(source_str, download=False)
                    source_title = info.get("title") or None
            except Exception:
                pass
    else:
        ffmpeg_input = str(Path(input_path))

    subprocess.run(
        _normalize_cmd(ffmpeg_input, str(output_path), clip_start, clip_end),
        check=True,
        capture_output=True,
    )

    if noise_reduce:
        import soundfile as sf

        samples, sr = load_wav(output_path)
        cleaned = apply_noise_reduction(samples, sr)
        sf.write(str(output_path), cleaned, sr)

    duration = get_audio_duration(output_path)

    return {
        "source": source_str,
        "output": str(output_path),
        "duration_seconds": duration,
        "sample_rate": config.SAMPLE_RATE,
        "channels": config.CHANNELS,
        "noise_reduced": noise_reduce,
        "clip_start_seconds": clip_start,
        "clip_end_seconds": clip_end,
        "source_title": source_title,
    }
