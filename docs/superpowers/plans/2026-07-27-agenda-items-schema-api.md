# Agenda Items Schema + API (ev-accounts) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the `meetings.agenda_items` table, scheduled-meeting support, and public API endpoints (upcoming meetings, agenda items per meeting, item by id) to ev-accounts.

**Architecture:** One migration (`backend/migrations/1476_*.sql`, applied ad hoc to the live Supabase DB via direct connection) adds the table, a `starts_at timestamptz` column, a votes→items FK, and indexes. A new focused service (`agendaItemsService.ts`) plus small additions to `meetingsService.ts` serve three new public (optionalAuth) endpoints. `getMeetings` starts defaulting to `status='published'` so scheduled rows don't leak into existing consumers.

**Tech Stack:** Express 4 + TypeScript ESM, `pg` Pool, Vitest + supertest. Repo: `/Users/chrisandrews/Documents/GitHub/ev-accounts` (work happens THERE, not in on-the-record). Spec: `on-the-record/docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md`.

**Conventions you must follow (from repo recon 2026-07-27):**
- All queries via `import { pool } from './db.js'`, fully schema-qualified (`meetings.agenda_items`). The meetings schema is NOT in PostgREST's exposed list — direct `pool.query()` only.
- Response DTOs built from EXPLICIT field whitelists via hand-written `mapX()`; never spread rows. camelCase JSON out, snake_case columns in.
- `Number()` every `numeric`/`bigint` (pg returns strings).
- Route handlers: `Promise<void>` return type, bare `return` after each response, `UUID_REGEX` check before any DB call, errors `{ code, message }` with 422/404/500 (never 400), `console.error('[METHOD /path] error:', err)`.
- Tests: `vi.hoisted` + `vi.mock` before importing the module under test; service tests mock the pool, route tests mock the service + auth middleware and drive supertest.
- Work on a branch off master: `feat/agenda-items-schema-api`.

---

### Task 1: Migration 1476

**Files:**
- Create: `backend/migrations/1476_agenda_items_and_scheduled_meetings.sql`

- [ ] **Step 1: Verify 1476 is still the next free number**

Run: `ls /Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/ | sort -n | tail -3`
Expected: highest existing number is 1475. If not, use the next free integer everywhere this plan says 1476 (filename, apply script, commit messages).

- [ ] **Step 2: Create branch**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts && git checkout master && git pull && git checkout -b feat/agenda-items-schema-api
```

- [ ] **Step 3: Write the migration**

Create `backend/migrations/1476_agenda_items_and_scheduled_meetings.sql`:

```sql
-- 1476: agenda_items table + scheduled-meeting support + votes->items FK
--
-- Part of the Bloomington item-centric civic coverage feature (spec:
-- on-the-record/docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md).
-- The agenda ITEM is the product atom: published days before a meeting
-- (status='upcoming', plain-language interpretation) and enriched after video
-- processing (status='happened', outcome + segment bounds + votes).
--
-- Design notes:
-- * meetings.meetings.status gains a new VALUE ('scheduled') — no DDL needed
--   (status is unconstrained text, default 'processing'). The API-side guard is
--   getMeetings() defaulting to status='published' (this migration's companion
--   code change), because every pre-existing read path assumes all rows are
--   published.
-- * starts_at: meetings.date was deliberately downgraded to DATE (migration 364);
--   scheduled meetings need time-of-day. timestamptz; the writer (on-the-record
--   pipeline) resolves the body's IANA zone. date stays NOT NULL — writers derive
--   it from starts_at in the body's local zone.
-- * kind/status/outcome are CHECK-constrained (closed vocabularies from the spec).
-- * continued_from_item_id is the matter-tracking seed (spec: one lifecycle edge,
--   no matter entity). ON DELETE SET NULL: losing a lineage edge must not block
--   deleting an old item row.
-- * votes.agenda_item_id mirrors the existing la_council_votes.agenda_item_id
--   precedent (migration 167): nullable because procedural votes have no item.
-- * RLS: enabled + public-read policy, matching phase-34 meetings pattern
--   (supabase/migrations/20260319000045). The API's ev_api role is BYPASSRLS
--   either way; the policy keeps direct-Supabase reads consistent with the
--   original meetings tables.
-- * Sole row writer is the on-the-record pipeline (delete-then-insert per
--   meeting, like meetings.votes). ev-accounts only reads.

