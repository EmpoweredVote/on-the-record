# Discovery review reorg (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the `/discovery` review page around geography and coverage counts, and make outlet trust a one-click, outlet-level decision that auto-keeps trusted outlets' news clips as quote sources.

**Architecture:** A pure lane classifier (`src/discovery/lanes.py`) and a pure geography classifier (`gui/coverage.py::race_level`) drive both the display and a single SQL auto-approve sweep (`src/discovery/autoapprove.py`). The sweep runs on two triggers — when the reviewer trusts an outlet, and at the end of each `poll_discovery` run — and only ever sets `status='approved', route='quote_source'` (it never ingests). The `/discovery` route (`gui/app.py`) and template are rebuilt to navigate state → section (statewide/federal band + localities) → level-grouped races with four counts each, expanding to per-outlet, per-lane review controls.

**Tech Stack:** Python 3 (FastAPI GUI in `gui/`, discovery engine in `src/discovery/`), psycopg2 against the ev-accounts Postgres (`essentials` schema), Jinja templates in `gui/templates/`, pytest.

## Global Constraints

- Python interpreter is `.venv/bin/python`; run tests with `.venv/bin/python -m pytest`. Never system `python3`.
- The GUI data layer is **best-effort**: no `DATABASE_URL` or any DB error returns empty/zero values, never raises (see `gui/discovery.py`). Writes commit explicitly. Match this in every new DB function.
- SQL is injection-safe the way `gui/discovery.py::_family_where` is: WHERE fragments come only from hardcoded maps/literals; every value is a bound parameter. Interpolate `FORMAL_EVENT_KINDS` only as a bound list, never as string-formatted SQL.
- `.env.local` is auto-loaded by `gui/env.py::load_env_local` (called in `scripts/poll_discovery.py` and `gui/asgi.py`); do not add `load_dotenv`.
- **Safety invariant:** the auto lane only ever writes `status='approved', route='quote_source'`. It never launches an ingest job. Ingest stays a human click.
- Migrations live in `ev-accounts/backend/migrations/` (a **separate repo/checkout** at `../ev-accounts`). House style: idempotent (`ADD COLUMN IF NOT EXISTS`), a `DO $$ … RAISE EXCEPTION` post-verify gate, applied to prod via the migration tooling on a **direct** connection (not the pooler). Pick the next free number with `node ev-accounts/scripts/check-migration-numbers.mjs` and match the neighboring source-discovery migrations (`1551`, `1563`).
- Geography scope: Slice 1 groups local races by their **government name** (a locality label like "City of Bloomington, Indiana" or "Monroe County, Indiana"), shown as sibling sections under a state. True county rollup (nesting city/school under a county via `essentials.districts` + `district_county_overlap` + `governments.geo_id`) is **out of scope** for Slice 1.
- **Testing harness — there is NO local/seeded test database.** `tests/conftest.py` deletes `DATABASE_URL` for every test (autouse `_no_real_db_env`); the `live_db` fixture opts back into the real DB and **skips** unless `DATABASE_URL` is exported before pytest. So, for every DB-touching function: (1) test pure logic directly; (2) test the best-effort path by asserting `[]`/`0`/`False` with no `DATABASE_URL`; (3) test SQL construction by injecting a **fake cursor/connection** — `monkeypatch.setattr(psycopg2, "connect", …)` returning an object whose `cursor().execute(sql, params)` is recorded and whose `fetchall`/`fetchone` return canned rows — and assert on the recorded SQL/params; (4) test routes/templates by monkeypatching the data-layer functions and using `TestClient` (mirror `tests/test_gui_discovery.py`). Real-schema behavior goes in a `live_db`-gated test that is skipped by default. **Wherever a task's sample test names a `seeded_*_db` or `poll_harness` DB fixture, replace it with these patterns — those fixtures do not exist.** Every implementer reads `tests/conftest.py` and `tests/test_gui_discovery.py` before writing tests.
- **Migration apply is gated.** An implementer writes and commits the migration but must **not** apply it to any database. Applying it, and any `live_db` verification against the real schema, is a manual step the human authorizes.

---

## File structure

