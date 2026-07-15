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


# Real Buzzsprout feed shape: items have no <link> and a synthetic guid
# ("Buzzsprout-<id>"); the episode slug only appears in the enclosure URL, and
# the pasted page URL lives on a different host than the enclosure.
_BUZZSPROUT_ENTRIES = [
    {"link": None, "guid": "Buzzsprout-18052144",
     "audio_url": "https://www.buzzsprout.com/1414123/episodes/18052144-mayor-karen-bass-on-the-record.mp3"},
    {"link": None, "guid": "Buzzsprout-18988424",
     "audio_url": "https://www.buzzsprout.com/1414123/episodes/18988424-nithya-raman-mayoral-candidate-spotlight.mp3"},
]


def test_match_entry_enclosure_slug_fallback():
    # No usable link/guid match; the episode slug in the page URL appears only in
    # the enclosure URL -> the fallback must find the right item.
    page = ("https://whatsnextlosangeles.buzzsprout.com/1414123/episodes/"
            "18988424-nithya-raman-mayoral-candidate-spotlight")
    assert match_entry(_BUZZSPROUT_ENTRIES, page)["guid"] == "Buzzsprout-18988424"


def test_match_entry_show_page_not_false_matched_by_numeric_id():
    # A whole-show landing page's last segment is the bare numeric show id, which
    # appears in EVERY episode enclosure URL -> must NOT match (returns None).
    assert match_entry(_BUZZSPROUT_ENTRIES, "https://show.buzzsprout.com/1414123/") is None
    assert match_entry(_BUZZSPROUT_ENTRIES, "https://show.buzzsprout.com/1414123") is None


# add to tests/test_podcast.py
from src.resolve import ResolvedSource
from src.podcast import resolve_podcast_episode

_PAGE = """<html><head>
  <link rel="alternate" type="application/rss+xml" href="https://feeds.x/f.rss">
</head></html>"""


def _fetch_map(mapping):
    def _fetch(url):
        return mapping[url]
    return _fetch


def test_resolve_podcast_episode_happy_path():
    page_url = "https://show.buzzsprout.com/1414123/ep-1-housing"
    fetch = _fetch_map({page_url: _PAGE, "https://feeds.x/f.rss": _FEED})
    rs = resolve_podcast_episode(page_url, fetch=fetch)
    assert isinstance(rs, ResolvedSource)
    assert rs.audio_url == "https://cdn/ep1.mp3"
    assert rs.outlet == "What's Next Los Angeles"
    assert rs.date == "2026-06-03"
    assert rs.image_url == "https://img/ep1.jpg"
    assert rs.transcript is None
    assert rs.resolver == "podcast"


def test_resolve_podcast_episode_none_when_no_feed():
    fetch = _fetch_map({"https://x/ep": "<html><head></head></html>"})
    assert resolve_podcast_episode("https://x/ep", fetch=fetch) is None


def test_resolve_podcast_episode_none_when_episode_unmatched():
    # Feed found, but URL is a whole-show page -> no matching item -> None.
    show_url = "https://show.buzzsprout.com/1414123/"
    fetch = _fetch_map({show_url: _PAGE, "https://feeds.x/f.rss": _FEED})
    assert resolve_podcast_episode(show_url, fetch=fetch) is None


def test_resolve_podcast_episode_none_when_page_fetch_raises():
    def _boom(url):
        raise RuntimeError("network down")
    assert resolve_podcast_episode("https://x/ep", fetch=_boom) is None


def test_resolve_podcast_episode_none_when_feed_fetch_raises():
    page_url = "https://show.buzzsprout.com/1414123/ep-1-housing"

    def _fetch(url):
        if url == page_url:
            return _PAGE
        raise RuntimeError("feed 500")

    assert resolve_podcast_episode(page_url, fetch=_fetch) is None


_FEED_NO_AUDIO = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Show</title>
<item><title>Ep</title>
<link>https://show.buzzsprout.com/1414123/ep-1-housing</link>
<guid>https://show.buzzsprout.com/1414123/ep-1-housing</guid>
</item></channel></rss>"""


def test_resolve_podcast_episode_none_when_entry_has_no_audio():
    page_url = "https://show.buzzsprout.com/1414123/ep-1-housing"
    fetch = _fetch_map({page_url: _PAGE, "https://feeds.x/f.rss": _FEED_NO_AUDIO})
    assert resolve_podcast_episode(page_url, fetch=fetch) is None


_FEED_FALLBACKS = """<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
<channel>
  <itunes:author>Author Only Show</itunes:author>
  <itunes:image href="https://img/show-fallback.jpg"/>
  <item>
    <title>Ep</title>
    <link>https://show.buzzsprout.com/1414123/ep-1-housing</link>
    <guid>https://show.buzzsprout.com/1414123/ep-1-housing</guid>
    <pubDate>Tue, 03 Jun 2026 10:00:00 -0700</pubDate>
    <enclosure url="https://cdn/ep1.mp3" type="audio/mpeg" length="1"/>
  </item>
</channel></rss>"""


def test_resolve_podcast_episode_outlet_and_image_fallbacks():
    page_url = "https://show.buzzsprout.com/1414123/ep-1-housing"
    fetch = _fetch_map({page_url: _PAGE, "https://feeds.x/f.rss": _FEED_FALLBACKS})
    rs = resolve_podcast_episode(page_url, fetch=fetch)
    assert rs is not None
    assert rs.outlet == "Author Only Show"
    assert rs.image_url == "https://img/show-fallback.jpg"
