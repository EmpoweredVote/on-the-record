"""Per-race YouTube search sweeps (yt-dlp ytsearch, flat) + item hydration."""
from __future__ import annotations

from src import config
from src.discovery.models import RawItem
from src.ingest import fetch_source_metadata

SEARCH_TERMS = ("debate", "forum", "town hall", "interview")


def queries_for_candidate(full_name: str) -> list:
    return [f'"{full_name}" {term}' for term in SEARCH_TERMS]


def ytsearch(query: str, *, limit: "int | None" = None) -> list:
    """Flat search — one network request, no per-video page fetches.
    Best-effort: any extractor error returns []."""
    n = limit or config.DISCOVERY_SEARCH_RESULTS_PER_QUERY
    try:
        import yt_dlp
        opts = {
            "quiet": True, "no_warnings": True, "skip_download": True,
            "extract_flat": "in_playlist",
            "js_runtimes": {"node": {}},
        }
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
    except Exception:
        return []
    items = []
    for entry in (info or {}).get("entries") or []:
        vid = entry.get("id")
        if not vid or len(vid) != 11:
            continue
        url = entry.get("url") or f"https://www.youtube.com/watch?v={vid}"
        if "watch?v=" not in url and "youtu.be/" not in url:
            url = f"https://www.youtube.com/watch?v={vid}"
        items.append(RawItem(
            url=url,
            title=entry.get("title") or None,
            channel_name=entry.get("channel") or entry.get("uploader") or None,
            channel_id=entry.get("channel_id") or None,
            duration_seconds=int(entry["duration"]) if entry.get("duration") else None,
            via="search",
        ))
    return items


def hydrate_item(item: RawItem) -> RawItem:
    """Fill missing metadata via one yt-dlp metadata fetch (no download).
    Existing values win; hydration only fills gaps."""
    meta = fetch_source_metadata(item.url)
    item.title = item.title or meta.get("title")
    item.description = item.description or meta.get("description")
    item.channel_name = item.channel_name or meta.get("channel")
    item.channel_id = item.channel_id or meta.get("channel_id")
    item.channel_url = item.channel_url or meta.get("channel_url")
    if item.duration_seconds is None and meta.get("duration"):
        item.duration_seconds = int(meta["duration"])
    item.published_at = item.published_at or meta.get("upload_date")
    return item
