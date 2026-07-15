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
