# Slice 2 Phase 3 — real local-type hubs + the hub flywheel — design

Date: 2026-09-18
Status: Draft (pending spec review)
Area: Discovery engine (`src/discovery/`, `scripts/poll_discovery.py`) + the local review GUI (`gui/discovery.py`, `gui/app.py`, `gui/templates/discovery.html`). Extends [[source-priority-comparable-questions]] Slice 2, whose Phase 1 (2B hub registry) and Phase 2 (2A classifier value-model + flag-vs-guard) are shipped and live. This is **Phase 3** of the phasing in `docs/superpowers/specs/2026-09-17-slice2-comparable-source-hubs-design.md` (§ "Phasing", items 3 + the two "Open questions").

## Goal

Make the `local_type` `scoped_search` hubs actually deliver, and build the flywheel that grows the hub registry. Concretely:

1. Give each race a **real locality**, so a `local_type` query is a sensible search (e.g. `"Los Angeles" League of Women Voters candidate forum 2026`) — and run `local_type` hubs **only** for races that have a genuine local locality (city/county/school), never for statewide or federal races.
2. **Tune cadence + budget** for the lower-yield, per-search-cost `local_type` hubs.
3. Build the **flywheel**: let a reviewer add a confirmed-good hub to `essentials.source_hubs` from the discovery review GUI, mirroring the Slice-1 outlet-trust flywheel ("Trust outlet" → `added_via='flywheel'`).

## Migration

**None.** All three parts are pure on-the-record code. The `essentials.source_hubs` table and its `added_via='flywheel'` value already exist and are live (ev-accounts migration 1869). The flywheel INSERT deduplicates with a `where not exists` guard because the table has no unique constraint — so no schema change is needed to keep it idempotent.

## The corrected premise (why Part 1 changed)

The Phase-3 task and the comment in [`gui/coverage.py`](../../../gui/coverage.py) both assumed statewide and federal races have **no** office row and so surface `locality = NULL`, and that a non-NULL locality therefore means "local". A read-only probe of the live tracked roster (452 races, status ∈ needs_quotes/quotes_staged/published, election in the future) disproves this:

| `governments.name` (via `races.office_id → offices.chamber_id → chambers.government_id`) | # races | real level |
|---|---:|---|
| `United States Federal Government` | 445 | federal |
| `State of Arizona`, `State of Nevada` | 2 | statewide |
| `NULL` | 4 | statewide (governors, no office chain) |
| `Los Angeles, California, US` | 1 | **local** |

So:
- **NULL does not mean local** — the four NULLs are all governors (statewide).
- **Non-NULL does not mean local** — 445 federal races carry `United States Federal Government`, and two governors carry `State of <X>`.
- The government-name shape is inconsistent even within one level (some governors NULL, some `State of <X>`).

Consequence: today the hub lane passes `locality=race_label` and runs `local_type` queries for **all 445 federal races** with a nonsensical locality string (e.g. `"U.S. Representative District 9 (CA, 2026-11-03)" League of Women Voters candidate forum 2026`). That is wasted Tavily budget and review noise. Eliminating it is Part 1's immediate win. Exactly one tracked race is local today (LA Mayor); the local set grows as county/city/school races enter the roster (coverage-follows-content).

## Part 1 — real per-race locality (gate + string)

Two cheap, independent signals; a race gets `local_type` hubs only when **both** agree (belt-and-suspenders against a mislabeled government row):

1. **Level** — `race_level(position_name)` (the Slice-1 regex classifier). `federal`/`state` ⇒ no `local_type`. `county`/`local`/`school` ⇒ eligible. On today's roster this correctly yields federal=445, state=6 (all governors, matched on "governor"), local=1 (LA Mayor — no federal/state/school/county keyword, falls through to `local`).
2. **Locality string** — derived from `governments.name`: reject the sentinels (`United States Federal Government`, `State of %`, `NULL`), then strip a trailing `, <State>, US`-style geography tail. `Los Angeles, California, US` → `Los Angeles`. A county row like `Brown County, Indiana, US` → `Brown County`.

The race gets `local_type` hubs only when the level is local **and** a clean non-empty locality string exists.

### Components / changes

