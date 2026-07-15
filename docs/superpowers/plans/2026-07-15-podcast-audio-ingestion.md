# Podcast & Web Audio-Interview Ingestion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ingest candidate podcast episodes and public-radio interview pages (audio-only, from a pasted episode page URL) into the existing meeting pipeline, capturing real metadata and, when a source publishes one, a clean transcript.

**Architecture:** A small set of pluggable *source resolvers* each turn one episode page URL into a normalized `ResolvedSource` (audio URL + metadata + optional transcript). A dispatcher picks the right resolver, exactly like the existing CATS TV branch. Everything downstream (normalize → diarize → transcribe → identify → summarize → publish) is unchanged and source-agnostic; a new reconciliation step optionally uses a provided transcript to correct Whisper output while keeping its timestamps.

**Tech Stack:** Python 3, `requests`/`httpx` (HTTP), `beautifulsoup4` (HTML), `feedparser` (RSS — new dep), Anthropic SDK (reconciliation), pytest.

**Design reference:** `docs/superpowers/specs/2026-07-15-podcast-audio-ingestion-design.md`

**Ship points (natural stopping places if you want to land work incrementally):**
- After Task 12 — podcast + Brightspot episodes ingest end-to-end with correct metadata and artwork thumbnails. Fully usable.
- After Task 15 — `podcast` event kind + `audio` playback wiring complete.
- After Task 18 — transcript-as-corrector (the riskiest piece) landed.

---

## File Structure

**New files:**
- `src/resolve.py` — `ResolvedSource` dataclass + `resolve_source()` dispatcher + detection. The single entry point the pipeline calls.
- `src/podcast.py` — podcast RSS resolver (feed autodiscovery → parse → match episode).
- `src/brightspot.py` — NPR/Brightspot CMS resolver (JSON-LD + og + NPR-CDN MP3 + article-body transcript).
- `src/reconcile.py` — transcript-as-corrector (LLM correction preserving timestamps/speakers).
- `tests/test_resolve.py`, `tests/test_podcast.py`, `tests/test_brightspot.py`, `tests/test_reconcile.py`, `tests/test_resolve_ingest.py` — unit tests with inline fixtures (no network).

**Modified files:**
- `src/models.py` — add `source_image_url`, `source_description` to `ProcessingMetadata`.
- `src/ingest.py` — `normalize_audio()` consumes `resolve_source()`.
- `src/thumbnail.py` — artwork-URL fallback when there's no video file.
- `src/event_kinds.py` — add `podcast` kind.
- `src/summarize.py` — add `podcast` to `_INTERVIEW_KINDS`; thread show-notes hint.
- `src/config.py` — gate threshold + section config for `podcast`.
- `src/publish.py` — `audio` playback kind.
- `gui/formmeta.py` — `podcast` labels/help.
- `gui/app.py` — `/api/source-meta` uses `resolve_source()` for prefill.
- `run_local.py` — Stage 1 consumes new metadata; reconciliation step after Stage 3.
- `requirements.txt` — add `feedparser`.
- `supabase/migrations/0006_playback_kind_audio.sql` — document `audio` value.

**Design conventions for resolvers (follow throughout):**
- Parsing functions are **pure**: they take already-fetched text (HTML/feed), never do network I/O. Network lives only in thin wrappers that accept an injectable `fetch` callable. This makes every test fixture-based and network-free.
- `fetch(url) -> str` is the single network primitive. Default implementation uses `requests`.
- Any resolver returns `None` when it does not confidently apply, so the dispatcher can fall through.

---

## Task 1: Add feedparser dependency

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Add the dependency**

Add this line to `requirements.txt` next to the other HTTP/parsing deps (after `beautifulsoup4>=4.12`):

```
feedparser>=6.0
```

- [ ] **Step 2: Install it**

Run: `.venv/bin/pip install "feedparser>=6.0"`
Expected: `Successfully installed feedparser-6.x` (or "already satisfied").

- [ ] **Step 3: Verify import**

Run: `.venv/bin/python -c "import feedparser; print(feedparser.__version__)"`
Expected: prints a version like `6.0.11`.

- [ ] **Step 4: Commit**

```bash
git add requirements.txt
git commit -m "build: add feedparser for podcast RSS parsing"
```

---

## Task 2: ResolvedSource dataclass + resolver dispatcher skeleton

**Files:**
- Create: `src/resolve.py`
- Test: `tests/test_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.resolve'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/resolve.py
"""Turn an episode page URL into a normalized ResolvedSource.

A ResolvedSource is what the ingestion pipeline consumes regardless of where the
audio came from (podcast RSS, public-radio CMS, ...). Resolvers are tried in
order; the first one that confidently applies wins. If none applies,
resolve_source returns None and the caller falls back to the existing yt-dlp /
direct-download path.

Parsing is pure and network-free; the only network primitive is `fetch`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional


@dataclass
class ResolvedSource:
    audio_url: str
    title: Optional[str] = None
    date: Optional[str] = None            # YYYY-MM-DD
    outlet: Optional[str] = None          # show / station name -> source_channel
    description: Optional[str] = None     # show notes / article summary
    image_url: Optional[str] = None       # episode / show / og artwork
    transcript: Optional[str] = None      # clean transcript text, when provided
    resolver: str = ""                    # 'podcast' | 'brightspot'


def _default_fetch(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def resolve_source(
    url: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
) -> Optional[ResolvedSource]:
    """Try each resolver; return the first ResolvedSource, or None."""
    # Resolvers are registered in Task 10. For now, nothing applies.
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_resolve.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/resolve.py tests/test_resolve.py
git commit -m "feat(resolve): add ResolvedSource and resolver dispatcher skeleton"
```

---

## Task 3: Podcast — feed autodiscovery from an episode page

