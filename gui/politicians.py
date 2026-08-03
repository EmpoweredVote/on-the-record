"""Search essentials.politicians for the GUI speaker-link picker (via
DATABASE_URL + psycopg2, like gui.races and gui.publish_api). Best-effort: when
the DB isn't configured or a query fails, search returns no results rather than
raising — mirroring gui.races.search_races_safe.

Why this exists rather than calling ev-accounts /candidates/search-by-name:

essentials.politicians is the *person* table and candidacy is an edge in
essentials.race_candidates, so a picker row that shows only name + office cannot
tell you whether the person you're about to link is in the race you care about.
For Thomas P. Tiffany the office-holding row IS the WI Governor candidate; for
Francesca Hong the office-holding row is NOT (a hand-add minted a second person
row and the race edge landed on that one). Picking wrong is silent and costly:
publish._reconcile_event_races derives a meeting's races solely from
politician_id -> race_candidates, and Read & Rank reaches quotes only through the
same edge. So every row carries its candidacies, and duplicate names are flagged.

Two more upstream sharp edges this module files down: office_current_holder
returns one row per office, so someone holding both a real office and a
"Candidate for ..." placeholder fans out to two rows with the same politician_id
(DISTINCT ON collapses it); and matching the whole query as one substring means
"Thomas Tiffany" misses "Thomas P. Tiffany" (per-token matching fixes it).
"""
from __future__ import annotations

import json
import os
import re
from typing import Optional

import psycopg2

from .races import race_display

# Name fields a query token is matched against, in the order the OR is built.
_NAME_FIELDS = ("full_name", "preferred_name", "first_name", "last_name")

# Candidacies shown in full before collapsing the tail into "+N more".
_MAX_CANDIDACIES = 3

# Query tokens that carry no matchable signal. Generational suffixes live in
# their own `name_suffix` column, which _NAME_FIELDS doesn't search, so keeping
# them would AND in a clause nothing can satisfy.
_NOISE_TOKENS = frozenset({"jr", "sr", "ii", "iii", "iv"})


def parse_name_query(q: str) -> list[str]:
    """Matchable lowercase tokens from a raw picker query.

    Each token becomes its own AND-ed clause, which is what lets "Thomas
    Tiffany" match the stored "Thomas P. Tiffany" — but it cuts both ways, so
    anything the stored row can't possibly carry has to be dropped or the whole
    query returns nothing. Two such cases, both verified against prod:

      "John F Kennedy"  -> stored "John Kennedy" has no F   -> 0 rows
      "Wesley Hunt Jr"  -> name_suffix isn't in _NAME_FIELDS -> 0 rows

    So single-character tokens (bare middle initials) and generational suffixes
    are dropped. Word order never mattered either way.
    """
    return [t for t in re.findall(r"[a-z0-9]+", (q or "").lower())
            if len(t) > 1 and t not in _NOISE_TOKENS]


def politician_display(rec: dict) -> str:
    """'Thomas P. Tiffany · U.S. Representative · Congressional District 7 ·
    United States Federal Government' — identity, empty parts omitted.

    office_title and district_label are frequently redundant: 112 prod rows store
    d.label == o.title outright ("Texas Attorney General" twice) and another 1141
    have one contained in the other ("Representative, 18th Middlesex District" /
    "18th Middlesex District"). Printing both crowds out the part that actually
    disambiguates, so when one contains the other only the longer survives —
    which side that is varies ("Attorney General" vs "Texas Attorney General"
    keeps the district).
    """
    office = (rec.get("office_title") or "").strip()
    district = (rec.get("district_label") or "").strip()
    if office and district:
        lower_office, lower_district = office.lower(), district.lower()
        if lower_district in lower_office:
            district = ""
        elif lower_office in lower_district:
            office, district = district, ""
    parts = [
        (rec.get("full_name") or "").strip(),
        office,
        district,
        (rec.get("government_name") or "").strip(),
    ]
    return " · ".join(p for p in parts if p)
