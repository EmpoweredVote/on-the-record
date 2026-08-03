from pathlib import Path

from src.discovery import feeds
from src.discovery.models import Outlet

FIXTURES = Path(__file__).parent / "fixtures"


def test_parse_youtube_feed_maps_entries():
    xml = (FIXTURES / "discovery_youtube_feed.xml").read_text()
    items = feeds.parse_youtube_feed(xml, outlet_id="o1")
    assert len(items) == 2
    first = items[0]
    assert first.url == "https://www.youtube.com/watch?v=abc12345678"
    assert first.title == "Texas Senate debate: full video"
    assert first.description == "All four candidates meet in Austin."
    assert first.channel_name == "KXAN"
    assert first.channel_id == "UCkxan000000000000000000"
    assert first.published_at == "2026-08-01T21:04:00+00:00"
    assert first.outlet_id == "o1" and first.via == "watchlist"
    assert first.duration_seconds is None  # RSS has no duration; hydration fills it


def test_parse_podcast_feed_prefers_page_link_over_enclosure():
    xml = (FIXTURES / "discovery_podcast_feed.xml").read_text()
    items = feeds.parse_podcast_feed(xml, outlet_id="o2")
    assert len(items) == 1
    assert items[0].url == "https://example.buzzsprout.com/ep/101"  # page URL = citation
    assert items[0].title == "Maria Delgado on housing"
    assert items[0].published_at.startswith("2026-08-01")


def test_fetch_outlet_items_dispatches_by_kind(monkeypatch):
    calls = {}

    def fake_fetch(url):
        calls["url"] = url
        return '<feed xmlns="http://www.w3.org/2005/Atom"></feed>'

    monkeypatch.setattr(feeds, "_fetch_text", fake_fetch)
    outlet = Outlet(id="o1", name="KXAN", kind="youtube_channel",
                    feed_url="https://www.youtube.com/feeds/videos.xml?channel_id=UCk")
    assert feeds.fetch_outlet_items(outlet) == []
    assert calls["url"] == outlet.feed_url


def test_fetch_outlet_items_web_page_kind_is_noop():
    outlet = Outlet(id="o3", name="Site", kind="web_page", feed_url="https://x.example")
    assert feeds.fetch_outlet_items(outlet) == []
