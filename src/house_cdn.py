"""Resolve a US House floor session date to its public Clerk CDN stream.

The Clerk's live site (live.house.gov) resolves a session via
`GET {LIVEPROXY}/broadcastevents/<YYYYMMDD>` -> a schema.org BroadcastEvent
JSON-LD whose `asset.files[]` lists HLS/DASH/WebVTT URLs in east+central CDN
mirrors. We take the HLS (`manifest.m3u8`) east mirror -- `publish.resolve_playback`
maps `.m3u8` to the `hls` playback kind, which the web FilePlayer plays. The video
is public-domain (Title 17 Section 105). Pure except the injected `fetch`.
"""
from __future__ import annotations

import json
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

_LIVEPROXY = "https://liveproxy-azapp-prod-eastus2-003.azurewebsites.net"
_CITATION = "https://live.house.gov/?date={date}"


@dataclass
class HouseFloorSource:
    date: str            # "2026-07-16"
    manifest_url: str    # HLS east manifest, #t= hash stripped
    title: str
    congress: str
    session: str
    start: str
    end: str
    citation_url: str
    rights: str


def _default_fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=30) as r:  # noqa: S310 (fixed gov host)
        return r.read().decode("utf-8")


def _pick_hls(files: list) -> Optional[str]:
    hls = [f for f in files if (f.get("type") or "").upper() == "HLS" and f.get("url")]
    if not hls:
        return None
    east = next((f for f in hls if "/east/" in f["url"]), None)
    chosen = east or hls[0]
    return chosen["url"].split("#", 1)[0]  # strip the #t=<offset> hash


def resolve_session(
    date: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
) -> Optional[HouseFloorSource]:
    """Resolve "YYYY-MM-DD" -> HouseFloorSource, or None if unavailable."""
    event_id = date.replace("-", "")
    try:
        raw = fetch(f"{_LIVEPROXY}/broadcastevents/{event_id}")
        doc = json.loads(raw)
    except Exception:
        return None
    events = doc if isinstance(doc, list) else [doc]
    if not events:
        return None
    ev = events[0]
    manifest = _pick_hls((ev.get("asset") or {}).get("files") or [])
    if not manifest:
        return None
    se = ev.get("superEvent") or {}
    return HouseFloorSource(
        date=date,
        manifest_url=manifest,
        title=ev.get("name", ""),
        congress=str(se.get("congressNum", "")),
        session=str(se.get("sessionNum", "")),
        start=ev.get("startDate", ""),
        end=ev.get("endDate", ""),
        citation_url=_CITATION.format(date=date),
        rights=ev.get("rights", ""),
    )
