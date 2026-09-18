# Slice 2 Phase 3 — local-type hubs + hub flywheel — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `local_type` comparable-source hubs deliver — run them only for genuinely local races, using a real derived locality — tune their budget, and let a reviewer add a confirmed hub from the discovery GUI.

**Architecture:** Three no-migration parts. Part 1 derives a per-race locality (`governments.name` cleaned) plus a level gate (`race_level`), so `local_type` hubs run only for county/local/school races; today they wrongly run for all 445 federal races with a garbage locality. Part 2 value-ranks the applicable hubs (domain hubs first, speculative `local_type` last) and sub-caps `local_type` searches per race. Part 3 adds an "Add as hub" inline form to the review GUI that writes `essentials.source_hubs` rows with `added_via='flywheel'`.

**Tech Stack:** Python 3.14 (project `.venv`), psycopg2, FastAPI + Jinja2 (`gui/`), pytest. Discovery engine in `src/discovery/`.

## Global Constraints

- **Run tests** with `.venv/bin/python -m pytest` (the venv lives in the on-the-record MAIN checkout; run from the worktree cwd so `src`/`gui` resolve to the worktree). Never system `python3`.
- **No ev-accounts migration this phase.** All code is pure on-the-record. `essentials.source_hubs` + `added_via='flywheel'` already exist and are live (migration 1869).
- **The engine (`src/discovery/`) MUST NOT import from `gui/`.** Shared logic goes in `src/`.
- **GUI DB helpers are best-effort:** no `DATABASE_URL` or any DB error returns a falsy/empty result, never raises (mirror `gui/discovery.py::watch_channel`).
- **The flywheel INSERT deduplicates with `where not exists`** on `(domain, scope, coalesce(state,''))` — the table has no unique constraint.
- **The flywheel adds domain-scoped hubs only** (`scope` ∈ {`global`,`state`}, `poll_method='scoped_search'`). `local_type` template hubs stay hand-curated.
- **Commit after each task.** Small, focused commits. End commit messages with the Co-Authored-By trailer.

---

### Task 1: Shared `race_level` module

Move the level classifier out of `gui/` so `src/discovery/` can use it without importing from `gui/`. Fix the factually-wrong locality comment while here.

**Files:**
- Create: `src/race_level.py`
- Modify: `gui/coverage.py:17-39` (delete the local regex + `race_level` + `LEVEL_ORDER`, import them instead) and `gui/coverage.py:59-63` (comment fix)
- Test: `tests/test_race_level.py` (new)

**Interfaces:**
- Produces: `src.race_level.race_level(position_name: "str | None") -> str` returning one of `"federal"|"state"|"county"|"local"|"school"`; `src.race_level.LEVEL_ORDER: tuple` = `("federal","state","county","local","school")`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_race_level.py`:

```python
from src.race_level import LEVEL_ORDER, race_level


def test_race_level_classifies_each_level():
    assert race_level("U.S. Representative District 9") == "federal"
    assert race_level("U.S. Senate Alabama") == "federal"
    assert race_level("Governor") == "state"
    assert race_level("Governor of Maine") == "state"
    assert race_level("Attorney General") == "state"
    assert race_level("Brown County Commissioner") == "county"
    assert race_level("Bloomington School Board") == "school"
    assert race_level("Los Angeles Mayor") == "local"
    assert race_level(None) == "local"
    assert LEVEL_ORDER == ("federal", "state", "county", "local", "school")


def test_gui_coverage_reexports_race_level():
    # coverage.py must keep exposing race_level for its existing callers/tests.
    from gui.coverage import race_level as cov_race_level
    assert cov_race_level("Governor") == "state"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_race_level.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.race_level'`

- [ ] **Step 3: Create `src/race_level.py`**

Move the definitions verbatim from `gui/coverage.py`:

```python
"""Race-level classifier over position_name.

Pure heuristic used to group races and to gate local-type hub searches. It is
easy to extend; misgrouping a race is cosmetic for the coverage view and, for
the hub gate, only widens/narrows which speculative local searches run.
"""
from __future__ import annotations

import re

_FEDERAL = re.compile(r"\b(president|u\.?s\.?|united states|congress)\b", re.I)
_STATE = re.compile(
    r"\b(governor|lieutenant governor|attorney general|secretary of state|"
    r"state\s+(?:senat\w*|represent\w*|assembly|house)|comptroller|treasurer|"
    r"superintendent)\b", re.I)
_SCHOOL = re.compile(r"\b(school board|board of education|school district)\b", re.I)
_COUNTY = re.compile(r"\bcounty\b", re.I)

LEVEL_ORDER = ("federal", "state", "county", "local", "school")


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

- [ ] **Step 4: Rewire `gui/coverage.py`**

Delete the moved block (`_FEDERAL`…`LEVEL_ORDER` and `def race_level`, currently `gui/coverage.py:17-39`). Add, next to the other imports at the top:

```python
from src.race_level import LEVEL_ORDER, race_level  # noqa: F401  (re-exported for callers/tests)
```

Then fix the stale comment at `gui/coverage.py:59-63`. Replace it with:

```python
# Join chain: races.office_id -> offices.id, offices.chamber_id ->
# chambers.id, chambers.government_id -> governments.id. governments.name is
# the jurisdiction name; NOTE it is populated for federal/statewide races too
# (e.g. 'United States Federal Government', 'State of Arizona'), NOT just local
# ones, and can be NULL for some statewide offices — so it is a display label
# here, never a "is this local?" test (see src/discovery/locality.py for that).
# races.election_id -> elections.id carries elections.state, the filter here.
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_race_level.py tests/test_gui_coverage.py -v`
Expected: PASS (both the new module tests and the untouched coverage tests)

- [ ] **Step 6: Commit**

```bash
git add src/race_level.py gui/coverage.py tests/test_race_level.py
git commit -m "refactor(discovery): extract race_level into src/race_level for the engine + fix coverage locality comment

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `local_query_locality` pure function

