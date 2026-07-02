"""Normalize a source recording reference (URL or path) to a stable 'source key'.

One source key identifies one recording regardless of how its URL was typed, so
the GUI can detect 'already processed this' before launching a duplicate. Pure
and network-free — see CONTEXT.md 'Source key'."""
from __future__ import annotations

import os
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
             "music.youtube.com", "youtu.be"}
_TRACKING = {"t", "feature", "utm_source", "utm_medium", "utm_campaign", "si", "pp"}


def _youtube_id(parsed) -> str | None:
    host = parsed.netloc.lower()
    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None
    if host in _YT_HOSTS:
        qs = parse_qs(parsed.query)
        if qs.get("v"):
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            return parts[1]
    return None


def source_key(raw: str) -> str:
    """Stable identity for a source. '' for blank input."""
    s = (raw or "").strip()
    if not s:
        return ""
    try:
        parsed = urlparse(s)
    except ValueError:
        # urlparse raises on malformed URLs (e.g. bad IPv6 brackets). source_key
        # runs at ingest in the pipeline, so it must never raise — degrade junk
        # input to the file: fallback (unique-ish; it just won't dedup).
        return f"file:{os.path.abspath(s)}"
    if parsed.scheme in ("http", "https"):
        yid = _youtube_id(parsed)
        if yid:
            return f"youtube:{yid}"
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        q = sorted((k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in _TRACKING)
        qstr = ("?" + urlencode(q)) if q else ""
        return f"url:{host}{path}{qstr}"
    if parsed.scheme == "file":
        return f"file:{os.path.abspath(parsed.path)}"
    return f"file:{os.path.abspath(s)}"
