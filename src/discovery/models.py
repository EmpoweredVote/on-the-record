"""Shared dataclasses for the source-discovery engine.

RawItem is the unified shape for anything found by a watchlist feed or a
search sweep; the engine hydrates missing fields via yt-dlp before stage 2.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RawItem:
    url: str
    title: Optional[str] = None
    description: Optional[str] = None
    channel_name: Optional[str] = None
    channel_id: Optional[str] = None
    channel_url: Optional[str] = None
    duration_seconds: Optional[int] = None
    published_at: Optional[str] = None  # ISO date or datetime string
    outlet_id: Optional[str] = None     # set for watchlist finds
    via: str = "watchlist"              # 'watchlist' | 'search' | 'agent'


@dataclass
class Outlet:
    id: str
    name: str
    kind: str          # 'youtube_channel' | 'podcast_rss' | 'web_page'
    feed_url: str
    external_channel_id: Optional[str] = None


@dataclass
class TrackedCandidate:
    politician_id: str
    race_id: str
    full_name: str
    race_label: str
    election_date: Optional[str] = None  # ISO date


@dataclass
class PrefilterVerdict:
    passed: bool
    matched_names: list = field(default_factory=list)
    duration_signal: str = "unknown"  # 'short' | 'long' | 'neutral' | 'unknown'
    reason: str = ""


@dataclass
class Verdict:
    relevant: bool
    confidence: float
    candidates_present: list = field(default_factory=list)
    event_kind_guess: Optional[str] = None
    source_tier_guess: Optional[int] = None
    original_vs_clip: Optional[str] = None  # 'original' | 'clip'
    route: str = "ingest"
    why: str = ""
    rejected_reason: Optional[str] = None   # set when the reply wasn't parseable