Turn `(position_name, government_name)` into a clean locality string, or `None` when the race is not local.

**Files:**
- Create: `src/discovery/locality.py`
- Test: `tests/test_discovery_locality.py` (new)

**Interfaces:**
- Consumes: `src.race_level.race_level`.
- Produces: `src.discovery.locality.local_query_locality(position_name: "str | None", government_name: "str | None") -> "str | None"`. Returns a cleaned locality (e.g. `"Los Angeles"`) only when `race_level(position_name)` ∈ {county, local, school} AND `government_name` is a real place (not the federal sentinel, not `State of …`, not empty). Else `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_discovery_locality.py`:

```python
from src.discovery.locality import local_query_locality


def test_federal_race_returns_none():
    assert local_query_locality(
        "U.S. Representative District 9", "United States Federal Government") is None


def test_statewide_state_of_prefix_returns_none():
    assert local_query_locality("Governor", "State of Arizona") is None


def test_statewide_null_government_returns_none():
    assert local_query_locality("Governor of Maine", None) is None


def test_local_place_is_cleaned():
    assert local_query_locality(
        "Los Angeles Mayor", "Los Angeles, California, US") == "Los Angeles"


def test_county_place_is_cleaned():
    assert local_query_locality(
        "Brown County Commissioner", "Brown County, Indiana, US") == "Brown County"


def test_local_bare_place_without_tail_is_unchanged():
    assert local_query_locality("Bloomington School Board", "Bloomington") == "Bloomington"


def test_local_level_but_no_government_returns_none():
    assert local_query_locality("Los Angeles Mayor", None) is None


def test_local_level_but_federal_sentinel_government_returns_none():
    # belt-and-suspenders: the sentinel wins even if the level looks local
    assert local_query_locality("Los Angeles Mayor", "United States Federal Government") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_locality.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'src.discovery.locality'`

- [ ] **Step 3: Create `src/discovery/locality.py`**

```python
"""Derive a per-race locality string for local-type hub searches.

Pure + DB-free. The governments.name value is populated for federal/statewide
races too (e.g. 'United States Federal Government', 'State of Arizona') and can
be NULL for some statewide offices, so a real locality needs BOTH a local race
level AND a government name that is an actual place.
"""
from __future__ import annotations

import re

from src.race_level import race_level

_LOCAL_LEVELS = {"county", "local", "school"}
_FED_SENTINEL = "united states federal government"
_STATE_PREFIX = "state of "
# A trailing ", <region>, US"/"USA" geography tail (e.g. ", California, US").
_GEO_TAIL = re.compile(r",\s*[^,]+,\s*(?:us|usa)\s*$", re.I)


def local_query_locality(position_name: "str | None",
                         government_name: "str | None") -> "str | None":
    if race_level(position_name) not in _LOCAL_LEVELS:
        return None
    name = (government_name or "").strip()
    if not name:
        return None
    low = name.lower()
    if low == _FED_SENTINEL or low.startswith(_STATE_PREFIX):
        return None
    cleaned = _GEO_TAIL.sub("", name).strip()
    return cleaned or None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery_locality.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/discovery/locality.py tests/test_discovery_locality.py
git commit -m "feat(discovery): local_query_locality — clean per-race locality for local-type hubs

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `TrackedCandidate` carries position_name + government_name

Load the two columns the locality derivation needs, per tracked candidate.

**Files:**
- Modify: `src/discovery/models.py:36-43` (add two fields)
- Modify: `src/discovery/db.py:49-65` (`fetch_tracked_candidates` SQL + mapping)
- Test: `tests/test_discovery_db.py:108-121` (update the existing test row + assertions)

**Interfaces:**
- Produces: `TrackedCandidate` gains `position_name: Optional[str] = None` and `government_name: Optional[str] = None` (appended AFTER `state`, so positional constructions like `TrackedCandidate("p1","r1","name","label","2026-11-03")` are unaffected). `fetch_tracked_candidates` returns each row with those two fields populated from `r.position_name` and `governments.name`.

- [ ] **Step 1: Write the failing test**

Replace the body of `tests/test_discovery_db.py::test_fetch_tracked_candidates_filters_active_pipeline_races` (currently a 6-column fake row) with an 8-column row and the new assertions:

```python
def test_fetch_tracked_candidates_filters_active_pipeline_races():
    cur = _FakeCursor(rows=[("p1", "r1", "Maria Delgado", "TX Senate (general)",
                             "2026-11-03", "TX", "U.S. Senate Texas",
                             "United States Federal Government")])
    tracked = db.fetch_tracked_candidates(cur)
    sql, _ = cur.executed[0]
    assert "readrank_race_pipeline" in sql
    assert "state" in sql.lower()
    assert "governments" in sql.lower()          # office->chamber->government chain joined
    assert "position_name" in sql.lower()
    assert tracked[0].state == "TX"
    assert tracked[0].position_name == "U.S. Senate Texas"
    assert tracked[0].government_name == "United States Federal Government"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py::test_fetch_tracked_candidates_filters_active_pipeline_races -v`
Expected: FAIL — `TypeError` (too many values to map) or `AttributeError: 'TrackedCandidate' object has no attribute 'government_name'`

- [ ] **Step 3: Add the fields to `TrackedCandidate`**

In `src/discovery/models.py`, extend the dataclass (append after `state`):

```python
@dataclass
class TrackedCandidate:
    politician_id: str
    race_id: str
    full_name: str
    race_label: str
    election_date: Optional[str] = None  # ISO date
    state: Optional[str] = None  # 2-letter, UPPERCASE (via races -> elections join)
    position_name: Optional[str] = None   # races.position_name (level classifier input)
    government_name: Optional[str] = None  # governments.name (locality source; see locality.py)
