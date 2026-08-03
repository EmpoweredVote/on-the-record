"""Stage-1 prefilter: free, pure triage of discovered items.

No DB, no network, no yt-dlp. Decides which raw items are worth a
stage-2 LLM verdict. Tuned for recall — the human gate owns precision.
"""
from __future__ import annotations

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


def _has_event_term(title: "str | None") -> bool:
    t = normalize(title)
    return any(term.replace("-", " ") in t for term in EVENT_TERMS)


def prefilter_item(title, description, duration_seconds, full_names) -> PrefilterVerdict:
    matched = match_names(title, description, full_names)
    sig = duration_signal(duration_seconds)
    if not matched:
        return PrefilterVerdict(False, [], sig, "no tracked candidate name")
    if sig == "short" and not _has_event_term(title):
        return PrefilterVerdict(False, matched, sig, "short clip without event term")
    return PrefilterVerdict(True, matched, sig, "name match")
