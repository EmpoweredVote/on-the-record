# src/crec_normalize.py
"""Resolve a Congressional Record speaker designation to a congress member.

Consumes a CongressRoster (src/congress_roster.py). Pure; no network. Handles
member designations ("Mr. McCONNELL", "Ms. BALDWIN of Wisconsin"), presiding
officers with a parenthetical ("The PRESIDING OFFICER (Mrs. Ernst)"), and bare
procedural roles ("The PRESIDING OFFICER", "The SPEAKER").

Matching is exact and case-insensitive on the full surname (CREC and the dataset
are both canonical text — no fuzzy matching needed), with state disambiguation
for same-surname collisions.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .congress_roster import CongressMember, CongressRoster

_STATE_NAME_TO_CODE = {
    "alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR",
    "california": "CA", "colorado": "CO", "connecticut": "CT", "delaware": "DE",
    "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID",
    "illinois": "IL", "indiana": "IN", "iowa": "IA", "kansas": "KS",
    "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
    "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS",
    "missouri": "MO", "montana": "MT", "nebraska": "NE", "nevada": "NV",
    "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
    "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK",
    "oregon": "OR", "pennsylvania": "PA", "rhode island": "RI",
    "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX",
    "utah": "UT", "vermont": "VT", "virginia": "VA", "washington": "WA",
    "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
    "district of columbia": "DC", "puerto rico": "PR", "guam": "GU",
    "american samoa": "AS", "virgin islands": "VI",
    "northern mariana islands": "MP",
}


def _role_slug(role_raw: str) -> str:
    """Normalize a procedural-role phrase to a stable slug."""
    r = role_raw.lower()
    if "presiding" in r:
        return "presiding_officer"
    if "speaker" in r:
        return "speaker"
    if "president pro tempore" in r:
        return "president_pro_tempore"
    if "vice president" in r:
        return "vice_president"
    if "chief justice" in r:
        return "chief_justice"
    if "chair" in r:
        return "chair"
    if "clerk" in r:
        return "clerk"
    return r.replace(" ", "_")


def _resolve_surname(
    surname: str, state_name: Optional[str], roster: CongressRoster
) -> "ResolvedSpeaker":
    """Match a surname (+ optional 'of <State>') against the roster."""
    cands = roster.by_surname(surname)
    if not cands:
        return ResolvedSpeaker(method="unresolved")
    used_state = False
    if state_name:
        code = _STATE_NAME_TO_CODE.get(state_name.strip().lower())
        if code:
            filtered = [m for m in cands if m.state == code]
            if filtered:
                cands = filtered
                used_state = True
    if len(cands) == 1:
        return ResolvedSpeaker(
            member=cands[0],
            method="surname_state" if used_state else "surname",
            confidence=1.0,
        )
    return ResolvedSpeaker(method="ambiguous", needs_review=True)


@dataclass
class ResolvedSpeaker:
    member: Optional[CongressMember] = None
    role: Optional[str] = None
    method: str = "unresolved"   # surname|surname_state|presiding_parenthetical|role|ambiguous|unresolved
    confidence: float = 0.0
    needs_review: bool = False