```

- [ ] **Step 4: Extend the query + mapping in `db.py`**

Replace `fetch_tracked_candidates` (`src/discovery/db.py:49-65`) with:

```python
def fetch_tracked_candidates(cur) -> list:
    cur.execute("""
        select rc.politician_id::text, rc.race_id::text, rc.full_name,
               p.race_label, p.election_date::text, e.state,
               r.position_name, g.name
        from essentials.race_candidates rc
        join essentials.readrank_race_pipeline p on p.race_id = rc.race_id
        left join essentials.races r on r.id = rc.race_id
        left join essentials.elections e on e.id = r.election_id
        left join essentials.offices o on o.id = r.office_id
        left join essentials.chambers ch on ch.id = o.chamber_id
        left join essentials.governments g on g.id = ch.government_id
        where p.status in ('needs_quotes','quotes_staged','published')
          and p.election_date >= current_date
          and coalesce(rc.candidate_status, 'active') not in ('withdrawn','removed')
          and rc.full_name is not null
        order by rc.race_id, rc.full_name
    """)
    return [TrackedCandidate(politician_id=r[0], race_id=r[1], full_name=r[2],
                             race_label=r[3], election_date=r[4], state=r[5],
                             position_name=r[6], government_name=r[7])
            for r in cur.fetchall()]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_db.py -v`
Expected: PASS (the updated test and all sibling db tests)

- [ ] **Step 6: Commit**

```bash
git add src/discovery/models.py src/discovery/db.py tests/test_discovery_db.py
git commit -m "feat(discovery): load position_name + governments.name per tracked candidate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `hubs_for_race` locality gate + `rank_hubs`

Gate `local_type` hubs on a locality, and add a value-rank ordering used before spending the budget.

**Files:**
- Modify: `src/discovery/hubs.py:31-57` (`hubs_for_race` gains `locality`) and add `rank_hubs`
- Test: `tests/test_discovery_hubs.py` (update the local_type-inclusion tests to pass a locality; add gate + rank tests)

**Interfaces:**
- Produces: `hubs_for_race(hubs, *, state, locality: "str | None" = None, scoped_only=True)` — includes `local_type` hubs only when `locality` is truthy; `global`/`state` selection unchanged; **preserves input order** (ranking is separate). `rank_hubs(hubs: "list[Hub]") -> "list[Hub]"` — returns a new list ordered domain-hubs-first, then a light kind order (`debate < forum < guide < pamphlet < questionnaire`), then name. Pure; does not mutate input.

- [ ] **Step 1: Write the failing tests**

In `tests/test_discovery_hubs.py`, (a) update the four tests that expect `local_type` included so they pass a `locality`, and (b) add the new tests. Update these existing tests:

```python
def test_hubs_for_race_scoped_only_default_includes_global_state_local_excludes_wrong_state_and_feed():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state="AZ", locality="Phoenix")
    names = [h.name for h in result]
    assert names == [
        "Ballotpedia",
        "AZ Clean Elections Voter Guide",
        "City Clerk Local Voter Guides",
    ]
    assert "OR Voter Guide" not in names
    assert "AZ Republic Candidate Q&A Feed" not in names


def test_hubs_for_race_scoped_only_false_includes_feed_hubs_too():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state="AZ", locality="Phoenix", scoped_only=False)
    names = [h.name for h in result]
    assert names == [
        "Ballotpedia",
        "AZ Clean Elections Voter Guide",
        "City Clerk Local Voter Guides",
        "AZ Republic Candidate Q&A Feed",
    ]
    assert "OR Voter Guide" not in names


def test_hubs_for_race_none_state_excludes_state_scoped_but_keeps_global_and_local_type():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state=None, locality="Phoenix")
    names = [h.name for h in result]
    assert names == ["Ballotpedia", "City Clerk Local Voter Guides"]


def test_hubs_for_race_empty_state_excludes_state_scoped_too():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state="", locality="Phoenix")
    names = [h.name for h in result]
    assert names == ["Ballotpedia", "City Clerk Local Voter Guides"]
```

Add these new tests (put them after the existing `hubs_for_race` tests):

```python
def test_local_type_excluded_without_locality():
    hubs = _mixed_hubs()
    result = hubs_for_race(hubs, state="AZ", locality=None)
    names = [h.name for h in result]
    assert "City Clerk Local Voter Guides" not in names
    assert names == ["Ballotpedia", "AZ Clean Elections Voter Guide"]


def test_local_type_included_only_when_locality_present():
    hubs = _mixed_hubs()
    with_loc = [h.name for h in hubs_for_race(hubs, state="AZ", locality="Phoenix")]
    without = [h.name for h in hubs_for_race(hubs, state="AZ", locality="")]
    assert "City Clerk Local Voter Guides" in with_loc
    assert "City Clerk Local Voter Guides" not in without


def test_rank_hubs_orders_domain_hubs_before_local_type_then_by_kind_then_name():
    from src.discovery.hubs import rank_hubs
    hubs = [
        Hub(name="Z Local", scope="local_type", poll_method="scoped_search"),  # no domain
        Hub(name="Ballotpedia", scope="global", poll_method="scoped_search",
            domain="ballotpedia.org", kind="questionnaire"),
        Hub(name="Debate Comm", scope="state", state="UT", poll_method="scoped_search",
            domain="utahdebatecommission.org", kind="debate"),
    ]
    ranked = [h.name for h in rank_hubs(hubs)]
    # domain hubs first (debate before questionnaire by kind), local_type last
    assert ranked == ["Debate Comm", "Ballotpedia", "Z Local"]


def test_rank_hubs_does_not_mutate_input():
    from src.discovery.hubs import rank_hubs
    hubs = _mixed_hubs()
    before = list(hubs)
    rank_hubs(hubs)
    assert hubs == before
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_hubs.py -v`
Expected: FAIL — `TypeError: hubs_for_race() got an unexpected keyword argument 'locality'` and `ImportError` for `rank_hubs`.

