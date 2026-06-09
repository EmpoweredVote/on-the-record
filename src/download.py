"""Download meeting videos from URLs and CATS TV archive."""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import requests

CATSTV_BASE_URL = "https://catstv.net/government.php"
CATSTV_BLOB_BASE = "https://catstv.blob.core.windows.net/videoarchive"

# Timeout for downloads (connect, read) in seconds
_CONNECT_TIMEOUT = 30
_READ_TIMEOUT = 600  # 10 minutes for large video files

# Domains handled by yt-dlp rather than direct requests
_YTDLP_DOMAINS = {
    "youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
    "facebook.com", "www.facebook.com", "m.facebook.com", "fb.com", "fb.watch",
}


def _is_ytdlp_url(url: str) -> bool:
    """Return True if the URL should be downloaded via yt-dlp."""
    try:
        return urlparse(url).netloc.lower() in _YTDLP_DOMAINS
    except Exception:
        return False


# Public alias used by run_local.py caption download logic.
is_ytdlp_url = _is_ytdlp_url


def _ytdlp_format() -> str:
    """yt-dlp format string: a capped (~480p) video+audio stream.

    Capped resolution keeps downloads modest — clips only need to show a face —
    while still producing a playable source video for the review step. Falls back
    to best available if the capped combo is unavailable.
    """
    return "bestvideo[height<=480]+bestaudio/best[height<=480]/best"


def download_via_ytdlp(
    url: str,
    output_path: str | Path,
    cookies_file: str | None = None,
    progress: bool = True,
) -> Path:
    """Download a video via yt-dlp (YouTube, Facebook, and 1000+ other sites).

    Downloads a capped-resolution video+audio stream (so the source video is
    available for review clips). The returned path may have a different extension
    than ``output_path`` depending on what yt-dlp selects.

    Args:
        url: YouTube, Facebook, or any yt-dlp-supported URL.
        output_path: Desired output path (stem is used as the filename template).
        cookies_file: Path to a Netscape-format cookies file for authenticated downloads.
        progress: If True, show yt-dlp progress output.

    Returns:
        Path to the downloaded file (extension may differ from output_path).
    """
    try:
        import yt_dlp
    except ImportError:
        raise RuntimeError(
            "yt-dlp is not installed. Run: pip install yt-dlp"
        )

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Use the stem as the output template; yt-dlp appends the real extension
    template = str(output_path.parent / output_path.stem) + ".%(ext)s"

    ydl_opts: dict = {
        "format": _ytdlp_format(),
        "outtmpl": template,
        "quiet": not progress,
        "no_warnings": not progress,
    }
    if cookies_file:
        ydl_opts["cookiefile"] = str(cookies_file)

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        ext = info.get("ext", "mp4")

    actual_path = output_path.parent / f"{output_path.stem}.{ext}"
    if not actual_path.exists():
        raise RuntimeError(
            f"yt-dlp reported success but file not found: {actual_path}"
        )

    return actual_path