BEGIN;

-- 1) scheduled-meeting support
ALTER TABLE meetings.meetings ADD COLUMN IF NOT EXISTS starts_at timestamptz;
COMMENT ON COLUMN meetings.meetings.starts_at IS
  'Scheduled start time (with zone). Set for status=scheduled rows published from agendas; null for legacy video-only rows.';

CREATE INDEX IF NOT EXISTS idx_meetings_status_date
  ON meetings.meetings (status, date);

-- 2) agenda_items
CREATE TABLE IF NOT EXISTS meetings.agenda_items (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  meeting_id uuid NOT NULL REFERENCES meetings.meetings(id) ON DELETE CASCADE,
  position integer NOT NULL,
  item_number text NOT NULL,
  title_raw text NOT NULL,
  kind text NOT NULL CHECK (kind IN
    ('ordinance','resolution','appointment','proclamation','report',
     'public-comment','minutes','procedural','other')),
  legislation_ref text,
  summary_plain text,
  decision_plain text,
  stage text,
  public_comment boolean NOT NULL DEFAULT false,
  public_comment_note text,
  status text NOT NULL DEFAULT 'upcoming' CHECK (status IN ('upcoming','happened')),
  outcome text CHECK (outcome IN ('passed','failed','continued','pulled','no-action')),
  segment_start_seconds numeric,
  segment_end_seconds numeric,
  continued_from_item_id uuid REFERENCES meetings.agenda_items(id) ON DELETE SET NULL,
  source_url text NOT NULL,
  created_at timestamptz DEFAULT now(),
  updated_at timestamptz DEFAULT now(),
  CONSTRAINT agenda_items_meeting_position_unique UNIQUE (meeting_id, position)
);

COMMENT ON TABLE meetings.agenda_items IS
  'One row per agenda item; the citizen-facing atom. Written only by the on-the-record pipeline (delete-then-insert per meeting).';
COMMENT ON COLUMN meetings.agenda_items.title_raw IS
  'Verbatim agenda title (government-speak), preserved for provenance.';
COMMENT ON COLUMN meetings.agenda_items.legislation_ref IS
  'e.g. "Ordinance 2026-16" — extracted from the agenda, never invented; joins to the city legislation pages.';
COMMENT ON COLUMN meetings.agenda_items.stage IS
  'Procedural stage from adapter-encoded body rules (e.g. "First reading"), never LLM-inferred.';
COMMENT ON COLUMN meetings.agenda_items.continued_from_item_id IS
  'Matter-tracking seed: points at the same matter''s item row on an earlier agenda.';
COMMENT ON COLUMN meetings.agenda_items.segment_start_seconds IS
  'Video-absolute seconds; null until the post-meeting alignment pass.';

CREATE INDEX IF NOT EXISTS idx_agenda_items_meeting_id
  ON meetings.agenda_items (meeting_id);

-- 3) votes -> items
ALTER TABLE meetings.votes
  ADD COLUMN IF NOT EXISTS agenda_item_id uuid REFERENCES meetings.agenda_items(id);
COMMENT ON COLUMN meetings.votes.agenda_item_id IS
  'Item the vote decided; null for procedural votes or pre-agenda-items rows.';
CREATE INDEX IF NOT EXISTS idx_meetings_votes_agenda_item_id
  ON meetings.votes (agenda_item_id) WHERE agenda_item_id IS NOT NULL;

-- 4) RLS (phase-34 pattern: on + public read)
ALTER TABLE meetings.agenda_items ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS "agenda_items_public_read" ON meetings.agenda_items;
CREATE POLICY "agenda_items_public_read" ON meetings.agenda_items
  FOR SELECT TO anon, authenticated USING (true);

-- 5) post-verify gate
DO $$
DECLARE
  n_cols int;
  n_fk int;
