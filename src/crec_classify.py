"""Classify a CREC granule into a floor 'kind' (Slice 1 of the Federal adapter).

A House floor day is a HYBRID: ~half the granules are printed back-matter never
spoken on the floor, most spoken granules are one-minute / special-order speeches,
a few are legislative business, the rest procedural. Detection is NOT title bill-
number regex (that flags dozens of paperwork granules). Kinds route to branches:
  LEGISLATIVE -> agenda branch;  ONE_MINUTE -> attention branch;
  PROCEDURAL  -> holes;          BACK_MATTER -> discard (won't align to audio).
"""
from __future__ import annotations

import re
from enum import Enum

from .crec_structure import CrecGranule


class GranuleKind(str, Enum):
    LEGISLATIVE = "legislative"
    ONE_MINUTE = "one_minute"
    PROCEDURAL = "procedural"
    BACK_MATTER = "back_matter"


_PROCEDURAL_TITLES = frozenset({
    "PRAYER", "THE JOURNAL", "PLEDGE OF ALLEGIANCE", "RECESS", "AFTER RECESS",
    "ADJOURNMENT", "DESIGNATION OF SPEAKER PRO TEMPORE",
    "ANNOUNCEMENT BY THE SPEAKER PRO TEMPORE", "CONGRESSIONAL RECORD",
    "HOUSE OF REPRESENTATIVES",
})

_BACK_MATTER_TITLE_RE = re.compile(
    r"constitutional authority statement|executive communications|"
    r"reports? of committees|public bills and resolutions|memorials|"
    r"additional (co)?sponsors|senate bill referred|"
    r"communication from the clerk|reported bill", re.I)

# Reliable "a bill was taken up" title signals (no vote required).
_LEGIS_TITLE_RE = re.compile(
    r"providing for consideration of|request to consider", re.I)

# Spoken-floor markers: presiding-officer address / recognition / vote machinery.
_SPOKEN_RE = re.compile(
    r"\bThe (Acting )?(CHAIR|SPEAKER|PRESIDING OFFICER)\b|"
    r"\bMr\. Speaker\b|\bI yield\b|\brecorded vote\b|\[Roll No", re.I)

_VOTE_RE = re.compile(r"\[Roll No\.?\s*\d+\]")


def classify(g: CrecGranule) -> GranuleKind:
    title = g.title or ""
    if _VOTE_RE.search(g.text) or _LEGIS_TITLE_RE.search(title):
        return GranuleKind.LEGISLATIVE
    if _BACK_MATTER_TITLE_RE.search(title):
        return GranuleKind.BACK_MATTER
    if title.strip().upper() in _PROCEDURAL_TITLES:
        return GranuleKind.PROCEDURAL
    if not _SPOKEN_RE.search(g.text):
        # unspoken printed matter (e.g. a Constitutional Authority Statement)
        return GranuleKind.BACK_MATTER
    return GranuleKind.ONE_MINUTE