- `src/discovery/lanes.py` **(new)** — pure `content_lane()` + `FORMAL_EVENT_KINDS`. Imported by both the UI (row tags) and the auto-approve sweep.
- `src/discovery/autoapprove.py` **(new)** — the auto-approve sweep and its SQL predicate; used by `poll_discovery` and by the trust action.
- `gui/coverage.py` **(new)** — pure `race_level()` and the DB aggregation for the state index, sections, level-grouped races, and four counts.
- `gui/discovery.py` **(modify)** — add `trusted` / `ingest_barred` to the row select; add `set_outlet_trusted`, `trust_from_row`, `unapprove_auto`, and a summary in `health()`.
- `gui/app.py` **(modify)** — rebuild the `/discovery` route; add the trust POST route; gate `approve-ingest` on `ingest_barred`.
- `gui/templates/discovery.html` **(modify)** — the reorganized layout.
- `scripts/poll_discovery.py` **(modify)** — call the auto-approve sweep at the end of a run.
- `ev-accounts/backend/migrations/<next>_source_outlets_trust.sql` **(new, other repo)** — the columns + barred-chain seed.
- Tests: `tests/test_discovery_lanes.py`, `tests/test_discovery_autoapprove.py`, `tests/test_gui_coverage.py` (new); additions to `tests/test_gui_discovery.py`.

---

### Task 1: Migration — outlet trust + barred columns and seed

**Files:**
- Create: `ev-accounts/backend/migrations/<next>_source_outlets_trust.sql` (number from `check-migration-numbers.mjs`)

**Interfaces:**
- Produces: on `essentials.source_outlets` — `trusted boolean not null default false`, `trusted_at timestamptz null`, `ingest_barred boolean not null default false`.

- [ ] **Step 1: Write the migration**

```sql
-- Outlet-level trust + barred-for-ingest flags for the discovery review reorg.
-- Spec: on-the-record/docs/superpowers/specs/2026-09-16-discovery-review-reorg-design.md
ALTER TABLE essentials.source_outlets
  ADD COLUMN IF NOT EXISTS trusted       boolean     NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS trusted_at    timestamptz,
  ADD COLUMN IF NOT EXISTS ingest_barred boolean     NOT NULL DEFAULT false;

-- Seed the barred-for-ingest flag from the chain ToS scoreboard (AI/ML bar).
-- Name-based, case-insensitive; extend as the scoreboard grows.
UPDATE essentials.source_outlets
SET ingest_barred = true
WHERE ingest_barred = false
  AND (name ILIKE ANY (ARRAY[
        '%nexstar%', '%gray %', '%gray media%', '%hearst%', '%graham media%',
        '%lee enterprises%', '%tollbit%'
      ]));

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema='essentials' AND table_name='source_outlets'
                   AND column_name='ingest_barred') THEN
    RAISE EXCEPTION 'source_outlets.ingest_barred missing after migration';
  END IF;
END $$;
```

- [ ] **Step 2: Do NOT apply — verify by eye (apply is gated)**

Do not apply the migration to any database. Read three neighboring migrations (`1551_source_discovery.sql`, `1563_source_outlets_county.sql`, and any recent `CA_*`) and confirm this file matches house style: idempotent `ADD COLUMN IF NOT EXISTS`, a `DO $$ … RAISE EXCEPTION` post-verify gate, `essentials` schema. Report the exact migration number/filename you chose (from `check-migration-numbers.mjs`) so the human can apply it. The verification query the human will run after applying:
```sql
SELECT column_name FROM information_schema.columns
WHERE table_schema='essentials' AND table_name='source_outlets'
  AND column_name IN ('trusted','trusted_at','ingest_barred');  -- expect 3 rows
```

- [ ] **Step 3: Commit (ev-accounts repo)**

```bash
cd ../ev-accounts
git add backend/migrations/<next>_source_outlets_trust.sql
git commit -m "feat(discovery): outlet trust + barred-for-ingest columns"
```

---

### Task 2: `content_lane()` — the pure lane classifier

**Files:**
- Create: `src/discovery/lanes.py`
- Test: `tests/test_discovery_lanes.py`

