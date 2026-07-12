# Migration A: Event Kinds and Flexible Titling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add human titles, controlled event kinds, and nullable cities across the database, ev-accounts API, publishing pipeline, and public web app without changing existing council presentation when the new fields are absent.

**Architecture:** Ship the database change first in `ev-accounts`, then expose the fields through its explicit row mapper and admin write routes. Add the same controlled event-kind set to the Python pipeline, persist it in meeting artifacts, and publish both fields. Finally, map the API fields in the web app and centralize title fallback and event-kind labels in pure formatting helpers.

**Tech Stack:** PostgreSQL migrations, TypeScript, Express, Zod, Vitest, Python dataclasses, argparse, psycopg2, pytest, Next.js 16, React 19.

---

## File Map

**ev-accounts repository (`/Users/chrisandrews/Documents/GitHub/ev-accounts`)**

- Create `backend/migrations/578_event_kinds.sql` for `title`, `event_kind`, and nullable `city`.
- Create `backend/src/lib/eventKinds.ts` as the backend's single controlled-set definition.
- Modify `backend/src/lib/meetingsService.ts` to map and write the new fields.
- Modify `backend/src/lib/meetingsService.test.ts` to lock the read/write SQL contract.
- Modify `backend/src/routes/meetings.ts` to validate admin writes.
- Create `backend/src/routes/meetings.test.ts` for HTTP validation and response behavior.

**on-the-record repository**

- Modify `src/models.py` for persisted meeting metadata.
- Create `src/event_kinds.py` for the pipeline's documented copy of the controlled set.
- Modify `run_local.py` for CLI arguments, prompting/defaults, validation, resume, and meeting creation.
- Modify `src/publish.py` and `tests/test_publish.py` for INSERT/UPDATE columns.
- Create `tests/test_event_kinds.py` for model and CLI behavior.
- Modify `web/lib/types.ts`, `web/lib/queries.ts`, and `web/lib/format.ts` for API mapping and display helpers.
- Modify `web/app/page.tsx`, `web/app/meetings/[meetingId]/page.tsx`, and `web/app/globals.css` for display.
- Modify `web/app/search/page.tsx` so nullable meeting cities do not enter the city filter list.
- Create `web/lib/format.test.ts` only if a test runner is added; otherwise verify the pure helpers through TypeScript compilation and the production build as described in Task 7.

### Task 1: Add the event-kind database migration

**Files:**
- Create: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/migrations/578_event_kinds.sql`

- [ ] **Step 1: Write the migration**

```sql
-- Migration 578: Add flexible titles and event kinds to meetings.
--
-- Existing rows retain their current display because title is nullable,
-- event_kind backfills to council, and city values are unchanged.

BEGIN;

ALTER TABLE meetings.meetings
  ADD COLUMN IF NOT EXISTS title TEXT,
  ADD COLUMN IF NOT EXISTS event_kind TEXT NOT NULL DEFAULT 'council',
  ALTER COLUMN city DROP NOT NULL;

COMMIT;
```

- [ ] **Step 2: Inspect the migration for the compatibility invariants**

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
rg -n "title|event_kind|DROP NOT NULL|CHECK" backend/migrations/578_event_kinds.sql
```

Expected:

```text
title TEXT
event_kind TEXT NOT NULL DEFAULT 'council'
ALTER COLUMN city DROP NOT NULL
```

Expected: no database `CHECK` constraint. The controlled set is application-owned.

- [ ] **Step 3: Commit the migration**

Before committing, verify `.env` remains ignored and no secret-bearing files are staged:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git check-ignore .env .env.local
git diff --check
git status --short
git add backend/migrations/578_event_kinds.sql
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(meetings): add event kind and flexible title columns"
```

Expected: the staged file list contains only `backend/migrations/578_event_kinds.sql`.

### Task 2: Extend ev-accounts meeting reads and writes

**Files:**
- Create: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/lib/eventKinds.ts`
- Modify: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/lib/meetingsService.ts`
- Modify: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/lib/meetingsService.test.ts`

- [ ] **Step 1: Write failing service tests**

Add these fields to `baseRow` in `meetingsService.test.ts`:

