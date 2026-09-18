"""Coverage view data layer for the discovery reorg: a pure level classifier
plus DB aggregation (added in Task 4). Best-effort like gui/discovery.py: no
DATABASE_URL or any DB error -> empty list, never a crash.

`race_level` is a heuristic over position_name. It is a shared classifier: this
module uses it for grouping, and it also gates local-type hub searches (see
src/race_level.py). It is easy to extend; misgrouping a race is cosmetic, never
unsafe.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

import psycopg2

from src.race_level import LEVEL_ORDER, race_level  # noqa: F401  (re-exported for callers/tests)


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


@dataclass
class RaceCoverage:
    race_id: str
    position_name: str
    level: str
    locality: Optional[str]
    candidates: int
    quote_sources: int
    ingested: int
    pending: int


# Join chain: races.office_id -> offices.id, offices.chamber_id ->
# chambers.id, chambers.government_id -> governments.id. governments.name is
# the jurisdiction name; NOTE it is populated for federal/statewide races too
# (e.g. 'United States Federal Government', 'State of Arizona'), NOT just local
# ones, and can be NULL for some statewide offices — so it is a display label
# here, never a "is this local?" test (see src/discovery/locality.py for that).
# races.election_id -> elections.id carries elections.state, the filter here.
_RACES_SQL = """
    select r.id::text, r.position_name, g.name as locality,
           coalesce(cand.n, 0), coalesce(qs.n, 0),
           coalesce(ing.n, 0), coalesce(pend.n, 0)
    from essentials.races r
    join essentials.elections e on e.id = r.election_id
    left join essentials.offices o on o.id = r.office_id
    left join essentials.chambers ch on ch.id = o.chamber_id
    left join essentials.governments g on g.id = ch.government_id
    left join lateral (
        select count(*) n from essentials.race_candidates rc
        where rc.race_id = r.id and coalesce(rc.candidate_status, 'active') <> 'withdrawn'
    ) cand on true
    left join lateral (
        select count(*) n from essentials.discovered_sources d
        where d.race_id = r.id and d.status = 'approved' and d.route = 'quote_source'
    ) qs on true
    left join lateral (
        select count(*) n from essentials.discovered_sources d
        where d.race_id = r.id and d.status = 'ingested'
    ) ing on true
    left join lateral (
        select count(*) n from essentials.discovered_sources d
        where d.race_id = r.id and d.status = 'pending'
    ) pend on true
    where e.state = %s
"""


def races_for_state(state: str) -> list:
    """Every race in `state` with its coverage counts, ordered by LEVEL_ORDER
    then locality then position name (see the state-index/sectioned view).
    Best-effort: no DATABASE_URL or any DB error returns []."""
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_RACES_SQL, (state.upper(),))
                rows = [
                    RaceCoverage(rid, name, race_level(name), locality,
                                 cand, qs, ing, pend)
                    for (rid, name, locality, cand, qs, ing, pend) in cur.fetchall()
                ]
        finally:
            conn.close()
    except Exception:
        return []
    rows.sort(key=lambda r: (LEVEL_ORDER.index(r.level), r.locality or "", r.position_name or ""))
    return rows


_STATE_INDEX_SQL = """
    select e.state, count(*) filter (where d.status = 'pending') as pending
    from essentials.elections e
    join essentials.races r on r.election_id = e.id
    left join essentials.discovered_sources d on d.race_id = r.id
    where e.state is not null
    group by e.state
    order by pending desc, e.state
"""


def state_index() -> list:
    """One row per state with any tracked race, most pending sources first
    (the SQL's own ORDER BY does the sorting; this just maps rows to dicts).
    Best-effort: no DATABASE_URL or any DB error returns []."""
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(_STATE_INDEX_SQL)
                return [{"state": s, "pending": n} for (s, n) in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []
