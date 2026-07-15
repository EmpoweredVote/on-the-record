"""Podcast RSS resolver: episode page -> autodiscovered feed -> matched item.

Handles any host that advertises its feed via standard RSS autodiscovery
(Buzzsprout, Omny, Libsyn, Simplecast, Megaphone, ...). Audio-only: never
returns a transcript. Parsing is pure; network lives behind an injected fetch.
"""
from __future__ import annotations

from urllib.parse import urljoin, urlparse


def discover_feed_url(html: str, base_url: str) -> str | None:
    """Return the RSS feed URL advertised by <link rel=alternate rss>, or None."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    link = soup.find(
        "link",
        rel=lambda v: v and "alternate" in v,
        type="application/rss+xml",
    )
    if not link or not link.get("href"):
        return None
    return urljoin(base_url, link["href"])


def _entry_date(entry) -> str | None:
    """feedparser struct_time -> 'YYYY-MM-DD', or None."""
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not st:
        return None
    return f"{st.tm_year:04d}-{st.tm_mon:02d}-{st.tm_mday:02d}"


def _entry_audio_url(entry) -> str | None:
    """First audio enclosure URL for a feed entry, or None."""
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href and (enc.get("type", "").startswith("audio") or href.lower().endswith((".mp3", ".m4a"))):
            return href
    for link in getattr(entry, "links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


def _entry_image(entry) -> str | None:
    img = getattr(entry, "image", None)
    if img and img.get("href"):
        return img["href"]
    return None


def parse_feed_entries(feed_text: str):
    """Parse an RSS feed. Returns (show_dict, [entry_dict, ...]).

    show_dict: {title, author, image}.
    entry_dict: {title, link, guid, audio_url, date ('YYYY-MM-DD'|None),
                 description, image}.
    """
    import feedparser

    parsed = feedparser.parse(feed_text)
    feed = parsed.feed
    show = {
        "title": feed.get("title"),
        "author": feed.get("author") or feed.get("itunes_author"),
        "image": (feed.get("image") or {}).get("href"),
    }
    entries = []
    for e in parsed.entries:
        entries.append({
            "title": e.get("title"),
            "link": e.get("link"),
            "guid": e.get("id") or e.get("guid"),
            "audio_url": _entry_audio_url(e),
            "date": _entry_date(e),
            "description": (e.get("summary") or e.get("description") or ""),
            "image": _entry_image(e),
        })
    return show, entries


def _norm(u: str | None) -> str:
    """Normalize a URL/guid for comparison: drop scheme, query, trailing slash."""
    if not u:
        return ""
    s = u.strip()
    try:
        p = urlparse(s)
    except ValueError:
        return s.rstrip("/")
    if p.scheme in ("http", "https"):
        return f"{p.netloc.lower()}{p.path.rstrip('/')}"
    return s.rstrip("/")


def match_entry(entries, page_url: str):
    """Return the entry whose link or guid matches page_url, else None."""
    target = _norm(page_url)
    if not target:
        return None
    for e in entries:
        if _norm(e.get("link")) == target or _norm(e.get("guid")) == target:
            return e
    return None


def resolve_podcast_episode(page_url: str, *, fetch):
    """Resolve an episode page URL to a ResolvedSource, or None if not a podcast.

    Returns None when no feed is discovered, or when the feed contains no item
    matching the pasted URL (e.g. a site-wide feed or a whole-show landing
    page — the deferred 'pick from feed list' case). Never trusts autodiscovery
    blindly: success requires a matching <item> with an audio enclosure.
    """
    from .resolve import ResolvedSource

    try:
        page_html = fetch(page_url)
    except Exception:
        return None

    feed_url = discover_feed_url(page_html, page_url)
    if not feed_url:
        return None

    try:
        feed_text = fetch(feed_url)
    except Exception:
        return None

    show, entries = parse_feed_entries(feed_text)
    entry = match_entry(entries, page_url)
    if not entry or not entry.get("audio_url"):
        return None

    return ResolvedSource(
        audio_url=entry["audio_url"],
        title=entry.get("title"),
        date=entry.get("date"),
        outlet=show.get("title") or show.get("author"),
        description=entry.get("description"),
        image_url=entry.get("image") or show.get("image"),
        transcript=None,
        resolver="podcast",
    )
