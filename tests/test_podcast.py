# tests/test_podcast.py
from __future__ import annotations

from src.podcast import discover_feed_url


def test_discover_feed_url_finds_autodiscovery_link():
    html = """
    <html><head>
      <link rel="alternate" type="application/rss+xml"
            title="My Show" href="https://feeds.buzzsprout.com/1414123.rss">
    </head><body></body></html>
    """
    assert discover_feed_url(html, "https://show.buzzsprout.com/ep") == \
        "https://feeds.buzzsprout.com/1414123.rss"


def test_discover_feed_url_resolves_relative_href():
    html = '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
    assert discover_feed_url(html, "https://show.example.com/episodes/5") == \
        "https://show.example.com/feed.xml"


def test_discover_feed_url_none_when_absent():
    assert discover_feed_url("<html><head></head></html>", "https://x/") is None