BEGIN
  SELECT count(*) INTO n_cols FROM information_schema.columns
   WHERE table_schema = 'meetings' AND table_name = 'agenda_items';
  IF n_cols <> 20 THEN
    RAISE EXCEPTION 'agenda_items has % columns, expected 20', n_cols;
  END IF;

  SELECT count(*) INTO n_fk FROM pg_constraint
   WHERE conrelid = 'meetings.votes'::regclass
     AND contype = 'f'
     AND confrelid = 'meetings.agenda_items'::regclass;
  IF n_fk <> 1 THEN
    RAISE EXCEPTION 'votes.agenda_item_id FK missing';
  END IF;

  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
    WHERE table_schema = 'meetings' AND table_name = 'meetings'
      AND column_name = 'starts_at') THEN
    RAISE EXCEPTION 'meetings.starts_at missing';
  END IF;
END $$;

COMMIT;
```

- [ ] **Step 4: Run the migration-number guard**

Run: `npm run check:migrations --prefix /Users/chrisandrews/Documents/GitHub/ev-accounts/backend`
Expected: passes (no duplicate numbers, above FLOOR).

- [ ] **Step 5: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts && git add backend/migrations/1476_agenda_items_and_scheduled_meetings.sql && git commit -m "feat(db): agenda_items table + scheduled meetings + votes->items FK (1476)"
```

**NOTE — applying to prod is Task 6 (last), after all code + tests pass, per the repo's deploy-ordering rule (migration before API deploy, code merged after apply).**

---

### Task 2: agendaItemsService.ts

**Files:**
- Create: `backend/src/lib/agendaItemsService.ts`
- Test: `backend/src/lib/agendaItemsService.test.ts`

- [ ] **Step 1: Write the failing tests**

Create `backend/src/lib/agendaItemsService.test.ts`:

```ts
import { describe, it, expect, beforeEach, vi } from 'vitest';

const { mockQuery } = vi.hoisted(() => ({ mockQuery: vi.fn() }));
vi.mock('./db.js', () => ({ pool: { query: mockQuery } }));

import {
  getAgendaItemsByMeetingId,
  getAgendaItemById,
} from './agendaItemsService.js';

const MEETING_ID = '11111111-1111-4111-8111-111111111111';
const ITEM_ID = '22222222-2222-4222-8222-222222222222';

// String-typed numerics on purpose: pg returns numeric as string.
const baseItemRow = {
  id: ITEM_ID,
  meeting_id: MEETING_ID,
  position: '6',
  item_number: '6A',
  title_raw:
    'Ordinance 2026-16 – To Amend an Ordinance Fixing the Salaries of Officers and Employees',
  kind: 'ordinance',
  legislation_ref: 'Ordinance 2026-16',
  summary_plain: 'Adjusts police and fire salaries.',
  decision_plain: 'First of two votes needed to change the salary ordinance.',
  stage: 'First reading',
  public_comment: false,
  public_comment_note: null,
  status: 'upcoming',
  outcome: null,
  segment_start_seconds: null,
  segment_end_seconds: null,
  continued_from_item_id: null,
  source_url: 'https://bloomington.in.gov/onboard/meetingFiles/17202/download',
};

beforeEach(() => {
  mockQuery.mockReset();
});

describe('getAgendaItemsByMeetingId', () => {
  it('maps rows to camelCase DTOs ordered by position', async () => {
    mockQuery.mockResolvedValueOnce({ rows: [baseItemRow] });
    const items = await getAgendaItemsByMeetingId(MEETING_ID);
    expect(mockQuery).toHaveBeenCalledTimes(1);
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toContain('FROM meetings.agenda_items');
    expect(sql).toContain('ORDER BY position ASC');
    expect(params).toEqual([MEETING_ID]);
    expect(items).toHaveLength(1);
    expect(items[0]).toEqual({
      id: ITEM_ID,
      meetingId: MEETING_ID,
      position: 6,
      itemNumber: '6A',
      titleRaw: baseItemRow.title_raw,
      kind: 'ordinance',
      legislationRef: 'Ordinance 2026-16',
      summaryPlain: 'Adjusts police and fire salaries.',
      decisionPlain:
        'First of two votes needed to change the salary ordinance.',
      stage: 'First reading',
      publicComment: false,
      publicCommentNote: null,
      status: 'upcoming',
      outcome: null,
      segmentStartSeconds: null,
      segmentEndSeconds: null,
      continuedFromItemId: null,
      sourceUrl: baseItemRow.source_url,
    });
  });

  it('coerces numeric segment bounds to numbers', async () => {
    mockQuery.mockResolvedValueOnce({
      rows: [
        {
          ...baseItemRow,
          status: 'happened',
          outcome: 'passed',
          segment_start_seconds: '1234.5',
          segment_end_seconds: '2000',
        },
      ],
    });
    const items = await getAgendaItemsByMeetingId(MEETING_ID);
    expect(items[0].segmentStartSeconds).toBe(1234.5);
    expect(items[0].segmentEndSeconds).toBe(2000);
    expect(items[0].outcome).toBe('passed');
  });

  it('returns [] when there are no items', async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] });
    expect(await getAgendaItemsByMeetingId(MEETING_ID)).toEqual([]);
  });
});

describe('getAgendaItemById', () => {
  it('returns the item with embedded meeting context', async () => {
    mockQuery.mockResolvedValueOnce({
      rows: [
        {
          ...baseItemRow,
          m_id: MEETING_ID,
          m_title: 'Common Council Regular Session',
          m_date: '2026-07-29',
          m_city: 'Bloomington',
          m_status: 'scheduled',
          m_starts_at: '2026-07-29T18:30:00-04:00',
        },
      ],
    });
    const detail = await getAgendaItemById(ITEM_ID);
    expect(detail?.itemNumber).toBe('6A');
    expect(detail?.meeting).toEqual({
      id: MEETING_ID,
      title: 'Common Council Regular Session',
      date: '2026-07-29',
      city: 'Bloomington',
      status: 'scheduled',
      startsAt: '2026-07-29T18:30:00-04:00',
    });
  });

  it('returns null when not found', async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] });
    expect(await getAgendaItemById(ITEM_ID)).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/agendaItemsService.test.ts` (from `backend/`)
