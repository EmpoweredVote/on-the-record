"""Watchlist feed fetching/parsing: YouTube channel Atom + podcast RSS + generic news RSS/Atom (web_rss).

Parsing is pure (string in, RawItems out); only _fetch_text/_fetch_bytes touch
the network. web_page outlets are a registered-but-unpolled kind in v1.

Web-lane fetches (web_rss feed, page-text peek, robots.txt) identify
themselves via WEB_USER_AGENT, respect robots.txt (including treating a
401/403 on robots.txt itself as a bot wall -> deny), and pace themselves
per-origin via _polite_pause. The YouTube/podcast lanes are unaffected —
they keep the original Mozilla UA and no robots gate (spec scope: only the
open-web watchlist layer needs the ToS-posture hardening).
"""
from __future__ import annotations

import html as html_
import re
import time
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import requests

from src import config
from src.discovery.models import Outlet, RawItem

_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "yt": "http://www.youtube.com/xml/schemas/2015",
    "media": "http://search.yahoo.com/mrss/",
}

UA_TOKEN = "CouncilScribeBot"
WEB_USER_AGENT = ("CouncilScribeBot/1.0 (+https://empowered.vote; "
                  "non-commercial civic source discovery)")


class RobotsDenied(Exception):
    """Raised by the real robots.txt fetch on 401/403: the robots.txt itself
    sits behind a bot wall, which we read as a clear no (not the usual
    "missing robots.txt means allowed")."""


def _fetch_text(url: str) -> str:
    """Plain-text fetch for the YouTube/podcast lanes (unaffected by the
    web-lane ToS hardening below)."""
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def parse_youtube_feed(xml_text: str, *, outlet_id: "str | None" = None) -> list:
    root = ET.fromstring(xml_text)
    items = []
    for entry in root.findall("atom:entry", _NS):
        video_id = entry.findtext("yt:videoId", default="", namespaces=_NS)
        link = entry.find("atom:link[@rel='alternate']", _NS)
        url = link.get("href") if link is not None else (
            f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        if not url:
            continue
        items.append(RawItem(
            url=url,
            title=entry.findtext("atom:title", default=None, namespaces=_NS),
            description=entry.findtext("media:group/media:description",
                                       default=None, namespaces=_NS),
            channel_name=entry.findtext("atom:author/atom:name",
                                        default=None, namespaces=_NS),
            channel_id=entry.findtext("yt:channelId", default=None, namespaces=_NS),
            channel_url=entry.findtext("atom:author/atom:uri",
                                       default=None, namespaces=_NS),
            published_at=entry.findtext("atom:published", default=None, namespaces=_NS),
            outlet_id=outlet_id,
            via="watchlist",
        ))
    return items


def parse_podcast_feed(xml_text: str, *, outlet_id: "str | None" = None) -> list:
    root = ET.fromstring(xml_text)
    items = []
    for item in root.findall("./channel/item"):
        page = item.findtext("link")
        if not page:
            guid = item.find("guid")
            if guid is not None and guid.get("isPermaLink", "true").lower() == "true":
                text = (guid.text or "").strip()
                if text.startswith(("http://", "https://")):
                    page = text
        enclosure = item.find("enclosure")
        url = page or (enclosure.get("url") if enclosure is not None else None)
        if not url:
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).isoformat()
            except (TypeError, ValueError):
                published = None
        items.append(RawItem(
            url=url,
            title=item.findtext("title"),
            description=item.findtext("description"),
            channel_name=root.findtext("./channel/title"),
            published_at=published,
            outlet_id=outlet_id,
            via="watchlist",
        ))
    return items


# --- HTML -> text ------------------------------------------------------------

_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
_BLOCK_RE = re.compile(
    r"<(script|style|nav|header|footer|aside|form|noscript|svg|iframe|template)"
    r"\b[\s\S]*?</\1>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[/!]?[a-zA-Z][^>]*>")  # requires a letter (optionally
# after / or !) right after '<' — political headlines legitimately contain
# "< 50%" / "> 40%" comparisons that the looser <[^>]+> pattern ate whole