**Files:**
- Create: `src/podcast.py`
- Test: `tests/test_podcast.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_podcast.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.podcast'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/podcast.py
"""Podcast RSS resolver: episode page -> autodiscovered feed -> matched item.

Handles any host that advertises its feed via standard RSS autodiscovery
(Buzzsprout, Omny, Libsyn, Simplecast, Megaphone, ...). Audio-only: never
returns a transcript. Parsing is pure; network lives behind an injected fetch.
"""
from __future__ import annotations

from urllib.parse import urljoin


def discover_feed_url(html: str, base_url: str) -> str | None:
    """Return the RSS feed URL advertised by <link rel=alternate rss>, or None."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    link = soup.find(
        "link",
        rel=lambda v: v and "alternate" in v,
        type="application/rss+xml",
    )
    if not link or not link.get("href"):
        return None
    return urljoin(base_url, link["href"])
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_podcast.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/podcast.py tests/test_podcast.py
git commit -m "feat(podcast): discover RSS feed URL from an episode page"
```

---

## Task 4: Podcast — parse feed entries into normalized dicts

**Files:**
- Modify: `src/podcast.py`
- Test: `tests/test_podcast.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_podcast.py::test_parse_feed_entries_maps_fields -v`
Expected: FAIL — `ImportError: cannot import name 'parse_feed_entries'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/podcast.py`:

```python
def _entry_date(entry) -> str | None:
    """feedparser struct_time -> 'YYYY-MM-DD', or None."""
    st = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not st:
        return None
    return f"{st.tm_year:04d}-{st.tm_mon:02d}-{st.tm_mday:02d}"


def _entry_audio_url(entry) -> str | None:
    """First audio enclosure URL for a feed entry, or None."""
    for enc in getattr(entry, "enclosures", []) or []:
        href = enc.get("href") or enc.get("url")
        if href and (enc.get("type", "").startswith("audio") or href.lower().endswith((".mp3", ".m4a"))):
            return href
    for link in getattr(entry, "links", []) or []:
        if link.get("rel") == "enclosure" and link.get("href"):
            return link["href"]
    return None


def _entry_image(entry) -> str | None:
    img = getattr(entry, "image", None)
    if img and img.get("href"):
        return img["href"]
    return None


def parse_feed_entries(feed_text: str):
    """Parse an RSS feed. Returns (show_dict, [entry_dict, ...]).

    show_dict: {title, author, image}.
    entry_dict: {title, link, guid, audio_url, date ('YYYY-MM-DD'|None),
                 description, image}.
    """
    import feedparser

    parsed = feedparser.parse(feed_text)
    feed = parsed.feed
    show = {
        "title": feed.get("title"),
        "author": feed.get("author") or feed.get("itunes_author"),
        "image": (feed.get("image") or {}).get("href"),
    }
    entries = []
    for e in parsed.entries:
        entries.append({
            "title": e.get("title"),
            "link": e.get("link"),
            "guid": e.get("id") or e.get("guid"),
            "audio_url": _entry_audio_url(e),
            "date": _entry_date(e),
            "description": (e.get("summary") or e.get("description") or ""),
            "image": _entry_image(e),
        })
    return show, entries
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_podcast.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/podcast.py tests/test_podcast.py
git commit -m "feat(podcast): parse feed entries into normalized dicts"
```

---

## Task 5: Podcast — match one episode to the pasted page URL

**Files:**
- Modify: `src/podcast.py`
- Test: `tests/test_podcast.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_podcast.py::test_match_entry_by_link_ignoring_trailing_slash_and_query -v`
Expected: FAIL — `ImportError: cannot import name 'match_entry'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/podcast.py`:

```python
from urllib.parse import urlparse


def _norm(u: str | None) -> str:
    """Normalize a URL/guid for comparison: drop scheme, query, trailing slash."""
    if not u:
        return ""
    s = u.strip()
    try:
        p = urlparse(s)
    except ValueError:
        return s.rstrip("/")
    if p.scheme in ("http", "https"):
        return f"{p.netloc.lower()}{p.path.rstrip('/')}"
    return s.rstrip("/")


def match_entry(entries, page_url: str):
    """Return the entry whose link or guid matches page_url, else None."""
    target = _norm(page_url)
    if not target:
        return None
    for e in entries:
        if _norm(e.get("link")) == target or _norm(e.get("guid")) == target:
            return e
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_podcast.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add src/podcast.py tests/test_podcast.py
git commit -m "feat(podcast): match one feed entry to the pasted episode URL"
```

---

## Task 6: Podcast — full resolver

**Files:**
- Modify: `src/podcast.py`
- Test: `tests/test_podcast.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_podcast.py::test_resolve_podcast_episode_happy_path -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_podcast_episode'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/podcast.py`:

```python
def resolve_podcast_episode(page_url: str, *, fetch):
    """Resolve an episode page URL to a ResolvedSource, or None if not a podcast.

    Returns None when no feed is discovered, or when the feed contains no item
    matching the pasted URL (e.g. a site-wide feed or a whole-show landing
    page — the deferred 'pick from feed list' case). Never trusts autodiscovery
    blindly: success requires a matching <item> with an audio enclosure.
    """
    from .resolve import ResolvedSource

    try:
        page_html = fetch(page_url)
    except Exception:
        return None

    feed_url = discover_feed_url(page_html, page_url)
    if not feed_url:
        return None

    try:
        feed_text = fetch(feed_url)
    except Exception:
        return None

    show, entries = parse_feed_entries(feed_text)
    entry = match_entry(entries, page_url)
    if not entry or not entry.get("audio_url"):
        return None

    return ResolvedSource(
        audio_url=entry["audio_url"],
        title=entry.get("title"),
        date=entry.get("date"),
        outlet=show.get("title") or show.get("author"),
        description=entry.get("description"),
        image_url=entry.get("image") or show.get("image"),
        transcript=None,
        resolver="podcast",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_podcast.py -v`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add src/podcast.py tests/test_podcast.py
git commit -m "feat(podcast): full episode resolver (page -> feed -> ResolvedSource)"
```

---

## Task 7: Brightspot — parse JSON-LD metadata

**Files:**
- Create: `src/brightspot.py`
- Test: `tests/test_brightspot.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_brightspot.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_brightspot.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.brightspot'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/brightspot.py
"""NPR/Brightspot CMS resolver.

Public-radio stations on NPR's Brightspot platform (ipm.org, kuer.org, ...)
publish an episode as an article page with JSON-LD metadata, an og:image, a
direct MP3 on NPR's distribution CDN (cpa.ds.npr.org), and often the cleaned
transcript in the article body. Parsing is pure; network lives behind fetch.
"""
from __future__ import annotations