Expected: FAIL — cannot find module `./agendaItemsService.js`.

- [ ] **Step 3: Write the service**

Create `backend/src/lib/agendaItemsService.ts`:

```ts
// Read-only service for meetings.agenda_items — the citizen-facing agenda-item
// atoms published by the on-the-record pipeline (which is the sole writer).
//
// Same rules as meetingsService.ts:
// - meetings schema is NOT PostgREST-exposed: pool.query() only, schema-qualified.
// - DTOs are explicit whitelists (never spread rows); camelCase out.
// - Number() every numeric — pg returns them as strings.
import { pool } from './db.js';

export interface AgendaItem {
  id: string;
  meetingId: string;
  position: number;
  itemNumber: string;
  titleRaw: string;
  kind: string;
  legislationRef: string | null;
  summaryPlain: string | null;
  decisionPlain: string | null;
  stage: string | null;
  publicComment: boolean;
  publicCommentNote: string | null;
  status: 'upcoming' | 'happened';
  outcome: string | null;
  segmentStartSeconds: number | null;
  segmentEndSeconds: number | null;
  continuedFromItemId: string | null;
  sourceUrl: string;
}

export interface AgendaItemDetail extends AgendaItem {
  meeting: {
    id: string;
    title: string | null;
    date: string;
    city: string | null;
    status: string;
    startsAt: string | null;
  };
}

interface AgendaItemRow {
  id: string;
  meeting_id: string;
  position: string | number;
  item_number: string;
  title_raw: string;
  kind: string;
  legislation_ref: string | null;
  summary_plain: string | null;
  decision_plain: string | null;
  stage: string | null;
  public_comment: boolean;
  public_comment_note: string | null;
  status: 'upcoming' | 'happened';
  outcome: string | null;
  segment_start_seconds: string | number | null;
  segment_end_seconds: string | number | null;
  continued_from_item_id: string | null;
  source_url: string;
}

interface AgendaItemDetailRow extends AgendaItemRow {
  m_id: string;
  m_title: string | null;
  m_date: string;
  m_city: string | null;
  m_status: string;
  m_starts_at: string | null;
}

const ITEM_COLS = `id, meeting_id, position, item_number, title_raw, kind,
  legislation_ref, summary_plain, decision_plain, stage,
  public_comment, public_comment_note, status, outcome,
  segment_start_seconds, segment_end_seconds, continued_from_item_id, source_url`;

function mapAgendaItem(row: AgendaItemRow): AgendaItem {
  return {
    id: row.id,
    meetingId: row.meeting_id,
    position: Number(row.position),
    itemNumber: row.item_number,
    titleRaw: row.title_raw,
    kind: row.kind,
    legislationRef: row.legislation_ref ?? null,
    summaryPlain: row.summary_plain ?? null,
    decisionPlain: row.decision_plain ?? null,
    stage: row.stage ?? null,
    publicComment: row.public_comment,
    publicCommentNote: row.public_comment_note ?? null,
    status: row.status,
    outcome: row.outcome ?? null,
    segmentStartSeconds:
      row.segment_start_seconds == null ? null : Number(row.segment_start_seconds),
    segmentEndSeconds:
      row.segment_end_seconds == null ? null : Number(row.segment_end_seconds),
    continuedFromItemId: row.continued_from_item_id ?? null,
    sourceUrl: row.source_url,
  };
}

export async function getAgendaItemsByMeetingId(
  meetingId: string
): Promise<AgendaItem[]> {
  const { rows } = await pool.query<AgendaItemRow>(
    `SELECT ${ITEM_COLS}
     FROM meetings.agenda_items
     WHERE meeting_id = $1
     ORDER BY position ASC`,
    [meetingId]
  );
  return rows.map(mapAgendaItem);
}

export async function getAgendaItemById(
  id: string
): Promise<AgendaItemDetail | null> {
  const { rows } = await pool.query<AgendaItemDetailRow>(
    `SELECT ai.id, ai.meeting_id, ai.position, ai.item_number, ai.title_raw,
            ai.kind, ai.legislation_ref, ai.summary_plain, ai.decision_plain,
            ai.stage, ai.public_comment, ai.public_comment_note, ai.status,
            ai.outcome, ai.segment_start_seconds, ai.segment_end_seconds,
            ai.continued_from_item_id, ai.source_url,
            m.id AS m_id, m.title AS m_title, m.date::text AS m_date,
            m.city AS m_city, m.status AS m_status, m.starts_at AS m_starts_at
     FROM meetings.agenda_items ai
     JOIN meetings.meetings m ON m.id = ai.meeting_id
     WHERE ai.id = $1`,
    [id]
  );
  if (rows.length === 0) return null;
  const row = rows[0];
  return {
    ...mapAgendaItem(row),
    meeting: {
      id: row.m_id,
      title: row.m_title ?? null,
      date: row.m_date,
      city: row.m_city ?? null,
      status: row.m_status,
      startsAt: row.m_starts_at ?? null,
    },
  };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/lib/agendaItemsService.test.ts`
