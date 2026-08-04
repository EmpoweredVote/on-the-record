from pathlib import Path

from src import config
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
    feeds._last_fetch_at.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_page_bytes", lambda url: ("text/html; charset=utf-8",
        b"<html><script>var x=1;</script><body><h1>Debate</h1>"
        b"<p>Watch the full governor debate &amp; forum.</p></body></html>"))
    text = fetch_page_text("https://x.example/article", sleep_fn=lambda s: None)
    assert "Debate Watch the full governor debate & forum." in text
    assert "var x" not in text


def test_fetch_page_text_prefers_article_slice(monkeypatch):
    feeds._robots_cache.clear()
    feeds._last_fetch_at.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_page_bytes", lambda url: ("text/html",
        b"<html><nav>Home About</nav><body><article><h1>Debate</h1>"
        b"<p>Full recap here.</p></article><footer>Contact us</footer></body></html>"))
    text = fetch_page_text("https://x.example/article", sleep_fn=lambda s: None)
    assert "Debate Full recap here." in text
    assert "Home About" not in text
    assert "Contact us" not in text


def test_fetch_page_text_ignores_aside_teaser_prefers_main_body(monkeypatch):
    feeds._robots_cache.clear()
    feeds._last_fetch_at.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_page_bytes", lambda url: ("text/html",
        b"<html><body><aside><article>Related: Storm hits region</article></aside>"
        b"<article><h1>Governor Debate</h1><p>The candidates sparred over taxes "
        b"and education for nearly two hours in a debate broadcast statewide, "
        b"touching on infrastructure, healthcare, and rural broadband access.</p>"
        b"</article></body></html>"))
    text = fetch_page_text("https://x.example/article", sleep_fn=lambda s: None)
    assert "Governor Debate" in text
    assert "candidates sparred" in text
    assert "Related: Storm hits region" not in text


def test_fetch_page_text_falls_back_when_article_slice_too_small(monkeypatch):
    feeds._robots_cache.clear()
    feeds._last_fetch_at.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_page_bytes", lambda url: ("text/html",
        b"<html><body><h1>KCTV5 News</h1><article>Breaking.</article>"
        b"<p>Full story: county commissioners voted 4-1 to approve the new "
        b"zoning ordinance after a contentious three-hour public hearing.</p>"
        b"</body></html>"))
    text = fetch_page_text("https://x.example/article", sleep_fn=lambda s: None)
    assert "county commissioners voted 4-1" in text
    assert "KCTV5 News" in text


def test_fetch_page_text_rejects_non_html_content_type(monkeypatch):
    feeds._robots_cache.clear()
    feeds._last_fetch_at.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_page_bytes",
                        lambda url: ("audio/mpeg", b"\xff\xfb\x90\x00" * 100))
    text = fetch_page_text("https://x.example/episode.mp3", sleep_fn=lambda s: None)
    assert text == ""


def test_fetch_page_bytes_caps_body_size(monkeypatch):
    feeds._robots_cache.clear()
    feeds._last_fetch_at.clear()

    class _FakeResp:
        headers = {"Content-Type": "text/html"}

        def raise_for_status(self):
            pass

        def iter_content(self, chunk_size=8192):
            # Far more than the cap -- the guard must stop reading, not just
            # truncate after accumulating everything.
            for _ in range(1000):
                yield b"x" * chunk_size

    monkeypatch.setattr(feeds.requests, "get", lambda *a, **kw: _FakeResp())
    content_type, body = feeds._fetch_page_bytes("https://x.example/huge", max_bytes=1000)
    assert content_type == "text/html"
    assert len(body) == 1000


def test_html_to_text_preserves_less_than_greater_than_comparisons():
    text = feeds._html_to_text("Turnout < 50% but > 40% statewide")
    assert text == "Turnout < 50% but > 40% statewide"


NEWS_RSS_CDATA_DESC = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <title>KS Statehouse</title>
  <item>
    <title>Governor debate recap</title>
    <link>https://news.example/tax-debate</link>
    <description><![CDATA[<p>Laura <em>Kelly</em> faced Derek&nbsp;Schmidt on taxes.</p>]]></description>
    <pubDate>Sat, 01 Aug 2026 21:00:00 GMT</pubDate>
  </item>