```ts
title: 'Bloomington Council Budget Hearing',
event_kind: 'council',
city: null,
```

Extend the detail test:

```ts
expect(meeting!.title).toBe('Bloomington Council Budget Hearing');
expect(meeting!.eventKind).toBe('council');
expect(meeting!.city).toBeNull();
```

Import `createMeeting` and `updateMeeting`, then add:

```ts
it('writes title, eventKind, and a null city on create', async () => {
  mockQuery.mockResolvedValueOnce({ rows: [baseRow] });

  await createMeeting({
    city: null,
    state: 'CA',
    date: '2026-06-02',
    meetingType: 'Governor Debate',
    title: 'California Governor Debate',
    eventKind: 'debate',
  });

  expect(mockQuery).toHaveBeenCalledWith(
    expect.stringContaining('title, event_kind'),
    [
      null,
      'CA',
      '2026-06-02',
      'Governor Debate',
      null,
      null,
      null,
      'processing',
      'California Governor Debate',
      'debate',
    ]
  );
});

it('updates title and eventKind independently', async () => {
  mockQuery.mockResolvedValueOnce({ rows: [baseRow] });

  await updateMeeting('m1', {
    title: 'Updated title',
    eventKind: 'forum',
  });

  expect(mockQuery).toHaveBeenCalledWith(
    expect.stringContaining('title = $1, event_kind = $2'),
    ['Updated title', 'forum', 'm1']
  );
});
```

- [ ] **Step 2: Run the service tests to verify RED**

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/lib/meetingsService.test.ts
```

Expected: TypeScript/test failures because `title`, `eventKind`, and nullable `city` are not in the service contract.

- [ ] **Step 3: Add the backend event-kind module**

Create `backend/src/lib/eventKinds.ts`:

```ts
export const EVENT_KINDS = [
  'council',
  'school_board',
  'debate',
  'forum',
  'community_meeting',
  'news_clip',
  'other',
] as const;

