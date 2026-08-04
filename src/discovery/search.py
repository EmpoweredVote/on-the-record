"""Per-race YouTube search sweeps (yt-dlp ytsearch, flat) + item hydration."""
from __future__ import annotations

import random
import time

from src import config
from src.discovery.models import RawItem
from src.ingest import fetch_source_metadata

SEARCH_TERMS = ("debate", "forum", "town hall", "interview")

RETRYABLE_MARKERS = ("429", "too many requests", "sign in to confirm", "bot")


def with_backoff(fn, *, retries: "int | None" = None,
                 base_delay: "float | None" = None, sleep_fn=time.sleep):
    """Run fn(); on a retryable yt-dlp error (bot-check / rate limit) retry
    with exponential backoff + jitter. Non-retryable errors and the final
    failure propagate — the engine's per-query handler stays the decider,
    and a hard bot-check wave still exits 1 without resetting the cadence
    clock (record_sweep skips failed sweeps)."""
    tries = retries if retries is not None else config.DISCOVERY_BACKOFF_RETRIES
    base = base_delay if base_delay is not None else config.DISCOVERY_BACKOFF_BASE_SECONDS
    for attempt in range(tries + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001 — filtered by marker below
            msg = str(exc).lower()
            if attempt >= tries or not any(m in msg for m in RETRYABLE_MARKERS):
                raise
            sleep_fn(base * (3 ** attempt) * (0.5 + random.random()))


def queries_for_candidate(full_name: str) -> list:
    return [f'"{full_name}" {term}' for term in SEARCH_TERMS]


def ytsearch(query: str, *, limit: "int | None" = None) -> list:
    """Flat search — one network request, no per-video page fetches.
    Extractor errors (and a missing yt_dlp) propagate: the caller (the
    engine's per-query try/except) is what decides a search failure is
    non-fatal and loud, so it must actually see the exception rather than
    have it silently laundered into an empty, indistinguishable result."""
    n = limit or config.DISCOVERY_SEARCH_RESULTS_PER_QUERY
    import yt_dlp
    opts = {
        "quiet": True, "no_warnings": True, "skip_download": True,
        "extract_flat": "in_playlist",
        "js_runtimes": {"node": {}},
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{n}:{query}", download=False)
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
