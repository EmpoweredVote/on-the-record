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