import json
import re

# Episode @type varies by station: RadioEpisode (IPM), PodcastEpisode (KUER),
# or a generic AudioObject. Date is read from the NewsArticle block.
_EPISODE_TYPES = {"RadioEpisode", "PodcastEpisode", "AudioObject"}


def _iter_jsonld(html: str):
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    for tag in soup.find_all("script", type="application/ld+json"):
        raw = tag.string or tag.get_text() or ""
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        for obj in (data if isinstance(data, list) else [data]):
            if isinstance(obj, dict):
                yield obj


def _og(html: str, prop: str) -> str | None:
    m = re.search(
        rf'<meta[^>]*property=["\']og:{prop}["\'][^>]*content=["\']([^"\']*)["\']',
        html, re.I,
    )
    return m.group(1) if m else None


def parse_jsonld_meta(html: str) -> dict:
    """Extract {date, title, outlet, image, description} from JSON-LD + og tags.

    Date comes from NewsArticle.datePublished (the episode block often omits it);
    title from any episode block or the NewsArticle headline; outlet, image and
    description from og tags.
    """
    title = None
    date = None
    for obj in _iter_jsonld(html):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(x in _EPISODE_TYPES for x in types) and obj.get("name"):
            title = title or obj["name"]
        if "NewsArticle" in types:
            date = date or (obj.get("datePublished") or "")[:10] or None
            title = title or obj.get("headline")
    return {
        "date": date,
        "title": title,
        "outlet": _og(html, "site_name"),
        "image": _og(html, "image"),
        "description": _og(html, "description"),
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_brightspot.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/brightspot.py tests/test_brightspot.py
git commit -m "feat(brightspot): parse JSON-LD + og metadata"
```

---

## Task 8: Brightspot — select the current episode's MP3

**Files:**
- Modify: `src/brightspot.py`
- Test: `tests/test_brightspot.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_brightspot.py::test_select_episode_mp3_prefers_slug_match -v`
Expected: FAIL — `ImportError: cannot import name 'select_episode_mp3'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/brightspot.py`:

```python
_MP3_RE = re.compile(r'https?://[^"\'\s]*cpa\.ds\.npr\.org[^"\'\s]*\.mp3', re.I)
_WORD_RE = re.compile(r"[a-z0-9]+")


def _mp3_urls(html: str) -> list[str]:
    seen, out = set(), []
    for u in _MP3_RE.findall(html):
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def select_episode_mp3(html: str, headline: str | None) -> str | None:
    """Pick the current episode's MP3 from the NPR CDN links on the page.

    The current episode's file is typically the first cpa.ds.npr.org MP3 and its
    filename usually echoes the headline (e.g. '...-thomson-web.mp3'). Prefer a
    filename that shares a distinctive headline word; else fall back to the first
    MP3 (related-episode links follow it on the page).
    """
    urls = _mp3_urls(html)
    if not urls:
        return None
    if headline:
        # Distinctive headline words (skip short stopword-ish tokens).
        words = [w for w in _WORD_RE.findall(headline.lower()) if len(w) >= 5]
        for u in urls:
            fname = u.rsplit("/", 1)[-1].lower()
            if any(w in fname for w in words):
                return u
    return urls[0]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_brightspot.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/brightspot.py tests/test_brightspot.py
git commit -m "feat(brightspot): select current episode MP3 from NPR CDN links"
```

---

## Task 9: Brightspot — extract article-body transcript + full resolver

**Files:**
- Modify: `src/brightspot.py`
- Test: `tests/test_brightspot.py`

- [ ] **Step 1: Write the failing test**

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_brightspot.py::test_extract_transcript_joins_body_paragraphs -v`
Expected: FAIL — `ImportError: cannot import name 'extract_transcript'`.

- [ ] **Step 3: Write minimal implementation**

Append to `src/brightspot.py`:

```python
_MIN_PARA_CHARS = 40  # a real transcript turn is longer than nav/label chrome


def extract_transcript(html: str) -> str | None:
    """Join the article body's substantive <p> paragraphs, or None.

    Prefers an <article> container; falls back to <body>. Paragraphs shorter than
    _MIN_PARA_CHARS are treated as chrome and dropped. Returns None if nothing
    substantive remains.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    container = soup.find("article") or soup.body
    if not container:
        return None
    paras = []
    for p in container.find_all("p"):
        text = p.get_text(" ", strip=True)
        if len(text) >= _MIN_PARA_CHARS:
            paras.append(text)
    if not paras:
        return None
    return "\n\n".join(paras)


def resolve_brightspot_episode(page_url: str, *, fetch):
    """Resolve an NPR/Brightspot article page to a ResolvedSource, or None.

    Returns None if the page has no NPR-CDN MP3 (i.e. it is not a Brightspot
    audio episode page).
    """
    from .resolve import ResolvedSource

    try:
        html = fetch(page_url)
    except Exception:
        return None

    meta = parse_jsonld_meta(html)
    audio_url = select_episode_mp3(html, meta.get("title"))
    if not audio_url:
        return None

    return ResolvedSource(
        audio_url=audio_url,
        title=meta.get("title"),
        date=meta.get("date"),
        outlet=meta.get("outlet"),
        description=meta.get("description"),
        image_url=meta.get("image"),
        transcript=extract_transcript(html),
        resolver="brightspot",
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_brightspot.py -v`
Expected: PASS (9 tests).

- [ ] **Step 5: Commit**

```bash
git add src/brightspot.py tests/test_brightspot.py
git commit -m "feat(brightspot): extract transcript + full episode resolver"
```

---

## Task 10: Wire resolvers into the dispatcher

**Files:**
- Modify: `src/resolve.py`
- Test: `tests/test_resolve.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_resolve.py

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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_resolve.py::test_resolve_source_routes_to_brightspot -v`
Expected: FAIL — `resolve_source` returns None (resolvers not registered yet).

- [ ] **Step 3: Write minimal implementation**

Replace the body of `resolve_source` in `src/resolve.py` with:

```python
def resolve_source(
    url: str,
    *,
    fetch: Callable[[str], str] = _default_fetch,
) -> Optional[ResolvedSource]:
    """Try each resolver; return the first ResolvedSource, or None.

    Brightspot is tried before the generic podcast resolver because it is more
    specific (NPR-CDN MP3 + JSON-LD). Each resolver returns None when it does not
    apply, so the caller falls back to the existing yt-dlp / direct path.
    """
    if not (url or "").startswith(("http://", "https://")):
        return None

    from .brightspot import resolve_brightspot_episode
    from .podcast import resolve_podcast_episode

    for resolver in (resolve_brightspot_episode, resolve_podcast_episode):
        try:
            resolved = resolver(url, fetch=fetch)
        except Exception:
            resolved = None
        if resolved is not None:
            return resolved
    return None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_resolve.py tests/test_podcast.py tests/test_brightspot.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add src/resolve.py tests/test_resolve.py
git commit -m "feat(resolve): register brightspot + podcast resolvers in dispatcher"
```

---

## Task 11: Add source_image_url / source_description to ProcessingMetadata

**Files:**
- Modify: `src/models.py:201-240`
- Test: `tests/test_models.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_models.py
from src.models import ProcessingMetadata


def test_processing_metadata_roundtrips_source_image_and_description():
    pm = ProcessingMetadata(
        source_image_url="https://cdn/art.jpg",
        source_description="Guest talks housing.",
    )
    d = pm.to_dict()
    assert d["source_image_url"] == "https://cdn/art.jpg"
    assert d["source_description"] == "Guest talks housing."
    back = ProcessingMetadata.from_dict(d)
    assert back.source_image_url == "https://cdn/art.jpg"
    assert back.source_description == "Guest talks housing."


def test_processing_metadata_omits_absent_source_fields():
    d = ProcessingMetadata().to_dict()
    assert "source_image_url" not in d
    assert "source_description" not in d
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_models.py::test_processing_metadata_roundtrips_source_image_and_description -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'source_image_url'`.

- [ ] **Step 3: Write minimal implementation**

In `src/models.py`, add two fields to the `ProcessingMetadata` dataclass (after `source_chapters`):

```python
    source_chapters: Optional[list] = None
    source_image_url: Optional[str] = None
    source_description: Optional[str] = None
```

In `to_dict`, after the `source_chapters` block:

```python
        if self.source_chapters is not None:
            d["source_chapters"] = self.source_chapters
        if self.source_image_url is not None:
            d["source_image_url"] = self.source_image_url
        if self.source_description is not None:
            d["source_description"] = self.source_description
        return d
```

In `from_dict`, add:

```python
            source_chapters=d.get("source_chapters"),
            source_image_url=d.get("source_image_url"),
            source_description=d.get("source_description"),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_models.py
git commit -m "feat(models): ProcessingMetadata source_image_url + source_description"
```

---

## Task 12: normalize_audio consumes resolve_source

**Files:**
- Modify: `src/ingest.py:168-249`
- Test: `tests/test_resolve_ingest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_resolve_ingest.py
from __future__ import annotations

from pathlib import Path

import pytest

from src import ingest
from src.resolve import ResolvedSource


@pytest.fixture
def _stub_ffmpeg(monkeypatch, tmp_path):
    # Make ffmpeg checks/commands no-ops and give a fake duration.
    monkeypatch.setattr(ingest, "check_ffmpeg_installed", lambda: True)
    monkeypatch.setattr(ingest.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(ingest, "get_audio_duration", lambda p: 123.0)


def test_normalize_audio_uses_resolved_metadata_and_saves_reference(
    monkeypatch, tmp_path, _stub_ffmpeg
):
    resolved = ResolvedSource(
        audio_url="https://cdn/ep.mp3",
        title="Ep 1",
        date="2026-06-03",
        outlet="What's Next LA",
        description="00:30 Intro\n02:10 Zoning\nGuest talks housing",
        image_url="https://cdn/art.jpg",
        transcript="Host: hi.\n\nGuest: hello.",
        resolver="podcast",
    )
    monkeypatch.setattr(ingest, "_resolve_source_safe", lambda url: resolved)

    downloaded = {}
    def _fake_download(url, out, cookies_file=None, progress=True):
        downloaded["url"] = url
        Path(out).write_bytes(b"x")
        return Path(out)
    # download_from_url is imported inside normalize_audio from .download
    import src.download as dl
    monkeypatch.setattr(dl, "download_from_url", _fake_download)

    out = tmp_path / "audio.wav"
    meta = ingest.normalize_audio("https://show/ep-1", out)

    assert downloaded["url"] == "https://cdn/ep.mp3"          # enclosure, not page
    assert meta["source_title"] == "Ep 1"
    assert meta["source_channel"] == "What's Next LA"
    assert meta["source_upload_date"] == "2026-06-03"
    assert meta["source_image_url"] == "https://cdn/art.jpg"
    assert meta["source_description"].startswith("00:30 Intro")
    # chapters parsed from the description
    assert any(c["title"] == "Zoning" for c in meta["source_chapters"])
    # reference transcript written next to the wav
    assert (out.parent / "reference_transcript.txt").read_text().startswith("Host: hi.")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_resolve_ingest.py -v`
Expected: FAIL — `AttributeError: module 'src.ingest' has no attribute '_resolve_source_safe'`.

- [ ] **Step 3: Write minimal implementation**

In `src/ingest.py`, add a safe wrapper near the top (after imports):

```python
def _resolve_source_safe(url: str):
    """resolve_source that never raises — returns None on any failure."""
    try:
        from .resolve import resolve_source

        return resolve_source(url)
    except Exception:
        return None
```

Then, inside `normalize_audio`, replace the URL-download block (currently the
`if _is_url(source_str):` branch that fetches yt-dlp metadata) with:

```python
    # Download from URL if needed
    source_title = None
    source_channel = None
    source_chapters: list[dict] = []
    source_upload_date = None
    source_image_url = None
    source_description = None
    if _is_url(source_str):
        from .download import download_from_url, is_ytdlp_url

        download_path = output_path.parent / "source.mp4"
        resolved = _resolve_source_safe(source_str)
        if resolved is not None:
            print(f"  Resolved {resolved.resolver} source; downloading audio...")
            actual_path = download_from_url(resolved.audio_url, download_path)
            ffmpeg_input = str(actual_path)
            source_title = resolved.title
            source_channel = resolved.outlet
            source_chapters = parse_description_chapters(resolved.description)
            source_upload_date = resolved.date
            source_image_url = resolved.image_url
            source_description = resolved.description
            if resolved.transcript:
                (output_path.parent / "reference_transcript.txt").write_text(
                    resolved.transcript, encoding="utf-8"
                )
        else:
            print(f"  Downloading from URL...")
            actual_path = download_from_url(
                source_str, download_path, cookies_file=cookies_file
            )
            ffmpeg_input = str(actual_path)
            if is_ytdlp_url(source_str):
                meta = fetch_source_metadata(source_str)
                source_title = meta["title"]
                source_channel = meta["channel"]
                source_chapters = meta["chapters"]
                source_upload_date = meta["upload_date"]
    else:
        ffmpeg_input = str(Path(input_path))
```

Finally, extend the returned dict (add the three new keys alongside the existing
`source_*` keys):

```python
        "source_title": source_title,
        "source_channel": source_channel,
        "source_chapters": source_chapters,
        "source_upload_date": source_upload_date,
        "source_image_url": source_image_url,
        "source_description": source_description,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_resolve_ingest.py tests/test_source_meta.py -v`
Expected: PASS (new test + existing yt-dlp metadata tests still green).

- [ ] **Step 5: Commit**

```bash
git add src/ingest.py tests/test_resolve_ingest.py
git commit -m "feat(ingest): normalize_audio resolves podcast/CMS sources"
```

---

## Task 13: run_local Stage 1 — persist new metadata

**Files:**
- Modify: `run_local.py` (Stage 1 block, around lines 872-884)

- [ ] **Step 1: Extend the Stage-1 metadata persistence**

In `run_local.py`, the Stage 1 block currently copies `source_title` /
`source_channel` / `source_chapters` from the `metadata` dict onto
`meeting.processing_metadata`. Immediately after those three `if metadata.get(...)`
lines, add:

```python
        if metadata.get("source_image_url"):
            meeting.processing_metadata.source_image_url = metadata["source_image_url"]
        if metadata.get("source_description"):
            meeting.processing_metadata.source_description = metadata["source_description"]
```

- [ ] **Step 2: Manual sanity check (no automated test — run_local is the CLI orchestrator)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('run_local.py').read()); print('run_local.py parses OK')"`
Expected: `run_local.py parses OK`.

- [ ] **Step 3: Commit**

```bash
git add run_local.py
git commit -m "feat(run_local): persist source_image_url + source_description at ingest"
```

---

## Task 14: Artwork thumbnail fallback

**Files:**
- Modify: `src/thumbnail.py`
- Test: `tests/test_thumbnail.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_thumbnail.py
from src.thumbnail import download_image


def test_download_image_writes_file(tmp_path, monkeypatch):
    import src.thumbnail as th

    class _Resp:
        content = b"\xff\xd8jpegbytes"
        def raise_for_status(self): pass

    monkeypatch.setattr(th, "requests", type("R", (), {"get": staticmethod(lambda *a, **k: _Resp())}))
    out = tmp_path / "art.jpg"
    assert download_image("https://cdn/art.jpg", out) == out
    assert out.read_bytes() == b"\xff\xd8jpegbytes"


def test_attach_thumbnail_uses_artwork_when_no_video(tmp_path, monkeypatch):
    import src.thumbnail as th

    # No video file present.
    monkeypatch.setattr(th, "find_video_file", lambda d, s: None)
    monkeypatch.setattr(th, "download_image", lambda url, out: Path(out).write_bytes(b"x") or Path(out))
    monkeypatch.setattr("src.storage.upload_thumbnail", lambda path, mid: "https://bucket/thumb.jpg")

    class _M:
        audio_source = "https://show/ep"
        clip_start_seconds = None
        duration_seconds = 60.0
        meeting_id = "2026-07-15-podcast"
        thumbnail_url = None
        class processing_metadata:
            source_image_url = "https://cdn/art.jpg"

    m = _M()
    th.attach_thumbnail(m, tmp_path)
    assert m.thumbnail_url == "https://bucket/thumb.jpg"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_thumbnail.py::test_download_image_writes_file -v`
