# Migration B: Event Entity Foreign Keys Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the loose meeting body slug with real chamber/race foreign keys, enforce event-kind/entity compatibility in application writes, and publish chamber or race links from the pipeline.

**Architecture:** Migration 579 performs a strict preflight audit: every non-null legacy body slug must resolve to exactly one chamber before any destructive change proceeds. It then adds nullable foreign keys, backfills `chamber_id`, and drops `body_slug`. ev-accounts exposes both IDs and validates complete entity state, including PATCH requests merged with the stored row. The pipeline retains `body_slug` only in local checkpoint metadata for roster selection, resolves it to a chamber UUID inside the publish transaction, and persists an explicit `race_id` for electoral events.

**Tech Stack:** PostgreSQL migrations and PL/pgSQL, TypeScript, Express, Zod, Vitest, Python dataclasses, argparse, psycopg2, pytest.

**Prerequisite:** Complete and deploy `2026-06-15-migration-a-event-kinds.md` first. This plan assumes `meetings.meetings.title`, `event_kind`, and nullable `city` already exist.

---

## Entity Rules

```text
council, school_board:
  chamber_id required
  race_id null

debate, forum:
  chamber_id null
  race_id required

news_clip, community_meeting, other:
  chamber_id optional
  race_id optional
  both together forbidden
```

The database enforces referential integrity only. The compatibility rules stay in the ev-accounts route layer and pipeline publisher so existing/incomplete rows can survive deployment and backfill operations.

## File Map

**ev-accounts repository (`/Users/chrisandrews/Documents/GitHub/ev-accounts`)**

- Create `backend/migrations/579_event_entity_fks.sql` for audit, FKs, backfill, indexes, and legacy-column removal.
- Create `backend/src/lib/eventEntityRules.ts` for shared entity validation.
- Modify `backend/src/lib/meetingsService.ts` for IDs, writes, and PATCH state loading.
- Modify `backend/src/lib/meetingsService.test.ts` for read/write SQL.
- Modify `backend/src/routes/meetings.ts` for create and merged PATCH validation.
- Modify `backend/src/routes/meetings.test.ts` for HTTP rule coverage.

**on-the-record repository**

- Modify `src/models.py` to persist `race_id`.
- Create `src/event_entities.py` for UUID and event-kind/entity validation.
- Modify `src/checkpoint.py` to persist `race_id` independently of roster metadata.
- Modify `run_local.py` for `--race-id`, resume, mismatch handling, and meeting construction.
- Modify `src/publish.py` to resolve chamber slugs transactionally and write both FKs.
- Create `tests/test_event_entities.py`.
- Modify `tests/test_publish.py`, `tests/test_body_tagging.py`, and checkpoint/resume tests as needed.
- Modify `web/lib/types.ts` and `web/lib/queries.ts` to consume `chamberId` and `raceId`; no new UI is required.

### Task 1: Add the strict audit/backfill migration

**Files:**
- Create: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/579_event_entity_fks.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Migration 579: Link events to chambers or races and remove body_slug.
--
-- This migration intentionally aborts unless every non-null legacy body_slug
-- resolves to exactly one essentials.chambers row.

BEGIN;

DO $$
DECLARE
  v_problem RECORD;
BEGIN
  SELECT
    m.body_slug,
    COUNT(DISTINCT c.id) AS match_count,
    COUNT(*) AS meeting_count
  INTO v_problem
  FROM meetings.meetings m
  LEFT JOIN essentials.chambers c ON c.slug = m.body_slug
  WHERE m.body_slug IS NOT NULL
  GROUP BY m.body_slug
  HAVING COUNT(DISTINCT c.id) <> 1
  ORDER BY m.body_slug
  LIMIT 1;

  IF FOUND THEN
    RAISE EXCEPTION
      'Cannot migrate body_slug=%: expected exactly one chamber match, found % across % meeting(s)',
      v_problem.body_slug,
      v_problem.match_count,
      v_problem.meeting_count;
  END IF;
END $$;

ALTER TABLE meetings.meetings
  ADD COLUMN IF NOT EXISTS chamber_id UUID
    REFERENCES essentials.chambers(id),
  ADD COLUMN IF NOT EXISTS race_id UUID
    REFERENCES essentials.races(id);

UPDATE meetings.meetings m
SET chamber_id = c.id
FROM essentials.chambers c
WHERE m.body_slug IS NOT NULL
  AND c.slug = m.body_slug
  AND m.chamber_id IS DISTINCT FROM c.id;