- [ ] **Step 3: Implement the gate + `rank_hubs`**

In `src/discovery/hubs.py`, replace `hubs_for_race` and add `rank_hubs`:

```python
def hubs_for_race(hubs: "list[Hub]", *, state: Optional[str],
                   locality: "str | None" = None,
                   scoped_only: bool = True) -> "list[Hub]":
    """Return the hubs applicable to a race in the given state.

    - every 'global' hub is included
    - a 'local_type' hub is included ONLY when `locality` is truthy (a real
      city/county/school locality was derived — see locality.py). Statewide and
      federal races pass locality=None and get no local_type searches.
    - a 'state' hub is included only when `state` is truthy AND matches the
      hub's state case-insensitively
    - when scoped_only (default True), only poll_method == 'scoped_search' hubs
      are kept (feed hubs are polled via source_outlets, not this lane)

    Pure: does not mutate `hubs`; preserves input order (see rank_hubs for the
    value ordering used before spending the search budget).
    """
    race_state = (state or "").upper()
    applicable = []
    for hub in hubs:
        if hub.scope == "global":
            applicable.append(hub)
        elif hub.scope == "local_type":
            if locality:
                applicable.append(hub)
        elif hub.scope == "state":
            if race_state and (hub.state or "").upper() == race_state:
                applicable.append(hub)

    if scoped_only:
        applicable = [h for h in applicable if h.poll_method == "scoped_search"]

    return applicable


_KIND_RANK = {"debate": 0, "forum": 1, "guide": 2, "pamphlet": 3, "questionnaire": 4}


def rank_hubs(hubs: "list[Hub]") -> "list[Hub]":
    """Value-rank hubs so a bounded per-race budget is spent best-first:
    concrete-domain hubs (deterministic site: search, high yield) before
    speculative local_type/query-template hubs (no domain); then a light kind
    order; then name for determinism. Pure; returns a new list."""
    def key(hub):
        return (0 if hub.domain else 1, _KIND_RANK.get(hub.kind, 9), hub.name or "")
    return sorted(hubs, key=key)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_hubs.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/discovery/hubs.py tests/test_discovery_hubs.py
git commit -m "feat(discovery): gate local_type hubs on a locality + rank_hubs value order

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: `raw_items_for_race` local_type sub-cap + config

Cap the speculative `local_type` searches per race separately from the overall hub budget.

**Files:**
- Modify: `src/config.py:139` (add the constant)
- Modify: `src/discovery/hub_search.py:32-61` (`raw_items_for_race` gains `local_type_budget`)
- Test: `tests/test_discovery_hub_search.py` (add sub-cap tests)

**Interfaces:**
- Produces: `config.DISCOVERY_HUB_LOCAL_TYPE_BUDGET = 2`. `raw_items_for_race(hubs_for_this_race, *, candidates, locality, year, budget=6, local_type_budget: "int | None" = None)` — when `local_type_budget` is not None, stops issuing searches for `scope == "local_type"` hubs once that many local_type searches have run; domain/other hubs still run up to `budget`. `None` = no sub-cap (back-compatible with existing callers/tests).

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_discovery_hub_search.py`:

```python
def test_local_type_sub_cap_limits_only_local_type_searches(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hubs = [
        Hub(name="LT1", scope="local_type", poll_method="scoped_search",
            query_template="<locality> forum <year>"),
        Hub(name="LT2", scope="local_type", poll_method="scoped_search",
            query_template="<locality> voter guide <year>"),
        Hub(name="LT3", scope="local_type", poll_method="scoped_search",
            query_template="<locality> chamber <year>"),
        Hub(name="Domain", scope="global", poll_method="scoped_search", domain="a.com"),
    ]
    raw_items_for_race(hubs, candidates=["Jane"], locality="Springfield",
                       year="2026", budget=6, local_type_budget=1)

    # exactly ONE local_type search + the domain search = 2 total
    assert len(fake.calls) == 2
    assert any(c.startswith("site:a.com") for c in fake.calls)
    assert sum(1 for c in fake.calls if "Springfield" in c and "site:" not in c) == 1


def test_local_type_budget_none_means_no_sub_cap(monkeypatch):
    fake = _FakeTavily()
    monkeypatch.setattr("src.discovery.hub_search.tavily_search", fake)

    hubs = [
        Hub(name="LT1", scope="local_type", poll_method="scoped_search",
            query_template="<locality> a <year>"),
        Hub(name="LT2", scope="local_type", poll_method="scoped_search",
            query_template="<locality> b <year>"),
    ]
    raw_items_for_race(hubs, candidates=["Jane"], locality="Springfield",
                       year="2026", budget=6)  # local_type_budget defaults None
    assert len(fake.calls) == 2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_discovery_hub_search.py::test_local_type_sub_cap_limits_only_local_type_searches -v`