def _html_to_text(raw: "str | bytes", max_chars: "int | None" = None) -> str:
    """Shared HTML -> text scrub: strips comments (which can carry
    prompt-injection-style hidden text), then whole block elements a reader
    never reads (script/style/nav/header/footer/aside/form/noscript/svg/
    iframe/template — widened past the original script/style-only cut),
    then remaining tags, unescapes entities, and collapses whitespace.

    Used for the page-text peek AND for feed title/description fields:
    `Laura <em>Kelly</em>` and a literal `&nbsp;` both silently broke
    candidate-name matching downstream, and unstripped entity/tag residue
    was reaching the GUI and the classify prompt."""
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8", errors="replace")
    text = _COMMENT_RE.sub(" ", raw)
    text = _BLOCK_RE.sub(" ", text)
    text = _TAG_RE.sub(" ", text)
    text = html_.unescape(text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars] if max_chars is not None else text


def _clean(value: "str | None") -> "str | None":
    """Run _html_to_text over a possibly-missing feed field, preserving None
    for a genuinely absent tag instead of turning it into ''."""
    return _html_to_text(value) if value else None


# --- robots.txt + per-origin politeness --------------------------------------

_robots_cache: dict = {}
_last_fetch_at: dict = {}


def _origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def _polite_pause(origin: str, *, crawl_delay: float = 0.0, sleep_fn=time.sleep) -> None:
    """Origin-keyed politeness gate, called before every web-lane network hit
    (robots fetch, feed fetch, page fetch): keeps consecutive contacts to the
    same origin spaced by at least DISCOVERY_WEB_FETCH_SLEEP_SECONDS, or the
    site's own robots.txt Crawl-delay when that's larger."""
    required = max(config.DISCOVERY_WEB_FETCH_SLEEP_SECONDS, crawl_delay or 0.0)
    now = time.monotonic()
    last = _last_fetch_at.get(origin)
    if last is not None:
        remaining = required - (now - last)
        if remaining > 0:
            sleep_fn(remaining)
    _last_fetch_at[origin] = time.monotonic()


def _fetch_robots_text(url: str) -> str:
    """Default robots.txt fetcher used by _robots_allowed when no
    fetch_text_fn is injected. Identifies via WEB_USER_AGENT."""
    resp = requests.get(url, timeout=(10, 30), headers={"User-Agent": WEB_USER_AGENT})
    if resp.status_code in (401, 403):
        # A bot wall on robots.txt itself is a clear "no" — don't fall
        # through to the "missing robots.txt means allowed" default below.
        raise RobotsDenied(f"{resp.status_code} fetching {url}")
    if 400 <= resp.status_code < 500:
        return ""  # other 4xx (typically 404 = no robots.txt) -> no rules -> allow, RFC 9309
    resp.raise_for_status()  # 5xx -> exception, caught by _robots_allowed's
    # broad except -> allowed. Simplification: at our volume a transient
    # outage on the target's own robots.txt shouldn't permanently block a
    # small civic-discovery crawl (RFC 9309 would have us treat 5xx as deny).
    return resp.text


def _robots_allowed(url: str, fetch_text_fn=None) -> bool:
    """Mechanical robots.txt respect for every web_rss fetch (spec Q8).
    Missing/unreachable robots.txt (the common case) means allowed. A
    401/403 on robots.txt itself (RobotsDenied) means the opposite."""
    fetch = fetch_text_fn or _fetch_robots_text
    origin = _origin(url)
    if origin not in _robots_cache:
        if fetch_text_fn is None:
            _polite_pause(origin)  # real network hit, not a test double
        rp = RobotFileParser()
        try:
            rp.parse(fetch(f"{origin}/robots.txt").splitlines())
        except RobotsDenied:
            rp = "deny"
        except Exception:  # noqa: BLE001 — no/broken robots.txt -> allowed
            rp = None
        _robots_cache[origin] = rp
    rp = _robots_cache[origin]
    if rp == "deny":
        return False
    if rp is None:
        return True
    return rp.can_fetch(UA_TOKEN, url)