export type EventKind = (typeof EVENT_KINDS)[number];
```

- [ ] **Step 4: Implement the service fields**

In `meetingsService.ts`, import the type:

```ts
import type { EventKind } from './eventKinds.js';
```

Change `Meeting`:

```ts
export interface Meeting {
  id: string;
  title: string | null;
  eventKind: EventKind;
  city: string | null;
  state: string | null;
```

Change `MeetingRow`:

```ts
interface MeetingRow {
  id: string;
  title: string | null;
  event_kind: EventKind;
  city: string | null;
```

Add to `mapMeeting` immediately after `id`:

```ts
title: row.title,
eventKind: row.event_kind,
```

Replace `MEETING_COLS` with:

```ts
const MEETING_COLS = `
  id, title, event_kind, city, state, date::text AS date, meeting_type,
  duration_seconds, video_url, audio_source, status, segment_count,
  speaker_count, created_at, updated_at, body_slug, source_url,
  playback_kind, slug, summary, processing_metadata
`;
```

Replace the `createMeeting` data type and INSERT with:

```ts
export async function createMeeting(data: {
  city?: string | null;
  state: string;
  date: string;
  meetingType: string;
  title?: string | null;
  eventKind?: EventKind;
  durationSeconds?: number | null;
  videoUrl?: string | null;
  audioSource?: string | null;
  status?: string;
}): Promise<Meeting> {
  const { rows } = await pool.query<MeetingRow>(
    `INSERT INTO meetings.meetings
       (city, state, date, meeting_type, duration_seconds, video_url,
        audio_source, status, title, event_kind)
     VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
     RETURNING ${MEETING_COLS}`,
    [
      data.city ?? null,
      data.state,
      data.date,
      data.meetingType,
      data.durationSeconds ?? null,
      data.videoUrl ?? null,
      data.audioSource ?? null,
      data.status ?? 'processing',
      data.title ?? null,
      data.eventKind ?? 'council',
    ]
  );
  return mapMeeting(rows[0]);
}
```

Extend `updateMeeting`'s partial data type:

```ts
city: string | null;
title: string | null;
eventKind: EventKind;
```

Add these clauses before `state`:

```ts
if (data.title !== undefined) {
  params.push(data.title);
  setClauses.push(`title = $${params.length}`);
}
if (data.eventKind !== undefined) {
  params.push(data.eventKind);
  setClauses.push(`event_kind = $${params.length}`);
}
```

Keep the existing `city` clause; its value type now permits `null`.

- [ ] **Step 5: Run service tests and typecheck**

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/lib/meetingsService.test.ts
npm run typecheck
```

Expected: both commands pass.

- [ ] **Step 6: Commit the service contract**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/src/lib/eventKinds.ts backend/src/lib/meetingsService.ts backend/src/lib/meetingsService.test.ts
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(meetings): expose titles and event kinds"
```

### Task 3: Validate ev-accounts admin write routes

**Files:**
- Modify: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/routes/meetings.ts`
- Create: `/Users/chrisandrews/Documents/GitHub/ev-accounts/backend/src/routes/meetings.test.ts`

- [ ] **Step 1: Write failing route tests**

Create `meetings.test.ts` using the same `vi.hoisted`, service mock, Express app, and `supertest` pattern as `people.test.ts`. Mock authentication modules so admin requests proceed, mount the router at `/api/meetings`, and include:

```ts
it('accepts a debate with a title and null city', async () => {
  mockCreateMeeting.mockResolvedValueOnce({
    id: MEETING_ID,
    title: 'California Governor Debate',
    eventKind: 'debate',
    city: null,
  });

  const response = await request(app)
    .post('/api/meetings')
    .send({
      city: null,
      state: 'CA',
      date: '2026-06-02',
      meetingType: 'Governor Debate',
      title: 'California Governor Debate',
      eventKind: 'debate',
    });

  expect(response.status).toBe(201);
  expect(mockCreateMeeting).toHaveBeenCalledWith(
    expect.objectContaining({
      city: null,
      title: 'California Governor Debate',
      eventKind: 'debate',
    })
  );
});

it('rejects an unknown event kind', async () => {
  const response = await request(app)
    .post('/api/meetings')
    .send({
      city: 'Los Angeles',
      state: 'CA',
      date: '2026-06-02',
      meetingType: 'Debate',
      eventKind: 'town_hall',
    });

  expect(response.status).toBe(422);
  expect(mockCreateMeeting).not.toHaveBeenCalled();
});

it('accepts clearing city and title on patch', async () => {
  mockUpdateMeeting.mockResolvedValueOnce({
    id: MEETING_ID,
    title: null,
    eventKind: 'news_clip',
    city: null,
  });

  const response = await request(app)
    .patch(`/api/meetings/${MEETING_ID}`)
    .send({ city: null, title: null, eventKind: 'news_clip' });

  expect(response.status).toBe(200);
  expect(mockUpdateMeeting).toHaveBeenCalledWith(MEETING_ID, {
    city: null,
    title: null,
    eventKind: 'news_clip',
  });
});
```

- [ ] **Step 2: Run route tests to verify RED**

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/routes/meetings.test.ts
```

Expected: FAIL because the schemas reject null city and do not accept title/eventKind.

- [ ] **Step 3: Implement route schemas from the shared controlled set**

In `meetings.ts`, import:

```ts
import { EVENT_KINDS } from '../lib/eventKinds.js';
```

Replace `createMeetingSchema` with:

```ts
const createMeetingSchema = z.object({
  city: z.string().min(1).optional().nullable(),
  state: z.string().min(1),
  date: z.string().min(1),
  meetingType: z.string().min(1),
  title: z.string().trim().min(1).optional().nullable(),
  eventKind: z.enum(EVENT_KINDS).default('council'),
  durationSeconds: z.number().int().positive().optional().nullable(),
  videoUrl: z.string().url().optional().nullable(),
  audioSource: z.string().optional().nullable(),
  status: z.string().optional(),
});
```

Replace `updateMeetingSchema` with:

```ts
const updateMeetingSchema = z.object({
  city: z.string().min(1).optional().nullable(),
  state: z.string().min(1).optional(),
  date: z.string().min(1).optional(),
  meetingType: z.string().min(1).optional(),
  title: z.string().trim().min(1).optional().nullable(),
  eventKind: z.enum(EVENT_KINDS).optional(),
  durationSeconds: z.number().int().positive().optional().nullable(),
  videoUrl: z.string().url().optional().nullable(),
  audioSource: z.string().optional().nullable(),
  status: z.string().optional(),
});
```

- [ ] **Step 4: Run route tests and backend verification**

Run:

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npx vitest run src/routes/meetings.test.ts src/lib/meetingsService.test.ts
npm run typecheck
npm run lint
```

Expected: all commands pass.

- [ ] **Step 5: Commit route validation**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/src/routes/meetings.ts backend/src/routes/meetings.test.ts
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "test(meetings): validate event-kind writes"
```

### Task 4: Persist and validate event metadata in the pipeline

**Files:**
- Create: `src/event_kinds.py`
- Modify: `src/models.py`
- Create: `tests/test_event_kinds.py`

- [ ] **Step 1: Write failing model and validation tests**

Create `tests/test_event_kinds.py`:

```python
from types import SimpleNamespace

import pytest

import run_local
from src.event_kinds import validate_event_kind
from src.models import Meeting


def test_meeting_round_trip_preserves_title_event_kind_and_null_city():
    meeting = Meeting(
        meeting_id="ca-governor-debate",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        title="California Governor Debate",
        event_kind="debate",
    )