**Interfaces:**
- Produces: `FORMAL_EVENT_KINDS: frozenset[str]`; `content_lane(original_vs_clip: str | None, event_kind_guess: str | None) -> str` returning one of `'full_event' | 'event_clip' | 'news_clip' | 'unknown'`. Only `'news_clip'` is auto-approvable.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from src.discovery.lanes import content_lane, FORMAL_EVENT_KINDS

@pytest.mark.parametrize("ovc,kind,expected", [
    ("original", "news_clip", "full_event"),
    ("original", None, "full_event"),
    ("original", "debate", "full_event"),
    ("clip", "debate", "event_clip"),
    ("clip", "forum", "event_clip"),
    ("clip", "press_conference", "event_clip"),
    ("clip", "community_meeting", "event_clip"),
    ("clip", "news_clip", "news_clip"),
    ("clip", "podcast", "news_clip"),
    ("clip", "council", "news_clip"),
    ("clip", "school_board", "news_clip"),
    ("clip", None, "news_clip"),
    (None, "debate", "unknown"),
    (None, None, "unknown"),
])
def test_content_lane(ovc, kind, expected):
    assert content_lane(ovc, kind) == expected

def test_formal_kinds_are_known_event_kinds():
    from src.event_kinds import EVENT_KINDS
    assert FORMAL_EVENT_KINDS <= set(EVENT_KINDS)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_lanes.py -v`
Expected: FAIL with `ModuleNotFoundError: src.discovery.lanes`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Content lanes for a discovered row, derived from the classifier's
`original_vs_clip` and `event_kind_guess`. Pure — no DB, no I/O — because both
the review UI (row tags) and the auto-approve sweep must agree on the rule.

Note: `src.event_kinds.EVENT_KINDS` has no `interview`/`town_hall`. A full
candidate interview surfaces as `original_vs_clip == 'original'` (the full-event
lane); a town hall is `community_meeting`.
"""
from __future__ import annotations

# Clip OF one of these = a lead to a full primary source worth chasing (lane 2).
FORMAL_EVENT_KINDS = frozenset({
    "debate", "forum", "press_conference", "community_meeting",
})


def content_lane(original_vs_clip: "str | None",
                 event_kind_guess: "str | None") -> str:
    if original_vs_clip == "original":
        return "full_event"
    if original_vs_clip == "clip":
        if event_kind_guess in FORMAL_EVENT_KINDS:
            return "event_clip"
        return "news_clip"
    return "unknown"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery_lanes.py -v`
Expected: PASS (all params).

- [ ] **Step 5: Commit**

```bash
git add src/discovery/lanes.py tests/test_discovery_lanes.py
git commit -m "feat(discovery): content_lane classifier (full_event/event_clip/news_clip/unknown)"
```

---

### Task 3: `race_level()` — the pure level classifier

**Files:**
- Create: `gui/coverage.py`
- Test: `tests/test_gui_coverage.py`

**Interfaces:**
- Produces: `race_level(position_name: str | None) -> str` returning one of `'federal' | 'state' | 'county' | 'local' | 'school'`. Used to group races and to decide which sit in the statewide/federal band (federal + state) vs a locality.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from gui.coverage import race_level

@pytest.mark.parametrize("name,expected", [
    ("U.S. Senate — Indiana", "federal"),
    ("U.S. Representative District 9", "federal"),
    ("President of the United States", "federal"),
    ("Indiana Governor", "state"),
    ("Attorney General", "state"),
    ("State Representative, District 060", "state"),
    ("Monroe County Commissioner, District 3", "county"),
    ("County Council At-Large", "county"),
    ("MCCSC School Board, District 1", "school"),
    ("Bloomington Board of Education", "school"),
    ("Bloomington Mayor", "local"),
    ("Bloomington City Council, District 4", "local"),
    (None, "local"),
])
def test_race_level(name, expected):
    assert race_level(name) == expected
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_coverage.py -v`
Expected: FAIL with `ImportError`/`ModuleNotFoundError`.

- [ ] **Step 3: Write minimal implementation**

```python
"""Coverage view data layer for the discovery reorg: a pure level classifier
plus DB aggregation (added in Task 4). Best-effort like gui/discovery.py.

`race_level` is a heuristic over position_name. It is used only for grouping and
is easy to extend; misgrouping a race is cosmetic, never unsafe.
"""
from __future__ import annotations