def _crawl_delay_for(origin: str) -> float:
    """The site's own robots.txt Crawl-delay for our UA (falling back to the
    wildcard entry), once robots.txt for this origin has been fetched. 0.0
    when unknown/denied/unset — _polite_pause floors on the configured
    default regardless."""
    rp = _robots_cache.get(origin)
    if rp is None or rp == "deny":
        return 0.0
    return rp.crawl_delay(UA_TOKEN) or rp.crawl_delay("*") or 0.0


def _fetch_bytes(url: str) -> bytes:
    """Web-lane bytes fetch (feed + page GETs). Returns resp.content — raw
    bytes — instead of resp.text, so XML callers let ElementTree honor the
    document's own encoding declaration rather than requests' text-decoding
    guess, which defaults text/* content-types with no charset param to
    latin-1 and measurably mangled accented names (e.g. José Núñez)."""
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": WEB_USER_AGENT})
    resp.raise_for_status()
    return resp.content


_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_DC_DATE = "{http://purl.org/dc/elements/1.1/}date"


def parse_news_feed(xml_text: "str | bytes", *, outlet_id: "str | None" = None,
                    outlet_name: "str | None" = None) -> list:
    """Generic news feed: RSS 2.0 or Atom. Items carry no duration — the
    stage-1 duration heuristics skip them and stage 2 owns depth. Accepts
    str or bytes (bytes lets ElementTree honor the document's own XML
    encoding declaration). Raises on an unrecognized root — an RDF/Atom-0.3
    feed silently yielding [] would otherwise let mark_outlet_polled keep an
    outlet looking healthy forever."""
    # A leading blank line before the XML declaration raises ParseError, for
    # both str and bytes; a leading UTF-8 BOM (common from CMSes that don't
    # strip it) compounds this when followed by whitespace/newlines.
    if isinstance(xml_text, bytes):
        xml_text = xml_text.lstrip(b"\xef\xbb\xbf \t\r\n")
    else:
        xml_text = xml_text.lstrip("﻿").lstrip()
    root = ET.fromstring(xml_text)
    items = []
    if root.tag == f"{_ATOM_NS}feed":
        channel = _clean(root.findtext(f"{_ATOM_NS}title")) or outlet_name
        for entry in root.findall(f"{_ATOM_NS}entry"):
            links = entry.findall(f"{_ATOM_NS}link")
            url = None
            alt = next((l for l in links if l.get("rel") == "alternate"), None)
            if alt is not None:
                href = alt.get("href")
                if href and href.startswith(("http://", "https://")):
                    url = href
            if url is None:
                for l in links:
                    if l.get("rel") == "self":
                        continue  # API/feed-self link, never content
                    href = l.get("href")
                    if href and href.startswith(("http://", "https://")):
                        url = href
                        break
            if not url:
                continue
            items.append(RawItem(
                url=url,
                title=_clean(entry.findtext(f"{_ATOM_NS}title")),
                description=_clean(entry.findtext(f"{_ATOM_NS}summary")
                                   or entry.findtext(f"{_ATOM_NS}content")),
                channel_name=channel,
                published_at=(entry.findtext(f"{_ATOM_NS}published")
                              or entry.findtext(f"{_ATOM_NS}updated")),
                outlet_id=outlet_id, via="watchlist"))
        return items
    if root.tag != "rss":
        raise ValueError(f"unrecognized feed root {root.tag!r}")
    channel = _clean(root.findtext("./channel/title")) or outlet_name
    for item in root.findall("./channel/item"):
        url = (item.findtext("link") or "").strip()
        if not url.startswith(("http://", "https://")):
            continue
        published = None
        raw_date = item.findtext("pubDate")
        if raw_date:
            try:
                published = parsedate_to_datetime(raw_date).isoformat()
            except (TypeError, ValueError):
                published = None
        if published is None:
            dc_date = item.findtext(_DC_DATE)
            if dc_date:
                published = dc_date.strip()  # ISO already, per dc:date convention
        items.append(RawItem(
            url=url, title=_clean(item.findtext("title")),
            description=_clean(item.findtext("description")),
            channel_name=channel, published_at=published,
            outlet_id=outlet_id, via="watchlist"))
    return items