Expected: PASS (6 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/agendaItemsService.ts backend/src/lib/agendaItemsService.test.ts && git commit -m "feat(api): agendaItemsService — items by meeting, item detail with meeting context"
```

---

### Task 3: meetingsService — starts_at, getUpcomingMeetings, default-published guard

**Files:**
- Modify: `backend/src/lib/meetingsService.ts` (MEETING_COLS ~line 332, `MeetingRow` ~line 139, `mapMeeting` ~line 232, `Meeting` interface ~line 27, `getMeetings` ~line 346)
- Test: `backend/src/lib/meetingsService.test.ts` (extend)

The invariant being protected: every pre-existing read path assumes all rows in
`meetings.meetings` are published. Once the pipeline starts writing
`status='scheduled'` rows, `getMeetings()` with no filter would leak them into the
main meeting list. Fix: default `status='published'` unless an explicit status
filter is passed. (`searchSegments` and transcripts are safe — scheduled meetings
have no segments.)

- [ ] **Step 1: Write the failing tests**

Add to `backend/src/lib/meetingsService.test.ts` (follow the file's existing `mockQuery`/`baseRow` conventions; extend `baseRow` with `starts_at: null`):

```ts
describe('getMeetings status default', () => {
  it('filters to published when no status filter is passed', async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] });
    await getMeetings();
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toContain(`status = 'published'`);
  });

  it('uses the explicit status filter when passed', async () => {
    mockQuery.mockResolvedValueOnce({ rows: [] });
    await getMeetings({ status: 'scheduled' });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).not.toContain(`status = 'published'`);
    expect(params).toContain('scheduled');
  });
});