import re

_FEDERAL = re.compile(r"\b(president|u\.?s\.?|united states|congress)\b", re.I)
_STATE = re.compile(
    r"\b(governor|lieutenant governor|attorney general|secretary of state|"
    r"state (senat|represent|assembly|house)|comptroller|treasurer|"
    r"superintendent)\b", re.I)
_SCHOOL = re.compile(r"\b(school board|board of education|school district)\b", re.I)
_COUNTY = re.compile(r"\bcounty\b", re.I)


def race_level(position_name: "str | None") -> str:
    name = position_name or ""
    if _FEDERAL.search(name):
        return "federal"
    if _STATE.search(name):
        return "state"
    if _SCHOOL.search(name):
        return "school"
    if _COUNTY.search(name):
        return "county"
    return "local"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_coverage.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/coverage.py tests/test_gui_coverage.py
git commit -m "feat(discovery): race_level classifier for coverage grouping"
```

---

### Task 4: Coverage aggregation — state index, sections, per-race counts

**Files:**
- Modify: `gui/coverage.py`
- Test: `tests/test_gui_coverage.py`

**Interfaces:**
- Consumes: `race_level` (Task 3).
- Produces:
  - `LEVEL_ORDER: tuple = ("federal","state","county","local","school")`.
  - `@dataclass RaceCoverage`: `race_id: str`, `position_name: str`, `level: str`, `locality: str | None`, `candidates: int`, `quote_sources: int`, `ingested: int`, `pending: int`.
  - `races_for_state(state: str) -> list[RaceCoverage]` (ordered by `LEVEL_ORDER` then locality then name).
  - `state_index() -> list[dict]` — `[{"state": "IN", "pending": 42}, …]`, states with any tracked race, most pending first.

- [ ] **Step 1: Write the failing test**

Read `tests/test_gui_discovery.py` first to reuse its DB-connection fixture (the module-level psycopg2 pattern; note the conftest `DATABASE_URL` handling). Then add:

```python
from gui import coverage

