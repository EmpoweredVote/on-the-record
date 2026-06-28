"""Stage 1: Audio ingestion and normalization."""

from __future__ import annotations

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