- **`src/race_level.py` (new, shared).** Move `race_level` + `LEVEL_ORDER` here (currently in `gui/coverage.py`). Reason: the engine (`src/discovery/`) must not import from `gui/`, but both need the classifier. `gui/coverage.py` imports from the new module — behaviour unchanged. This is the only Slice-1 code touched, and it is a move, not a rewrite.
- **`src/discovery/locality.py` (new) — pure function** `local_query_locality(position_name, government_name) -> str | None`. Returns the clean locality string when the race is local and a place is derivable, else `None`. No DB, fully unit-testable. Encapsulates: the level gate (via `race_level`), the sentinel rejection, and the `, <State>, US` tail-strip. (Kept separate from `hubs.py` so the geography logic has one clear home.)
- **`src/discovery/db.py::fetch_tracked_candidates`** selects `r.position_name` and `governments.name` through the office→chamber→government LEFT JOIN chain; **`TrackedCandidate`** (in `models.py`) gains `position_name: Optional[str]` and `government_name: Optional[str]`. The existing `state` field (via `races → elections`) is unchanged.
- **`src/discovery/hubs.py::hubs_for_race`** gains a `locality: "str | None"` parameter (default `None`). `local_type` hubs are included only when `locality` is truthy. `global` and `state` hubs are unaffected. The function stays pure.
- **`src/discovery/engine.py`** (hub phase): compute `locality = local_query_locality(cands[0].position_name, cands[0].government_name)` once per race; pass it to both `hubs_for_race(..., locality=locality)` (to gate `local_type`) and `hub_raw_items_fn(..., locality=locality)` (as the interpolation string). Federal/state races get `locality=None`: their `global` + matching-`state` hubs still run on candidate names, and `local_type` hubs are excluded.

### Data flow (per race, hub phase)

```
cands[0].position_name, cands[0].government_name
        │
        ▼
local_query_locality() ──► locality  (str | None)
        │                      │
        ├─ hubs_for_race(all_hubs, state=cands[0].state, locality=locality)
        │        → global ∪ matching-state ∪ (local_type IFF locality)   [scoped_search only]
        │
        └─ raw_items_for_race(applicable, candidates=…, locality=locality, year=…, budget=…)
                 → RawItems (via="hub") → existing classify + insert pipeline
```

## Part 2 — cadence + budget (rank-first, no migration)

Decision (approved): **rank + budget, no migration.** The lane keeps piggybacking on the existing race-sweep cadence (`sweep_due` against the pre-run `discovery_race_state` snapshot — unchanged). Two changes make the spend land on the best hubs:

1. **Value-rank the applicable hubs before spending the budget.** `hubs_for_race` returns hubs ordered by value so the per-race `DISCOVERY_HUB_BUDGET` (=6) is consumed best-first:
   - **Primary key — concrete `domain` present, first.** Domain-scoped hubs (Ballotpedia, Utah Debate Commission, Voter's Edge, Vote Smart, Oregon Voters' Pamphlet) run a deterministic `site:<domain>` search and are the high-yield known venues. `local_type` hubs have `domain = NULL` + a `query_template` and are speculative; they sort **last**.
   - **Secondary key — a light `kind` order** as a stable tiebreak (debate, forum, guide, pamphlet, questionnaire), reflecting the value model's "comparable multi-candidate sources first". Kept minor; domain-presence is the real lever.
   - **Tertiary — `name`**, so the order is deterministic (stable across runs/tests).
2. **A sub-cap on speculative `local_type` searches per race.** New config constant `DISCOVERY_HUB_LOCAL_TYPE_BUDGET` (default **2**). Enforced inside `raw_items_for_race`: it already counts `searches_done` against the overall `budget`; add a parallel counter that stops issuing `local_type` (query-template / no-domain) searches once the sub-cap is hit, while domain hubs keep going up to the overall budget. This directly answers the spec's open question ("how aggressively to run the scoped_search hubs, esp. local-type — cadence + a per-race hub budget") without new persistent state.