DO $$
DECLARE
  v_remaining BIGINT;
BEGIN
  SELECT COUNT(*) INTO v_remaining
  FROM meetings.meetings
  WHERE body_slug IS NOT NULL
    AND chamber_id IS NULL;

  IF v_remaining <> 0 THEN
    RAISE EXCEPTION
      'Refusing to drop body_slug: % non-null slug row(s) remain unlinked',
      v_remaining;
  END IF;
END $$;

CREATE INDEX IF NOT EXISTS meetings_meetings_chamber_id_idx
  ON meetings.meetings(chamber_id);

CREATE INDEX IF NOT EXISTS meetings_meetings_race_id_idx
  ON meetings.meetings(race_id);

ALTER TABLE meetings.meetings
  DROP COLUMN body_slug;

COMMIT;
```

- [ ] **Step 2: Run static migration assertions**

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
rg -n "COUNT\\(DISTINCT c.id\\).*<> 1|REFERENCES essentials.chambers|REFERENCES essentials.races|DROP COLUMN body_slug|CHECK" backend/migrations/579_event_entity_fks.sql
```

Expected: the audit, both FKs, and the drop are present. No entity compatibility `CHECK` constraint exists.

- [ ] **Step 3: Exercise the migration in a disposable database**

Use a disposable clone that has Migration A applied and contains at least one
meeting. First record a real chamber slug:

```sql
SELECT slug
FROM essentials.chambers
WHERE slug IS NOT NULL
ORDER BY slug
LIMIT 1;
```

Set one cloned meeting to a missing slug:

```sql
UPDATE meetings.meetings
SET body_slug = 'migration-579-no-match'
WHERE id = (
  SELECT id
  FROM meetings.meetings
  ORDER BY id
  LIMIT 1
);
```

Run Migration 579.

Expected: it aborts with `expected exactly one chamber match, found 0` and
leaves `body_slug` intact.

Restore the disposable clone, set that meeting's `body_slug` to the real slug
returned by the first query, and rerun Migration 579.

Expected: migration commits; `chamber_id` is populated; `body_slug` no longer
exists; both FK indexes exist.

- [ ] **Step 4: Commit the migration**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/migrations/579_event_entity_fks.sql
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(meetings): replace body slug with entity foreign keys"
```

### Task 2: Define ev-accounts entity validation

**Files:**
- Create: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/lib/eventEntityRules.ts`
- Create: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/lib/eventEntityRules.test.ts`

- [ ] **Step 1: Write failing rule tests**

Create:

```ts
import { describe, expect, it } from 'vitest';
import { validateEventEntities } from './eventEntityRules.js';

const CHAMBER_ID = '11111111-1111-4111-8111-111111111111';
const RACE_ID = '22222222-2222-4222-8222-222222222222';

