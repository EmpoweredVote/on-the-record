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


def parse_name_query(q: str) -> list[str]:
    """Lowercase alphanumeric tokens from a raw picker query.

    Each token becomes its own AND-ed clause, so "Thomas Tiffany" matches the
    stored "Thomas P. Tiffany" (the dropped middle initial stops mattering) and
    word order stops mattering too.
    """
    return re.findall(r"[a-z0-9]+", (q or "").lower())


def politician_display(rec: dict) -> str:
    """'Thomas P. Tiffany · U.S. Representative · Congressional District 7 ·
    United States Federal Government' — identity, empty parts omitted.

    district_label is dropped when it merely repeats office_title: essentials
    stores d.label == o.title for many single-seat offices, and printing it twice
    crowds out the part that actually disambiguates.
    """
    office = (rec.get("office_title") or "").strip()
    district = (rec.get("district_label") or "").strip()
    if district and district == office:
        district = ""
    parts = [
        (rec.get("full_name") or "").strip(),
        office,
        district,
        (rec.get("government_name") or "").strip(),
    ]
    return " · ".join(p for p in parts if p)
