# Multi-Race Events (`meetings.event_races`) — Design

**Date:** 2026-06-27
**Status:** Approved for planning
**Repos:** on-the-record (pipeline / `run_local.py`, `src/publish.py`) **and**
ev-accounts (`backend/` API + schema migrations)
**Context:** Sub-project **E** of the speaker-linking effort, surfaced during the
first live `--bulk-relink-apply` run (sub-project C). Candidate **forums** cover
**multiple races** (e.g. "Clerk + Prosecutor", "House-61 + County Commissioner"),
but `meetings.meetings` has a single `race_id` column, so such forums can't be
represented or published correctly. See memory `speaker-politician-id-rekey`.

## Problem

A meeting can belong to more than one race. The publish gate requires a single
`race_id` for `event_kind in ('debate','forum')`, and `meetings.meetings.race_id`
is a single FK. Consequences observed live:

- The **House-61 + County Commissioner forum** can't publish: its linked
  candidates span two races, so a single `race_id` can't be chosen (the
  bulk-relink resolver returns "ambiguous → none" and publish is blocked).
- The **Clerk + Prosecutor forum** published tagged with only the Clerk race
  (an arbitrary single pick), mis-associating it.
- Race association is **manually set** on each debate/forum transcript today —
  fragile and easy to get wrong (it was hand-set three times during the C run).

## Goal

Model a meeting's races as a **set**, derived automatically from its linked
candidates, stored in a `meetings.event_races` join table that is the single
source of truth — replacing the single `meetings.meetings.race_id` column. A
meeting then publishes under **all** the races its linked candidates belong to,
and both the API and (future) race pages can read the association in both
directions.

## Decisions (resolved in brainstorming)

- **Join table is the source of truth; the single `race_id` column is dropped.**
  Not a transitional dual-write — full migration to join-only. This is a
  destructive, **cross-repo coordinated** change (see Sequencing).
- **Races are derived from the meeting's linked candidates, reconciled on every
  publish.** A meeting's races = the union of races its linked politicians
  belong to (via `essentials.race_candidates`). No manual `race_id` juggling;
  always consistent with who's linked; self-corrects as more candidates are
  linked. The transcript's `Meeting.race_id` field becomes vestigial (publish
  ignores it).
- **Read model is bidirectional.** The join table supports both
  race → meetings (`WHERE race_id = $1`, needs an index on `race_id`) and
  meeting → races (`WHERE meeting_id = $1`, served by the PK).