    restored = Meeting.from_dict(meeting.to_dict())

    assert restored.title == "California Governor Debate"
    assert restored.event_kind == "debate"
    assert restored.city is None


def test_legacy_meeting_defaults_to_council():
    restored = Meeting.from_dict({
        "meeting_id": "legacy",
        "city": "Bloomington",
        "date": "2026-02-18",
        "meeting_type": "Regular Session",
    })

    assert restored.title is None
    assert restored.event_kind == "council"


def test_validate_event_kind_lists_allowed_values():
    with pytest.raises(ValueError, match="town_hall.*council.*school_board.*debate"):
        validate_event_kind("town_hall")


def test_resolve_metadata_defaults_event_kind_without_prompt(monkeypatch):
    monkeypatch.setattr(run_local.sys.stdin, "isatty", lambda: False)
    args = SimpleNamespace(
        city="Bloomington",
        date="2026-02-18",
        meeting_type="Regular Session",
        title=None,
        event_kind=None,
        default=False,
    )

    run_local._resolve_metadata(args)

    assert args.event_kind == "council"
    assert args.title is None


def test_cityless_debate_does_not_inherit_council_city_default(monkeypatch):
    monkeypatch.setattr(run_local.sys.stdin, "isatty", lambda: False)
    args = SimpleNamespace(
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        title="California Governor Debate",
        event_kind="debate",
        default=False,
    )

    run_local._resolve_metadata(args)

