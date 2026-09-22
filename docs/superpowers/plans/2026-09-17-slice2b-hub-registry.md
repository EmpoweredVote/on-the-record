# Slice 2B — comparable-source hub registry + polling — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** For every tracked race, systematically discover comparable common-question sources by polling a maintained hub registry — hubs become a third source of `RawItem`s feeding the existing classify-and-insert discovery pipeline.

**Architecture:** A new `essentials.source_hubs` table (registry) + per-race resolution + a `scoped_search` lane that runs ONE bounded, domain/query-scoped web search per applicable hub and yields `RawItem`s. Those flow through the engine's existing `process()` → classify → insert, so **Plan-2A's current-cycle check is the verification and Slice-1's insert is the disposition** — minimal new machinery. Feed-type hubs (public media, clean-elections) are registered as `source_outlets` and reuse Slice-1 polling.

**Tech Stack:** Python 3, `src/discovery/` (engine, classify, a new hub module + web-search util), `scripts/poll_discovery.py`, ev-accounts Postgres (`essentials`), pytest.

## Global Constraints
- **Depends on Plan 2A** (questionnaire kind + current-cycle/stale rejection in the classifier), which lands on the SAME branch `feat/slice2-comparable-hubs`. Execute 2B only after 2A's commits are on the branch.
- `.venv/bin/python -m pytest`. Never system `python3`. 🔴 The `.venv` lives only in the main checkout at `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv` — work in the main checkout, not a fresh worktree.
- Migration lives in `../ev-accounts/backend/migrations/` (separate repo), house style (idempotent, `DO $$` verify gate); pick the next number with `node backend/scripts/check-migration-numbers.mjs`. **Migration apply is GATED** — write + commit it; do not apply to prod (the human applies).
- Test harness has NO local DB (`tests/conftest.py` deletes `DATABASE_URL`): pure-fn tests + fake-cursor injection + `live_db`-gated integration (skipped by default). See `tests/test_gui_coverage.py` / `tests/test_discovery_db.py` for the fake-cursor pattern.
- **Reuse, don't reinvent:** the scoped web search reuses the Tavily call proven in the spike (`docs/superpowers/spikes/2026-09-17-slice2-comparable-hubs/bakeoff/engine_a.py::_tavily_search_real` — with its error-catch + query clamp); verification reuses `src/discovery/classify.py::classify_item` (post-2A it does current-cycle + questionnaire + tier + route); insertion reuses `src/discovery/db.py::insert_discovered` via the engine's existing `process()`. `TAVILY_API_KEY` + `OPENROUTER_API_KEY` are in `.env.local` (loaded by `gui.env.load_env_local`).
- **Bounded:** one scoped search per hub per race; a per-race hub budget (default 6). Deterministic, NOT an agentic loop (the spike showed agentic loops are low-recall/stale-prone).
- **ToS:** VOTE411 stays `pointer-only` — record it but do not scrape (LWV permission; 403). Newspaper/TV hubs ride Slice-1's `ingest_barred`. Prefer govt / public-media / clean-elections.

## File structure
- Create: `../ev-accounts/backend/migrations/<n>_source_hubs.sql` — the registry table + seed + feed-hub outlet seed.
- Create: `src/discovery/hubs.py` — `Hub` model, `load_hubs`, `hubs_for_race`.
- Create: `src/discovery/web_search.py` — a small Tavily web-search util (ported + hardened from the spike).
- Create: `src/discovery/hub_search.py` — per-race scoped-search lane → `RawItem`s.
- Modify: `src/discovery/engine.py` + `scripts/poll_discovery.py` — add the hub lane.
- Tests: `tests/test_discovery_hubs.py`, `tests/test_discovery_web_search.py`, `tests/test_discovery_hub_search.py`, additions to `tests/test_poll_discovery.py`.

---

### Task 1: `source_hubs` migration + seed (ev-accounts, GATED)

**Files:** Create `../ev-accounts/backend/migrations/<n>_source_hubs.sql`.

**Interfaces:** Produces `essentials.source_hubs` (`id`, `name`, `scope` in (`global`,`state`,`local_type`), `state char(2) null`, `kind`, `poll_method` in (`feed`,`scoped_search`), `domain text null`, `query_template text null`, `tos_bucket`, `active bool default true`, `added_via` in (`seed`,`flywheel`,`manual`), `notes`, `created_at`, `updated_at`).