def test_races_for_state_no_db(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    assert coverage.races_for_state("IN") == []
    assert coverage.state_index() == []

def test_race_coverage_shape_and_order(seeded_test_db):
    # seeded_test_db inserts (per the existing discovery DB test fixture):
    #   IN Governor (state), Monroe County Commissioner (county, govt
    #   "Monroe County, Indiana"), Bloomington Mayor (local, govt "City of
    #   Bloomington, Indiana"); a handful of discovered_sources rows across
    #   statuses; 3 active race_candidates on the Governor race.
    rows = coverage.races_for_state("IN")
    by_name = {r.position_name: r for r in rows}
    gov = by_name["Indiana Governor"]
    assert gov.level == "state" and gov.locality is None
    assert gov.candidates == 3
    assert (gov.quote_sources, gov.ingested, gov.pending) == (2, 1, 3)
    mayor = by_name["Bloomington Mayor"]
    assert mayor.level == "local"
    assert mayor.locality == "City of Bloomington, Indiana"
    # federal/state precede county precede local
    levels = [r.level for r in rows]
    assert levels == sorted(levels, key=coverage.LEVEL_ORDER.index)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_coverage.py -v`
Expected: FAIL (`AttributeError: races_for_state`).

- [ ] **Step 3: Write minimal implementation**

```python
import os
from dataclasses import dataclass
from typing import Optional
import psycopg2

LEVEL_ORDER = ("federal", "state", "county", "local", "school")


def _db_url() -> Optional[str]:
    return os.environ.get("DATABASE_URL", "").strip() or None


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


_RACES_SQL = """
    select r.id::text, r.position_name, g.name as locality,
           coalesce(cand.n, 0), coalesce(qs.n, 0),
           coalesce(ing.n, 0), coalesce(pend.n, 0)
    from essentials.races r
    join essentials.elections e on e.id = r.election_id
    left join essentials.offices o on o.id = r.office_id
    left join essentials.chambers ch on ch.id = o.chamber_id
    left join essentials.governments g on g.id = ch.government_id
    left join lateral (select count(*) n from essentials.race_candidates rc
        where rc.race_id = r.id and coalesce(rc.candidate_status,'active') <> 'withdrawn') cand on true
    left join lateral (select count(*) n from essentials.discovered_sources d
        where d.race_id = r.id and d.status = 'approved' and d.route = 'quote_source') qs on true
    left join lateral (select count(*) n from essentials.discovered_sources d
        where d.race_id = r.id and d.status = 'ingested') ing on true
    left join lateral (select count(*) n from essentials.discovered_sources d
        where d.race_id = r.id and d.status = 'pending') pend on true
    where e.state = %s
"""


def races_for_state(state: str) -> list:
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


def state_index() -> list:
    url = _db_url()
    if not url:
        return []
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select e.state, count(*) filter (where d.status = 'pending')
                    from essentials.elections e
                    join essentials.races r on r.election_id = e.id
                    left join essentials.discovered_sources d on d.race_id = r.id
                    where e.state is not null
                    group by e.state
                    order by 2 desc, e.state
                """)
                return [{"state": s, "pending": n} for (s, n) in cur.fetchall()]
        finally:
            conn.close()
    except Exception:
        return []
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gui_coverage.py -v`
Expected: PASS. If the `chambers.government_id` / `offices.chamber_id` join names differ, fix them here — the `test_race_coverage_shape_and_order` locality assertion is the guard.

- [ ] **Step 5: Commit**

```bash
git add gui/coverage.py tests/test_gui_coverage.py
git commit -m "feat(discovery): coverage aggregation — state index, sections, per-race counts"
```

---

### Task 5: Auto-approve sweep + trust/undo DB layer

**Files:**
- Create: `src/discovery/autoapprove.py`
- Modify: `gui/discovery.py`
- Test: `tests/test_discovery_autoapprove.py`, `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `FORMAL_EVENT_KINDS` (Task 2).
- Produces:
  - `auto_approve_pending(cur, outlet_id: str | None = None) -> int` — flips eligible pending rows to `approved`/`quote_source` with `status_reason='auto: trusted outlet'`; returns rows changed. Takes an open cursor (caller commits) so `poll_discovery` and the GUI can both use it.
  - `gui/discovery.py::set_outlet_trusted(outlet_id: str) -> bool`.
  - `gui/discovery.py::trust_from_row(row: DiscoveredRow) -> tuple[bool, str, int]` — register the outlet if the row is channel-only (reuse `watch_channel`), set it trusted, sweep it; returns `(ok, message, n_auto)`.
  - `gui/discovery.py::unapprove_auto(row_ids: list[str]) -> int` — returns only `auto:%` approved rows to pending.
  - `DiscoveredRow` gains `outlet_trusted: bool = False`, `outlet_ingest_barred: bool = False`, populated from a new `source_outlets` join in `_SELECT`.

- [ ] **Step 1: Write the failing test (the sweep predicate)**

```python
from src.discovery.autoapprove import ELIGIBLE_LANE_SQL  # a reusable WHERE fragment

def test_eligible_where_shape():
    # The fragment must require: trusted outlet, pending, race_id present,
    # original_vs_clip = 'clip', and event_kind NOT in the formal set.
    frag = ELIGIBLE_LANE_SQL
    for needle in ("o.trusted", "d.status = 'pending'", "d.race_id is not null",
                   "d.original_vs_clip = 'clip'", "not in %s"):
        assert needle in frag
```

Then an integration test against the discovery DB fixture:

```python
from src.discovery import autoapprove

def test_auto_approve_only_touches_trusted_lane3(seeded_autoapprove_db):
    # Fixture: one trusted outlet with a news-clip (clip/news_clip, race set),
    # an event-clip (clip/debate), a full_event (original), and one with no
    # race_id; plus an untrusted outlet with a news-clip. All pending.
    conn = seeded_autoapprove_db
    with conn.cursor() as cur:
        n = autoapprove.auto_approve_pending(cur)
    conn.commit()
    with conn.cursor() as cur:
        cur.execute("select status, route, status_reason from essentials.discovered_sources "
                    "where kind_tag = 'trusted_news_clip'")
        assert cur.fetchone() == ('approved', 'quote_source', 'auto: trusted outlet')
        for tag in ('trusted_event_clip', 'trusted_full_event', 'trusted_no_race', 'untrusted_news_clip'):
            cur.execute("select status from essentials.discovered_sources where kind_tag = %s", (tag,))
            assert cur.fetchone()[0] == 'pending'
    assert n == 1
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_autoapprove.py -v`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the sweep**

```python
"""Auto-approve trusted outlets' news-clip rows as quote sources.

Safety invariant: this only ever sets status='approved', route='quote_source'.
It never ingests. Lane parity with src.discovery.lanes.content_lane: a row is
eligible iff its outlet is trusted, it is pending, it has a race, it is a clip,
and its event kind is NOT a formal-event kind (so it is the 'news_clip' lane).
Barred outlets are still eligible — the bar is on ingest, not on quoting.
"""
from __future__ import annotations

from src.discovery.lanes import FORMAL_EVENT_KINDS

ELIGIBLE_LANE_SQL = (
    "o.trusted and d.status = 'pending' and d.race_id is not null "
    "and d.original_vs_clip = 'clip' "
    "and coalesce(d.event_kind_guess, '') not in %s"
)


def auto_approve_pending(cur, outlet_id: "str | None" = None) -> int:
    params = [tuple(FORMAL_EVENT_KINDS)]
    outlet_filter = ""
    if outlet_id is not None:
        outlet_filter = " and o.id = %s::uuid"
        params.append(outlet_id)
    cur.execute(f"""
        update essentials.discovered_sources d
        set status = 'approved', route = 'quote_source',
            status_reason = 'auto: trusted outlet', reviewed_at = now()
        from essentials.source_outlets o
        where d.outlet_id = o.id and {ELIGIBLE_LANE_SQL}{outlet_filter}
    """, tuple(params))
    return cur.rowcount
```

- [ ] **Step 4: Add the GUI trust/undo helpers**

In `gui/discovery.py`, extend `_SELECT` to join outlet flags and extend `DiscoveredRow`:

```python
# in _SELECT, add to the column list: o.trusted, o.ingest_barred
# and add to the FROM/joins:
#   left join essentials.source_outlets o on o.id = d.outlet_id
```
```python
# DiscoveredRow: add two fields (defaulted so get_row/_to_row positional
# construction still lines up — append them at the END of the select and the
# dataclass, after election_date; update _to_row to map them).
    outlet_trusted: bool = False
    outlet_ingest_barred: bool = False
```
```python
def set_outlet_trusted(outlet_id: str) -> bool:
    url = _db_url()
    if not url:
        return False
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.source_outlets
                    set trusted = true, trusted_at = now(), updated_at = now()
                    where id = %s::uuid
                """, (outlet_id,))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def trust_from_row(row: "DiscoveredRow") -> "tuple[bool, str, int]":
    """Trust the row's outlet (registering it first if the row is channel-only),
    then sweep its pending news clips. Returns (ok, message, n_auto_kept)."""
    from src.discovery.autoapprove import auto_approve_pending
    outlet_id = row.outlet_id
    if not outlet_id:
        ok, _ = watch_channel(row)          # upserts an outlet for the channel
        if not ok:
            return False, "could not register outlet", 0
        outlet_id = _outlet_id_for_channel(row.channel_id)  # small helper: select id by feed_url
        if not outlet_id:
            return False, "outlet not found after register", 0
    if not set_outlet_trusted(outlet_id):
        return False, "failed to set trusted", 0
    url = _db_url()
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                n = auto_approve_pending(cur, outlet_id)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        n = 0
    return True, f"trusted {row.channel_name or 'outlet'}", n


