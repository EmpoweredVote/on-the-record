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


# Member designation: "Mr./Mrs./Ms./Miss <surname>[ of <State>]".
# surname is non-greedy so a trailing " of <State>" is split off; it still
# captures compound surnames ("Van Hollen", "Cortez Masto").
_MEMBER_RE = re.compile(
    r"^(?:Mr|Mrs|Ms|Miss)\.?\s+(?P<surname>.+?)(?:\s+of\s+(?P<state>[A-Za-z .]+?))?$"
)

# "The PRESIDING OFFICER (Mr. <surname>)" — presiding officer named in a paren.
_PRESIDING_PAREN_RE = re.compile(
    r"^The\s+PRESIDING\s+OFFICER\s+\((?:Mr|Mrs|Ms|Miss)\.\s+(?P<surname>.+?)\)$",
    re.I,
)

# Bare procedural roles (no member named).
_ROLE_RE = re.compile(
    r"^The\s+(?P<role>PRESIDING\s+OFFICER|SPEAKER(?:\s+pro\s+tempore)?"
    r"|(?:ACTING\s+)?PRESIDENT\s+pro\s+tempore|VICE\s+PRESIDENT"
    r"|CHIEF\s+JUSTICE|CHAIR|CLERK)$",
    re.I,
)


def normalize_designation(speaker_raw: str, roster: CongressRoster) -> ResolvedSpeaker:
    """Resolve a CREC speaker designation against a chamber-scoped roster.

    Order matters: a parenthetical presiding officer is tried before the bare
    role, and member forms last. Roster is already chamber-scoped, so no chamber
    argument is needed.
    """
    s = (speaker_raw or "").strip()

    m = _PRESIDING_PAREN_RE.match(s)
    if m:
        res = _resolve_surname(m.group("surname"), None, roster)
        if res.member is not None:
            res.method = "presiding_parenthetical"
            return res
        return ResolvedSpeaker(role="presiding_officer", method="role", confidence=1.0)

    m = _ROLE_RE.match(s)
    if m:
        return ResolvedSpeaker(role=_role_slug(m.group("role")), method="role", confidence=1.0)

    m = _MEMBER_RE.match(s)
    if m:
        return _resolve_surname(m.group("surname"), m.group("state"), roster)

    return ResolvedSpeaker(method="unresolved")