Expected: FAIL — `ImportError: cannot import name 'download_image'`.

- [ ] **Step 3: Write minimal implementation**

In `src/thumbnail.py`, add `import requests` at the top, then add:

```python
def download_image(url: str, out_path: Path) -> Optional[Path]:
    """Download an image to out_path; return it, or None on failure."""
    try:
        resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        Path(out_path).write_bytes(resp.content)
    except Exception as exc:
        logger.warning("artwork download failed: %s", exc)
        return None
    return Path(out_path) if Path(out_path).exists() else None
```

Then modify `attach_thumbnail` so that when there is no video file it falls back
to resolver artwork. Replace the `video_path = ...; if not video_path: return`
section with:

```python
        video_path = find_video_file(meeting_dir, meeting.audio_source)
        out = Path(meeting_dir) / "thumbnail.jpg"
        if not video_path:
            # Audio-only source: use the resolver-provided artwork, if any.
            image_url = getattr(meeting.processing_metadata, "source_image_url", None)
            if not image_url:
                return
            if download_image(image_url, out):
                url = upload_thumbnail(out, meeting.meeting_id)
                if url:
                    meeting.thumbnail_url = url
                    logger.info("Thumbnail (artwork): %s", url)
            return
        if extract_thumbnail(
            video_path, meeting.clip_start_seconds, meeting.duration_seconds, out
        ):
```

(Keep the remaining `extract_thumbnail` success body — the `url = upload_thumbnail(...)`
lines — exactly as it is today.)

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_thumbnail.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/thumbnail.py tests/test_thumbnail.py
git commit -m "feat(thumbnail): fall back to resolver artwork for audio-only sources"
```

---

## Task 15: Add the `podcast` event kind

**Files:**
- Modify: `src/event_kinds.py`, `src/summarize.py:20`, `src/config.py`, `gui/formmeta.py`
- Test: `tests/test_event_kinds.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_event_kinds.py
from src import event_kinds
from src.summarize import _INTERVIEW_KINDS


