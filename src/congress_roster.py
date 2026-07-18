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
