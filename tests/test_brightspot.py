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
