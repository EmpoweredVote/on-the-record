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

# candidate_status values that mean the person is actually contesting the race.
# essentials uses {active, filed, withdrawn}; "filed" is 111 live rows and means
# the paperwork is in, so it earns the "running:" lead exactly like "active".
# Note publish.resolve_races_for_politicians ignores status entirely, so ALL
# three resolve the race on publish — the distinction here is informational.
_RUNNING_STATUSES = frozenset({"active", "filed"})

# The label for a person row with no race_candidates edge. Public because it is
# the warning case, and callers assert on it.
NO_CANDIDACIES = "no candidacies"

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


def _status(c: dict) -> str:
    """Normalized candidacy status. Missing status means active, matching
    publish.resolve_races_for_politicians, which doesn't filter status."""
    return (c.get("status") or "active").strip().lower()


def _one_candidacy(c) -> Optional[tuple[str, bool]]:
    """('WI · Governor · Republican primary · 2026', True) — the label plus
    whether it's a live run. A non-running status is prefixed
    ('withdrawn: <that>', False). None when the entry isn't a usable dict.

    Returning both together keeps the dict validated and the status normalized
    exactly once per entry.
    """
    if not isinstance(c, dict):
        return None
    label = race_display(
        c.get("position_name") or "",
        c.get("year"),
        c.get("state"),
        c.get("primary_party"),
        c.get("election_type"),
    )
    if not label:
        return None
    status = _status(c)
    running = status in _RUNNING_STATUSES
    return (label if running else f"{status}: {label}", running)


def candidacy_display(candidacies) -> str:
    """'running: <race>; <race>' — or the literal 'no candidacies', which is the
    signal that this person row has no race_candidates edge and so cannot carry a
    meeting or a quote into a race.

    The 'running:' lead is dropped when nothing is running, because
    'running: withdrawn: ...' reads as nonsense and a withdrawn-only person is
    precisely the case a curator needs to notice.

    Capped at _MAX_CANDIDACIES with a '+N more' tail so one row can't run away.
    Malformed entries are skipped rather than breaking the whole label.
    """
    pairs = [p for p in (_one_candidacy(c) for c in (candidacies or [])) if p]
    if not pairs:
        return NO_CANDIDACIES
    shown = pairs[:_MAX_CANDIDACIES]
    text = "; ".join(label for label, _running in shown)
    extra = len(pairs) - len(shown)
    if extra:
        text += f"; +{extra} more"
    return f"running: {text}" if any(running for _label, running in pairs) else text


def _dupe_key(full_name: str) -> str:
    """'thomas tiffany' from 'Thomas P. Tiffany' — a loose identity key for
    spotting two rows that a curator would read as the same person.

    Bare middle initials and generational suffixes are dropped for the same
    reason parse_name_query drops them: they're display choices, not identity.
    Dropping the suffix is what stops the key collapsing to the suffix itself —
    'John G. Roberts Jr.' and 'John P. Wiley Jr.' would BOTH key to 'john jr'
    and get flagged as the same person, and prod is full of such names.
    """
    tokens = [t for t in re.findall(r"[a-z0-9]+", (full_name or "").lower())
              if len(t) > 1 and t not in _NOISE_TOKENS]
    if len(tokens) <= 2:
        return " ".join(tokens)
    return f"{tokens[0]} {tokens[-1]}"


def mark_duplicate_names(results: list[dict]) -> list[dict]:
    """Set `duplicate_note` on every row whose normalized name is shared with
    another row in the same response ('' on the rest). Mutates and returns the
    list.

    Deliberately flags the WHOLE group rather than guessing which row is right:
    two rows can be one person split by a bad hand-add, or two genuinely
    different people with the same name, and nothing in the name distinguishes
    those cases. The candidacy line is what tells the curator which to pick; this
    marker only says "look before you click".
    """
    counts: dict[str, int] = {}
    for r in results:
        key = _dupe_key(r.get("full_name") or "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    for r in results:
        n = counts.get(_dupe_key(r.get("full_name") or ""), 0)
        r["duplicate_note"] = f"⚠ {n} records for this name" if n > 1 else ""
    return results