def test_podcast_is_a_valid_event_kind():
    assert "podcast" in event_kinds.EVENT_KINDS
    assert event_kinds.validate_event_kind("podcast") == "podcast"


def test_podcast_uses_interview_summarization_path():
    assert "podcast" in _INTERVIEW_KINDS
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_event_kinds.py::test_podcast_is_a_valid_event_kind -v`
Expected: FAIL — `podcast` not in `EVENT_KINDS`.

- [ ] **Step 3: Write minimal implementation**

In `src/event_kinds.py`, add `"podcast"` to the `EVENT_KINDS` tuple (after
`"press_conference"`):

```python
    "news_clip",
    "press_conference",
    "podcast",
    "other",
```

Also add podcast to the campaign-style local roles in `LOCAL_ROLE_SETS`
(a podcast is host + guest, like an interview):

```python
    "press_conference": ("official", "staff", "presenter", "public_comment"),
    "podcast": ("candidate", "moderator", "panelist"),
    "forum": _CAMPAIGN_ROLES,
```

In `src/summarize.py:20`, extend the set:

```python
_INTERVIEW_KINDS = {"news_clip", "press_conference", "podcast"}
```

In `src/config.py` `GATE_THRESHOLDS`, add a podcast row next to the interview kinds:

```python
    "news_clip":        {"high": 0.90, "low": 0.50},
    "press_conference": {"high": 0.90, "low": 0.50},
    "podcast":          {"high": 0.90, "low": 0.50},
```

In `gui/formmeta.py`, add entries to both dicts (keys must equal `EVENT_KINDS`;
a test enforces this):

```python
# in EVENT_KIND_HELP
    "press_conference": "A subject making a statement and taking questions.",
    "podcast": "A podcast or radio interview episode (audio-only).",
    "other": "Anything else.",
```

```python
# in MEETING_TYPE_DEFAULTS
    "press_conference": "Press Conference",
    "podcast": "Podcast",
    "other": "Recording",
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_event_kinds.py tests/test_gui_env.py tests/test_summarize.py -v`
Expected: PASS (the formmeta/EVENT_KINDS sync test in the GUI suite stays green).

- [ ] **Step 5: Commit**

```bash
git add src/event_kinds.py src/summarize.py src/config.py gui/formmeta.py tests/test_event_kinds.py
git commit -m "feat: add podcast event kind (interview summarization path)"
```

---

## Task 16: `audio` playback kind + migration

**Files:**
- Modify: `src/publish.py`
- Create: `supabase/migrations/0006_playback_kind_audio.sql`
- Test: `tests/test_publish.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_publish.py
def test_resolve_playback_audio_mp3():
    url = "https://cpa.ds.npr.org/s385/audio/2026/07/ep.mp3"
    assert resolve_playback(url) == ("audio", url)


def test_resolve_playback_audio_m4a():
    url = "https://cdn/ep.m4a"
    assert resolve_playback(url) == ("audio", url)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_publish.py::test_resolve_playback_audio_mp3 -v`
Expected: FAIL — returns `(None, None)`.

- [ ] **Step 3: Write minimal implementation**

In `src/publish.py`, add an audio-extensions constant near `_DIRECT_FILE_EXTENSIONS`:

```python
_DIRECT_FILE_EXTENSIONS = (".mp4", ".m4v", ".mov", ".webm")
_AUDIO_EXTENSIONS = (".mp3", ".m4a", ".aac", ".ogg", ".wav")
```

In `resolve_playback`, before the `.m3u8` check, add:

```python
    if path.endswith(_AUDIO_EXTENSIONS):
        return "audio", source

    if path.endswith(".m3u8"):
        return "hls", source
