# Event → Entity Model — Design

Stress-tested 2026-06-15 via grill-with-docs session. Resolves the foundational
question of how `meetings.meetings` ties into the EV entity graph, unblocking
the event-kinds migration and the curation web app work that follows.

This is a **follow-on to** `2026-06-13-event-kinds-and-titling-design.md`.
That spec (title, event_kind, city nullable) ships first; the entity FKs
described here ship in a second migration.

---

## The problem

Events in prod already span multiple kinds — "California Governor Debate",
"LA Mayoral Debate", "LWV Candidate Forum" — published with council-shaped
typing. `meetings.meetings` only carries `city TEXT` and `body_slug TEXT`.
Neither is the right anchor for electoral events, and `body_slug` is a loose
text reference rather than a true FK.

A council meeting should link to the deliberative body (Chamber). A governor
debate should link to the race being contested (Race). A news clip may link
to either or neither. The model needs to support all three without forcing
the council case through the race model or vice versa.

---

## Resolved decisions

### 1. Chamber is the anchor for deliberative events — not Government

`essentials.chambers` (not `essentials.governments`) is the right FK target
for council and school board events. Reasons:
- Chamber carries the roster (what `body_slug` already encoded)
- Chamber distinguishes "LA City Council" from "LA County Board of Supervisors"
  from "LAUSD Board" even when all are geographically "Los Angeles"
- Government is always reachable one FK step up from Chamber

### 2. Race is the anchor for electoral events

`essentials.races` is the right FK target for debates and forums. A race
links to an election (date, type, state) and to an office, giving full
electoral context. For a "California Governor Debate", `race_id` alone
implies CA, the General election, and the Governor office — nothing else
is needed.

### 3. Two nullable FKs — not polymorphic, not an association table

```sql
-- Migration: event entity FKs (second migration, after event-kinds)
ALTER TABLE meetings.meetings
  ADD COLUMN chamber_id UUID REFERENCES essentials.chambers(id),
  ADD COLUMN race_id    UUID REFERENCES essentials.races(id);
```

The two FKs are **mutually exclusive in intent** — an event anchors to a
chamber OR a race, not both. Enforced at the application layer (pipeline +
ev-accounts write routes), not as a DB CHECK constraint (see §4 below).

Rejected alternatives:
- **Polymorphic `(entity_kind TEXT, entity_id UUID)`**: no FK constraint
  possible; CASE-joins in queries; nothing gained for two known entity types.
- **Association table `meetings.meeting_entities`**: correct for M:M, but
  M:M is not needed at the event level yet (see §Deferred).

### 4. Application-layer enforcement, not DB CHECK

`event_kind` drives which FK is populated:

| event_kind         | chamber_id  | race_id     |
|--------------------|-------------|-------------|
| council            | required    | null        |
| school_board       | required    | null        |
| debate             | null        | required    |
| forum              | null        | required    |
| news_clip          | optional    | optional    |
| community_meeting  | optional    | optional    |
| other              | optional    | optional    |

"Required" is enforced in code (pipeline validation + ev-accounts write-route
zod schema), not by a DB constraint. Rationale: a hard DB constraint would
block the migration until every existing row is backfilled; some events may
not have their chamber/race seeded in essentials yet; the controlled-set
pattern already used for `event_kind`, `meeting_type`, and `status` is
the right precedent.

### 5. `body_slug` is deprecated — replaced by `chamber_id`

`meetings.meetings.body_slug TEXT` was a loose text reference to
`essentials.chambers.slug`. Once `chamber_id` is backfilled, `body_slug`
is fully redundant and should be dropped.

**Migration path:**
1. Add `chamber_id` column (nullable).
2. Run pre-migration audit: for every distinct `body_slug` in prod, verify a
   matching row exists in `essentials.chambers` (by slug). Log any gaps.
3. Backfill: `UPDATE meetings.meetings SET chamber_id = c.id FROM essentials.chambers c WHERE c.slug = meetings.meetings.body_slug`.
4. After confirming coverage, drop `body_slug`.