Expected: FAIL — `TypeError: raw_items_for_race() got an unexpected keyword argument 'local_type_budget'`

- [ ] **Step 3: Add the config constant**

In `src/config.py`, immediately after the `DISCOVERY_HUB_BUDGET` line:

```python
DISCOVERY_HUB_LOCAL_TYPE_BUDGET = 2             # sub-cap: speculative local_type scoped searches per race
```

- [ ] **Step 4: Implement the sub-cap**

Replace `raw_items_for_race` in `src/discovery/hub_search.py`:

```python
def raw_items_for_race(hubs_for_this_race, *, candidates, locality, year,
                        budget: int = 6,
                        local_type_budget: "int | None" = None) -> "list[RawItem]":
    items: list[RawItem] = []
    searches_done = 0
    local_type_done = 0

    for hub in hubs_for_this_race:
        if _is_pointer_only(hub):
            continue
        if searches_done >= budget:
            break
        is_local_type = hub.scope == "local_type"
        if is_local_type and local_type_budget is not None \
                and local_type_done >= local_type_budget:
            continue

        query = _query_for_hub(hub, candidates=candidates, locality=locality, year=year)
        if query is None:
            continue

        results = tavily_search(query)
        searches_done += 1
        if is_local_type:
            local_type_done += 1

        if hub.domain:
            results = [r for r in results if hub.domain in (r.get("url") or "")]

        for r in results[:MAX_ITEMS_PER_HUB]:
            items.append(RawItem(
                url=r["url"],
                title=r.get("title"),
                description=r.get("content"),
                via="hub",
            ))

    return items
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_hub_search.py -v`
Expected: PASS (new sub-cap tests + all existing hub_search tests, which omit `local_type_budget` and are unchanged)

- [ ] **Step 6: Commit**

```bash
git add src/config.py src/discovery/hub_search.py tests/test_discovery_hub_search.py
git commit -m "feat(discovery): per-race local_type search sub-cap (DISCOVERY_HUB_LOCAL_TYPE_BUDGET)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Engine wires locality + rank + budgets

Compute the locality per race, rank the applicable hubs, and pass the locality + sub-cap into the hub search.

**Files:**
- Modify: `src/discovery/engine.py:18-24` (imports) and `src/discovery/engine.py:259-270` (the hub-phase call)
- Test: `tests/test_discovery_engine.py` (add a locality-gate test; existing hub tests stay green)

**Interfaces:**
- Consumes: `local_query_locality` (Task 2), `hubs_for_race` + `rank_hubs` (Task 4), `config.DISCOVERY_HUB_LOCAL_TYPE_BUDGET` (Task 5), `TrackedCandidate.position_name`/`government_name` (Task 3).
- Produces: for each swept race, `locality = local_query_locality(cands[0].position_name, cands[0].government_name)`; `applicable = rank_hubs(hubs_for_race(all_hubs, state=cands[0].state, locality=locality))`; `hub_raw_items_fn(applicable, candidates=…, locality=locality, year=…, budget=config.DISCOVERY_HUB_BUDGET, local_type_budget=config.DISCOVERY_HUB_LOCAL_TYPE_BUDGET)`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_discovery_engine.py` (after the existing hub-lane block). It injects one federal and one local race, a real hub set, and a capturing `hub_raw_items_fn`:

```python
def test_hub_lane_gates_local_type_and_passes_clean_locality(monkeypatch):
    import dataclasses
    fed = TrackedCandidate("pf", "rf", "Jane Fed", "U.S. Representative District 9",
                           "2026-11-03", state="CA",
                           position_name="U.S. Representative District 9",
                           government_name="United States Federal Government")
    loc = TrackedCandidate("pl", "rl", "Kay Local", "Los Angeles Mayor",
                           "2026-11-03", state="CA",
                           position_name="Los Angeles Mayor",
                           government_name="Los Angeles, California, US")
    monkeypatch.setattr(db, "fetch_tracked_candidates", lambda cur: [fed, loc])

    hubs = [
        Hub(name="Ballotpedia", scope="global", poll_method="scoped_search",
            domain="ballotpedia.org", kind="questionnaire"),
        Hub(name="Local LWV forum", scope="local_type", poll_method="scoped_search",
            query_template='"<locality>" League of Women Voters candidate forum <year>'),
    ]
    calls = {}

    def capture(applicable, **kw):
        # key by whether a local_type hub survived the gate for this race
        calls[kw["locality"]] = [h.name for h in applicable]
        return []

    inserted = []
    stats, _ = _run(
        monkeypatch, inserted, skip_watchlist=True, skip_sweeps=True,
        load_hubs_fn=lambda cur: hubs, hub_raw_items_fn=capture)

    # federal race: locality is None -> local_type excluded
    assert calls[None] == ["Ballotpedia"]
    # local race: clean locality "Los Angeles" -> local_type included, ranked last
    assert calls["Los Angeles"] == ["Ballotpedia", "Local LWV forum"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_engine.py::test_hub_lane_gates_local_type_and_passes_clean_locality -v`
Expected: FAIL — `KeyError: None` / assertion mismatch (engine still passes `locality=race_label`, so the keys are race labels and local_type is never gated).

- [ ] **Step 3: Update the engine imports**

In `src/discovery/engine.py`, change the hubs import (line 20) and add the locality import:

```python
from src.discovery.hubs import hubs_for_race, rank_hubs
from src.discovery.locality import local_query_locality
```

- [ ] **Step 4: Update the hub-phase call**

