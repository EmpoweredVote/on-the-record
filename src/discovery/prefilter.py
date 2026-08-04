"""Stage-1 prefilter: free, pure triage of discovered items.

No DB, no network, no yt-dlp. Decides which raw items are worth a
stage-2 LLM verdict. Tuned for recall — the human gate owns precision.
"""
from __future__ import annotations

import datetime as dt
import re
import unicodedata

from src import config
from src.discovery.models import PrefilterVerdict

EVENT_TERMS = (
    "debate", "forum", "town hall", "townhall", "town-hall",
    "interview", "q&a", "one-on-one", "sits down", "candidates",
)


def normalize(text: "str | None") -> str:
    """Lowercase, strip accents and punctuation, collapse whitespace."""
    if not text:
        return ""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def duration_signal(duration_seconds: "int | None") -> str:
    if duration_seconds is None:
        return "unknown"
    if duration_seconds < config.DISCOVERY_SHORT_CLIP_MAX_SECONDS:
        return "short"
    if duration_seconds >= config.DISCOVERY_FULL_EVENT_MIN_SECONDS:
        return "long"
    return "neutral"


def match_names(title: "str | None", description: "str | None",
                 full_names: list) -> list:
    """Full-name contiguous matches only. Single-token names are skipped —
    they are collision bait ('Cher for School Board' matches concert uploads)."""
    hay = f" {normalize(title)} {normalize(description)} "
    out = []
    for name in full_names:
        norm = normalize(name)
        if len(norm.split()) < 2:
            continue
        if f" {norm} " in hay:
            out.append(name)
    return out


def _has_event_term(title: "str | None", description: "str | None") -> bool:
    t = f"{normalize(title)} {normalize(description)}"
    return any(normalize(term) in t for term in EVENT_TERMS)


def prefilter_item(title, description, duration_seconds, full_names) -> PrefilterVerdict:
    matched = match_names(title, description, full_names)
    sig = duration_signal(duration_seconds)
    if not matched:
        return PrefilterVerdict(False, [], sig, "no tracked candidate name")
    if sig == "short" and not _has_event_term(title, description):
        return PrefilterVerdict(False, matched, sig, "short clip without event term")
    return PrefilterVerdict(True, matched, sig, "name match")


def is_stale(published_at: "str | None", today: dt.date) -> bool:
    """True when the item predates the recency window (old-cycle noise —
    the biggest observed reject class). Undated or unparseable dates pass:
    stage 2 owns them."""
    if not published_at:
        return False
    try:
        # [:10] truncates a date/datetime string to its calendar-date prefix
        # rather than converting timezone -- at most a 1-day skew, immaterial
        # against a multi-hundred-day horizon.
        published = dt.date.fromisoformat(str(published_at)[:10])
    except ValueError:
        return False
    return (today - published).days > config.DISCOVERY_MAX_ITEM_AGE_DAYS