- [ ] **Step 1: Write the migration** — `CREATE TABLE IF NOT EXISTS essentials.source_hubs (...)` with the columns above + CHECK constraints on the enums, idempotent, plus a `DO $$ … RAISE EXCEPTION` gate verifying the table exists. Seed rows (from the spec's seed list): GLOBAL — Ballotpedia (`scoped_search`, domain `ballotpedia.org`, kind `questionnaire`), VOTE411 (`scoped_search`, `vote411.org`, `questionnaire`, tos `vote411-lwv`, note "pointer-only"), Vote Smart. PER-STATE (scope `state`, `state`=XX) — AZ Clean Elections (`feed`)/Arizona PBS/KJZZ; OR Voters' Pamphlet (`scoped_search`, `oregonvotes.gov`, `pamphlet`)/OPB; CA LAist (`feed`)/Voter's Edge (`scoped_search`); TX Community Impact (`feed`); UT Utah Debate Commission (`scoped_search`)/KUER. LOCAL_TYPES (scope `local_type`) — local LWV forum, local newspaper guide, chamber forum, govt sample ballot, each with a `query_template` using `<locality>`/`<year>` placeholders.
   Also seed the `feed`-method hubs into `essentials.source_outlets` (so Slice-1 polls them) where a concrete feed URL is known.
- [ ] **Step 2: Do NOT apply — verify by eye** against neighbor migrations (`1551_source_discovery.sql`, `1863`, `1864`); report the chosen number. (Apply is the human's gated step.)
- [ ] **Step 3: Commit** the migration on the ev-accounts branch `feat/discovery-outlet-trust` (via a worktree if the primary checkout is on other WIP, as in Slice 1), `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

### Task 2: `src/discovery/hubs.py` — model + per-race resolution

**Files:** Create `src/discovery/hubs.py`; Test `tests/test_discovery_hubs.py`.

**Interfaces:** `@dataclass Hub` (fields mirroring the columns); `load_hubs(cur) -> list[Hub]` (active rows); `hubs_for_race(hubs, *, state, scoped_only=True) -> list[Hub]` = `scope=='global'` ∪ (`scope=='state'` and `hub.state==state`) ∪ `scope=='local_type'`, filtered to `poll_method=='scoped_search'` when `scoped_only`.

- [ ] **Step 1:** failing test — `hubs_for_race` selects global + matching-state + local_type, excludes other states, and (scoped_only) excludes `feed` hubs. Use in-memory `Hub` objects (pure selection, no DB).
- [ ] **Step 2:** run, confirm fail (`ModuleNotFoundError`).
- [ ] **Step 3:** implement the dataclass + `hubs_for_race` (pure) + a best-effort `load_hubs(cur)` that maps rows to `Hub`.
- [ ] **Step 4:** run tests, pass. **Step 5:** commit.

---

### Task 3: `src/discovery/web_search.py` + `hub_search.py` — scoped search → RawItems

**Files:** Create `src/discovery/web_search.py`, `src/discovery/hub_search.py`; Test `tests/test_discovery_web_search.py`, `tests/test_discovery_hub_search.py`.

**Interfaces:**
- `web_search.tavily_search(query, *, max_results=5) -> list[dict]` — ported from the spike's `_tavily_search_real` (POST `https://api.tavily.com/search`, `api_key` from env, query clamped to 380 chars, try/except → `[]` on failure, never raises). Returns `[{title,url,content}]`.
- `hub_search.raw_items_for_race(hubs_for_this_race, *, candidates, locality, year, budget=6) -> list[RawItem]` — for each hub (up to `budget`): build the scoped query from `hub.domain` (`site:<domain> <candidates/race>`) or `hub.query_template` (fill `<locality>`/`<year>`); run ONE `tavily_search`; turn the top hub-domain result(s) into `RawItem`s (`via="hub"`, url/title/description from the result). Skip `pointer-only`/VOTE411 hubs (record, don't fetch). No LLM here — verification happens downstream in `classify`.

- [ ] **Step 1:** failing tests — (a) `tavily_search` returns `[]` when `TAVILY_API_KEY` unset / on HTTP error (monkeypatch `requests.post`); (b) `raw_items_for_race` builds one scoped query per hub, respects `budget`, tags `via="hub"`, and skips pointer-only hubs. Inject a fake `tavily_search`.
- [ ] **Step 2:** run, confirm fail. **Step 3:** implement (port the hardened Tavily call; build queries; cap at `budget`). **Step 4:** run, pass. **Step 5:** commit.

---

### Task 4: wire the hub lane into the engine + poll

**Files:** Modify `src/discovery/engine.py` (`run_discovery`), `scripts/poll_discovery.py`; Test `tests/test_poll_discovery.py`, `tests/test_discovery_engine.py`.

**Interfaces:** `run_discovery` gains a hub lane: for each race that is due (reuse the existing per-race sweep cadence/`sweep_due`), resolve `hubs_for_race`, get `raw_items_for_race`, and feed those `RawItem`s through the SAME `process(item, roster_names, race_hint)` path the watchlist/search lanes use — so prefilter + `classify_item` (now with 2A's current-cycle + questionnaire handling) + `insert_discovered` run unchanged. Add a `--skip-hubs` flag + a `hub_items_examined` counter to the run stats.

- [ ] **Step 1:** failing test — a stubbed engine run where one race's hub lane yields a `RawItem` that classifies relevant asserts it is inserted `pending` via the existing path; and `--skip-hubs` skips the lane. Mirror existing `test_poll_discovery.py` / `test_discovery_engine.py` harness (mocks the provider + DB).
- [ ] **Step 2:** run, confirm fail. **Step 3:** implement the lane (guard it per-item like the other lanes — a hub failure never aborts the run; wrap in the engine's `process_safe`). **Step 4:** run target tests + the FULL suite green. **Step 5:** commit.

---

## Self-review
- **Spec coverage (Part 1, Phase 1):** hub registry table + seed → Task 1; per-race resolution → Task 2; `scoped_search` (one bounded search/hub, per-candidate-capable via query building) → Task 3; disposition via the existing classify+insert (Plan-2A does current-cycle+fill verification) → Task 4; feed hubs as outlets → Task 1 seed; VOTE411 pointer-only → Task 3 skip. Local-type aggressiveness + lane-1 glance remain open (later).
- **Placeholder scan:** none — reusable pieces are named with file:symbol; the migration number is chosen via the guard script (a process step, not a placeholder).
- **Type consistency:** `RawItem(via="hub")` matches `src/discovery/models.py::RawItem`; `hubs_for_race` output feeds `raw_items_for_race` feeds `process()`; `tavily_search` shape (`title/url/content`) matches the spike's proven call. Depends on 2A's `questionnaire` kind + current-cycle check being present on the branch.