In the hub lane (currently `src/discovery/engine.py:259-270`), replace the `applicable = …` line and the `hub_raw_items_fn(...)` call:

```python
                locality = local_query_locality(cands[0].position_name,
                                                 cands[0].government_name)
                applicable = rank_hubs(hubs_for_race(
                    all_hubs, state=cands[0].state, locality=locality))
                if not applicable:
                    continue
                year = (cands[0].election_date or "")[:4]
                try:
                    items = hub_raw_items_fn(
                        applicable,
                        candidates=[c.full_name for c in cands],
                        locality=locality,                       # None for federal/statewide
                        year=year,
                        budget=config.DISCOVERY_HUB_BUDGET,
                        local_type_budget=config.DISCOVERY_HUB_LOCAL_TYPE_BUDGET,
                    )
```

(Leave the surrounding `for race_id, cands in by_race.items():` loop, the `sweep_due`/cap guards, the `except`, and the per-race `commit_unit` exactly as they are.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_discovery_engine.py -v`
Expected: PASS (the new gate test AND the four existing hub-lane tests — those inject `hub_raw_items_fn=lambda applicable, **kw: …`, which absorbs the new `local_type_budget` kwarg, and their TRACKED rows have `position_name=None`/`government_name=None` → `locality=None`, so only their global Ballotpedia hub is applicable as before).

- [ ] **Step 6: Commit**

```bash
git add src/discovery/engine.py tests/test_discovery_engine.py
git commit -m "feat(discovery): engine derives per-race locality, ranks hubs, sub-caps local_type

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 7: Flywheel data layer (`add_hub_from_row` + row properties)

Add the domain parser, the hub-kind default, and the guarded INSERT helper.

**Files:**
- Modify: `gui/discovery.py` (add `_hub_domain`, `_HUB_KINDS`, `_race_state`, `DiscoveredRow.hub_domain`, `DiscoveredRow.hub_kind_default`, `add_hub_from_row`)
- Test: `tests/test_gui_discovery.py` (properties + `add_hub_from_row` via a fake connect)

**Interfaces:**
- Produces:
  - `gui.discovery._HUB_KINDS = ("debate","forum","questionnaire","guide","pamphlet")`
  - `DiscoveredRow.hub_domain -> "str | None"` — registrable host of `self.url` (lowercased, `www.` stripped), `None` for empty/non-http/YouTube URLs.
  - `DiscoveredRow.hub_kind_default -> str` — `event_kind_guess` if it is one of `_HUB_KINDS`, else `"guide"`.
  - `gui.discovery.add_hub_from_row(row, *, scope: str, kind: str) -> "tuple[bool, str]"` — best-effort; inserts a `scoped_search`, `added_via='flywheel'` hub for `row.hub_domain`, guarded by `where not exists (domain, scope, coalesce(state,''))`. `scope='state'` resolves the state from `row.race_id`; missing state → `(False, ...)`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py`:

```python
def test_hub_domain_property_strips_www_and_ignores_youtube():
    assert _row(url="https://www.laist.com/x/y").hub_domain == "laist.com"
    assert _row(url="https://ballotpedia.org/Karen_Bass").hub_domain == "ballotpedia.org"
    assert _row(url="https://www.youtube.com/watch?v=abc12345678").hub_domain is None
    assert _row(url="").hub_domain is None


def test_hub_kind_default_maps_guess_or_falls_back():
    assert _row(event_kind_guess="forum").hub_kind_default == "forum"
    assert _row(event_kind_guess="questionnaire").hub_kind_default == "questionnaire"
    assert _row(event_kind_guess="news_clip").hub_kind_default == "guide"
    assert _row(event_kind_guess=None).hub_kind_default == "guide"


class _FakeHubConn:
    def __init__(self, inserted_id=("hub-1",)):
        self.executed = []
        self._id = inserted_id

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, sql, params=None):
        self.executed.append((sql, params))

    def fetchone(self):
        return self._id

    def commit(self):
        pass

    def close(self):
        pass


def test_add_hub_from_row_inserts_flywheel_hub(monkeypatch):
    conn = _FakeHubConn()
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setattr(discovery.psycopg2, "connect", lambda url: conn)
    monkeypatch.setattr(discovery, "_race_state", lambda race_id: "CA")

    row = _row(url="https://www.laist.com/elections/la-mayor", channel_name="LAist",
               event_kind_guess="forum", race_id="r1")
    ok, msg = discovery.add_hub_from_row(row, scope="state", kind="forum")

    assert ok is True
    sql, params = conn.executed[0]
    assert "essentials.source_hubs" in sql
    assert "'flywheel'" in sql and "'scoped_search'" in sql
    assert "where not exists" in sql.lower()
    assert "laist.com" in params            # domain bound
    assert "CA" in params                   # resolved state bound
    assert "forum" in params                # kind bound