_ARTICLE_OR_MAIN_RE = re.compile(r"<(article|main)\b[^>]*>[\s\S]*?</\1>", re.IGNORECASE)

_PAGE_TEXT_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml")
_PAGE_TEXT_MAX_BYTES = 500_000


def _fetch_page_bytes(url: str, *, max_bytes: int = _PAGE_TEXT_MAX_BYTES) -> "tuple[str, bytes]":
    """Streaming bytes fetch for the page-text peek only (the feed/robots
    fetches keep using _fetch_bytes). Reads at most max_bytes of body — a
    slow or huge page shouldn't stall the peek or bloat memory/prompt size —
    and hands the caller the response's raw Content-Type so it can refuse
    non-HTML/text content before ever decoding it (a podcast .mp3 enclosure
    or a PDF must not dump binary into a prompt)."""
    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": WEB_USER_AGENT},
                        stream=True)
    resp.raise_for_status()
    content_type = resp.headers.get("Content-Type", "")
    chunks = []
    total = 0
    for chunk in resp.iter_content(chunk_size=8192):
        if not chunk:
            continue
        chunks.append(chunk)
        total += len(chunk)
        if total >= max_bytes:
            break
    return content_type, b"".join(chunks)[:max_bytes]


def fetch_page_text(url: str, max_chars: int = 6000, *, sleep_fn=time.sleep) -> str:
    """Article-page text for the stage-2 page peek (web analog of the
    captions peek). Robots-gated and paced like every other web-lane fetch;
    returns '' when disallowed OR when the Content-Type isn't text/html,
    text/plain, or application/xhtml (a non-HTML enclosure must not reach
    the classify prompt). Strips comments/block-chrome FIRST, then prefers
    the LONGEST remaining <article>/<main> slice, falling back to the whole
    cleaned page when the best slice is too short (~200 chars) to be the
    real body. Slicing raw HTML instead let a small <article> teaser nested
    in an <aside>/<template> rail (a normal station template shape) hijack
    the peek — stripping block chrome first removes the teaser along with
    its wrapper before slicing ever sees it. _html_to_text re-running the
    strips on the chosen slice is idempotent."""
    if not _robots_allowed(url):
        return ""
    origin = _origin(url)
    _polite_pause(origin, crawl_delay=_crawl_delay_for(origin), sleep_fn=sleep_fn)
    content_type, raw = _fetch_page_bytes(url)
    ctype = content_type.split(";")[0].strip().lower()
    if not ctype.startswith(_PAGE_TEXT_CONTENT_TYPES):
        return ""
    html_str = raw.decode("utf-8", errors="replace")
    cleaned = _BLOCK_RE.sub(" ", _COMMENT_RE.sub(" ", html_str))
    match = max(_ARTICLE_OR_MAIN_RE.finditer(cleaned),
                key=lambda m: len(m.group(0)), default=None)
    slice_ = match.group(0) if match and len(match.group(0)) >= 200 else cleaned
    return _html_to_text(slice_, max_chars=max_chars)


def fetch_outlet_items(outlet: Outlet, *, sleep_fn=time.sleep) -> list:
    """Network + parse for one outlet. Raises on HTTP/parse errors — the
    engine catches per-outlet and keeps going."""
    if outlet.kind == "youtube_channel":
        return parse_youtube_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    if outlet.kind == "podcast_rss":
        return parse_podcast_feed(_fetch_text(outlet.feed_url), outlet_id=outlet.id)
    if outlet.kind == "web_rss":
        if not _robots_allowed(outlet.feed_url):
            raise RuntimeError(f"robots.txt disallows {outlet.feed_url}")
        origin = _origin(outlet.feed_url)
        _polite_pause(origin, crawl_delay=_crawl_delay_for(origin), sleep_fn=sleep_fn)
        raw = _fetch_bytes(outlet.feed_url)
        return parse_news_feed(raw, outlet_id=outlet.id, outlet_name=outlet.name)
    if outlet.kind == "web_page":
        return []  # registered but unpolled (v1 behavior, unchanged)
    raise ValueError(f"unknown outlet kind {outlet.kind!r} for {outlet.name}")
