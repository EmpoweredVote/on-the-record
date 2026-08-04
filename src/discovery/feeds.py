"""Watchlist feed fetching/parsing: YouTube channel Atom + podcast RSS + generic news RSS/Atom (web_rss).

Parsing is pure (string in, RawItems out); only _fetch_text touches the
network. web_page outlets are a registered-but-unpolled kind in v1.
"""
from __future__ import annotations

import html as html_
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from src import config
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


_robots_cache: dict = {}


def _robots_allowed(url: str, fetch_text_fn=None) -> bool:
    """Mechanical robots.txt respect for every web_rss fetch (spec Q8).
    Missing/unreachable robots.txt (the common case) means allowed."""
    fetch = fetch_text_fn or _fetch_text
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    if origin not in _robots_cache:
        rp = RobotFileParser()
        try:
            rp.parse(fetch(f"{origin}/robots.txt").splitlines())
        except Exception:  # noqa: BLE001 — no robots.txt -> allowed
            rp = None
        _robots_cache[origin] = rp
    rp = _robots_cache[origin]
    return True if rp is None else rp.can_fetch("*", url)


_ATOM_NS = "{http://www.w3.org/2005/Atom}"


def parse_news_feed(xml_text: str, *, outlet_id: "str | None" = None,
                    outlet_name: "str | None" = None) -> list:
    """Generic news feed: RSS 2.0 or Atom. Items carry no duration — the
    stage-1 duration heuristics skip them and stage 2 owns depth."""
    root = ET.fromstring(xml_text)
    items = []
    if root.tag == f"{_ATOM_NS}feed":
        channel = root.findtext(f"{_ATOM_NS}title") or outlet_name
        for entry in root.findall(f"{_ATOM_NS}entry"):
            link = entry.find(f"{_ATOM_NS}link[@rel='alternate']")
            if link is None:
                link = entry.find(f"{_ATOM_NS}link")
            url = link.get("href") if link is not None else None
            if not url:
                continue
            items.append(RawItem(
                url=url,
                title=entry.findtext(f"{_ATOM_NS}title"),
                description=(entry.findtext(f"{_ATOM_NS}summary")
                             or entry.findtext(f"{_ATOM_NS}content")),
                channel_name=channel,
                published_at=(entry.findtext(f"{_ATOM_NS}published")
                              or entry.findtext(f"{_ATOM_NS}updated")),
                outlet_id=outlet_id, via="watchlist"))
        return items
    channel = root.findtext("./channel/title") or outlet_name
    for item in root.findall("./channel/item"):
        url = (item.findtext("link") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).isoformat()
            except (TypeError, ValueError):
                published = None
        items.append(RawItem(
            url=url, title=item.findtext("title"),
            description=item.findtext("description"),
            channel_name=channel, published_at=published,
            outlet_id=outlet_id, via="watchlist"))
    return items


_SCRIPT_RE = re.compile(r"<(script|style)[\s\S]*?</\1>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")


def fetch_page_text(url: str, max_chars: int = 6000) -> str:
    """Article-page text for the stage-2 page peek (web analog of the
    captions peek). Robots-gated like every web fetch; returns '' when
    disallowed."""
    if not _robots_allowed(url):
        return ""
    raw = _fetch_text(url)
    text = _TAG_RE.sub(" ", _SCRIPT_RE.sub(" ", raw))
    text = html_.unescape(text)
    return re.sub(r"\s+", " ", text).strip()[:max_chars]


def fetch_outlet_items(outlet: Outlet) -> list:
    """Network + parse for one outlet. Raises on HTTP/parse errors — the
    engine catches per-outlet and keeps going."""
    if outlet.kind == "youtube_channel":
        return parse_youtube_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    if outlet.kind == "podcast_rss":
        return parse_podcast_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    if outlet.kind == "web_rss":
        if not _robots_allowed(outlet.feed_url):
            raise RuntimeError(f"robots.txt disallows {outlet.feed_url}")
        text = _fetch_text(outlet.feed_url)
        time.sleep(config.DISCOVERY_WEB_FETCH_SLEEP_SECONDS)  # per-domain politeness
        return parse_news_feed(text, outlet_id=outlet.id, outlet_name=outlet.name)
    if outlet.kind == "web_page":
        return []  # registered but unpolled (v1 behavior, unchanged)
    raise ValueError(f"unknown outlet kind {outlet.kind!r} for {outlet.name}")
