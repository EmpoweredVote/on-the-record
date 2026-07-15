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


# add to tests/test_podcast.py
from src.podcast import parse_feed_entries

_FEED = """<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel>
    <title>What's Next Los Angeles</title>
    <itunes:author>WNLA</itunes:author>
    <itunes:image href="https://img/show.jpg"/>
    <item>
      <title>Ep 1: Housing</title>
      <link>https://show.buzzsprout.com/1414123/ep-1-housing</link>
      <guid>https://show.buzzsprout.com/1414123/ep-1-housing</guid>
      <pubDate>Tue, 03 Jun 2026 10:00:00 -0700</pubDate>
      <description>Guest talks &lt;b&gt;housing&lt;/b&gt;. 00:30 Intro 02:10 Zoning</description>
      <enclosure url="https://cdn/ep1.mp3" type="audio/mpeg" length="1"/>
      <itunes:image href="https://img/ep1.jpg"/>
    </item>
  </channel>
</rss>"""


def test_parse_feed_entries_maps_fields():
    show, entries = parse_feed_entries(_FEED)
    assert show["title"] == "What's Next Los Angeles"
    assert show["image"] == "https://img/show.jpg"
    assert len(entries) == 1
    e = entries[0]
    assert e["title"] == "Ep 1: Housing"
    assert e["link"] == "https://show.buzzsprout.com/1414123/ep-1-housing"
    assert e["guid"] == "https://show.buzzsprout.com/1414123/ep-1-housing"
    assert e["audio_url"] == "https://cdn/ep1.mp3"
    assert e["date"] == "2026-06-03"
    assert e["image"] == "https://img/ep1.jpg"
    assert "housing" in e["description"].lower()


# add to tests/test_podcast.py
from src.podcast import match_entry

_ENTRIES = [
    {"link": "https://show.buzzsprout.com/1414123/ep-1-housing",
     "guid": "guid-1", "audio_url": "https://cdn/ep1.mp3"},
    {"link": "https://show.buzzsprout.com/1414123/ep-2-transit",
     "guid": "guid-2", "audio_url": "https://cdn/ep2.mp3"},
]


def test_match_entry_by_link_ignoring_trailing_slash_and_query():
    page = "https://show.buzzsprout.com/1414123/ep-2-transit/?utm=x"
    assert match_entry(_ENTRIES, page)["audio_url"] == "https://cdn/ep2.mp3"


def test_match_entry_by_guid():
    assert match_entry(_ENTRIES, "guid-1")["audio_url"] == "https://cdn/ep1.mp3"


def test_match_entry_none_when_no_match():
    # A whole-show / site-wide feed page matches nothing -> None (deferred
    # "pick from feed list" case).
    assert match_entry(_ENTRIES, "https://show.buzzsprout.com/1414123/") is None