Rows for bodies not yet seeded in essentials will have `chamber_id = NULL`
after backfill. That is acceptable — they are unlinked, not broken.

The ev-accounts roster API call (`/api/essentials/bodies/{slug}/roster`) still
uses slug as the URL parameter. The service layer derives the slug from the
`essentials.chambers` row via the `chamber_id` FK when needed. No API change
required.

### 6. `essentials.issues` is a dead table

`essentials.issues` exists in the Phase 34 RLS migration but has zero
INSERT, UPDATE, or SELECT usage anywhere in the codebase. It is a relic
of an earlier design that was superseded by `inform.compass_topics`.

**`inform.compass_topics` is the canonical topic spine** across the EV
ecosystem. All topic references — in meetings, quotes, politician stances,
and the Compass calibration flow — use `topic_key` from `compass_topics`.
Do not use `essentials.issues` for anything.

### 7. No `government_id` FK needed

News clips and other non-governmental events do not need a
`government_id UUID REFERENCES essentials.governments(id)`. The
optional `chamber_id` handles the case where a clip is about a specific
body's action. For statewide clips, `city` (already nullable after the
event-kinds migration) plus the `race_id` chain (race → election → state)
provides sufficient context. A third FK type solves no current problem.

---

## Two-migration sequence

### Migration A — Event kinds (ships first, unblocked today)
Already fully designed in `2026-06-13-event-kinds-and-titling-design.md`:
- `ADD COLUMN title TEXT`
- `ADD COLUMN event_kind TEXT NOT NULL DEFAULT 'council'`
- `ALTER COLUMN city DROP NOT NULL`

Ships independently. No entity FK prerequisite.

### Migration B — Entity FKs (ships after essentials coverage verified)
- `ADD COLUMN chamber_id UUID REFERENCES essentials.chambers(id)`
- `ADD COLUMN race_id UUID REFERENCES essentials.races(id)`
- Backfill `chamber_id` from `body_slug`
- Drop `body_slug` after backfill confirms coverage
- Update `meetingsService` in ev-accounts: add `chamber_id`, `race_id` to
  `Meeting` interface, `MeetingRow`, `mapMeeting`, `MEETING_COLS`
- Update write-route zod schemas: accept optional `chamberId`, `raceId`;
  validate against `event_kind` (council/school_board → require chamberId,
  debate/forum → require raceId)
- Update pipeline `publish.py`: write `chamber_id`, `race_id` on INSERT/UPDATE

---

## Deferred

These are explicitly out of scope for both migrations. They must not be
contradicted by the above.

**Section-level entity links** — the ability to tag a time range within a
recording as tied to a specific chamber or race (e.g. "8:48–23:15 is the
County Recorder section of this LWV forum"). This requires human curation
and belongs in the post-publish curation web app, not the pipeline. Schema
design deferred.

**Multi-race forum M:M** — a candidate forum covering multiple races
simultaneously needs `meetings.meeting_races(meeting_id, race_id)` or
similar. The single `race_id` FK handles the primary race for now. M:M
deferred to the curation web app alongside section-level entity links.

**Non-roster participants** — candidates not yet seeded in essentials,
moderators, journalists, and panelists who have no `politician_slug`. The
pipeline's `politician_id` link on `meetings.speakers` handles known
politicians; a new person type for non-politicians is deferred.

**Post-publish curation web app** — verify/correct AI topic tags, promote
predicted → verified, tag section-level entities, clear the Uncategorized
backlog. All of the above deferred items land here.

**Official agenda ingestion** — unaffected by this design.

**LA council-file linking** — unaffected by this design.

**Meeting splitting / clip offsets** — `clip_start_seconds` / `clip_end_seconds`
on meeting rows, enabling one YouTube video to produce multiple meeting
records. Not needed to resolve the multi-race forum case (that lands in
curation instead).
