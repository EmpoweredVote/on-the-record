"""Build a speaker roster for one IGA committee recording.

Roster = the committee's members plus the legislators who author or sponsor a
bill on that meeting's agenda (they present their bills, so they speak).
Each legislator is matched to essentials.politicians by state (IN), chamber,
district number and last name — never by name alone, since common names
collide across states.

Parsing/matching is pure; ``fetch`` and ``lookup`` are the network seams.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Callable, Optional

from . import iga


@dataclass
class Legislator:
    lpid: str
    first_name: str
    last_name: str
    chamber: str               # 'senate' | 'house'
    district: Optional[str]
    roles: list[str] = field(default_factory=list)  # 'Chair', 'Bill author (SB 52)', ...


@dataclass
class RosterMatch:
    legislator: Legislator
    politician_id: Optional[str]
    full_name: Optional[str]
    district_label: Optional[str]


def _chamber_from_honorific(honorific: str) -> str:
    return "senate" if (honorific or "").lower().startswith("sen") else "house"


def collect_legislators(members: dict, details: dict, directory: dict) -> list[Legislator]:
    """Committee members first (in IGA's order), then agenda authors/sponsors.

    ``directory`` is getLegislators JSON; it fills chamber + district for the
    agenda people, whom getMeetingDetails lists without a district.
    """
    by_lpid = {d["lpid"]: d for d in directory.get("legislators") or []}
    out: dict[str, Legislator] = {}

    for m in members.get("members") or []:
        if (m.get("type") or "LEGISLATOR") != "LEGISLATOR":
            continue
        lpid = m["holderid"]
        out[lpid] = Legislator(
            lpid=lpid, first_name=m.get("first_name", ""), last_name=m.get("last_name", ""),
            chamber=_chamber_from_honorific(m.get("honorific", "")),
            district=str(m["district"]) if m.get("district") else None,
            roles=[m.get("position") or "Member"],
        )

    for item in details.get("agenda") or []:
        bill = item.get("label") or item.get("base_name") or "bill"
        for kind, people in (("author", item.get("authors")), ("sponsor", item.get("sponsors"))):
            for p in people or []:
                lpid = p.get("lpid")
                if not lpid:
                    continue
                role = f"Bill {kind} ({bill})"
                if lpid in out:
                    if role not in out[lpid].roles:
                        out[lpid].roles.append(role)
                    continue
                d = by_lpid.get(lpid, {})
                out[lpid] = Legislator(
                    lpid=lpid,
                    first_name=p.get("first_name") or d.get("firstname", ""),
                    last_name=p.get("last_name") or d.get("lastname", ""),
                    chamber=_chamber_from_honorific(p.get("honorific") or d.get("honorific", "")),
                    district=str(d["district"]) if d.get("district") else None,
                    roles=[role],
                )
    return list(out.values())


_DISTRICT_RE = re.compile(r"\b(senate|house)\b.*\bdistrict\s+(\d+)\s*$", re.I)


def pick_politician(leg: Legislator, candidates: list[dict]) -> Optional[dict]:
    """The one candidate row holding this legislator's IN seat, else None.

    candidates: rows {politician_id, full_name, last_name, representing_state,
    district_label} for politicians sharing the last name.
    """
    hits = []
    for c in candidates:
        if (c.get("representing_state") or "").upper() != "IN":
            continue
        if (c.get("last_name") or "").lower() != leg.last_name.lower():
            continue
        m = _DISTRICT_RE.search(c.get("district_label") or "")
        if not m or m.group(1).lower() != leg.chamber or m.group(2) != (leg.district or ""):
            continue
        hits.append(c)
    unique = {h["politician_id"]: h for h in hits}
    return next(iter(unique.values())) if len(unique) == 1 else None


def db_lookup(database_url: str) -> Callable[[str], list[dict]]:
    """lookup(last_name) -> current-office rows, via essentials (read-only)."""
    import psycopg2
    import psycopg2.extras

    conn = psycopg2.connect(database_url)
    conn.set_session(readonly=True, autocommit=True)

    def lookup(last_name: str) -> list[dict]:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                select p.id::text as politician_id, p.full_name, p.last_name,
                       o.representing_state, d.label as district_label
                from essentials.politicians p
                join essentials.current_office_holders h on h.politician_id = p.id
                join essentials.offices o on o.id = h.office_id
                left join essentials.districts d on d.id = o.district_id
                where lower(p.last_name) = lower(%s)
                """,
                (last_name,),
            )
            return [dict(r) for r in cur.fetchall()]

    return lookup


def build_roster(
    recording_url: str,
    *,
    fetch: Callable[[str], str],
    lookup: Callable[[str], list[dict]],
) -> tuple[dict, list[RosterMatch]]:
    """(roster_api_response_shape, matches) for one committee recording URL.

    The first element has the ev-accounts RosterResponse shape that
    refresh_roster._build_cache_payload consumes; unmatched legislators are
    left out of it but returned in ``matches`` so the caller can report them.
    """
    resolved = iga.resolve_iga_video(recording_url, fetch=fetch)
    if resolved is None:
        raise ValueError(f"Not an IGA video URL: {recording_url}")
    year, video_key, stem = iga.parse_page_url(resolved.page_url)
    facts = iga.parse_stream_url(resolved.audio_url)
    if video_key in ("senate", "house"):
        raise ValueError("Floor sessions have no committee roster; use a committee recording.")

    committee = iga._find_committee(year, video_key, fetch)
    if not committee:
        raise ValueError(f"No IGA committee {video_key!r} in session {year}")
    lp = iga._session_lpid(year)
    members = iga._get_json(fetch, f"{iga.API_BASE}/getMembers?committee_id={committee['id']}&session_lpid={lp}")
    meeting = iga.find_committee_meeting(year, committee["id"], facts["media"], fetch)
    details = iga.get_meeting_details(year, meeting["id"], fetch) if meeting else {}
    directory = iga._get_json(fetch, f"{iga.API_BASE}/getLegislators?session_lpid={lp}")

    matches: list[RosterMatch] = []
    response_members = []
    for leg in collect_legislators(members, details, directory):
        row = pick_politician(leg, lookup(leg.last_name))
        matches.append(RosterMatch(leg, row and row["politician_id"], row and row["full_name"],
                                   row and row["district_label"]))
        if row:
            response_members.append({
                "politician_slug": None,
                "politician_id": row["politician_id"],
                "full_name": row["full_name"],
                "preferred_name": "",
                "title": "Senator" if leg.chamber == "senate" else "Representative",
                "district_label": row["district_label"] or "",
            })

    response = {"name_formal": f"{resolved.title} (members + agenda authors/sponsors)",
                "members": response_members}
    return response, matches
