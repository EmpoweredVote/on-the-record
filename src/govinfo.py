# src/govinfo.py
"""GovInfo Congressional Record (CREC) fetch.

Turns a (date, chamber) into the official Congressional Record's ordered speaker
turns for that chamber's floor proceedings. This is a *speaker-identity* source,
not a transcript substitute: CREC is non-verbatim and has no timestamps, but it
records exactly who spoke, in what order.

Parsing is pure and network-free; the only network primitive is an injected
`fetch`. Mirrors src/podcast.py and src/brightspot.py.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Callable, Optional

_API_ROOT = "https://api.govinfo.gov"
_CHAMBER_TO_CLASS = {"house": "HOUSE", "senate": "SENATE"}


@dataclass
class CrecTurn:
    speaker_raw: str      # "Mr. Cotton", "The PRESIDING OFFICER (Mr. Cotton)"
    text: str             # remarks attributed to that speaker
    granule_id: str       # source granule (provenance)
    order: int            # 0-based position across the day's matched granules


def _package_id(date: str) -> str:
    """'YYYY-MM-DD' -> 'CREC-YYYY-MM-DD'."""
    return f"CREC-{date}"


def _resolve_api_key(api_key: Optional[str]) -> str:
    """arg -> GOVINFO_API_KEY env -> 'DEMO_KEY'."""
    return api_key or os.environ.get("GOVINFO_API_KEY") or "DEMO_KEY"
