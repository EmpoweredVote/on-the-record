from __future__ import annotations

from src.brightspot import parse_jsonld_meta

_HTML = """<html><head>
<meta property="og:site_name" content="Indiana Public Media">
<meta property="og:image" content="https://cdn/og.jpg">
<meta property="og:description" content="A chat about the land swap.">
<script type="application/ld+json">
{"@type":"RadioEpisode","name":"Thomson on the land swap"}
</script>
<script type="application/ld+json">
{"@type":"NewsArticle","headline":"Thomson on the land swap",
 "datePublished":"2026-07-15T07:35:25Z",
 "author":[{"@type":"Person","name":"Joe Hren"}]}
</script>
</head><body></body></html>"""


def test_parse_jsonld_meta_extracts_date_title_outlet():
    meta = parse_jsonld_meta(_HTML)
    assert meta["date"] == "2026-07-15"
    assert meta["title"] == "Thomson on the land swap"
    assert meta["outlet"] == "Indiana Public Media"
    assert meta["image"] == "https://cdn/og.jpg"
    assert meta["description"] == "A chat about the land swap."


def test_parse_jsonld_meta_accepts_podcastepisode_type():
    html = _HTML.replace("RadioEpisode", "PodcastEpisode")
    assert parse_jsonld_meta(html)["title"] == "Thomson on the land swap"


# add to tests/test_brightspot.py
from src.brightspot import select_episode_mp3


def test_select_episode_mp3_prefers_slug_match():
    # Current episode file shares the headline slug; sidebar episodes don't.
    html = """
      <a href="https://cpa.ds.npr.org/s385/audio/2026/07/07-13-2026-atm-thomson-web.mp3">play</a>
      <a href="https://cpa.ds.npr.org/s385/audio/2026/06/06-30-2026-columbusatm-web.mp3">old</a>
    """
    got = select_episode_mp3(html, headline="Thomson on the land swap")
    assert got == "https://cpa.ds.npr.org/s385/audio/2026/07/07-13-2026-atm-thomson-web.mp3"


def test_select_episode_mp3_falls_back_to_first():
    html = '<a href="https://cpa.ds.npr.org/s1/audio/x.mp3">a</a>' \
           '<a href="https://cpa.ds.npr.org/s1/audio/y.mp3">b</a>'
    # No slug token matches -> first MP3 (the current episode leads the page).
    assert select_episode_mp3(html, headline="Totally Unrelated Title") == \
        "https://cpa.ds.npr.org/s1/audio/x.mp3"


def test_select_episode_mp3_none_when_no_mp3():
    assert select_episode_mp3("<html></html>", headline="x") is None


# add to tests/test_brightspot.py
from src.resolve import ResolvedSource
from src.brightspot import extract_transcript, resolve_brightspot_episode


def test_extract_transcript_joins_body_paragraphs():
    html = """<html><body><article>
      <p>Joe Hren: Welcome to the show.</p>
      <p>Mayor Thomson: Thanks for having me.</p>
      <p>x</p>
    </article></body></html>"""
    # Short boilerplate <p>x</p> is dropped; real turns are kept and joined.
    text = extract_transcript(html)
    assert "Welcome to the show." in text
    assert "Thanks for having me." in text
    assert text.count("\n\n") >= 1


def test_extract_transcript_none_when_no_body_paragraphs():
    assert extract_transcript("<html><body><p>hi</p></body></html>") is None


def test_resolve_brightspot_episode_happy_path():
    html = _HTML.replace(
        "</head>",
        '<a href="https://cpa.ds.npr.org/s385/audio/2026/07/07-13-2026-atm-thomson-web.mp3">p</a></head>',
    ).replace(
        "<body></body>",
        "<body><article>"
        "<p>Joe Hren: Welcome to Ask the Mayor.</p>"
        "<p>Mayor Thomson: Glad to be here talking land swap.</p>"
        "</article></body>",
    )
    rs = resolve_brightspot_episode("https://www.ipm.org/show/askthemayor/2026-07-15/x",
                                    fetch=lambda u: html)
    assert isinstance(rs, ResolvedSource)
    assert rs.audio_url.endswith("07-13-2026-atm-thomson-web.mp3")
    assert rs.outlet == "Indiana Public Media"
    assert rs.date == "2026-07-15"
    assert "land swap" in rs.transcript
    assert rs.resolver == "brightspot"


def test_resolve_brightspot_episode_none_without_mp3():
    assert resolve_brightspot_episode("https://x/y", fetch=lambda u: _HTML) is None


def test_parse_jsonld_meta_prefers_episode_name_regardless_of_block_order():
    html = """<html><head>
    <meta property="og:site_name" content="KUER">
    <script type="application/ld+json">
    {"@type":"NewsArticle","headline":"HEADLINE VERSION","datePublished":"2026-06-03T02:00:00Z"}
    </script>
    <script type="application/ld+json">
    {"@type":"PodcastEpisode","name":"EPISODE NAME VERSION"}
    </script>
    </head><body></body></html>"""
    assert parse_jsonld_meta(html)["title"] == "EPISODE NAME VERSION"


def test_og_unescapes_html_entities():
    html = ('<meta property="og:description" content="&quot;Seminary Pointe&quot; &#x27;quote&#x27;">'
            '<meta property="og:image" content="https://cdn/i.jpg?a=1&amp;b=2">')
    meta = parse_jsonld_meta(html)
    assert meta["description"] == '"Seminary Pointe" \'quote\''
    assert meta["image"] == "https://cdn/i.jpg?a=1&b=2"


def test_resolve_brightspot_episode_none_when_fetch_returns_non_html():
    # fetch returns None (bad data, no exception) -> must return None, not raise
    assert resolve_brightspot_episode("https://x/y", fetch=lambda u: None) is None


def test_parse_jsonld_meta_rejects_short_date():
    html = ('<script type="application/ld+json">'
            '{"@type":"NewsArticle","headline":"h","datePublished":"2026"}</script>')
    assert parse_jsonld_meta(html)["date"] is None
