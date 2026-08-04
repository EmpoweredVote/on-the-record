from pathlib import Path

from src.discovery import feeds
from src.discovery.feeds import (_robots_allowed, fetch_page_text,
                                 parse_news_feed)
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
    assert len(items) == 2
    assert items[0].url == "https://example.buzzsprout.com/ep/101"  # page URL = citation
    assert items[0].title == "Maria Delgado on housing"
    assert items[0].published_at.startswith("2026-08-01")


def test_parse_podcast_feed_falls_back_to_permalink_guid():
    xml = (FIXTURES / "discovery_podcast_feed.xml").read_text()
    items = feeds.parse_podcast_feed(xml, outlet_id="o2")
    assert len(items) == 2
    assert items[1].url == "https://example.buzzsprout.com/ep/102"  # guid page, not .mp3


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


NEWS_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>KCTV5 Politics</title>
  <item>
    <title>Kansas governor candidates meet in first debate</title>
    <link>https://www.kctv5.com/2026/08/01/governor-debate/</link>
    <description>The full debate aired Thursday.</description>
    <pubDate>Sat, 01 Aug 2026 21:00:00 GMT</pubDate>
  </item>
  <item><title>No link, skipped</title></item>
</channel></rss>"""

NEWS_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Statehouse Bureau</title>
  <entry>
    <title>Candidate forum recap</title>
    <link rel="alternate" href="https://news.example/forum-recap"/>
    <summary>Watch the full forum.</summary>
    <published>2026-08-02T09:00:00Z</published>
  </entry>
</feed>"""


def test_parse_news_feed_rss():
    items = parse_news_feed(NEWS_RSS, outlet_id="o9")
    assert len(items) == 1
    it = items[0]
    assert it.url == "https://www.kctv5.com/2026/08/01/governor-debate/"
    assert it.channel_name == "KCTV5 Politics"
    assert it.duration_seconds is None
    assert it.published_at.startswith("2026-08-01")
    assert it.outlet_id == "o9" and it.via == "watchlist"


def test_parse_news_feed_atom():
    items = parse_news_feed(NEWS_ATOM, outlet_id="o9")
    assert len(items) == 1
    assert items[0].url == "https://news.example/forum-recap"
    assert items[0].channel_name == "Statehouse Bureau"
    assert items[0].published_at == "2026-08-02T09:00:00Z"


def test_robots_disallow_blocks_and_missing_allows():
    feeds._robots_cache.clear()
    blocked = lambda url: "User-agent: *\nDisallow: /"
    assert _robots_allowed("https://x.example/feed.rss", fetch_text_fn=blocked) is False

    feeds._robots_cache.clear()
    def missing(url):
        raise RuntimeError("404")
    assert _robots_allowed("https://x.example/feed.rss", fetch_text_fn=missing) is True


def test_robots_cache_is_per_origin(monkeypatch):
    feeds._robots_cache.clear()
    calls = []
    def fetch(url):
        calls.append(url)
        return "User-agent: *\nAllow: /"
    assert _robots_allowed("https://x.example/a", fetch_text_fn=fetch)
    assert _robots_allowed("https://x.example/b", fetch_text_fn=fetch)
    assert calls == ["https://x.example/robots.txt"]


def test_fetch_outlet_items_web_rss_respects_robots(monkeypatch):
    feeds._robots_cache.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: False)
    outlet = Outlet(id="o9", name="KCTV5", kind="web_rss",
                    feed_url="https://www.kctv5.com/rss/politics/")
    try:
        feeds.fetch_outlet_items(outlet)
        assert False, "expected RuntimeError"
    except RuntimeError as exc:
        assert "robots.txt" in str(exc)


def test_fetch_outlet_items_unknown_kind_is_loud():
    outlet = Outlet(id="o9", name="Mystery", kind="mystery", feed_url="https://x")
    try:
        feeds.fetch_outlet_items(outlet)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unknown outlet kind" in str(exc)


def test_fetch_page_text_strips_markup(monkeypatch):
    feeds._robots_cache.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_text", lambda url:
        "<html><script>var x=1;</script><body><h1>Debate</h1>"
        "<p>Watch the full governor debate &amp; forum.</p></body></html>")
    text = fetch_page_text("https://x.example/article")
    assert "Debate Watch the full governor debate & forum." in text
    assert "var x" not in text