No per-hub poll state, no new table/column, so no migration. (The alternative — a separate slower `local_type` cadence needing per-`(race,hub)` state — was considered and rejected as not worth an ev-accounts migration, especially now that Part 1's gate already shrinks the `local_type` set to genuinely-local races.)

## Part 3 — the hub flywheel (GUI, no migration)

Decision (approved): **a small inline form**, mirroring the outlet-trust flywheel's placement but respecting that a hub carries `scope`/`kind`.

- **Where.** On a **web-sourced** pending row (non-YouTube; the URL has a real registrable domain), a compact `<form>` — an "Add as hub" control alongside the existing per-row actions. YouTube rows keep the existing "Watch this channel" / "Trust outlet" flywheel; they are not hubs.
- **Pre-filled fields (reviewer can adjust before submit):**
  - `name` — the outlet/channel name if present, else the domain.
  - `domain` — the registrable domain parsed from the row URL (e.g. `azpm.org`).
  - `scope` — default **`state`** using the row's race state (derived from `race_id`); choices offered: `global`, `state`.
  - `kind` — default from `event_kind_guess` mapped to the hub kind set; choices: `debate`, `forum`, `questionnaire`, `guide`, `pamphlet`.
  - `tos_bucket` — default `other` (a hidden/fixed field for v1).
  - `poll_method` — fixed `scoped_search`. `added_via` — fixed `flywheel`.
- **Scope boundary (deliberate).** The flywheel adds **domain-scoped** hubs only (`global` or `state`). Generic `local_type` template hubs are hand-curated — they are cross-race patterns (`"<locality>" … <year>`), not something a single find should mint. This keeps the one-glance form simple and avoids a reviewer inventing a `query_template`.
- **Server.** New helper `gui/discovery.py::add_hub_from_row(row, *, scope, kind)` — best-effort (any DB failure returns `(False, msg)`, never raises), mirroring `watch_channel`/`set_outlet_trusted`. It:
  - parses the registrable domain from `row.url` (reuse an existing helper if present, else a small pure parser);
  - looks up the race's 2-letter state from `race_id` when `scope='state'`;
  - `INSERT ... SELECT ... WHERE NOT EXISTS` on `(domain, scope, coalesce(state,''))` so re-adding the same hub is a no-op (the table has no unique constraint);
  - sets `active=true`, `added_via='flywheel'`.
- **Route.** `POST /discovery/{row_id}/add-hub` (Form: `scope`, `kind`, and the carry-through `state`/`show` used by the other discovery routes), then `_discovery_redirect` with a flash — same pattern as `discovery_trust`.
- **Template.** Add the inline form to `gui/templates/discovery.html`, gated to web rows (a `DiscoveredRow` property, e.g. `is_web` / `hub_domain`, decides visibility and supplies the pre-fill). Mirrors the existing "Trust outlet" `<form>` block.

## Testing (no-local-DB harness; `.venv/bin/python -m pytest`)

Test-first, following the established discovery test style (pure functions + injected fakes + `live_db`-gated integration).

- **Pure:** `local_query_locality` over the observed cases — federal sentinel → `None`; `State of X` → `None`; `NULL` → `None`; `Los Angeles, California, US` → `Los Angeles`; a county `… , Indiana, US` → `Brown County`; a bare place with no tail → unchanged.
- **Pure:** `race_level` still classifies correctly after the module move (re-home the existing coverage tests or add a thin import test so both call sites are covered).
- **Pure:** `hubs_for_race` — `local_type` excluded when `locality=None`, included when set; `global`/`state` selection unchanged; **value-rank order** asserted (domain hubs before `local_type`; deterministic).
- **Fake tavily:** `raw_items_for_race` — the `local_type` sub-cap stops speculative searches at `DISCOVERY_HUB_LOCAL_TYPE_BUDGET` while domain hubs continue to the overall budget; count `tavily_search` calls by hub type.
- **Fake cursor:** `fetch_tracked_candidates` maps the new columns onto `TrackedCandidate`; `add_hub_from_row` issues the guarded INSERT with the right column values and dedupe predicate.
- **Engine (injected fakes):** a federal race issues **zero** `local_type` hub searches; the LA-Mayor-shaped local race issues a `local_type` search whose query carries the clean locality (`Los Angeles`, not the race label). Reuse the existing engine fake harness.
- **Optional `live_db`-gated:** load real hubs + one local and one federal tracked race, assert the gate end-to-end (skipped without `DATABASE_URL`).

## Small cleanup (code I'm working in)

Correct the factually-wrong comment at `gui/coverage.py:59-63` (it claims statewide/federal → `NULL`). The coverage view uses `locality` only for cosmetic grouping, so behaviour is unaffected — but I now depend on this exact chain and the comment must not mislead the next reader.

## Decided 2026-09-18 (the two Phase-3 design questions)

- **Cadence/budget:** rank-first + a `local_type` sub-cap, **no migration** (over a separate per-hub cadence needing new state). See Part 2.
- **Flywheel UX:** a **small inline form** (pre-filled domain/scope/kind, reviewer adjusts) (over a one-click, defaults-only button). Domain-scoped hubs only. See Part 3.

## Out of scope (this phase)

- The recall/precision regression eval promoted into the repo (spec Phasing item 4) — separate.
- Wiring `poll_method='feed'` hubs' real feed URLs into `source_outlets` (spec Part-1 §D follow-up).
- `local_type`-hub creation from the flywheel (kept hand-curated).
- Any change to the classifier value model or the flag-vs-guard logic (Phase 2, shipped).

## Open questions / watch-items

- `governments.name` tail-strip is tuned to the observed `Place, State, US` shape and the two sentinels; a genuinely different government-name format (e.g. a state named bare `California` rather than `State of California`) would slip the string gate — the `race_level` level gate is the backstop, and a new format is easy to add to `local_query_locality`.
- Value-rank uses `domain`-presence as the yield proxy; if a domain hub proves low-yield in practice, the `kind` tiebreak or an explicit per-hub priority column (migration) is the next lever.
- With one local race in the roster today, `local_type` end-to-end yield is effectively unmeasured until more local races enter — the gate's value now is correctness (stop the 445-race waste), not volume.
