# tests/test_resolve.py
from __future__ import annotations

from src.resolve import ResolvedSource, resolve_source


def test_resolved_source_defaults():
    rs = ResolvedSource(audio_url="https://x/ep.mp3")
    assert rs.audio_url == "https://x/ep.mp3"
    assert rs.title is None
    assert rs.date is None
    assert rs.outlet is None
    assert rs.description is None
    assert rs.image_url is None
    assert rs.transcript is None
    assert rs.resolver == ""


def test_resolve_source_returns_none_for_unhandled_url():
    # No resolver applies to a plain YouTube URL — it stays on the yt-dlp path.
    assert resolve_source("https://youtube.com/watch?v=abc", fetch=lambda u: "") is None


_BRIGHTSPOT_HTML = """<html><head>
<meta property="og:site_name" content="KUER">
<script type="application/ld+json">
{"@type":"NewsArticle","headline":"McAdams interview","datePublished":"2026-06-03T02:00:00Z"}
</script>
<a href="https://cpa.ds.npr.org/s213/audio/2026/06/260603-mcadams.mp3">play</a>
</head><body><article>
<p>This is a one-on-one sit-down with candidate Ben McAdams about the race.</p>
</article></body></html>"""

_PODCAST_PAGE = """<html><head>
<link rel="alternate" type="application/rss+xml" href="https://feeds.x/f.rss">
</head></html>"""

_PODCAST_FEED = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>The Show</title>
<item><title>Ep</title><link>https://omny.fm/shows/the-show/ep</link>
<guid>https://omny.fm/shows/the-show/ep</guid>
<pubDate>Tue, 03 Jun 2026 10:00:00 -0700</pubDate>
<enclosure url="https://cdn/ep.mp3" type="audio/mpeg" length="1"/>
</item></channel></rss>"""


def test_resolve_source_routes_to_brightspot():
    rs = resolve_source("https://www.kuer.org/statestreet/2026-06-03/x",
                        fetch=lambda u: _BRIGHTSPOT_HTML)
    assert rs is not None and rs.resolver == "brightspot"
    assert rs.audio_url.endswith("260603-mcadams.mp3")


def test_resolve_source_routes_to_podcast():
    page = "https://omny.fm/shows/the-show/ep"
    fetch = lambda u: _PODCAST_PAGE if u == page else _PODCAST_FEED
    rs = resolve_source(page, fetch=fetch)
    assert rs is not None and rs.resolver == "podcast"
    assert rs.audio_url == "https://cdn/ep.mp3"


def test_resolve_source_none_when_nothing_applies():
    assert resolve_source("https://example.com/article", fetch=lambda u: "<html></html>") is None