    assert args.city is None
```

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python -m pytest tests/test_event_kinds.py -q
```

Expected: collection/import failures because `src.event_kinds`, `Meeting.title`, and `Meeting.event_kind` do not exist.

- [ ] **Step 3: Add the pipeline controlled set**

Create `src/event_kinds.py`:

```python
EVENT_KINDS = (
    "council",
    "school_board",
    "debate",
    "forum",
    "community_meeting",
    "news_clip",
    "other",
)


def validate_event_kind(value: str) -> str:
    normalized = value.strip()
    if normalized not in EVENT_KINDS:
        allowed = ", ".join(EVENT_KINDS)
        raise ValueError(
            f"Unknown event kind {value!r}; allowed values: {allowed}"
        )
    return normalized
```

- [ ] **Step 4: Extend `Meeting`**

Import behavior is unchanged. Replace the leading fields of `Meeting` with:

```python
@dataclass
class Meeting:
    meeting_id: str
    city: Optional[str]
    date: str
    meeting_type: str = "Regular Session"
    title: Optional[str] = None
    event_kind: str = "council"
```

Add to `to_dict()` after `meeting_type`:

```python
"title": self.title,
"event_kind": self.event_kind,
```

Change `from_dict()` fields to:

```python
city=d.get("city"),
date=d["date"],
meeting_type=d.get("meeting_type", "Regular Session"),
title=d.get("title"),
event_kind=d.get("event_kind", "council"),
```

- [ ] **Step 5: Extend metadata resolution**

In `run_local.py`, import:

```python
from src.event_kinds import EVENT_KINDS, validate_event_kind
```

At the beginning of `_resolve_metadata`, resolve `event_kind` before deciding
city defaults:

```python
if args.event_kind is None:
    args.event_kind = "council"
else:
    args.event_kind = validate_event_kind(args.event_kind)
```

Define:

```python
requires_city_default = args.event_kind in ("council", "school_board")
```

In both the non-interactive and interactive branches, only apply or prompt for
`CITY_DEFAULT` when `requires_city_default` is true. For every other event
kind, preserve an explicitly supplied city and leave an omitted city as
`None`. Date and meeting type retain their existing prompt/default behavior.
Do not prompt for `title`; a missing title is meaningful. Do not prompt for
`event_kind`; the default is `council`.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python -m pytest tests/test_event_kinds.py tests/test_metadata_prompt.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit model and validation**

```bash
git add src/event_kinds.py src/models.py run_local.py tests/test_event_kinds.py
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(pipeline): persist event kind and title metadata"
```

### Task 5: Wire pipeline CLI, resume, and meeting construction

**Files:**
- Modify: `run_local.py`
- Modify: `tests/test_event_kinds.py`

- [ ] **Step 1: Add failing CLI/resume tests**

Append:

```python
def test_parser_accepts_title_and_event_kind():
    parser = run_local.build_parser()
    args = parser.parse_args([
        "--input", "meeting.mp4",
        "--title", "California Governor Debate",
        "--event-kind", "debate",
    ])

    assert args.title == "California Governor Debate"
    assert args.event_kind == "debate"


def test_parser_rejects_unknown_event_kind():
    parser = run_local.build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args([
            "--input", "meeting.mp4",
            "--event-kind", "town_hall",
        ])
```

If the parser currently lives only inside `main`, first extract its construction unchanged into `build_parser() -> argparse.ArgumentParser`, and make `main()` call `build_parser()`. The extraction must not alter existing options.

- [ ] **Step 2: Run tests to verify RED**

Run:

```bash
python -m pytest tests/test_event_kinds.py -q
```

Expected: FAIL because the parser has no new options or public builder.

- [ ] **Step 3: Add parser options**

Add beside the existing meeting metadata arguments:

```python
parser.add_argument(
    "--title",
    default=None,
    help="Optional human display title; blank/omitted uses city + meeting type",
)
parser.add_argument(
    "--event-kind",
    choices=EVENT_KINDS,
    default=None,
    help="Event format (default: council)",
)
```

When constructing `Meeting` in `run_pipeline`, add:

```python
title=args.title.strip() if args.title and args.title.strip() else None,
event_kind=args.event_kind,
```

Change the operator display to avoid rendering `None`:

```python
display_title = meeting.title or " ".join(
    part for part in (meeting.city, meeting.meeting_type) if part
)
print(f"\nMeeting: {display_title} ({meeting.date})")
```

In resume mode, after loading `transcript_named.json`, add:

```python
args.title = data.get("title", args.title)
args.event_kind = data.get("event_kind", args.event_kind)
```

In the resume default block, add:

```python
if args.event_kind is None:
    args.event_kind = "council"
else:
    args.event_kind = validate_event_kind(args.event_kind)
```

Do not replace a stored null city with `CITY_DEFAULT` when a named transcript exists. Track whether `named_path.exists()` and only apply the legacy city default when no named transcript was loaded.

- [ ] **Step 4: Run CLI and regression tests**

Run:

```bash
python -m pytest tests/test_event_kinds.py tests/test_metadata_prompt.py tests/test_redo_arg.py tests/test_rewind_to.py -q
python run_local.py --help | rg "title|event-kind"
```

Expected: tests pass and help lists both options.

- [ ] **Step 5: Commit CLI wiring**

```bash
git add run_local.py tests/test_event_kinds.py
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(cli): accept event titles and kinds"
```

### Task 6: Publish title and event kind

**Files:**
- Modify: `src/publish.py`
- Modify: `tests/test_publish.py`

- [ ] **Step 1: Add a recording cursor and failing SQL tests**

Add to `tests/test_publish.py`:

```python
from src.models import Meeting
from src.publish import _upsert_meeting


class RecordingCursor:
    def __init__(self, select_row):
        self.select_row = select_row
        self.calls = []
        self._fetchone = None

    def execute(self, sql, params=None):
        self.calls.append((sql, params))
        if "SELECT id FROM meetings.meetings" in sql:
            self._fetchone = self.select_row
        elif "RETURNING id" in sql:
            self._fetchone = ("new-uuid",)

    def fetchone(self):
        return self._fetchone


@pytest.mark.parametrize("existing_row", [("existing-uuid",), None])
def test_upsert_meeting_writes_title_and_event_kind(existing_row):
    cur = RecordingCursor(existing_row)
    meeting = Meeting(
        meeting_id="ca-governor-debate",
        city=None,
        date="2026-06-02",
        meeting_type="Governor Debate",
        title="California Governor Debate",
        event_kind="debate",
    )

    _upsert_meeting(cur, meeting, None)

    write_sql, write_params = cur.calls[1]
    assert "title" in write_sql
    assert "event_kind" in write_sql
    assert "California Governor Debate" in write_params
    assert "debate" in write_params
```

- [ ] **Step 2: Run publish tests to verify RED**

Run:

```bash
python -m pytest tests/test_publish.py::test_upsert_meeting_writes_title_and_event_kind -q
```

Expected: FAIL because the SQL does not include either column.

- [ ] **Step 3: Extend UPDATE and INSERT**

In the UPDATE statement, add after `meeting_type`:

```sql
title = %s,
event_kind = %s,
```

Add corresponding values after `meeting.meeting_type`:

```python
meeting.title,
meeting.event_kind,
```

In the INSERT column list, add after `meeting_type`:

```sql
title, event_kind,
```

Add two `%s` placeholders in the matching position and add:

```python
meeting.title,
meeting.event_kind,
```

Do not convert a null city to an empty string.

- [ ] **Step 4: Run publish and model tests**

Run:

```bash
python -m pytest tests/test_publish.py tests/test_event_kinds.py -q
```

Expected: PASS.

- [ ] **Step 5: Commit publisher changes**

```bash
git add src/publish.py tests/test_publish.py
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(publish): write event titles and kinds"
```

### Task 7: Map and display event metadata in the web app

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/queries.ts`
- Modify: `web/lib/format.ts`
- Modify: `web/app/page.tsx`
- Modify: `web/app/meetings/[meetingId]/page.tsx`
- Modify: `web/app/search/page.tsx`
- Modify: `web/app/globals.css`

- [ ] **Step 1: Add the web types**

In `web/lib/types.ts`, add:

```ts
export type EventKind =
  | "council"
  | "school_board"
  | "debate"
  | "forum"
  | "community_meeting"
  | "news_clip"
  | "other";
```

Change the leading `Meeting` fields to:

```ts
export interface Meeting {
  meeting_id: string;
  slug: string | null;
  title: string | null;
  event_kind: EventKind;
  city: string | null;
  body_slug: string | null;
```

- [ ] **Step 2: Map API fields**

In `web/lib/queries.ts`, import `EventKind` and add to `mapMeeting`:

```ts
title: m.title ?? null,
event_kind: (m.eventKind ?? "council") as EventKind,
city: m.city ?? null,
```

Remove the existing duplicate `city: m.city` line.

- [ ] **Step 3: Add pure formatting helpers**

Append to `web/lib/format.ts`:

```ts
import type { EventKind, Meeting } from "./types";

export function meetingTitle(
  meeting: Pick<Meeting, "title" | "city" | "meeting_type">
): string {
  const explicit = meeting.title?.trim();
  if (explicit) return explicit;
  return [meeting.city, meeting.meeting_type]
    .filter((part): part is string => Boolean(part?.trim()))
    .join(" ");
}

const EVENT_KIND_LABELS: Record<EventKind, string> = {
  council: "Council",
  school_board: "School board",
  debate: "Debate",
  forum: "Forum",
  community_meeting: "Community meeting",
  news_clip: "News clip",
  other: "Other",
};

export function eventKindLabel(kind: EventKind): string {
  return EVENT_KIND_LABELS[kind];
}
```

Move the new imports to the top of the file.

- [ ] **Step 4: Use the helpers on both pages**

In `web/app/page.tsx`, import:

```ts
import {
  eventKindLabel,
  formatMeetingDate,
  meetingTitle,
} from "@/lib/format";
```

Replace:

```tsx
<span className="meetingTitle">
  {m.city} {m.meeting_type}
</span>
```

with:

```tsx
<span className="meetingTitle">{meetingTitle(m)}</span>
<span className="eventKind">{eventKindLabel(m.event_kind)}</span>
```

In `web/app/meetings/[meetingId]/page.tsx`, import the same helpers and replace the hardcoded city/type heading with:

```tsx
<h1>{meetingTitle(meeting)}</h1>
<span className="eventKind">{eventKindLabel(meeting.event_kind)}</span>
```

Keep the date rendering and all transcript/summary behavior unchanged.

- [ ] **Step 5: Exclude null cities from the search filter**

In `web/app/search/page.tsx`, replace the city list construction with:

```ts
cities = [
  ...new Set(
    meetings
      .map((meeting) => meeting.city)
      .filter((city): city is string => city !== null)
  ),
].sort();
```

This preserves city filtering for municipal events without rendering a null
option for statewide or cityless events.

- [ ] **Step 6: Add subtle label styling**

Append to `web/app/globals.css`:

```css
.eventKind {
  display: inline-flex;
  align-items: center;
  width: fit-content;
  padding: 0.15rem 0.45rem;
  border: 1px solid color-mix(in srgb, currentColor 24%, transparent);
  border-radius: 999px;
  font-size: 0.72rem;
  font-weight: 650;
  letter-spacing: 0.04em;
  line-height: 1.3;
  text-transform: uppercase;
  opacity: 0.72;
}
```

- [ ] **Step 7: Verify the web build**

Run:

```bash
cd web
npm run lint
npm run build
```

Expected: both pass. The build may render the existing temporary-unavailable fallback when no API is reachable, but it must not fail.

- [ ] **Step 8: Commit web display changes**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add web/lib/types.ts web/lib/queries.ts web/lib/format.ts web/app/page.tsx 'web/app/meetings/[meetingId]/page.tsx' web/app/search/page.tsx web/app/globals.css
git diff --cached --name-only
git diff --cached | rg -n "API_KEY|SECRET|TOKEN|PASSWORD|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY" && exit 1 || true
git commit -m "feat(web): display flexible event titles and kinds"
```

### Task 8: Complete cross-repository verification

**Files:**
- Verify all files changed in Tasks 1-7.

- [ ] **Step 1: Run the complete on-the-record test suite**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
python -m pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run backend verification**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend
npm test -- --run src/lib/meetingsService.test.ts src/routes/meetings.test.ts
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

- [ ] **Step 4: Verify migration order and repository safety**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
test -f backend/migrations/578_event_kinds.sql
git check-ignore .env .env.local
git diff --check
git status --short

cd /Users/chrisandrews/Documents/GitHub/on-the-record
git check-ignore .env .env.local web/.env.local
git diff --check
git status --short
```

Expected: Migration 578 exists; `.env` files are ignored; no secrets, environment files, generated build output, or unrelated dirty files are staged.

- [ ] **Step 5: Smoke-test the compatibility contract**

Construct or inspect these cases:

```text
Existing row: title=null, eventKind=council, city=Bloomington
Display: "Bloomington Regular Session"

Debate: title="California Governor Debate", eventKind=debate, city=null
Display: "California Governor Debate"

Untitled cityless event: title=null, eventKind=forum, city=null,
meetingType="Mayoral Candidate Forum"
Display: "Mayoral Candidate Forum"
```

Expected: no `"null"` or `"undefined"` appears in rendered titles, and existing council rows remain visually equivalent except for the subtle `Council` label.