describe('validateEventEntities', () => {
  it.each(['council', 'school_board'] as const)(
    'requires a chamber for %s',
    (eventKind) => {
      expect(validateEventEntities({
        eventKind,
        chamberId: null,
        raceId: null,
      })).toMatch(/chamberId is required/);
    }
  );

  it.each(['debate', 'forum'] as const)(
    'requires a race for %s',
    (eventKind) => {
      expect(validateEventEntities({
        eventKind,
        chamberId: null,
        raceId: null,
      })).toMatch(/raceId is required/);
    }
  );

  it('rejects both IDs for every kind', () => {
    expect(validateEventEntities({
      eventKind: 'news_clip',
      chamberId: CHAMBER_ID,
      raceId: RACE_ID,
    })).toMatch(/cannot both be set/);
  });

  it.each(['news_clip', 'community_meeting', 'other'] as const)(
    'allows neither entity for %s',
    (eventKind) => {
      expect(validateEventEntities({
        eventKind,
        chamberId: null,
        raceId: null,
      })).toBeNull();
    }
  );

  it('accepts the required single entity', () => {
    expect(validateEventEntities({
      eventKind: 'council',
      chamberId: CHAMBER_ID,
      raceId: null,
    })).toBeNull();
    expect(validateEventEntities({
      eventKind: 'debate',
      chamberId: null,
      raceId: RACE_ID,
    })).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/lib/eventEntityRules.test.ts
```

Expected: FAIL because the module does not exist.

- [ ] **Step 3: Implement the pure validator**

Create `eventEntityRules.ts`:

```ts
import type { EventKind } from './eventKinds.js';

export interface EventEntityState {
  eventKind: EventKind;
  chamberId: string | null;
  raceId: string | null;
}

export function validateEventEntities(
  state: EventEntityState
): string | null {
  if (state.chamberId !== null && state.raceId !== null) {
    return 'chamberId and raceId cannot both be set';
  }

  if (
    (state.eventKind === 'council' ||
      state.eventKind === 'school_board') &&
    state.chamberId === null
  ) {
    return `chamberId is required for eventKind ${state.eventKind}`;
  }

  if (
    (state.eventKind === 'debate' || state.eventKind === 'forum') &&
    state.raceId === null
  ) {
    return `raceId is required for eventKind ${state.eventKind}`;
  }

  return null;
}
```

- [ ] **Step 4: Run tests**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/lib/eventEntityRules.test.ts
```

Expected: PASS.

- [ ] **Step 5: Commit the validation module**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/src/lib/eventEntityRules.ts backend/src/lib/eventEntityRules.test.ts
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(meetings): define event entity rules"
```

### Task 3: Expose entity IDs through meetingsService

**Files:**
- Modify: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/lib/meetingsService.ts`
- Modify: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/lib/meetingsService.test.ts`

- [ ] **Step 1: Write failing mapping and write tests**

Replace `body_slug` in `baseRow` with:

```ts
chamber_id: '11111111-1111-4111-8111-111111111111',
race_id: null,
```

Add to the detail assertion:

```ts
expect(meeting!.chamberId).toBe('11111111-1111-4111-8111-111111111111');
expect(meeting!.raceId).toBeNull();
expect('bodySlug' in meeting!).toBe(false);
```

Update the create test input to include:

```ts
chamberId: null,
raceId: '22222222-2222-4222-8222-222222222222',
```

Assert both values are the final INSERT parameters after `eventKind`.

Add:

```ts
it('loads the current entity state for patch validation', async () => {
  mockQuery.mockResolvedValueOnce({ rows: [baseRow] });

  const state = await getMeetingEntityState('m1');

  expect(state).toEqual({
    eventKind: 'council',
    chamberId: '11111111-1111-4111-8111-111111111111',
    raceId: null,
  });
});
```

- [ ] **Step 2: Run service tests to verify RED**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/lib/meetingsService.test.ts
```

Expected: FAIL because the new fields and `getMeetingEntityState` do not exist.

- [ ] **Step 3: Replace bodySlug with entity IDs**

In `Meeting`:

```ts
chamberId: string | null;
raceId: string | null;
```

Remove `bodySlug`.

In `MeetingRow`:

```ts
chamber_id: string | null;
race_id: string | null;
```

Remove `body_slug`.

In `mapMeeting`:

```ts
chamberId: row.chamber_id,
raceId: row.race_id,
```

Replace `body_slug` in `MEETING_COLS` with:

```sql
chamber_id, race_id
```

- [ ] **Step 4: Extend create and update writes**

Add to `createMeeting` data:

```ts
chamberId?: string | null;
raceId?: string | null;
```

Add `chamber_id, race_id` after `event_kind` in the INSERT columns, add `$11, $12`, and append:

```ts
data.chamberId ?? null,
data.raceId ?? null,
```

Add to the update partial type:

```ts
chamberId: string | null;
raceId: string | null;
```

Add update clauses:

```ts
if (data.chamberId !== undefined) {
  params.push(data.chamberId);
  setClauses.push(`chamber_id = $${params.length}`);
}
if (data.raceId !== undefined) {
  params.push(data.raceId);
  setClauses.push(`race_id = $${params.length}`);
}
```

- [ ] **Step 5: Add a current-state query for PATCH**

Import:

```ts
import type { EventEntityState } from './eventEntityRules.js';
```

Add:

```ts
export async function getMeetingEntityState(
  id: string
): Promise<EventEntityState | null> {
  const { rows } = await pool.query<{
    event_kind: EventKind;
    chamber_id: string | null;
    race_id: string | null;
  }>(
    `SELECT event_kind, chamber_id, race_id
     FROM meetings.meetings
     WHERE id = $1`,
    [id]
  );

  if (rows.length === 0) return null;
  return {
    eventKind: rows[0].event_kind,
    chamberId: rows[0].chamber_id,
    raceId: rows[0].race_id,
  };
}
```

- [ ] **Step 6: Run service tests and typecheck**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/lib/meetingsService.test.ts
npm run typecheck
```

Expected: PASS.

- [ ] **Step 7: Commit service changes**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/src/lib/meetingsService.ts backend/src/lib/meetingsService.test.ts
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(meetings): expose chamber and race links"
```

### Task 4: Enforce create and merged PATCH entity state

**Files:**
- Modify: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/routes/meetings.ts`
- Modify: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/routes/meetings.test.ts`

- [ ] **Step 1: Add failing create-route cases**

Use fixed UUIDs and add:

```ts
it.each([
  ['council', null, null, 'chamberId is required'],
  ['school_board', null, null, 'chamberId is required'],
  ['debate', null, null, 'raceId is required'],
  ['forum', null, null, 'raceId is required'],
  ['news_clip', CHAMBER_ID, RACE_ID, 'cannot both be set'],
])(
  'rejects invalid create entity state for %s',
  async (eventKind, chamberId, raceId, message) => {
    const response = await request(app)
      .post('/api/meetings')
      .send({
        city: null,
        state: 'CA',
        date: '2026-06-02',
        meetingType: 'Event',
        eventKind,
        chamberId,
        raceId,
      });

    expect(response.status).toBe(422);
    expect(response.body.message).toContain(message);
    expect(mockCreateMeeting).not.toHaveBeenCalled();
  }
);
```

- [ ] **Step 2: Add failing merged PATCH cases**

Mock `getMeetingEntityState` and add:

```ts
it('rejects changing a chamber event to debate without supplying raceId', async () => {
  mockGetMeetingEntityState.mockResolvedValueOnce({
    eventKind: 'council',
    chamberId: CHAMBER_ID,
    raceId: null,
  });

  const response = await request(app)
    .patch(`/api/meetings/${MEETING_ID}`)
    .send({ eventKind: 'debate', chamberId: null });

  expect(response.status).toBe(422);
  expect(response.body.message).toContain('raceId is required');
  expect(mockUpdateMeeting).not.toHaveBeenCalled();
});

it('accepts an atomic chamber-to-race transition', async () => {
  mockGetMeetingEntityState.mockResolvedValueOnce({
    eventKind: 'council',
    chamberId: CHAMBER_ID,
    raceId: null,
  });
  mockUpdateMeeting.mockResolvedValueOnce({
    id: MEETING_ID,
    eventKind: 'debate',
    chamberId: null,
    raceId: RACE_ID,
  });

  const response = await request(app)
    .patch(`/api/meetings/${MEETING_ID}`)
    .send({
      eventKind: 'debate',
      chamberId: null,
      raceId: RACE_ID,
    });

  expect(response.status).toBe(200);
  expect(mockUpdateMeeting).toHaveBeenCalledWith(
    MEETING_ID,
    expect.objectContaining({
      eventKind: 'debate',
      chamberId: null,
      raceId: RACE_ID,
    })
  );
});
```

- [ ] **Step 3: Run route tests to verify RED**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/routes/meetings.test.ts
```

Expected: FAIL because entity IDs and merged validation are absent.

- [ ] **Step 4: Extend schemas**

Import:

```ts
import {
  getMeetingEntityState,
  // existing imports remain
} from '../lib/meetingsService.js';
import { validateEventEntities } from '../lib/eventEntityRules.js';
```

Add to both create and update schemas:

```ts
chamberId: z.string().uuid().optional().nullable(),
raceId: z.string().uuid().optional().nullable(),
```

- [ ] **Step 5: Validate create requests**

After `safeParse` succeeds and before `createMeeting`:

```ts
const entityError = validateEventEntities({
  eventKind: parsed.data.eventKind,
  chamberId: parsed.data.chamberId ?? null,
  raceId: parsed.data.raceId ?? null,
});
if (entityError) {
  res.status(422).json({
    code: 'VALIDATION_ERROR',
    message: entityError,
  });
  return;
}
```

- [ ] **Step 6: Validate merged PATCH requests**

After PATCH parsing and before `updateMeeting`:

```ts
const current = await getMeetingEntityState(id);
if (!current) {
  res.status(404).json({
    code: 'NOT_FOUND',
    message: 'Meeting not found',
  });
  return;
}

const nextState = {
  eventKind: parsed.data.eventKind ?? current.eventKind,
  chamberId:
    parsed.data.chamberId !== undefined
      ? parsed.data.chamberId
      : current.chamberId,
  raceId:
    parsed.data.raceId !== undefined
      ? parsed.data.raceId
      : current.raceId,
};

const entityError = validateEventEntities(nextState);
if (entityError) {
  res.status(422).json({
    code: 'VALIDATION_ERROR',
    message: entityError,
  });
  return;
}
```

Keep this inside the existing `try` so database errors use the route's 500 response. The subsequent `updateMeeting` call remains unchanged.

- [ ] **Step 7: Run backend tests**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/lib/eventEntityRules.test.ts src/lib/meetingsService.test.ts src/routes/meetings.test.ts
npm run typecheck
npm run lint
```

Expected: PASS.

- [ ] **Step 8: Commit route enforcement**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/src/routes/meetings.ts backend/src/routes/meetings.test.ts
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(meetings): enforce event entity compatibility"
```

### Task 5: Persist race metadata in pipeline models and checkpoints

**Files:**
- Create: `src/event_entities.py`
- Modify: `src/models.py`
- Modify: `src/checkpoint.py`
- Create: `tests/test_event_entities.py`

- [ ] **Step 1: Write failing validation/model/checkpoint tests**

Create:

```python
import json

import pytest

from src.checkpoint import PipelineState
from src.event_entities import validate_event_entities
from src.models import Meeting

CHAMBER_ID = "11111111-1111-4111-8111-111111111111"
RACE_ID = "22222222-2222-4222-8222-222222222222"


def test_meeting_round_trip_preserves_race_id():
    restored = Meeting.from_dict(Meeting(
        meeting_id="debate",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        event_kind="debate",
        race_id=RACE_ID,
    ).to_dict())
    assert restored.race_id == RACE_ID


def test_legacy_meeting_defaults_race_id_to_none():
    restored = Meeting.from_dict({
        "meeting_id": "legacy",
        "city": "Bloomington",
        "date": "2026-02-18",
    })
    assert restored.race_id is None


def test_pipeline_state_persists_race_id(tmp_path):
    state = PipelineState(tmp_path)
    state.race_id = RACE_ID
    state.save()
    assert PipelineState(tmp_path).race_id == RACE_ID
    assert json.loads((tmp_path / "pipeline_state.json").read_text())["race_id"] == RACE_ID


def test_entity_validation_rules():
    assert validate_event_entities("council", CHAMBER_ID, None) is None
    assert validate_event_entities("debate", None, RACE_ID) is None
    assert "chamber_id is required" in validate_event_entities(
        "council", None, None
    )
    assert "race_id is required" in validate_event_entities(
        "debate", None, None
    )
    assert "cannot both be set" in validate_event_entities(
        "other", CHAMBER_ID, RACE_ID
    )


def test_entity_validation_rejects_bad_uuid():
    with pytest.raises(ValueError, match="race_id must be a UUID"):
        validate_event_entities("debate", None, "not-a-uuid")
```

- [ ] **Step 2: Run tests to verify RED**

```bash
python -m pytest tests/test_event_entities.py -q
```

Expected: import and attribute failures.

- [ ] **Step 3: Implement the pipeline validator**

Create `src/event_entities.py`:

```python
from __future__ import annotations

from typing import Optional
from uuid import UUID


def _validate_uuid(name: str, value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    try:
        return str(UUID(value))
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"{name} must be a UUID") from exc


def validate_event_entities(
    event_kind: str,
    chamber_id: Optional[str],
    race_id: Optional[str],
) -> Optional[str]:
    chamber_id = _validate_uuid("chamber_id", chamber_id)
    race_id = _validate_uuid("race_id", race_id)

    if chamber_id is not None and race_id is not None:
        return "chamber_id and race_id cannot both be set"
    if event_kind in ("council", "school_board") and chamber_id is None:
        return f"chamber_id is required for event_kind {event_kind}"
    if event_kind in ("debate", "forum") and race_id is None:
        return f"race_id is required for event_kind {event_kind}"
    return None
```

- [ ] **Step 4: Extend Meeting and PipelineState**

Add to `Meeting` after `event_kind`:

```python
race_id: Optional[str] = None
```

Add `"race_id": self.race_id` to `to_dict()` and:

```python
race_id=d.get("race_id"),
```

to `from_dict()`.

In `PipelineState.__init__`, add:

```python
self.race_id: Optional[str] = None
```

In `_load`, add:

```python
self.race_id = data.get("race_id")
```

In `save`, add:

```python
"race_id": self.race_id,
```

Do not remove `body_slug`; it remains local pipeline metadata for roster selection.

- [ ] **Step 5: Run focused tests**

```bash
python -m pytest tests/test_event_entities.py tests/test_body_tagging.py tests/test_rewind_to.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit model/checkpoint support**

```bash
git add src/event_entities.py src/models.py src/checkpoint.py tests/test_event_entities.py
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(pipeline): persist event race metadata"
```

### Task 6: Add race CLI and resume semantics

**Files:**
- Modify: `run_local.py`
- Modify: `tests/test_event_entities.py`

- [ ] **Step 1: Write failing CLI and state-mismatch tests**

Add:

```python
from types import SimpleNamespace

import run_local


def test_parser_accepts_race_id():
    parser = run_local.build_parser()
    args = parser.parse_args([
        "--input", "debate.mp4",
        "--event-kind", "debate",
        "--race-id", RACE_ID,
    ])
    assert args.race_id == RACE_ID


def test_resolve_race_id_persists_first_value(tmp_path):
    state = PipelineState(tmp_path)
    assert run_local._resolve_race_id(state, RACE_ID) == RACE_ID
    assert PipelineState(tmp_path).race_id == RACE_ID


def test_resolve_race_id_rejects_mismatch(tmp_path):
    state = PipelineState(tmp_path)
    state.race_id = RACE_ID
    state.save()
    with pytest.raises(RuntimeError, match="already linked"):
        run_local._resolve_race_id(
            state,
            "33333333-3333-4333-8333-333333333333",
        )
```

- [ ] **Step 2: Run tests to verify RED**

```bash
python -m pytest tests/test_event_entities.py -q
```

Expected: FAIL because the parser option and resolver do not exist.

- [ ] **Step 3: Add the parser option and resolver**

Add beside `--event-kind`:

```python
parser.add_argument(
    "--race-id",
    default=None,
    help="essentials.races UUID for a debate/forum",
)
```

Add:

```python
def _resolve_race_id(
    state: PipelineState,
    cli_race_id: str | None,
) -> str | None:
    if cli_race_id is not None:
        from uuid import UUID
        try:
            cli_race_id = str(UUID(cli_race_id))
        except ValueError as exc:
            raise RuntimeError("--race-id must be a UUID") from exc

    if state.race_id and cli_race_id and state.race_id != cli_race_id:
        raise RuntimeError(
            f"Meeting already linked to race {state.race_id}; "
            "changing races requires editing the meeting metadata explicitly"
        )

    if cli_race_id and state.race_id is None:
        state.race_id = cli_race_id
        state.save()

    return state.race_id
```

This intentionally has no force-retag equivalent. A race change is metadata correction, not a roster-dependent reprocessing operation.

- [ ] **Step 4: Wire pipeline construction and resume**

After body resolution:

```python
effective_race_id = _resolve_race_id(
    state,
    getattr(args, "race_id", None),
)
```

Add to `Meeting(...)`:

```python
race_id=effective_race_id,
```

When loading a named transcript in resume mode:

```python
args.race_id = data.get("race_id", args.race_id)
```

When standalone-publishing an old artifact, prefer the artifact value and fall back to state:

```python
if meeting.race_id is None:
    meeting.race_id = state.race_id
```

- [ ] **Step 5: Run focused tests**

```bash
python -m pytest tests/test_event_entities.py tests/test_body_tagging.py tests/test_redo_arg.py tests/test_rewind_to.py -q
python run_local.py --help | rg "race-id"
```

Expected: PASS and help includes `--race-id`.

- [ ] **Step 6: Commit CLI support**

```bash
git add run_local.py tests/test_event_entities.py
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(cli): accept race links for electoral events"
```

### Task 7: Resolve chamber IDs and publish entity links

**Files:**
- Modify: `src/publish.py`
- Modify: `tests/test_publish.py`

- [ ] **Step 1: Add failing chamber-resolution tests**

Extend `RecordingCursor` so tests can queue fetch results, then add:

```python
from src.publish import _resolve_chamber_id


def test_resolve_chamber_id_returns_unique_match():
    cur = RecordingCursor(fetch_rows=[
        ("11111111-1111-4111-8111-111111111111",),
    ])
    assert _resolve_chamber_id(cur, "test-council") == (
        "11111111-1111-4111-8111-111111111111"
    )


def test_resolve_chamber_id_rejects_missing_match():
    cur = RecordingCursor(fetch_rows=[])
    with pytest.raises(RuntimeError, match="matched 0 chambers"):
        _resolve_chamber_id(cur, "missing")


def test_resolve_chamber_id_rejects_duplicate_slug():
    cur = RecordingCursor(fetch_rows=[
        ("11111111-1111-4111-8111-111111111111",),
        ("22222222-2222-4222-8222-222222222222",),
    ])
    with pytest.raises(RuntimeError, match="matched 2 chambers"):
        _resolve_chamber_id(cur, "duplicate")
```

- [ ] **Step 2: Add failing publish compatibility tests**

Add:

```python
@pytest.mark.parametrize(
    "event_kind,body_slug,race_id,error",
    [
        ("council", None, None, "chamber_id is required"),
        ("debate", None, None, "race_id is required"),
        ("other", "test-council", RACE_ID, "cannot both be set"),
    ],
)
def test_publish_rejects_invalid_entity_state(
    event_kind, body_slug, race_id, error
):
    cur = RecordingCursor(fetch_rows=[])
    meeting = Meeting(
        meeting_id="event",
        city=None,
        date="2026-06-02",
        meeting_type="Event",
        event_kind=event_kind,
        race_id=race_id,
    )
    with pytest.raises(RuntimeError, match=error):
        _upsert_meeting(cur, meeting, body_slug)
```

Add one successful council test and one successful debate test asserting `chamber_id` and `race_id` occur in both UPDATE/INSERT SQL and parameter tuples.

- [ ] **Step 3: Run publish tests to verify RED**

```bash
python -m pytest tests/test_publish.py -q
```

Expected: FAIL because chamber resolution and entity-aware SQL do not exist.

- [ ] **Step 4: Implement chamber resolution**

Import:

```python
from .event_entities import validate_event_entities
```

Add:

```python
def _resolve_chamber_id(cur, body_slug: Optional[str]) -> Optional[str]:
    if body_slug is None:
        return None

    cur.execute(
        """
        SELECT id
        FROM essentials.chambers
        WHERE slug = %s
        ORDER BY id
        LIMIT 2
        """,
        (body_slug,),
    )
    rows = cur.fetchall()
    if len(rows) != 1:
        raise RuntimeError(
            f"Body slug {body_slug!r} matched {len(rows)} chambers; "
            "publishing requires exactly one"
        )
    return str(rows[0][0])
```

- [ ] **Step 5: Validate and write entity IDs in `_upsert_meeting`**

At the start of `_upsert_meeting`, before selecting the meeting row:

```python
chamber_id = _resolve_chamber_id(cur, body_slug)
entity_error = validate_event_entities(
    meeting.event_kind,
    chamber_id,
    meeting.race_id,
)
if entity_error:
    raise RuntimeError(entity_error)
```

Replace `body_slug` in UPDATE with:

```sql
chamber_id = %s,
race_id = %s,
```

and values:

```python
chamber_id,
meeting.race_id,
```

Replace `body_slug` in INSERT columns and values with `chamber_id, race_id`.

The local `body_slug` argument remains part of `publish_meeting` because it is the lookup key. It is never written after Migration B.

- [ ] **Step 6: Ensure test cursor supports `fetchall`**

Use this cursor shape:

```python
class RecordingCursor:
    def __init__(self, select_row=None, fetch_rows=None):
        self.select_row = select_row
        self.fetch_rows = list(fetch_rows or [])
        self.calls = []
        self._fetchone = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "FROM essentials.chambers" in sql:
            return
        if "SELECT id FROM meetings.meetings" in sql:
            self._fetchone = self.select_row
        elif "RETURNING id" in sql:
            self._fetchone = ("new-uuid",)

    def fetchall(self):
        return self.fetch_rows

    def fetchone(self):
        return self._fetchone
```

- [ ] **Step 7: Run publish and entity tests**

```bash
python -m pytest tests/test_publish.py tests/test_event_entities.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit publisher changes**

```bash
git add src/publish.py tests/test_publish.py
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(publish): link events to chambers and races"
```

### Task 8: Remove bodySlug from API consumers

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/queries.ts`

- [ ] **Step 1: Replace the web type**

In `Meeting`, remove:

```ts
body_slug: string | null;
```

Add:

```ts
chamber_id: string | null;
race_id: string | null;
```

- [ ] **Step 2: Replace API mapping**

In `mapMeeting`, remove:

```ts
body_slug: m.bodySlug ?? null,
```

Add:

```ts
chamber_id: m.chamberId ?? null,
race_id: m.raceId ?? null,
```

No page should render these IDs in Migration B. They are carried for later entity links and curation work.

- [ ] **Step 3: Search for stale API-field usage**

Run:

```bash
rg -n "bodySlug|body_slug" web backend/src/lib/meetingsService.ts backend/src/routes/meetings.ts
```

Expected: no matches in the API service, routes, or web app. Matches in pipeline checkpoint/roster code are expected and must remain.

- [ ] **Step 4: Verify web**

```bash
cd web
npm run lint
npm run build
```

Expected: PASS.

- [ ] **Step 5: Commit consumer cleanup**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add web/lib/types.ts web/lib/queries.ts
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "refactor(web): consume event entity ids"
```

### Task 9: Complete cross-repository verification

**Files:**
- Verify all files changed in Tasks 1-8.

- [ ] **Step 1: Run the complete pipeline suite**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run backend verification**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run \
  src/lib/eventEntityRules.test.ts \
  src/lib/meetingsService.test.ts \
  src/routes/meetings.test.ts
npm run typecheck
npm run lint
```

Expected: PASS.

- [ ] **Step 3: Run web verification**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/web
npm run lint
npm run build
```

Expected: PASS.

- [ ] **Step 4: Verify stale database-field references**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
rg -n "body_slug|bodySlug" backend/src backend/migrations/579_event_entity_fks.sql
```

Expected: only Migration 579's audit/backfill/drop references remain.

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
rg -n "body_slug" src run_local.py tests
```

Expected: references remain only for local roster selection, checkpoint state, chamber lookup input, and their tests. No publisher SQL writes `body_slug`.

- [ ] **Step 5: Verify end-to-end rule scenarios**

Exercise these exact cases through backend route tests and publisher tests:

```text
council + chamber only: accepted
school_board + chamber only: accepted
debate + race only: accepted
forum + race only: accepted
news_clip + neither: accepted
news_clip + chamber only: accepted
news_clip + race only: accepted
other + both: rejected
council + neither: rejected
debate + neither: rejected
PATCH council/chamber -> debate/race in one request: accepted
PATCH council/chamber -> debate without race: rejected
unknown/duplicate body slug during publish: rejected before meeting write
```

- [ ] **Step 6: Verify repository safety**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git check-ignore .env .env.local
git diff --check
git status --short

cd /Users/chrisandrews/Documents/GitHub/on-the-record
git check-ignore .env .env.local web/.env.local
git diff --check
git status --short
```

Expected: no `.env` file, credentials, generated build output, database dump, or unrelated dirty file is staged.

### Task 10: Deployment order and smoke checks

**Files:**
- No source changes.

- [ ] **Step 1: Verify essentials coverage before production migration**

Run against production with read-only credentials:

```sql
SELECT
  m.body_slug,
  COUNT(DISTINCT c.id) AS chamber_matches,
  COUNT(*) AS meeting_count
FROM meetings.meetings m
LEFT JOIN essentials.chambers c ON c.slug = m.body_slug
WHERE m.body_slug IS NOT NULL
GROUP BY m.body_slug
ORDER BY m.body_slug;
```

Expected: every row has `chamber_matches = 1`. Do not apply Migration 579 otherwise.

- [ ] **Step 2: Deploy in compatibility order**

```text
1. Confirm Migration A and all Migration A application code are live.
2. Pause pipeline publishing and enter a short ev-accounts maintenance window.
3. Apply Migration 579.
4. Immediately deploy the Migration B ev-accounts backend.
5. Deploy the Migration B pipeline before resuming publishing.
6. Deploy the final web build that consumes chamberId/raceId.
7. End maintenance and resume publishing.
```

Migration 579 adds and drops columns in one transaction, so there is no version
that is simultaneously compatible with both the old backend query and the new
backend query. The maintenance window prevents old ev-accounts reads and old
pipeline writes from running between the migration and application deploys.

- [ ] **Step 3: Smoke-test API responses**

Check:

```text
GET /api/meetings
GET /api/meetings/{existing-council-id}
```

Expected: both include `chamberId` and `raceId`, omit `bodySlug`, and the existing council row has a non-null `chamberId`.

- [ ] **Step 4: Smoke-test one publish path per anchor**

Publish or republish:

```text
One council/school-board meeting with a persisted body_slug
One debate/forum artifact with --race-id
```

Expected: the council row has only `chamber_id`; the electoral row has only `race_id`; both remain readable through ev-accounts and the public web build.
