# src/crec_essentials.py
"""Bridge a CREC-resolved congress member to an essentials politician_id.

Read-only: reuses search_politicians (the ev-accounts search-by-name endpoint).
Attaches a politician_id ONLY on a single unambiguous federal match (chamber +,
for the House, district); any ambiguity or error -> None (the caller falls back
to a name-only mapping). Never writes to essentials, never links the wrong person.
"""
from __future__ import annotations

import re
from typing import Optional

from .congress_roster import CongressMember
from .essentials_client import search_politicians


def _is_federal(rec: dict) -> bool:
    return "united states federal" in (rec.get("government_name") or "").lower()


def _chamber_matches(rec: dict, chamber: str) -> bool:
    office = (rec.get("office_title") or "").lower()
    if chamber == "senate":
        return "senator" in office
    return "representative" in office   # house


def _district_number(district_label: str) -> Optional[int]:
    """First integer in a district_label ('Congressional District 9' -> 9), or None."""
    m = re.search(r"\d+", district_label or "")
    return int(m.group()) if m else None


def resolve_politician_id(
    member: CongressMember, *, search=search_politicians,
) -> Optional[tuple]:
    """Resolve a CongressMember to an essentials (politician_id, politician_slug).

    Searches by LAST NAME (essentials display names differ from congress-legislators
    official_full — nicknames, dropped middle initials), then filters to a single
    unambiguous federal member of the right chamber (and, for the House, district).
    Returns None on no match, ambiguity, or any search error (best-effort).
    """
    try:
        cands = search(member.last_name, limit=25)
    except Exception:
        return None

    matches = [c for c in cands if _is_federal(c) and _chamber_matches(c, member.chamber)]
    if member.chamber == "house" and member.district is not None:
        matches = [c for c in matches
                   if _district_number(c.get("district_label")) == member.district]

    if len(matches) == 1:
        c = matches[0]
        return (c.get("politician_id"), c.get("politician_slug"))
    return None