describe('getUpcomingMeetings', () => {
  it('selects scheduled meetings from today forward, soonest first', async () => {
    mockQuery.mockResolvedValueOnce({
      rows: [{ ...baseRow, status: 'scheduled', starts_at: '2026-07-29T18:30:00-04:00' }],
    });
    const meetings = await getUpcomingMeetings();
    const [sql] = mockQuery.mock.calls[0];
    expect(sql).toContain(`status = 'scheduled'`);
    expect(sql).toContain('date >= CURRENT_DATE');
    expect(sql).toContain('ORDER BY date ASC');
    expect(meetings[0].startsAt).toBe('2026-07-29T18:30:00-04:00');
  });
});
```

Also add one assertion to an existing `getMeetingById`-style test that the mapped object now carries `startsAt: null` from `baseRow`.

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/lib/meetingsService.test.ts`
Expected: FAIL — `getUpcomingMeetings` not exported; status-default assertions fail.

- [ ] **Step 3: Implement**

In `backend/src/lib/meetingsService.ts`:

1. Add `starts_at` to `MEETING_COLS`.
2. Add `starts_at: string | null;` to `MeetingRow`, `startsAt: string | null;` to the `Meeting` interface (and to `MeetingListItem` if it's a distinct interface), and `startsAt: row.starts_at ?? null,` to `mapMeeting` (and the list mapper if separate).
3. In `getMeetings`, where the status filter is accumulated, change to:

```ts
if (filters?.status !== undefined) {
  params.push(filters.status);
  conditions.push(`status = $${params.length}`);
} else {
  // Scheduled (agenda-only) meetings must not leak into the default list;
  // every consumer of the unfiltered list predates their existence.
  conditions.push(`status = 'published'`);
}
```

4. Add, near `getMeetings`:

```ts
/** Scheduled (agenda-published) meetings from today forward, soonest first. */
export async function getUpcomingMeetings(): Promise<MeetingListItem[]> {
  const { rows } = await pool.query<MeetingRow>(
    `SELECT ${MEETING_COLS}
     FROM meetings.meetings
     WHERE status = 'scheduled' AND date >= CURRENT_DATE
     ORDER BY date ASC, starts_at ASC NULLS LAST`
  );
  return rows.map(mapMeeting);
}
```

(If the list path uses a different mapper/DTO than `mapMeeting`, mirror exactly what `getMeetings` does at lines 346-384.)

- [ ] **Step 4: Run the whole service test file**

Run: `npx vitest run src/lib/meetingsService.test.ts`
Expected: PASS, including all pre-existing tests (they must not regress — the `baseRow` fixture gains `starts_at: null`).

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/meetingsService.ts backend/src/lib/meetingsService.test.ts && git commit -m "feat(api): starts_at + getUpcomingMeetings; default getMeetings to published"
```

---

### Task 4: Routes

**Files:**
- Modify: `backend/src/routes/meetings.ts` (add `GET /upcoming` and `GET /:id/agenda-items` — **both BEFORE the `GET /:id` registration at ~line 144**)
- Create: `backend/src/routes/agendaItems.ts`
- Modify: `backend/src/index.ts` (import + `app.use('/api/agenda-items', ...)` next to the meetings mount at ~line 181)
- Test: `backend/src/routes/meetings.test.ts` (extend), `backend/src/routes/agendaItems.test.ts` (create)

- [ ] **Step 1: Write the failing route tests**

Extend `backend/src/routes/meetings.test.ts` — add `mockGetUpcomingMeetings` and `mockGetAgendaItemsByMeetingId` to the existing `vi.hoisted` block, add `getUpcomingMeetings: mockGetUpcomingMeetings` to the existing `vi.mock('../lib/meetingsService.js', ...)` factory, and add a new `vi.mock('../lib/agendaItemsService.js', () => ({ getAgendaItemsByMeetingId: mockGetAgendaItemsByMeetingId }))`. Then:

```ts
describe('GET /api/meetings/upcoming', () => {
  it('returns scheduled meetings and is NOT swallowed by /:id', async () => {
    mockGetUpcomingMeetings.mockResolvedValueOnce([{ id: MEETING_ID }]);
    const res = await request(app).get('/api/meetings/upcoming');
    expect(res.status).toBe(200);
    expect(res.body).toEqual([{ id: MEETING_ID }]);
    expect(mockGetUpcomingMeetings).toHaveBeenCalledTimes(1);
  });

  it('500s with INTERNAL_ERROR on service failure', async () => {
    mockGetUpcomingMeetings.mockRejectedValueOnce(new Error('boom'));
    const res = await request(app).get('/api/meetings/upcoming');
    expect(res.status).toBe(500);
    expect(res.body.code).toBe('INTERNAL_ERROR');
  });
});

describe('GET /api/meetings/:id/agenda-items', () => {
  it('422s on a non-UUID id', async () => {
    const res = await request(app).get('/api/meetings/not-a-uuid/agenda-items');
    expect(res.status).toBe(422);
    expect(res.body.code).toBe('INVALID_ID');
  });

  it('returns the items list', async () => {
    mockGetAgendaItemsByMeetingId.mockResolvedValueOnce([{ itemNumber: '6A' }]);
    const res = await request(app).get(`/api/meetings/${MEETING_ID}/agenda-items`);
    expect(res.status).toBe(200);
    expect(res.body).toEqual([{ itemNumber: '6A' }]);
  });
});
```

Create `backend/src/routes/agendaItems.test.ts` (same harness shape as `meetings.test.ts`: `vi.hoisted` mocks for `getAgendaItemById`, mock `../middleware/auth.js`, express app with `app.use('/api/agenda-items', agendaItemsRouter)`):

```ts
describe('GET /api/agenda-items/:id', () => {
  it('422s on non-UUID', async () => {
    const res = await request(app).get('/api/agenda-items/nope');
    expect(res.status).toBe(422);
  });

  it('404s when missing', async () => {
    mockGetAgendaItemById.mockResolvedValueOnce(null);
    const res = await request(app).get(`/api/agenda-items/${ITEM_ID}`);
    expect(res.status).toBe(404);
    expect(res.body.code).toBe('NOT_FOUND');
  });

  it('returns the item detail', async () => {
    mockGetAgendaItemById.mockResolvedValueOnce({ id: ITEM_ID, itemNumber: '6A' });
    const res = await request(app).get(`/api/agenda-items/${ITEM_ID}`);
    expect(res.status).toBe(200);
    expect(res.body.itemNumber).toBe('6A');
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `npx vitest run src/routes/meetings.test.ts src/routes/agendaItems.test.ts`
Expected: FAIL (routes don't exist).

- [ ] **Step 3: Implement the routes**

In `backend/src/routes/meetings.ts`, immediately after `GET /` (~line 47) and before every `/:id*` route:

```ts
// NOTE: registered before /:id so Express doesn't treat "upcoming" as an id.
router.get('/upcoming', optionalAuth, async (_req: Request, res: Response): Promise<void> => {
  try {
    const meetings = await getUpcomingMeetings();
    res.status(200).json(meetings);
  } catch (err) {
    console.error('[GET /meetings/upcoming] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

router.get('/:id/agenda-items', optionalAuth, async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }
  try {
    const items = await getAgendaItemsByMeetingId(id);
    res.status(200).json(items);
  } catch (err) {
    console.error('[GET /meetings/:id/agenda-items] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});
```

(add `getUpcomingMeetings` to the meetingsService import and `import { getAgendaItemsByMeetingId } from '../lib/agendaItemsService.js';`)

Create `backend/src/routes/agendaItems.ts`:

```ts
// Public read endpoint for single agenda items (the citizen-facing permalink).
// Called cross-origin by the on-the-record static site (CORS_ORIGIN allowlist).
import { Router, type Request, type Response } from 'express';
import { optionalAuth } from '../middleware/auth.js';
import { getAgendaItemById } from '../lib/agendaItemsService.js';

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

const router = Router();

router.get('/:id', optionalAuth, async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }
  try {
    const item = await getAgendaItemById(id);
    if (!item) {
      res.status(404).json({ code: 'NOT_FOUND', message: 'Agenda item not found' });
      return;
    }
    res.status(200).json(item);
  } catch (err) {
    console.error('[GET /agenda-items/:id] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

export default router;
```

In `backend/src/index.ts`, next to the meetings mount (~line 181):

```ts
import agendaItemsRouter from './routes/agendaItems.js';
// ...
app.use('/api/agenda-items', agendaItemsRouter);
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `npx vitest run src/routes/meetings.test.ts src/routes/agendaItems.test.ts`
Expected: PASS, including all pre-existing meetings route tests.

- [ ] **Step 5: Commit**

```bash
git add backend/src/routes/meetings.ts backend/src/routes/agendaItems.ts backend/src/routes/agendaItems.test.ts backend/src/routes/meetings.test.ts backend/src/index.ts && git commit -m "feat(api): GET /meetings/upcoming, /meetings/:id/agenda-items, /agenda-items/:id"
```

---

### Task 5: Full check + PR

- [ ] **Step 1: Run the full gate**

Run (from `backend/`): `npm run lint && npm run typecheck && npm test && npm run check:migrations`
Expected: all pass.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/agenda-items-schema-api
gh pr create --repo EmpoweredVote/ev-accounts --title "Agenda items schema + API (Bloomington item-centric coverage, Phase 1)" --body "$(cat <<'EOF'
Adds meetings.agenda_items + scheduled-meeting support + three public endpoints, per the approved spec (on-the-record docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md).

- Migration 1476: agenda_items table (CHECK-constrained kind/status/outcome, continued_from_item_id matter seed, RLS public-read), meetings.starts_at, votes.agenda_item_id, (status,date) index. NOT YET APPLIED — apply before deploy.
- getMeetings now defaults to status='published' (scheduled rows must not leak into pre-existing consumers).
- getUpcomingMeetings + agendaItemsService (items by meeting, item detail with meeting context).
- GET /api/meetings/upcoming (registered before /:id), GET /api/meetings/:id/agenda-items, GET /api/agenda-items/:id — all optionalAuth/public.

Written by the on-the-record pipeline only (delete-then-insert per meeting); ev-accounts is read-only for these tables.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

### Task 6: Apply migration 1476 to prod (gated — do this LAST, with user confirmation)

Ordering rule (learned the hard way in the meeting-list feature): **apply the DB migration to prod BEFORE merging/deploying the API code** that selects the new columns — but only after all tests pass and the PR is up.

- [ ] **Step 1: Confirm with the user before touching prod.** This is a production DDL change; do not run it silently.

- [ ] **Step 2: Apply via direct connection** (NOT the transaction pooler — multi-statement files fail there; use the `db.<ref>.supabase.co:5432` host as `postgres`, per `DEPLOY.md:108-124`). Either run `backend/migrations/1476_agenda_items_and_scheduled_meetings.sql` in the Supabase SQL Editor, or use the Supabase MCP `apply_migration` tool if connected.

- [ ] **Step 3: Verify**

Run against prod (read-only):

```sql
SELECT count(*) FROM information_schema.columns
 WHERE table_schema='meetings' AND table_name='agenda_items';  -- expect 20
SELECT column_name FROM information_schema.columns
 WHERE table_schema='meetings' AND table_name='meetings' AND column_name='starts_at';  -- expect 1 row
```

- [ ] **Step 4: Merge the PR** (after review), then confirm the deployed API serves `GET /api/meetings/upcoming` → `[]` (empty until the pipeline publishes).