def unapprove_auto(row_ids: "list[str]") -> int:
    if not row_ids:
        return 0
    url = _db_url()
    if not url:
        return 0
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    update essentials.discovered_sources
                    set status = 'pending', status_reason = null, reviewed_at = null
                    where id = any(%s::uuid[])
                      and status = 'approved' and status_reason like 'auto:%%'
                """, (row_ids,))
                n = cur.rowcount
            conn.commit()
            return n
        finally:
            conn.close()
    except Exception:
        return 0
```

Add the tiny `_outlet_id_for_channel(channel_id)` helper (select `id` from `source_outlets` where `external_channel_id = %s`, best-effort).

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_autoapprove.py tests/test_gui_discovery.py -v`
Expected: PASS. Add a `test_unapprove_auto_only_auto_rows` case asserting a human-approved row (no `auto:` reason) is untouched.

- [ ] **Step 6: Commit**

```bash
git add src/discovery/autoapprove.py gui/discovery.py tests/test_discovery_autoapprove.py tests/test_gui_discovery.py
git commit -m "feat(discovery): auto-approve sweep + outlet trust/undo helpers"
```

---

### Task 6: Wire auto-approve into the poll + a health summary line

**Files:**
- Modify: `scripts/poll_discovery.py`
- Modify: `gui/discovery.py` (`health()`)
- Test: `tests/test_poll_discovery.py`, `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `auto_approve_pending` (Task 5).
- Produces: `health()` dict gains `auto_kept_week: int` and `auto_kept_outlets: int`.

- [ ] **Step 1: Write the failing test (poll hook)**

```python
def test_poll_runs_auto_approve_after_insert(monkeypatch, poll_harness):
    # poll_harness stubs the engine to insert one trusted news-clip pending row,
    # then runs the poll's main(). Assert the row ends 'approved'/'quote_source'.
    poll_harness.run()
    assert poll_harness.status_of("trusted_news_clip") == ("approved", "quote_source")
```

And a `health()` test:

```python
def test_health_reports_auto_kept(seeded_autoapprove_db_with_auto_rows):
    from gui import discovery
    h = discovery.health()
    assert h["auto_kept_week"] >= 1
    assert h["auto_kept_outlets"] >= 1
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_poll_discovery.py -k auto_approve tests/test_gui_discovery.py -k auto_kept -v`
Expected: FAIL.

- [ ] **Step 3: Implement the poll hook**

In `scripts/poll_discovery.py`, after the discovery run finishes and before final teardown, open a cursor on the run's connection and call the sweep:

```python
from src.discovery.autoapprove import auto_approve_pending
# ... after run_discovery(...) completes on `conn`:
with conn.cursor() as cur:
    n_auto = auto_approve_pending(cur)      # all trusted outlets
conn.commit()
print(f"auto-kept {n_auto} trusted news-clip rows")
```

Add the `health()` summary query (fold onto the existing cursor in `health()`):

```python
cur.execute("""
    select count(*), count(distinct outlet_id)
    from essentials.discovered_sources
    where status = 'approved' and status_reason like 'auto:%%'
      and reviewed_at > now() - interval '7 days'