def download_from_url(
    url: str,
    output_path: str | Path,
    cookies_file: str | None = None,
    progress: bool = True,
) -> Path:
    """Download a video file from a URL.

    Supports:
    - YouTube and Facebook URLs (routed through yt-dlp)
    - CATS TV page URLs (blob URL extracted automatically)
    - Any other direct video URL (mp4, m4v, mkv, etc.)

    Args:
        url: Video URL (YouTube, Facebook, CATS TV page, or direct file URL).
        output_path: Local path to save the downloaded file.
        cookies_file: Path to a Netscape-format cookies file (used by yt-dlp for
            authenticated downloads, e.g. private Facebook videos).
        progress: If True, print download progress.

    Returns:
        Path to the downloaded file (may differ from output_path for yt-dlp downloads).
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    if _is_ytdlp_url(url):
        return download_via_ytdlp(url, output_path, cookies_file=cookies_file, progress=progress)

    # If it's a CATS TV page URL, resolve to the blob URL
    resolved = _resolve_video_url(url)

    resp = requests.get(resolved, stream=True, timeout=(_CONNECT_TIMEOUT, _READ_TIMEOUT))
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    chunk_size = 8192

    with open(output_path, "wb") as f:
        for chunk in resp.iter_content(chunk_size=chunk_size):
            f.write(chunk)
            downloaded += len(chunk)
            if progress and total > 0:
                pct = (downloaded / total) * 100
                mb = downloaded / (1024 * 1024)
                total_mb = total / (1024 * 1024)
                print(f"\r  Downloading: {mb:.1f}/{total_mb:.1f} MB ({pct:.0f}%)", end="", flush=True)

    if progress:
        print()  # newline after progress

    return output_path


def _resolve_video_url(url: str) -> str:
    """If url is a CATS TV page, extract the blob video URL. Otherwise return as-is."""
    parsed = urlparse(url)

    # Already a direct blob URL
    if "catstv.blob.core.windows.net" in parsed.netloc:
        return url

    # CATS TV page URL — scrape the video filename
    if "catstv.net" in parsed.netloc:
        return _extract_blob_url_from_page(url)

    # Any other direct URL — return as-is
    return url


def _extract_blob_url_from_page(page_url: str) -> str:
    """Scrape a CATS TV page to find the video blob URL.

    The page loads videos via JavaScript with data-m4v attributes or
    inline jPlayer config. We try both approaches.
    """
    from bs4 import BeautifulSoup

    resp = requests.get(page_url, timeout=(_CONNECT_TIMEOUT, 60))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    # Try 1: Find jPlayer config in inline script (default video for the page)
    for script in soup.find_all("script"):
        text = script.string or ""
        match = re.search(r'm4v:\s*["\']([^"\']+\.m4v)["\']', text)
        if match:
            m4v = match.group(1)
            if m4v.startswith("http"):
                return m4v
            return f"{CATSTV_BLOB_BASE}/{m4v}"

    # Try 2: Find data-m4v attributes on result links
    link = soup.find("a", attrs={"data-m4v": True})
    if link:
        m4v = link["data-m4v"]
        if m4v.startswith("http"):
            return m4v
        return f"{CATSTV_BLOB_BASE}/{m4v}"

    raise ValueError(
        f"Could not find a video URL on the CATS TV page: {page_url}\n"
        "Try using a direct blob URL instead (https://catstv.blob.core.windows.net/videoarchive/...)."
    )


# ---------------------------------------------------------------------------
# CATS TV Meeting Browser
# ---------------------------------------------------------------------------

def fetch_catstv_meetings(search_url: str | None = None) -> list[dict]:
    """Scrape CATS TV archive and return a list of available meetings.

    Each meeting dict contains:
        - name: Meeting title
        - subtitle: Additional description
        - date: Meeting date string
        - duration: Duration string
        - m4v: Filename on blob storage
        - video_url: Full blob download URL
        - permalink: CATS TV permalink
        - has_agenda: Whether an agenda link exists
        - documents_url: Link to meeting documents

    Args:
        search_url: CATS TV search URL. Defaults to the full government archive.

    Returns:
        List of meeting dicts sorted by date (newest first).
    """
    from bs4 import BeautifulSoup

    if search_url is None:
        search_url = f"{CATSTV_BASE_URL}?issearch=govt"

    resp = requests.get(search_url, timeout=(_CONNECT_TIMEOUT, 60))
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    meetings = []
    for link in soup.find_all("a", attrs={"data-m4v": True}):
        m4v = link.get("data-m4v", "")
        if not m4v:
            continue

        video_url = f"{CATSTV_BLOB_BASE}/{m4v}" if not m4v.startswith("http") else m4v

        # Derive VTT URL: same path as m4v but with .vtt extension
        vtt_url = ""
        m4v_base = m4v.rsplit(".", 1)[0] if "." in m4v else m4v
        if m4v_base:
            vtt_candidate = f"{m4v_base}.vtt"
            if not vtt_candidate.startswith("http"):
                vtt_url = f"{CATSTV_BLOB_BASE}/{vtt_candidate}"
            else:
                vtt_url = vtt_candidate

        meetings.append({
            "name": link.get("data-name", "").strip(),
            "subtitle": link.get("data-subtitle", "").strip(),
            "date": link.get("data-date", "").strip(),
            "duration": link.get("data-duration", "").strip(),
            "m4v": m4v,
            "video_url": video_url,
            "vtt_url": vtt_url,
            "permalink": link.get("data-permalink", "").strip(),
            "has_agenda": link.get("data-hasagenda", "").lower() == "true",
            "documents_url": link.get("data-documentsurl", "").strip(),
        })

    return meetings


def download_captions_via_ytdlp(
    url: str,
    vtt_output_path: str | Path,
    languages: list[str] | None = None,
) -> "Path | None":
    """Download closed captions for a YouTube / Facebook / etc. URL.

    Tries manual subtitles first (higher quality), then auto-generated captions.
    Saves the best matching English track as a WebVTT file at *vtt_output_path*.

    Returns the saved path, or None if no captions are available or yt-dlp is
    not installed.
    """
    try:
        import yt_dlp
    except ImportError:
        return None

    vtt_output_path = Path(vtt_output_path)
    vtt_output_path.parent.mkdir(parents=True, exist_ok=True)
    out_dir = vtt_output_path.parent

    if languages is None:
        languages = ["en", "en-US", "en-GB"]

    # Output template: yt-dlp appends the language code before the extension,
    # e.g. "captions.en.vtt" or "captions.en-US.vtt".
    template = str(out_dir / "captions.%(ext)s")

    ydl_opts: dict = {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": True,
        "subtitlesformat": "vtt",
        "subtitleslangs": languages,
        "outtmpl": template,
        "quiet": True,
        "no_warnings": True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            ydl.download([url])
    except Exception:
        return None

    # yt-dlp names the file "captions.<lang>.vtt"; rename to the desired path.
    for lang in languages:
        candidate = out_dir / f"captions.{lang}.vtt"
        if candidate.exists():
            candidate.rename(vtt_output_path)
            return vtt_output_path

    # Some extractors write directly without the language suffix.
    direct = out_dir / "captions.vtt"
    if direct.exists() and direct != vtt_output_path:
        direct.rename(vtt_output_path)
        return vtt_output_path

    if vtt_output_path.exists():
        return vtt_output_path

    return None


def extract_catstv_vtt_url(url: str) -> str | None:
    """Find the VTT caption URL for a CATS TV video.

    For page URLs (catstv.net/...), scrapes the transcript section for a .vtt
    link. For blob URLs, probes the _subtitles.vtt convention then plain .vtt.
    Returns the URL string, or None if not found.
    """
    from bs4 import BeautifulSoup

    parsed = urlparse(url)

    if "catstv.net" in parsed.netloc:
        try:
            resp = requests.get(url, timeout=(_CONNECT_TIMEOUT, 60))
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            # Prefer the dedicated transcript section
            transcript_div = soup.find(class_="video-transcript")
            if transcript_div:
                link = transcript_div.find("a", href=re.compile(r"\.vtt$", re.I))
                if link:
                    return link["href"]
            # Fallback: any link on the page ending in .vtt
            for a in soup.find_all("a", href=re.compile(r"\.vtt$", re.I)):
                return a["href"]
        except Exception:
            pass
        return None

    if "catstv.blob.core.windows.net" in parsed.netloc:
        filename = parsed.path.rsplit("/", 1)[-1]
        base_no_ext = filename.rsplit(".", 1)[0] if "." in filename else filename
        dir_url = url.rsplit("/", 1)[0]
        for candidate in (f"{base_no_ext}_subtitles.vtt", f"{base_no_ext}.vtt"):
            candidate_url = f"{dir_url}/{candidate}"
            try:
                r = requests.head(candidate_url, timeout=(_CONNECT_TIMEOUT, 30))
                if r.status_code == 200:
                    return candidate_url
            except Exception:
                continue

    return None


def download_vtt(vtt_url: str, output_path: str | Path) -> Path | None:
    """Download a VTT subtitle file. Returns path or None on failure."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        resp = requests.get(vtt_url, timeout=(_CONNECT_TIMEOUT, 60))
        if resp.status_code == 200 and resp.text.strip().startswith("WEBVTT"):
            output_path.write_text(resp.text, encoding="utf-8")
            return output_path
        return None
    except Exception:
        return None


def display_catstv_meetings(meetings: list[dict], limit: int = 25) -> None:
    """Print a numbered table of meetings for user selection."""
    shown = meetings[:limit]
    print(f"{'#':>4}  {'Date':<12} {'Duration':<10} Title")
    print(f"{'─'*4}  {'─'*12} {'─'*10} {'─'*50}")
    for i, m in enumerate(shown):
        title = m["name"]
        if m["subtitle"]:
            title += f" — {m['subtitle']}"
        if len(title) > 60:
            title = title[:57] + "..."
        print(f"{i:>4}  {m['date']:<12} {m['duration']:<10} {title}")

    if len(meetings) > limit:
        print(f"\n  ... and {len(meetings) - limit} more. Pass a larger limit to see all.")