</channel></rss>"""


def test_parse_news_feed_cleans_html_in_description():
    items = parse_news_feed(NEWS_RSS_CDATA_DESC, outlet_id="o9")
    assert len(items) == 1
    desc = items[0].description
    assert "<em>" not in desc and "&nbsp;" not in desc
    assert "Laura Kelly" in desc
    assert "Derek Schmidt" in desc


NEWS_RSS_BYTES = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<rss version="2.0"><channel><title>Wire</title>'
    '<item><title>José Núñez wins primary</title>'
    '<link>https://news.example/jose-nunez</link></item>'
    '</channel></rss>'
).encode("utf-8")


def test_parse_news_feed_accepts_bytes_and_keeps_accents():
    items = parse_news_feed(NEWS_RSS_BYTES, outlet_id="o9")
    assert len(items) == 1
    assert items[0].title == "José Núñez wins primary"


NEWS_RSS_BOM_THEN_NEWLINE = (
    b"\xef\xbb\xbf\n"
    b'<?xml version="1.0"?>\n'
    b'<rss version="2.0"><channel><title>Wire</title>'
    b'<item><title>Item</title>'
    b'<link>https://example.com/a</link></item>'
    b'</channel></rss>'
)


def test_parse_news_feed_bom_then_newline_bytes():
    items = parse_news_feed(NEWS_RSS_BOM_THEN_NEWLINE, outlet_id="o9")
    assert len(items) == 1
    assert items[0].url == "https://example.com/a"


RDF_FEED = """<?xml version="1.0"?>
<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns="http://purl.org/rss/1.0/">
  <channel><title>Old RDF feed</title></channel>
  <item><title>Item</title><link>https://example.com/a</link></item>
</rdf:RDF>"""


def test_parse_news_feed_unrecognized_root_raises():
    try:
        parse_news_feed(RDF_FEED)
        assert False, "expected ValueError"
    except ValueError as exc:
        assert "unrecognized feed root" in str(exc)


NEWS_RSS_DC_DATE = """<?xml version="1.0"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"><channel>
  <title>KS Statehouse</title>
  <item>
    <title>Special session called</title>
    <link>https://news.example/special-session</link>
    <dc:date>2026-08-03T14:00:00Z</dc:date>
  </item>
</channel></rss>"""


def test_parse_news_feed_falls_back_to_dc_date():
    items = parse_news_feed(NEWS_RSS_DC_DATE, outlet_id="o9")
    assert len(items) == 1
    assert items[0].published_at == "2026-08-03T14:00:00Z"


NEWS_ATOM_SELF_ONLY = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Statehouse Bureau</title>
  <entry>
    <title>Orphan entry</title>
    <link rel="self" href="https://api.example/feed/entries/9"/>
  </entry>
</feed>"""


def test_parse_news_feed_atom_skips_self_only_link():
    items = parse_news_feed(NEWS_ATOM_SELF_ONLY, outlet_id="o9")
    assert items == []


def test_robots_403_denies():
    feeds._robots_cache.clear()
    def denied(url):
        raise feeds.RobotsDenied("403 fetching robots.txt")
    assert _robots_allowed("https://x.example/feed.rss", fetch_text_fn=denied) is False


NEWS_RSS_NO_TITLE = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item>
    <title>Item</title>
    <link>https://example.com/a</link>
  </item>
</channel></rss>"""


def test_fetch_outlet_items_web_rss_happy_path(monkeypatch):
    feeds._robots_cache.clear()
    feeds._last_fetch_at.clear()
    monkeypatch.setattr(feeds, "_robots_allowed", lambda url: True)
    monkeypatch.setattr(feeds, "_fetch_bytes", lambda url: NEWS_RSS_NO_TITLE.encode("utf-8"))
    outlet = Outlet(id="o9", name="KCTV5", kind="web_rss",
                    feed_url="https://www.kctv5.com/rss/politics/")
    items = feeds.fetch_outlet_items(outlet, sleep_fn=lambda s: None)
    assert len(items) == 1
    assert items[0].url == "https://example.com/a"
    assert items[0].channel_name == "KCTV5"  # outlet_name threaded (feed has no <title>)


def test_polite_pause_enforces_gap_per_origin(monkeypatch):
    feeds._last_fetch_at.clear()
    monkeypatch.setattr(feeds.time, "monotonic", lambda: 1000.0)
    sleeps = []
    feeds._polite_pause("https://p.example", sleep_fn=sleeps.append)
    assert sleeps == []  # first-ever contact never sleeps
    feeds._polite_pause("https://p.example", sleep_fn=sleeps.append)
    assert len(sleeps) == 1
    assert sleeps[0] >= config.DISCOVERY_WEB_FETCH_SLEEP_SECONDS