""")
ak_n, ak_outlets = cur.fetchone()
# add to the returned dict: "auto_kept_week": ak_n, "auto_kept_outlets": ak_outlets
```
(Also add both keys to the `empty` dict at the top of `health()`.)

- [ ] **Step 4: Run tests**

Run: `.venv/bin/python -m pytest tests/test_poll_discovery.py tests/test_gui_discovery.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/poll_discovery.py gui/discovery.py tests/test_poll_discovery.py tests/test_gui_discovery.py
git commit -m "feat(discovery): run auto-approve after each poll + health summary line"
```

---

### Task 7: Reorganized `/discovery` route + template + trust/ingest-gate

**Files:**
- Modify: `gui/app.py`
- Modify: `gui/templates/discovery.html`
- Test: `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `coverage.state_index`, `coverage.races_for_state`, `coverage.LEVEL_ORDER` (Tasks 3–4); `discovery.pending_rows`, `discovery.trust_from_row`, `discovery.unapprove_auto`, `DiscoveredRow.outlet_trusted/outlet_ingest_barred` (Task 5); `lanes.content_lane` (Task 2).
- Produces: `GET /discovery?state=IN` renders the reorganized page; `POST /discovery/{row_id}/trust`; `approve-ingest` refuses barred outlets.

- [ ] **Step 1: Write the failing route tests**

```python
def test_discovery_defaults_to_state_index(client_no_selection):
    r = client_no_selection.get("/discovery")
    assert r.status_code == 200
    assert "Indiana" in r.text  # a state from the index

def test_discovery_state_view_groups_by_level(client_seeded):
    r = client_seeded.get("/discovery?state=IN")
    body = r.text
    assert "Indiana Governor" in body
    assert "Bloomington Mayor" in body
    # statewide/federal band label present
    assert "tracked once" in body.lower()

def test_trust_route_sweeps(client_seeded):
    row_id = client_seeded.a_trusted_news_clip_row_id()
    r = client_seeded.post(f"/discovery/{row_id}/trust", follow_redirects=False)
    assert r.status_code == 303
    assert "trusted" in r.headers["location"].lower()

def test_ingest_blocked_for_barred_outlet(client_seeded):
    row_id = client_seeded.a_barred_outlet_row_id()
    r = client_seeded.post(f"/discovery/{row_id}/approve-ingest", follow_redirects=False)
    assert "chain tos" in r.headers["location"].lower()
```

- [ ] **Step 2: Run to verify fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "state_view or trust_route or barred" -v`
Expected: FAIL.

- [ ] **Step 3: Rebuild the route**

Rewrite `discovery_page` in `gui/app.py` to:
- read `state` (and optional `flash`, `show`) from the query;
- when no `state`: pass `coverage.state_index()` to render a state-picker list;
- when `state` set: build `sections` — a `statewide` list (races whose `level in ('federal','state')`) and `localities` (an ordered dict of `locality -> races` for the rest), each race carrying its counts; and, for the expanded race(s), its `pending_rows` filtered to that `race_id`, each tagged via `content_lane(row.original_vs_clip, row.event_kind_guess)` and grouped by outlet;
- keep passing `health()`.

Add the trust route:

```python
@app.post("/discovery/{row_id}/trust")
def discovery_trust(row_id: str):
    from gui import discovery
    row = discovery.get_row(row_id)
    if row is None:
        raise HTTPException(status_code=404)
    ok, msg, n = discovery.trust_from_row(row)
    flash = f"{msg} — auto-kept {n}" if ok else f"trust failed: {msg}"
    return _discovery_redirect(flash)
```

Gate ingest in `discovery_approve_ingest` (add right after the `row.status != 'pending'` check):

```python
    if row.outlet_ingest_barred:
        return _discovery_redirect(
            "chain ToS: don't host a transcript — pull a direct quote instead")
```

- [ ] **Step 4: Update the template**

In `gui/templates/discovery.html`, implement the two-pane layout matching the approved wireframe: a left rail from `state_index` / the selected state's localities (each with a pending count or a done check when local pending is 0), and a main pane of level-grouped races with the four counts, expanding to per-outlet, lane-tagged rows. Trusted outlets show their auto-kept news clips as a muted collapsed line; unknown outlets show a `Trust outlet` button (posts to the trust route) alongside today's approve/reject controls; the `Approve → ingest` control is disabled with the ToS note when `row.outlet_ingest_barred`.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: PASS.

- [ ] **Step 6: Visual check + commit**

Start the GUI (`.venv/bin/python -m gui`) and load `/discovery?state=IN`; confirm the sections, counts, done cue, a trust action, and a barred outlet's disabled ingest button. Then:

```bash
git add gui/app.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat(discovery): reorganized review page — state/locality sections, counts, trust, ingest gate"
```

---

## Self-review

- **Spec coverage:** state/county nav → Tasks 3,4,7 (locality grouping; county rollup explicitly deferred in Global Constraints). Four counts + done cue → Tasks 4,7. Frictionless trust → Task 5,7. Barred-for-ingest → Tasks 1,5,7. Auto-approve lane 3 + safety invariant → Tasks 2,5,6. Provenance + summary + undo → Tasks 5,6. Content lanes → Task 2 (Slice 1 uses lane 3; lane 1/2 rows stay pending with today's controls — matches spec). Slice 2 (hunt, ingest glance) and earned auto-qualify are correctly absent.
- **Placeholder scan:** no TBD/TODO; the one deferred item (county rollup) is a scoped non-goal, not a gap. The `chambers.government_id`/`offices.chamber_id` join is guarded by a locality assertion (Task 4 Step 4).
- **Type consistency:** `content_lane` returns the same four strings used by Task 7's tagging and by the SQL parity in Task 5 (`ELIGIBLE_LANE_SQL` mirrors `FORMAL_EVENT_KINDS`). `RaceCoverage` fields match between Task 4 and Task 7. `trust_from_row` returns `(ok, msg, n)` consumed verbatim in Task 7.