```

Create `supabase/migrations/0006_playback_kind_audio.sql`:

```sql
-- civic.meetings.playback_kind gains an 'audio' value for podcast / radio
-- episodes (a direct MP3/M4A enclosure). The column is plain text with no CHECK
-- constraint, so this migration only updates the documentation comment; no data
-- or schema change is required to start writing 'audio'.
COMMENT ON COLUMN civic.meetings.playback_kind IS
  '''youtube'' | ''file'' | ''hls'' | ''audio'' | null (extensible: ''vimeo'', ''self_hosted''...)';
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_publish.py -v`
Expected: PASS (existing + 2 new).

- [ ] **Step 5: Commit**

```bash
git add src/publish.py supabase/migrations/0006_playback_kind_audio.sql tests/test_publish.py
git commit -m "feat(publish): 'audio' playback kind for podcast/radio MP3 enclosures"
```

---

## Task 17: Show-notes summarizer hint

**Files:**
- Modify: `src/summarize.py`
- Test: `tests/test_summarize.py`

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_summarize.py
from src.summarize import _show_notes_hint
from src.models import Meeting, ProcessingMetadata


def test_show_notes_hint_present():
    m = Meeting(meeting_id="x")
    m.processing_metadata = ProcessingMetadata(source_description="Guest: Mayor Kerry Thomson on housing.")
    hint = _show_notes_hint(m)
    assert "Show notes" in hint
    assert "Kerry Thomson" in hint


def test_show_notes_hint_empty_when_absent():
    m = Meeting(meeting_id="x")
    assert _show_notes_hint(m) == ""
```

(If `Meeting(meeting_id="x")` needs more required args, mirror the construction
already used elsewhere in `tests/test_summarize.py`.)

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_summarize.py::test_show_notes_hint_present -v`
Expected: FAIL — `ImportError: cannot import name '_show_notes_hint'`.

- [ ] **Step 3: Write minimal implementation**

In `src/summarize.py`, add a helper near `_resolve_outlet`:

```python
def _show_notes_hint(meeting: Meeting) -> str:
    """A short 'Show notes:' block for interview classification prompts.

    Podcast/radio notes often name the guest and topics, improving section
    classification. Empty string when there are no notes.
    """
    notes = (meeting.processing_metadata.source_description or "").strip()
    if not notes:
        return ""
    return f"\n\nShow notes (context, may name the guest/topics):\n{notes[:2000]}"
```

Then, in `generate_summary`, where the interview path builds `chapter_hint` and
calls `_classify_sections_interview`, append the show-notes hint. Find the
interview classify call and change it to pass both hints — locate:

```python
    is_interview = meeting.event_kind in _INTERVIEW_KINDS
```

and where the interview branch calls `_classify_sections_interview(client, segments, chapter_hint=...)`,
change the passed hint to:

```python
        chapter_hint=chapter_hint + _show_notes_hint(meeting),
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_summarize.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/summarize.py tests/test_summarize.py
git commit -m "feat(summarize): feed show notes as an interview classification hint"
```

---

## Task 18: Transcript-as-corrector — pure reconciliation function

**Files:**
- Create: `src/reconcile.py`
- Test: `tests/test_reconcile.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_reconcile.py
from __future__ import annotations

from dataclasses import dataclass

from src.reconcile import word_overlap_ratio, reconcile_segments


@dataclass
class _Seg:
    segment_id: int
    start_time: float
    end_time: float
    speaker_label: str
    text: str


def _segs():
    return [
        _Seg(0, 0.0, 2.0, "A", "welcome to ask the mare"),   # 'mare' misheard
        _Seg(1, 2.0, 4.0, "B", "glad to be here"),
    ]


def test_word_overlap_ratio_high_and_low():
    assert word_overlap_ratio("welcome to the show", "welcome to the show") == 1.0
    assert word_overlap_ratio("welcome to the show", "utterly different words here") < 0.3


def test_reconcile_applies_corrections_preserving_timing_and_speaker():
    reference = "Welcome to Ask the Mayor.\n\nGlad to be here."
    def fake_llm(prompt):
        # Return corrected text keyed by segment index.
        return '{"0": "Welcome to Ask the Mayor.", "1": "Glad to be here."}'
    segs = _segs()
    out, applied = reconcile_segments(segs, reference, call_llm=fake_llm)
    assert applied is True
    assert out[0].text == "Welcome to Ask the Mayor."
    assert out[0].start_time == 0.0 and out[0].end_time == 2.0
    assert out[0].speaker_label == "A"
    assert out[1].text == "Glad to be here."


def test_reconcile_skipped_when_overlap_too_low():
    reference = "This transcript is about something else entirely, unrelated."
    called = {"n": 0}
    def fake_llm(prompt):
        called["n"] += 1
        return "{}"
    segs = _segs()
    out, applied = reconcile_segments(segs, reference, call_llm=fake_llm, min_overlap=0.5)
    assert applied is False
    assert called["n"] == 0                     # never calls the LLM
    assert out[0].text == "welcome to ask the mare"   # unchanged


def test_reconcile_noop_when_no_reference():
    segs = _segs()
    out, applied = reconcile_segments(segs, "", call_llm=lambda p: "{}")
    assert applied is False
    assert out is segs
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_reconcile.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.reconcile'`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/reconcile.py
"""Transcript-as-corrector.

When a source publishes a clean transcript (e.g. an NPR/Brightspot article
page), we still run Whisper + diarization for timestamps and speaker turns, then
use the clean transcript as an LLM reference to fix proper nouns, mishearings,
and punctuation — WITHOUT changing any timing or speaker attribution. If the
reference and the Whisper output don't overlap enough, reconciliation is skipped
so a mismatched reference can never corrupt the segments.

Pure and injectable: pass a `call_llm(prompt) -> str` so tests need no network.
"""
from __future__ import annotations

import json
import re

_WORD_RE = re.compile(r"[a-z0-9']+")
_CHUNK_SEGMENTS = 40  # segments per LLM call, to bound prompt size


def _words(text: str) -> set[str]:
    return set(_WORD_RE.findall((text or "").lower()))


def word_overlap_ratio(a: str, b: str) -> float:
    """Jaccard overlap of the word sets of a and b (0.0-1.0)."""
    wa, wb = _words(a), _words(b)
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _build_prompt(chunk, reference: str) -> str:
    lines = [f"{s.segment_id}: {s.text}" for s in chunk]
    return (
        "You are correcting an automatic (Whisper) transcript using a clean "
        "reference transcript of the SAME audio.\n"
        "Fix proper nouns, mishearings, and punctuation ONLY. Do NOT merge, "
        "split, reorder, add, or drop segments. Return STRICT JSON mapping each "
        "segment id (string) to its corrected text.\n\n"
        f"Reference transcript:\n{reference}\n\n"
        f"Whisper segments:\n" + "\n".join(lines) + "\n\n"
        'Return only JSON, e.g. {"0": "Corrected text.", "1": "..."}'
    )


def reconcile_segments(segments, reference_text: str, *, call_llm, min_overlap: float = 0.30):
    """Correct segment text against a reference transcript.

    Returns (segments, applied). `applied` is False (and segments are returned
    unchanged) when there is no reference or the word overlap is below
    min_overlap. Timing and speaker_label are never modified.
    """
    reference = (reference_text or "").strip()
    if not reference or not segments:
        return segments, False

    whisper_text = " ".join(s.text for s in segments)
    if word_overlap_ratio(whisper_text, reference) < min_overlap:
        return segments, False

    by_id = {s.segment_id: s for s in segments}
    for i in range(0, len(segments), _CHUNK_SEGMENTS):
        chunk = segments[i:i + _CHUNK_SEGMENTS]
        try:
            raw = call_llm(_build_prompt(chunk, reference))
            match = re.search(r"\{[\s\S]*\}", raw)
            corrections = json.loads(match.group()) if match else {}
        except Exception:
            corrections = {}
        for sid, text in corrections.items():
            try:
                seg = by_id.get(int(sid))
            except (TypeError, ValueError):
                seg = None
            if seg is not None and isinstance(text, str) and text.strip():
                seg.text = text.strip()

    return segments, True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_reconcile.py -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Commit**

```bash
git add src/reconcile.py tests/test_reconcile.py
git commit -m "feat(reconcile): transcript-as-corrector preserving timing/speakers"
```

---

## Task 19: Wire reconciliation into run_local after Stage 3

**Files:**
- Modify: `run_local.py` (after the Stage 3 / TRANSCRIBED block, before Stage 4)

- [ ] **Step 1: Add the reconciliation step**

In `run_local.py`, after Stage 3 completes (the block around lines 1188-1229 that
ends with `state.mark_complete(PipelineStage.TRANSCRIBED)`), and before Stage 4,
insert:

```python
    # ------------------------------------------------------------------
    # Stage 3.5: Reconcile transcript against a source-provided reference
    # ------------------------------------------------------------------
    reference_path = meeting_dir / "reference_transcript.txt"
    reconciled_marker = meeting_dir / "reconciled.done"
    if reference_path.exists() and not reconciled_marker.exists() and meeting.segments:
        try:
            import anthropic

            from src import config as _cfg
            from src.reconcile import reconcile_segments

            client = anthropic.Anthropic()

            def _call_llm(prompt: str) -> str:
                msg = client.messages.create(
                    model=_cfg.SUMMARY_CLASSIFY_MODEL,
                    max_tokens=4096,
                    messages=[{"role": "user", "content": prompt}],
                )
                return msg.content[0].text

            reference_text = reference_path.read_text(encoding="utf-8")
            _, applied = reconcile_segments(
                meeting.segments, reference_text, call_llm=_call_llm
            )
            if applied:
                # Persist corrected text so review/export/publish see it.
                from src.checkpoint import save_transcript_named

                save_transcript_named(meeting, meeting_dir)
                print("  Reconciled transcript against source reference.")
            else:
                print("  Reference transcript overlap too low; kept Whisper text.")
            reconciled_marker.write_text("done", encoding="utf-8")
        except Exception as exc:  # never fatal — timestamps/segments are intact
            print(f"  Transcript reconciliation skipped ({exc}).")
```

**Note on `save_transcript_named`:** confirm the exact serializer used elsewhere
in `run_local.py` to persist `transcript_named.json` (grep for `transcript_named`
in `run_local.py`), and call that same function/inline write here. If run_local
persists it inline rather than via a helper, replicate that exact write.

- [ ] **Step 2: Sanity check parse**

Run: `.venv/bin/python -c "import ast; ast.parse(open('run_local.py').read()); print('OK')"`
Expected: `OK`.

- [ ] **Step 3: Run the full suite**

Run: `.venv/bin/pytest -q`
Expected: PASS (no regressions).

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "feat(run_local): reconcile transcript against source reference (stage 3.5)"
```

---

## Task 20: source_key regression coverage for episode page URLs

**Files:**
- Test: `tests/test_source_key.py`

**Rationale:** `source_key()` already normalizes any `http(s)` URL to a stable
`url:host/path` identity (dropping tracking params), which correctly dedups a
pasted episode page URL. No production code change is needed; lock the behavior
with a regression test so a future refactor can't silently break podcast dedup.

- [ ] **Step 1: Write the test**

```python
# add to tests/test_source_key.py
from src.source_key import source_key


def test_source_key_stable_for_episode_page_with_tracking_params():
    a = source_key("https://show.buzzsprout.com/1414123/ep-1-housing")
    b = source_key("https://show.buzzsprout.com/1414123/ep-1-housing/?utm_source=x&si=y")
    assert a == b
    assert a.startswith("url:show.buzzsprout.com/1414123/ep-1-housing")


def test_source_key_distinct_for_different_episodes():
    a = source_key("https://www.ipm.org/show/askthemayor/2026-07-15/a")
    b = source_key("https://www.ipm.org/show/askthemayor/2026-07-15/b")
    assert a != b
```

- [ ] **Step 2: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_source_key.py -v`
Expected: PASS (existing url: normalization already satisfies this).

- [ ] **Step 3: Commit**

```bash
git add tests/test_source_key.py
git commit -m "test(source_key): lock stable dedup for episode page URLs"
```

---

## Final verification

- [ ] **Run the whole test suite**

Run: `.venv/bin/pytest -q`
Expected: all green.

- [ ] **Optional live smoke test (network; do only if you have API keys + audio time)**

Process one real episode end-to-end (a short one), e.g.:

```bash
.venv/bin/python run_local.py \
  --event-kind podcast \
  --date 2026-07-15 \
  --title "Ask the Mayor — Thomson" \
  "https://www.ipm.org/show/askthemayor/2026-07-15/bloomingtons-thomson-on-seminary-pointe-land-swap-jail-and-your-questions"
```

Confirm: audio downloads from the NPR CDN, `source_channel` = the station,
`thumbnail.jpg` comes from artwork, `reference_transcript.txt` is written, and
Stage 3.5 reports reconciliation applied.

---

## Self-Review (completed during authoring)

**Spec coverage:**
- Pluggable resolvers + `ResolvedSource` → Tasks 2, 6, 9, 10. ✓
- Podcast RSS resolver (autodiscovery, parse, match, no-blind-trust) → Tasks 3-6. ✓
- Brightspot/NPR CMS resolver (JSON-LD, MP3 select, transcript) → Tasks 7-9. ✓
- Metadata mapping (title/outlet/date/description/image) → Tasks 11-13. ✓
- Transcript-as-corrector (Whisper timing + reference correction, overlap gate) → Tasks 18-19. ✓
- Artwork thumbnail fallback → Task 14. ✓
- `podcast` event kind + interview path → Task 15. ✓
- `audio` playback kind + migration → Task 16. ✓
- Show-notes → chapters (via `parse_description_chapters` in Task 12) + summarizer hint (Task 17). ✓
- Detection with graceful fallback → Tasks 10, 12. ✓
- Source dedup → Task 20 (existing behavior, locked by test). ✓
- Testing (fixture-based, no network) → every task. ✓
- Out of scope (front-end audio player, feed subscriptions, text-only civic sources) → not implemented, per spec. ✓

**Type consistency:** `ResolvedSource` field names are identical across Tasks 2/6/9/10/12. `_resolve_source_safe` (Task 12) wraps `resolve_source` (Task 10). `source_image_url` / `source_description` names match across Tasks 11/12/13/14/17. `reconcile_segments` signature matches between Task 18 (definition) and Task 19 (call).

**Placeholder scan:** No TBD/TODO. The one flagged confirmation (exact `transcript_named.json` serializer name in Task 19) is an explicit "grep and match existing" instruction, not a placeholder — the surrounding code is complete.