def test_add_hub_from_row_state_scope_without_state_fails(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    monkeypatch.setattr(discovery, "_race_state", lambda race_id: None)
    row = _row(url="https://www.laist.com/x", race_id="r1")
    ok, msg = discovery.add_hub_from_row(row, scope="state", kind="forum")
    assert ok is False and "state" in msg.lower()


def test_add_hub_from_row_rejects_youtube_row(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://x")
    row = _row(url="https://www.youtube.com/watch?v=abc12345678")
    ok, msg = discovery.add_hub_from_row(row, scope="global", kind="forum")
    assert ok is False
```

(Add `import gui.discovery as discovery` is already at the top of the test file; `psycopg2` is referenced as `discovery.psycopg2`, which resolves because `gui/discovery.py` does `import psycopg2`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "hub" -v`
Expected: FAIL — `AttributeError: 'DiscoveredRow' object has no attribute 'hub_domain'` / `module 'gui.discovery' has no attribute 'add_hub_from_row'`

- [ ] **Step 3: Add the domain parser + kinds constant near the top of `gui/discovery.py`**

After the `_YT_ID` regex (`gui/discovery.py:21`), add:

```python
from urllib.parse import urlparse  # noqa: E402 (grouped with the module's other stdlib imports)

_HUB_KINDS = ("debate", "forum", "questionnaire", "guide", "pamphlet")
_WWW = re.compile(r"^www\.")


def _hub_domain(url: "str | None") -> "str | None":
    """Registrable host of an http(s) URL (lowercased, leading www. stripped);
    None for empty/non-http/YouTube URLs (YouTube uses the outlet flywheel)."""
    try:
        host = urlparse((url or "").strip()).netloc.lower()
    except ValueError:
        return None
    if not host or "youtube" in host or host == "youtu.be":
        return None
    return _WWW.sub("", host) or None
```

(If `urlparse` is already imported at the top of the file, do not import it again — just add the constant + function.)

- [ ] **Step 4: Add the two `DiscoveredRow` properties**

Inside `class DiscoveredRow`, next to the other `@property` methods (after `safe_url`):

```python
    @property
    def hub_domain(self) -> "str | None":
        return _hub_domain(self.url)

    @property
    def hub_kind_default(self) -> str:
        return self.event_kind_guess if self.event_kind_guess in _HUB_KINDS else "guide"
```

- [ ] **Step 5: Add `_race_state` and `add_hub_from_row`**

Add near the other flywheel helpers (e.g. after `set_outlet_trusted`):

```python
def _race_state(race_id: "str | None") -> "str | None":
    """Best-effort 2-letter state for a race (races -> elections), or None."""
    if not race_id:
        return None
    url = _db_url()
    if not url:
        return None
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    select e.state from essentials.races r
                    join essentials.elections e on e.id = r.election_id
                    where r.id = %s::uuid
                """, (race_id,))
                r = cur.fetchone()
                return (r[0] or None) if r else None
        finally:
            conn.close()
    except Exception:
        return None


def add_hub_from_row(row: "DiscoveredRow", *, scope: str, kind: str) -> "tuple[bool, str]":
    """Flywheel: register the row's web domain as a comparable-source hub
    (poll_method='scoped_search', added_via='flywheel'). Domain-scoped only —
    scope in {'global','state'}; state is resolved from the row's race. Idempotent
    via WHERE NOT EXISTS (no unique constraint on the table). Best-effort."""
    domain = _hub_domain(row.url)
    if not domain:
        return False, "no web domain on this row"
    if scope not in ("global", "state"):
        return False, "scope must be global or state"
    if kind not in _HUB_KINDS:
        return False, "invalid hub kind"
    url = _db_url()
    if not url:
        return False, "no DATABASE_URL"
    state = None
    if scope == "state":
        state = _race_state(row.race_id)
        if not state:
            return False, "no state for this race — choose global scope"
    name = row.channel_name or domain
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    insert into essentials.source_hubs
                      (name, scope, state, kind, poll_method, domain, tos_bucket,
                       active, added_via)
                    select %s, %s, %s, %s, 'scoped_search', %s, 'other', true, 'flywheel'
                    where not exists (
                        select 1 from essentials.source_hubs
                        where domain = %s and scope = %s
                          and coalesce(state, '') = coalesce(%s, ''))
                    returning id
                """, (name, scope, state, kind, domain, domain, scope, state))
                added = cur.fetchone() is not None
            conn.commit()
            return (True, f"added hub {domain}") if added \
                else (True, f"hub {domain} already registered")
        finally:
            conn.close()
    except Exception:
        return False, "failed to add hub (db error)"
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add gui/discovery.py tests/test_gui_discovery.py
git commit -m "feat(discovery): hub flywheel data layer — add_hub_from_row + DiscoveredRow hub props

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 8: Flywheel route + template

Expose the flywheel in the GUI: a route and an inline form on web rows.

**Files:**
- Modify: `gui/app.py` (add `POST /discovery/{row_id}/add-hub` beside `discovery_trust`)
- Modify: `gui/templates/discovery.html` (add the form inside the `row_actions` macro)
- Test: `tests/test_gui_discovery.py` (route + render)

**Interfaces:**
- Consumes: `discovery.get_row`, `discovery.add_hub_from_row` (Task 7), `_discovery_redirect` (existing).
- Produces: `POST /discovery/{row_id}/add-hub` (Form: `scope`, `kind`, carry-through `state`, `show`) → 303 redirect with a flash.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_gui_discovery.py`:

```python
def test_add_hub_route_calls_helper_and_flashes(monkeypatch):
    captured = {}
    monkeypatch.setattr(discovery, "get_row",
                        lambda rid: _row(url="https://www.laist.com/x"))

    def fake_add(row, *, scope, kind):
        captured["scope"], captured["kind"] = scope, kind
        return (True, "added hub laist.com")

    monkeypatch.setattr(discovery, "add_hub_from_row", fake_add)
    client = TestClient(create_app())
    resp = client.post("/discovery/d1/add-hub",
                       data={"scope": "state", "kind": "forum"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert "added hub laist.com" in resp.headers["location"]
    assert captured == {"scope": "state", "kind": "forum"}


def test_add_hub_form_shown_for_web_row_not_youtube(monkeypatch):
    monkeypatch.setattr(coverage, "races_for_state",
                        lambda state: [_race(position_name="Los Angeles Mayor", level="local")])
    monkeypatch.setattr(discovery, "health", lambda: {
        "pending": 1, "auto_kept": 0, "deferred": 0, "alarms": [], "recent_runs": []})
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda status="pending": [_row(url="https://www.laist.com/la-mayor",
                                                       race_id="r1")])
    client = TestClient(create_app())
    html = client.get("/discovery", params={"state": "CA"}).text
    assert "/add-hub" in html
    assert "laist.com" in html

    # a YouTube row must NOT show the add-hub form
    monkeypatch.setattr(discovery, "pending_rows",
                        lambda status="pending": [_row()])  # default youtube url
    html2 = client.get("/discovery", params={"state": "CA"}).text
    assert "/add-hub" not in html2
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "add_hub" -v`
Expected: FAIL — 404 (no route) / `/add-hub` absent from HTML.

- [ ] **Step 3: Add the route in `gui/app.py`**

Immediately after `discovery_trust` (ends at `gui/app.py:302`), add:

```python
    @app.post("/discovery/{row_id}/add-hub")
    def discovery_add_hub(row_id: str, scope: str = Form("state"),
                          kind: str = Form("guide"), state: str = Form(""),
                          show: str = Form("")):
        from gui import discovery
        row = discovery.get_row(row_id)
        if row is None:
            raise HTTPException(status_code=404)
        ok, msg = discovery.add_hub_from_row(row, scope=scope, kind=kind)
        return _discovery_redirect(msg if ok else f"add hub failed: {msg}",
                                   state=state, show=show)
```

- [ ] **Step 4: Add the form to the `row_actions` macro**

In `gui/templates/discovery.html`, inside the `row_actions` macro (after the `+ Watch this channel` block, before the closing `</div>` at line 97), add:

```html
  {% if r.hub_domain %}
  <form method="post" action="/discovery/{{ r.id }}/add-hub" style="margin:0;display:flex;gap:var(--sp-1);align-items:center;">
    <input type="hidden" name="state" value="{{ state or '' }}">
    <input type="hidden" name="show" value="{{ show or '' }}">
    <select name="scope" title="Hub scope">
      <option value="state" selected>state</option>
      <option value="global">global</option>
    </select>
    <select name="kind" title="Hub kind">
      {% for k in ['debate','forum','questionnaire','guide','pamphlet'] %}
      <option value="{{ k }}"{% if k == r.hub_kind_default %} selected{% endif %}>{{ k }}</option>
      {% endfor %}
    </select>
    <button type="submit" title="Register {{ r.hub_domain }} as a comparable-source hub for future races in this scope">+ Add {{ r.hub_domain }} as hub</button>
  </form>
  {% endif %}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add gui/app.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat(discovery): 'Add as hub' review-GUI flywheel (route + inline form)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 9: Full-suite verification

Confirm the whole discovery + GUI suite is green and the poll script imports cleanly.

**Files:** none (verification only)

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS (no failures; the pre-existing skip count is unchanged). If anything fails, fix it before proceeding — do not claim completion on a red suite.

- [ ] **Step 2: Smoke-check the poll script wiring (no network, no writes)**

Run: `.venv/bin/python -c "import scripts.poll_discovery as p; from src.discovery import engine, hubs, hub_search, locality; from src import config; print('HUB_BUDGET', config.DISCOVERY_HUB_BUDGET, 'LOCAL_TYPE', config.DISCOVERY_HUB_LOCAL_TYPE_BUDGET)"`
Expected: prints the two budgets, no ImportError.

- [ ] **Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "test(discovery): Phase 3 full-suite green

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

(If Step 1 was already green and no files changed, skip this commit.)

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-09-18-slice2-phase3-local-hubs-flywheel-design.md`):
- Part 1 corrected-premise gate + string → Tasks 1 (race_level), 2 (locality), 3 (load columns), 4 (hubs_for_race gate), 6 (engine wiring). ✓
- Part 2 rank + local_type sub-cap → Tasks 4 (rank_hubs), 5 (sub-cap + config), 6 (engine passes both). ✓
- Part 3 flywheel inline form, domain-scoped, `added_via='flywheel'`, `where not exists` → Tasks 7 (data layer), 8 (route + template). ✓
- Small cleanup (coverage.py comment) → Task 1 Step 4. ✓
- Testing (pure fns, fake cursor, fake tavily, engine fakes) → each task's tests. ✓
- Migration: none → no migration task; confirmed in Global Constraints. ✓

**2. Placeholder scan:** No TBD/TODO; every code + test step carries real content. ✓

**3. Type consistency:** `local_query_locality(position_name, government_name)` used identically in Tasks 2 and 6. `hubs_for_race(..., locality=...)` and `rank_hubs(...)` defined in Task 4, consumed in Task 6. `raw_items_for_race(..., local_type_budget=...)` defined in Task 5, called by the engine in Task 6 (via the injected `hub_raw_items_fn`, whose real binding is `hub_search.raw_items_for_race` in `scripts/poll_discovery.py:134` — already passing through `**kw`). `add_hub_from_row(row, *, scope, kind)` and `DiscoveredRow.hub_domain`/`hub_kind_default` defined in Task 7, used in Task 8. `TrackedCandidate.position_name`/`government_name` appended in Task 3, read in Task 6. ✓

Note: the injected `hub_raw_items_fn` binding in `scripts/poll_discovery.py` is `hub_search.raw_items_for_race`; Task 5 gives it the new `local_type_budget` kwarg and Task 6 passes it — no change needed in the poll script itself.
