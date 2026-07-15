"""NPR/Brightspot CMS resolver.

Public-radio stations on NPR's Brightspot platform (ipm.org, kuer.org, ...)
publish an episode as an article page with JSON-LD metadata, an og:image, a
direct MP3 on NPR's distribution CDN (cpa.ds.npr.org), and often the cleaned
transcript in the article body. Parsing is pure; network lives behind fetch.
"""
from __future__ import annotations

import json
import re

# Episode @type varies by station: RadioEpisode (IPM), PodcastEpisode (KUER),
# or a generic AudioObject. Date is read from the NewsArticle block.
_EPISODE_TYPES = {"RadioEpisode", "PodcastEpisode", "AudioObject"}


def _iter_jsonld(html: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict):
                yield obj


def _og(html: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]*property=["\']og:{prop}["\'][^>]*content=["\']([^"\']*)["\']',
        html, re.I,
    )
    return m.group(1) if m else None


def parse_jsonld_meta(html: str) -> dict:
    """Extract {date, title, outlet, image, description} from JSON-LD + og tags.

    Date comes from NewsArticle.datePublished (the episode block often omits it);
    title from any episode block or the NewsArticle headline; outlet, image and
    description from og tags.
    """
    title = None
    date = None
    for obj in _iter_jsonld(html):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in _EPISODE_TYPES for x in types) and obj.get("name"):
            title = title or obj["name"]
        if "NewsArticle" in types:
            date = date or (obj.get("datePublished") or "")[:10] or None
            title = title or obj.get("headline")
    return {
        "date": date,
        "title": title,
        "outlet": _og(html, "site_name"),
        "image": _og(html, "image"),
        "description": _og(html, "description"),
    }
