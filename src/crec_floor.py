"""Top-level Federal floor structure extraction (Slice 1, no timestamps).

(date, chamber) -> FloorStructure: legislative + one-minute granules split out,
roll-call votes parsed from legislative granules and enriched with member bioguide
IDs via each granule's MODS, back-matter/procedural discarded. Timestamps (Slice 2)
and the essentials politician_id join (follow-on) are out of scope.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from . import govinfo
from .crec_structure import CrecGranule, fetch_granules
from .crec_classify import GranuleKind, classify
from .crec_votes import RollCallVote, parse_votes
from .crec_members import build_bioguide_index, enrich_vote


@dataclass
class GranuleVotes:
    granule: CrecGranule
    votes: list          # list[RollCallVote]
    members: list        # flat list[MemberVote] across the granule's votes


@dataclass
class FloorStructure:
    date: str
    chamber: str
    agenda_granules: list = field(default_factory=list)     # LEGISLATIVE CrecGranule
    attention_granules: list = field(default_factory=list)  # ONE_MINUTE CrecGranule
    votes: list = field(default_factory=list)               # list[GranuleVotes]
    discarded: int = 0                                       # back-matter + procedural


def _fetch_mods(date: str, granule_id: str, key: str, fetch: Callable[[str], str]) -> str:
    url = (f"{govinfo._API_ROOT}/packages/{govinfo._package_id(date)}"
           f"/granules/{granule_id}/mods?api_key={key}")
    try:
        return fetch(url)
    except Exception:
        return ""


def extract_floor_structure(
    date: str,
    chamber: str,
    *,
    fetch: Callable[[str], str] = govinfo._default_fetch,
    api_key: Optional[str] = None,
    max_granules: Optional[int] = None,
) -> Optional[FloorStructure]:
    key = govinfo._resolve_api_key(api_key)
    granules = fetch_granules(date, chamber, fetch=fetch, api_key=key, max_granules=max_granules)
    if granules is None:
        return None

    out = FloorStructure(date=date, chamber=chamber)
    for g in granules:
        kind = classify(g)
        if kind is GranuleKind.LEGISLATIVE:
            out.agenda_granules.append(g)
            votes = parse_votes(g.text)
            if votes:
                index = build_bioguide_index(_fetch_mods(date, g.granule_id, key, fetch))
                members = [mv for v in votes for mv in enrich_vote(v, index)]
                out.votes.append(GranuleVotes(granule=g, votes=votes, members=members))
        elif kind is GranuleKind.ONE_MINUTE:
            out.attention_granules.append(g)
        else:
            out.discarded += 1
    return out
