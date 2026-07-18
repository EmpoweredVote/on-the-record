# src/congress_roster.py
"""Current-Congress member roster from the congress-legislators dataset.

Fetches (and caches) the public `legislators-current.json` and builds a
chamber-scoped roster indexed by surname, for resolving Congressional Record
speaker designations to member identities. Pure parsing; network behind an
injected `fetch` (mirrors src/govinfo.py).

Source: https://unitedstates.github.io/congress-legislators/legislators-current.json
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import config

_LEGISLATORS_URL = (
    "https://unitedstates.github.io/congress-legislators/legislators-current.json"
)
_CHAMBER_TO_TERM_TYPE = {"house": "rep", "senate": "sen"}


@dataclass
class CongressMember:
    bioguide: str
    full_name: str
    last_name: str
    state: str
    district: Optional[int]
    chamber: str
    party: Optional[str]


@dataclass
class CongressRoster:
    chamber: str
    members: list[CongressMember] = field(default_factory=list)
    _by_surname: dict[str, list[CongressMember]] = field(default_factory=dict)

    def by_surname(self, surname: str) -> list[CongressMember]:
        """Members whose last name matches `surname` (case-insensitive)."""
        return self._by_surname.get(surname.lower(), [])


def _member_from_raw(entry: dict, chamber: str) -> Optional[CongressMember]:
    """Build a CongressMember from a legislators-current entry when its latest
    term matches the chamber; else None."""
    terms = entry.get("terms") or []
    if not terms:
        return None
    term = terms[-1]
    if term.get("type") != _CHAMBER_TO_TERM_TYPE[chamber.lower()]:
        return None
    name = entry.get("name") or {}
    last = name.get("last")
    if not last:
        return None
    full = name.get("official_full") or f"{name.get('first', '')} {last}".strip()
    return CongressMember(
        bioguide=(entry.get("id") or {}).get("bioguide", ""),
        full_name=full,
        last_name=last,
        state=term.get("state", ""),
        district=term.get("district"),
        chamber=chamber.lower(),
        party=term.get("party"),
    )


def build_roster(raw: list[dict], chamber: str) -> CongressRoster:
    """Chamber-scoped roster from a parsed legislators-current list.

    Keeps members whose latest term matches the chamber, indexed by lowercased
    last name (a list per surname, so same-surname collisions are preserved for
    the normalizer to disambiguate).
    """
    members: list[CongressMember] = []
    by_surname: dict[str, list[CongressMember]] = {}
    for entry in raw:
        m = _member_from_raw(entry, chamber)
        if m is None:
            continue
        members.append(m)
        by_surname.setdefault(m.last_name.lower(), []).append(m)
    return CongressRoster(chamber=chamber.lower(), members=members, _by_surname=by_surname)


def _default_fetch(url: str) -> str:
    import requests

    resp = requests.get(url, timeout=(30, 120), headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    return resp.text


def _default_cache_path():
    return config.CONFIG_DIR / "congress" / "legislators-current.json"


def fetch_current_legislators(
    *, fetch: Callable[[str], str] = _default_fetch, cache_path=None
) -> list[dict]:
    """Fetch and parse legislators-current.json; write it to cache (best-effort)."""
    text = fetch(_LEGISLATORS_URL)
    data = json.loads(text)
    path = cache_path or _default_cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except Exception:
        pass   # cache is an optimization; never fail the fetch on a write error
    return data


def load_current_roster(
    chamber: str, *, fetch: Callable[[str], str] = _default_fetch, cache_path=None
) -> CongressRoster:
    """Chamber roster: read a non-empty cache if present, else fetch + cache."""
    path = cache_path or _default_cache_path()
    raw = None
    try:
        if path.exists():
            txt = path.read_text(encoding="utf-8")
            raw = json.loads(txt) if txt.strip() else None
    except Exception:
        raw = None
    if not raw:
        raw = fetch_current_legislators(fetch=fetch, cache_path=path)
    return build_roster(raw, chamber)
