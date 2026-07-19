"""Map CREC roll-call surnames to bioguide IDs via a granule's MODS (Slice 1).

MODS <congMember> carries bioGuideId + a <name type="parsed"> that matches the
surname form used in the text vote lists ('Adams', 'Higgins (LA)'). Build a
parsed-name -> bioguide index and enrich a RollCallVote. Best-effort: a surname
absent OR ambiguous in MODS stays unresolved (bioguide=None) — never link the
wrong member, mirroring crec_essentials. Pure parsing.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .crec_votes import RollCallVote

_CONGMEMBER_RE = re.compile(
    r'<congMember\b[^>]*\bbioGuideId="(?P<bio>[^"]+)"[^>]*>(?P<body>.*?)</congMember>',
    re.S)
_PARSED_NAME_RE = re.compile(r'<name type="parsed">(?P<n>[^<]+)</name>')


def build_bioguide_index(mods_text: str) -> dict:
    """{parsed_surname: bioguide}; a surname mapping to >1 bioguide is dropped."""
    seen: dict[str, set] = {}
    for m in _CONGMEMBER_RE.finditer(mods_text):
        pn = _PARSED_NAME_RE.search(m.group("body"))
        if not pn:
            continue
        seen.setdefault(pn.group("n").strip(), set()).add(m.group("bio"))
    return {name: next(iter(bios)) for name, bios in seen.items() if len(bios) == 1}


@dataclass
class MemberVote:
    surname: str
    position: str            # YEA | NAY | PRESENT | NOT_VOTING
    bioguide: Optional[str]  # None when unresolved


def enrich_vote(vote: RollCallVote, index: dict) -> list:
    out: list = []
    for position, surnames in vote.positions.items():
        for s in surnames:
            out.append(MemberVote(s, position, index.get(s)))
    return out
