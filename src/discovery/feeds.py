"""Watchlist feed fetching/parsing: YouTube channel Atom + podcast RSS.

Parsing is pure (string in, RawItems out); only _fetch_text touches the
network. web_page outlets are a registered-but-unpolled kind in v1.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime

import requests

from src.discovery.models import Outlet, RawItem

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}


def _fetch_text(url: str) -> str:
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def parse_youtube_feed(xml_text: str, *, outlet_id: "str | None" = None) -> list:
    root = ET.fromstring(xml_text)
    items = []
    for entry in root.findall("atom:entry", _NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=_NS)
        link = entry.find("atom:link[@rel='alternate']", _NS)
        url = link.get("href") if link is not None else (
            f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        if not url:
            continue
        items.append(RawItem(
            url=url,
            title=entry.findtext("atom:title", default=None, namespaces=_NS),
            description=entry.findtext("media:group/media:description",
                                       default=None, namespaces=_NS),
            channel_name=entry.findtext("atom:author/atom:name",
                                        default=None, namespaces=_NS),
            channel_id=entry.findtext("yt:channelId", default=None, namespaces=_NS),
            channel_url=entry.findtext("atom:author/atom:uri",
                                       default=None, namespaces=_NS),
            published_at=entry.findtext("atom:published", default=None, namespaces=_NS),
            outlet_id=outlet_id,
            via="watchlist",
        ))
    return items


def parse_podcast_feed(xml_text: str, *, outlet_id: "str | None" = None) -> list:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall("./channel/item"):
        page = item.findtext("link")
        if not page:
            guid = item.find("guid")
            if guid is not None and guid.get("isPermaLink", "true").lower() == "true":
                text = (guid.text or "").strip()
                if text.startswith(("http://", "https://")):
                    page = text
        enclosure = item.find("enclosure")
        url = page or (enclosure.get("url") if enclosure is not None else None)
        if not url:
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).isoformat()
            except (TypeError, ValueError):
                published = None
        items.append(RawItem(
            url=url,
            title=item.findtext("title"),
            description=item.findtext("description"),
            channel_name=root.findtext("./channel/title"),
            published_at=published,
            outlet_id=outlet_id,
            via="watchlist",
        ))
    return items


def fetch_outlet_items(outlet: Outlet) -> list:
    """Network + parse for one outlet. Raises on HTTP/parse errors — the
    engine catches per-outlet and keeps going."""
    if outlet.kind == "youtube_channel":
        return parse_youtube_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    if outlet.kind == "podcast_rss":
        return parse_podcast_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    return []  # web_page: registered but not polled in v1
