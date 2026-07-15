"""Podcast RSS resolver: episode page -> autodiscovered feed -> matched item.

Handles any host that advertises its feed via standard RSS autodiscovery
(Buzzsprout, Omny, Libsyn, Simplecast, Megaphone, ...). Audio-only: never
returns a transcript. Parsing is pure; network lives behind an injected fetch.
"""
from __future__ import annotations

from urllib.parse import urljoin


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