- **Validation: `debate`/`forum` require ≥1 derived race** (replacing "require a
  `race_id`"). Checked after derivation; zero races (no linked candidates yet)
  blocks publish with a clear, recoverable message.
- **Admin manual race-setting is removed** (consequence of pure-derive). The
  ev-accounts admin `POST/PATCH /api/meetings` endpoints stop accepting a
  `raceId`; races are owned by pipeline derivation. (Flagged for review — this
  is a behavior change on those endpoints.)

## Architecture & decomposition

The `meetings.event_races` join table is the integration seam. Work splits into
two repo-scoped sub-plans that share this contract; this single design governs
both. Each repo gets its own implementation plan.

### Shared contract — schema (owned by ev-accounts migrations)

```sql
-- ev-accounts/backend/migrations/<next>_event_races.sql
create table meetings.event_races (
  meeting_id uuid not null references meetings.meetings(id) on delete cascade,
  race_id    uuid not null references essentials.races(id),  -- preserve the FK 579 had
  primary key (meeting_id, race_id)              -- serves meeting → races
);
create index event_races_race_id_idx on meetings.event_races (race_id);  -- race → meetings

-- backfill existing single-race associations
insert into meetings.event_races (meeting_id, race_id)
select id, race_id from meetings.meetings where race_id is not null
on conflict do nothing;

-- FINAL, E2-gated step (separate migration, applied only after E2 code is live):
-- alter table meetings.meetings drop column race_id;
```

The schema lives in ev-accounts because that repo owns `meetings.*` DDL
(migration `579_event_entity_fks.sql` created the current `race_id` column +
index, with a FK to `essentials.races`). `event_races.race_id` preserves that
FK to `essentials.races(id)` — the backfilled data already satisfies it, and it
keeps referential integrity the single column had. (This differs from
`meetings.speakers.politician_id`, which intentionally carries essentials ids
without a FK; races keep theirs because 579 established it.)

### E1 — on-the-record pipeline (this repo)

**1. `resolve_races_for_politicians(cur, politician_ids) -> list[str]`** (new, in
`src/publish.py`, replacing the single-race `resolve_race_id_for_politicians`):
distinct `race_id`s for the given politician ids, **no `LIMIT`, no "exactly one"
gate**, returning all. Uses the corrected `ANY(%s::uuid[])` cast (the bug fixed
in PR #30). Returns `[]` when none.

**2. Race derivation + reconcile in `publish.py`.** After speakers are upserted
(so each mapping's `politician_id` is known in the DB for this meeting), within
the same transaction:
- collect the meeting's linked politician ids (from the speaker mappings),
- `races = resolve_races_for_politicians(cur, ids)`,
- reconcile: `DELETE FROM meetings.event_races WHERE meeting_id = %s`, then
  `INSERT` the current `races` set. Idempotent across re-publishes.
- stop writing `meetings.meetings.race_id` (it no longer exists post-drop;
  pre-drop it is simply left unwritten).

**3. Validation change.** `src/event_entities.py::validate_event_entities` no
longer requires `race_id` for `debate`/`forum` (and drops the `race_id` side of
the chamber/race mutual-exclusion). The new rule — **`debate`/`forum` require ≥1
derived race** — is enforced in `publish.py` *after* derivation: if the
reconciled race set is empty for a race-bearing event, raise a clear
`RuntimeError` (aborting the transaction), message naming the meeting and that
no linked candidates resolved to a race yet (recoverable: link candidates, then
re-publish). Chamber rules for `council`/`school_board` are unchanged.

**4. Bulk-relink simplification.** Remove `_resolve_debate_race_id` and the
`event_kind == "debate"` race-resolution block from `_bulk_relink_apply`
(`run_local.py`); publish now owns race derivation for **all** race-bearing
events (debate *and* forum), via every publish path (`--publish-meeting`,
`republish_all.sh`, bulk-relink apply). The apply loop just relinks → folds →
publishes.

**5. `Meeting.race_id`** (`src/models.py`) becomes vestigial — publish ignores
it. Keep the field for transcript back-compat (removing it would break parsing
of existing transcripts that carry it); note it as deprecated.

### E2 — ev-accounts (`backend/`, separate repo & plan)

Data layer is raw `pg` SQL in `backend/src/lib/meetingsService.ts`; tests are
vitest with a mocked `pool.query`. Changes:

- **Migration** (`backend/migrations/`): the `event_races` create + backfill
  above; the `DROP COLUMN race_id` as a **later, gated** migration.
- **`meetingsService.ts`:** remove `race_id` from `MEETING_COLS` (line ~315);
  return `raceIds: string[]` on the `Meeting` (interface line ~45) and `MeetingRow`
  (line ~153) / `mapMeeting` (line ~230) via a correlated subquery or LEFT JOIN +
  aggregate against `event_races` in `getMeetings` (~340) and `getMeetingById`
  (~354); update `getMeetingEntityState` (~612). `createMeeting` (~532) /
  `updateMeeting` (~576): drop `race_id` from INSERT/SET (admin no longer sets
  races — see Decisions).
- **New endpoint — race → meetings:** `GET /api/races/:raceId/meetings` (or
  equivalent under the existing meetings/readrank routing) querying
  `event_races` by `race_id`. Net-new (no such endpoint exists today).
- **`eventEntityRules.ts`:** `EventEntityState.raceId` → reflect the set model;
  the chamber/race mutual-exclusion and the debate/forum requirement mirror the
  pipeline's new rules. (ev-accounts admin writes no longer set races, so the
  debate/forum "requires a race" check there becomes advisory or is dropped —
  resolve in the E2 plan.)
- **`routes/meetings.ts`:** Zod schemas (lines ~176, ~226) drop `raceId`.
- **Tests:** `meetingsService.test.ts`, `meetings.test.ts`,
  `eventEntityRules.test.ts` updated for `raceIds` + the new endpoint.

## Data flow

```
bulk-relink apply / --publish-meeting / republish_all.sh
   │  publish_meeting(meeting)
   │    upsert meeting row (chamber validated for council/school_board)
   │    upsert speakers  → politician_ids now in meetings.speakers
   │    ids = linked politician_ids in this meeting
   │    races = resolve_races_for_politicians(cur, ids)   # all distinct races
   │    reconcile meetings.event_races (delete + insert races)
   │    if event_kind in (debate, forum) and not races: raise (block, recoverable)
   ▼
meetings.event_races  ← source of truth (both directions)
   ▲
ev-accounts API: meeting→races (payload raceIds[]), race→meetings (new endpoint)
```

## Migration / deploy sequencing (the coordinated, destructive part)

1. **ev-accounts:** apply the `event_races` create + backfill migration (column
   still present). Non-breaking.
2. **on-the-record (E1):** deploy publish writing `event_races`; stop writing
   `race_id`. (Column now stale but still read by ev-accounts until step 3.)
3. **ev-accounts (E2 code):** deploy reads/writes/endpoint/validation switched to
   `event_races`. Now nothing reads `race_id`.
4. **ev-accounts:** apply the `DROP COLUMN race_id` migration (final).

Steps 2 and 3 are the flag-day pair: between them, single-race meetings still
read correctly from the backfilled join table via E2 only after step 3, so
**E2 (step 3) must precede any reliance on the column being gone**. Order: 1 → 2
→ 3 → 4. A reader hitting `race_id` between 2 and 3 sees stale data, so keep that
window short or deploy 2+3 together.

## Error handling / edge cases

- **Race-bearing meeting with no linked candidates** → 0 derived races → publish
  blocked with a clear message; recover by linking candidates, then re-publish.
- **Re-publish** → reconcile is delete+insert, so the race set always reflects
  current linkage (idempotent; corrects drift).
- **Candidate linked to multiple races** → all are included (the union is the
  point).
- **Moderators / non-candidate speakers** → contribute no races (not in
  `race_candidates`); correct.
- **`council`/`school_board`** → unaffected; still chamber-based, no races.
- **Partial linkage** → races attach incrementally as candidates are linked;
  expected.

## Testing

**E1 (pytest, on-the-record):**
- `resolve_races_for_politicians`: returns all distinct races; `[]` for none;
  SQL casts `::uuid[]` (regression). Mocked cursor.
- publish reconcile: multi-race forum writes the union; re-publish replaces stale
  rows; debate → single row; **zero races for a debate/forum → publish raises**.
  (Integration test with a mocked DB cursor capturing the delete+insert.)
- `validate_event_entities`: council still requires chamber; debate/forum no
  longer require `race_id` at this layer.
- bulk-relink apply: multi-race forum publishes (previously blocked); the
  debate-only special-case is gone (assert it's removed / forums handled).

**E2 (vitest, ev-accounts):**
- `meetingsService`: `getMeetingById`/`getMeetings` return `raceIds[]`; the new
  race→meetings query; `createMeeting`/`updateMeeting` no longer write `race_id`.
- new race→meetings route (supertest).
- `eventEntityRules` updated expectations.

## Out of scope / deferred

- **Race-page UI** (rendering a race's meeting list / a meeting's race badges) —
  this delivers the API + data contract; visual pages are a later web task.
- **Removing `Meeting.race_id`** from the transcript model — kept vestigial for
  back-compat; remove in a later cleanup.
- **A manual race override** (the rejected hybrid) — races are purely derived.
- **Backfilling races for meetings whose candidates aren't linked yet** — happens
  naturally as linking proceeds; no special backfill.
