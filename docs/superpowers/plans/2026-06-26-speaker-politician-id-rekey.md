# Re-key Speaker → Politician on `politician_id` — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Switch the speaker→politician connection from `politician_slug` (NULL for 99.4% of `essentials.politicians`, including all candidates) to the stable `politician_id` UUID across the ev-accounts people/search API, the web `/people` layer, and the pipeline voice-profile enrollment key — so candidates like Steve Hilton link end-to-end and auto-propagate.

**Architecture:** `politician_id` already rides the pipeline, `meetings.speakers`, and the meeting API. This plan re-keys the two layers still hard-keyed on slug: (1) the public people layer (ev-accounts joins on `p.id = sp.politician_id`; web routes become `/people/<uuid>`); (2) the pipeline enrollment key (`essentials:<politician_id>`). Per-segment identity (appearances, search hits) resolves via a join to `meetings.speakers` (Approach J) — no `meetings.segments` schema change. Public URLs are the raw UUID. Prod public data is empty, so there is no migration/redirect on the web/API side; the profile DB re-keys via one `reenroll_profiles.py` run.

**Tech Stack:** ev-accounts: Express 4/5 + `pg` `pool.query` (no PostgREST), vitest + supertest. Web: Next.js 16 App Router static export (`output: "export"`). Pipeline: Python + pytest.

**Spec:** `docs/superpowers/specs/2026-06-26-speaker-politician-id-rekey-design.md`

---

## Working contexts & branches

This plan spans two repos. Set up both before starting:

- **ev-accounts** (Part A) — repo `~/Documents/GitHub/ev-accounts`, branch off `master`:
  ```bash
  cd ~/Documents/GitHub/ev-accounts && git checkout master && git pull && git checkout -b feat/people-rekey-politician-id
  ```
- **on-the-record** (Parts B + C) — the current worktree on branch
  `claude/magical-bhaskara-056787` (off `main`). All `web/` and `src/` work and
  commits happen here. Paths below are relative to the on-the-record repo root.

**Python rule (this repo):** always run Python via `.venv/bin/python`, never
system `python3` (system Python lacks project deps). All pytest commands below
use `.venv/bin/python -m pytest`.

**Pipeline-side identity rule:** `publish.py` already writes both
`politician_slug` and `politician_id` to `meetings.speakers`; the pipeline
mappings carry both. This plan changes only the *key* the enrollment profile is
filed under (slug → id); it never stops carrying slug.

---

## File Structure

**Part A — ev-accounts (`backend/src/`):**
- Modify `lib/peopleService.ts` — roster/profile join + appearances re-keyed to `politician_id`; drop `slug` from the payload.
- Modify `routes/people.ts` — `:slug` → `:id` with UUID validation; rename service calls.
- Modify `routes/people.test.ts` — id-keyed expectations.
- Modify `lib/searchService.ts` — join speakers for `politician_id`; `?speaker=` filter by id.
- Modify `routes/search.ts` — `speaker` param validated as UUID.
- Modify `routes/search.test.ts` — id-keyed speaker expectations.

**Part B — on-the-record web (`web/`):**
- Modify `lib/types.ts` — `MeetingSpeaker.politician_id`; `Person` single id field; `SearchResult.politician_id`.
- Modify `lib/queries.ts` — map `politicianId` on speakers + person; `fetchPerson(id)`/`fetchAppearances(id)`.
- Modify `app/people/page.tsx` — roster links/keys by `politician_id`.
- Rename `app/people/[slug]/` → `app/people/[id]/`; re-key `generateStaticParams` + lookups; fix the static-export sentinel.
- Modify `app/meetings/[meetingId]/MeetingView.tsx` — link from a `label→politician_id` map.
- Modify `app/search/page.tsx` + `app/search/SearchView.tsx` — speaker filter + hit links by `politician_id`.

**Part C — on-the-record pipeline (`src/`):**
- Modify `src/enroll.py` — `essentials:<politician_id>` key.
- Modify `tests/test_profile_v3.py` — id-keyed assertions.
- Operational: one `reenroll_profiles.py` run (migration).

---

# Part A — ev-accounts API (branch `feat/people-rekey-politician-id`)

### Task 1: peopleService — re-key roster/profile/appearances to `politician_id`

**Files:**
- Modify: `backend/src/lib/peopleService.ts`

(No unit test for this service — exercised via route tests with the service mocked, plus the smoke test in Task 4. Matches `meetingsService.ts`.)

- [ ] **Step 1: Drop `slug` from the `Person` interface, keep the UUID id**

In `backend/src/lib/peopleService.ts`, replace the `Person` interface (currently starts with `slug: string;` then `politicianId: string | null;`):

```typescript
export interface Person {
  politicianId: string;
  name: string;
  headshotUrl: string | null;
  party: string | null;
  officeTitle: string | null;
  district: string | null;
  jurisdiction: string | null;
  meetingCount: number;
  cities: string[];
  lastSpokeDate: string | null;
}
```

- [ ] **Step 2: Update `PersonRow` and `mapPerson`**

Replace the `PersonRow` interface's `slug` + `politician_id` lines so it no longer has `slug`:

```typescript
interface PersonRow {
  politician_id: string;
  name: string;
  headshot_url: string | null;
  party: string | null;
  bio_text: string | null;
  office_title: string | null;
  district: string | null;
  jurisdiction: string | null;
  meeting_count: string;
  cities: string[];
  last_spoke_date: string | null;
}
```

Replace `mapPerson` (drop the `slug` field):

```typescript
function mapPerson(row: PersonRow): Person {
  return {
    politicianId: row.politician_id,
    name: row.name,
    headshotUrl: row.headshot_url,
    party: row.party,
    officeTitle: row.office_title,
    district: row.district,
    jurisdiction: row.jurisdiction,
    meetingCount: Number(row.meeting_count),
    cities: row.cities,
    lastSpokeDate: row.last_spoke_date,
  };
}
```

(`mapPersonDetail` is unchanged — it spreads `mapPerson(row)` and adds `bioText`.)

- [ ] **Step 3: Re-key `PERSON_SELECT` + `PERSON_GROUP_BY` to id**

Replace the `PERSON_SELECT` and `PERSON_GROUP_BY` constants:

```typescript
// Shared SELECT for roster and profile. Keyed on essentials.politicians.id
// (politician_slug is NULL for ~99.4% of rows, incl. candidates). GROUP BY p.id
// is valid: it is the PK, so p.* columns are functionally dependent; the lateral
// office columns must be grouped explicitly.
const PERSON_SELECT = `
  SELECT
    p.id                                                                     AS politician_id,
    COALESCE(p.full_name, MAX(sp.display_name))                              AS name,
    COALESCE(NULLIF(p.photo_custom_url, ''), NULLIF(p.photo_origin_url, '')) AS headshot_url,
    p.party                                                                  AS party,
    p.bio_text                                                               AS bio_text,
    off.office_title,
    off.district,
    off.jurisdiction,
    COUNT(DISTINCT sp.meeting_id)                                            AS meeting_count,
    ARRAY_AGG(DISTINCT m.city)                                               AS cities,
    MAX(m.date)::text                                                        AS last_spoke_date
  FROM meetings.speakers sp
  JOIN meetings.meetings m ON m.id = sp.meeting_id
  LEFT JOIN essentials.politicians p ON p.id = sp.politician_id
  LEFT JOIN LATERAL (
    SELECT o.title AS office_title, d.label AS district, g.name AS jurisdiction
    FROM essentials.offices o
    LEFT JOIN essentials.districts d ON d.id = o.district_id
    LEFT JOIN essentials.chambers ch ON ch.id = o.chamber_id
    LEFT JOIN essentials.governments g ON g.id = ch.government_id
    WHERE o.politician_id = p.id AND o.is_vacant = false
    ORDER BY o.id
    LIMIT 1
  ) off ON true
`;

const PERSON_GROUP_BY = `
  GROUP BY p.id, off.office_title, off.district, off.jurisdiction
`;
```

- [ ] **Step 4: Re-key `getPeople` filter**

In `getPeople`, change the `WHERE` clause from `sp.politician_slug IS NOT NULL` to `sp.politician_id IS NOT NULL`:

```typescript
  const { rows } = await pool.query<PersonRow>(
    `${PERSON_SELECT}
     WHERE sp.politician_id IS NOT NULL
     ${cityClause}
     ${PERSON_GROUP_BY}
     ORDER BY name`,
    params
  );
```

- [ ] **Step 5: Rename `getPersonBySlug` → `getPersonById`**

Replace the function:

```typescript
export async function getPersonById(politicianId: string): Promise<PersonDetail | null> {
  const { rows } = await pool.query<PersonRow>(
    `${PERSON_SELECT}
     WHERE sp.politician_id = $1
     ${PERSON_GROUP_BY}`,
    [politicianId]
  );

  return rows.length > 0 ? mapPersonDetail(rows[0]) : null;
}
```

- [ ] **Step 6: Rename `getAppearancesBySlug` → `getAppearancesById`, join speakers**

Replace the function (the only change to the query is the `speakers` join + the `WHERE`):

```typescript
export async function getAppearancesById(politicianId: string): Promise<Appearance[]> {
  const { rows } = await pool.query<AppearanceRow>(
    `SELECT s.meeting_id, s.segment_index, s.start_time, s.end_time, s.text,
            m.city, m.meeting_type, m.date::text AS date, m.playback_kind
     FROM meetings.segments s
     JOIN meetings.speakers sp ON sp.id = s.speaker_id
     JOIN meetings.meetings m ON m.id = s.meeting_id
     WHERE sp.politician_id = $1
     ORDER BY m.date DESC, s.meeting_id, s.segment_index`,
    [politicianId]
  );

  const byMeeting = new Map<string, Appearance>();
  for (const row of rows) {
    let appearance = byMeeting.get(row.meeting_id);
    if (!appearance) {
      appearance = {
        meetingId: row.meeting_id,
        city: row.city,
        meetingType: row.meeting_type,
        date: row.date,
        playbackKind: row.playback_kind,
        segments: [],
      };
      byMeeting.set(row.meeting_id, appearance);
    }
    appearance.segments.push({
      segmentIndex: Number(row.segment_index),
      startTime: Number(row.start_time),
      endTime: Number(row.end_time),
      text: row.text,
    });
  }
  return [...byMeeting.values()];
}
```

- [ ] **Step 7: Update the service doc comment**

Replace the top-of-file comment's roster-source line:

```typescript
 * Roster source: DISTINCT politician_id from meetings.speakers, enriched
 * from essentials.politicians via the shared id. Speakers without a
 * politician_id (unidentified) are not listed.
```

- [ ] **Step 8: Typecheck**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck`
Expected: peopleService compiles, but `routes/people.ts` errors (`getPersonBySlug`/`getAppearancesBySlug` no longer exist) — fixed in Task 2. (If you want a clean typecheck here, do Task 2 first; the rename is intentional.)

### Task 2: people routes — `:slug` → `:id` with UUID validation (TDD)

**Files:**
- Modify: `backend/src/routes/people.test.ts`
- Modify: `backend/src/routes/people.ts`

- [ ] **Step 1: Rewrite the route tests for id-keying**

Replace the entire contents of `backend/src/routes/people.test.ts`:

```typescript
import { vi, describe, it, expect, beforeEach } from 'vitest';
import express from 'express';
import request from 'supertest';

const { mockGetPeople, mockGetPersonById, mockGetAppearancesById } = vi.hoisted(() => ({
  mockGetPeople: vi.fn(),
  mockGetPersonById: vi.fn(),
  mockGetAppearancesById: vi.fn(),
}));
vi.mock('../lib/peopleService.js', () => ({
  getPeople: mockGetPeople,
  getPersonById: mockGetPersonById,
  getAppearancesById: mockGetAppearancesById,
}));
vi.mock('../middleware/auth.js', () => ({
  optionalAuth: (_req: unknown, _res: unknown, next: () => void) => next(),
}));

import peopleRouter from './people.js';

const app = express();
app.use('/api/people', peopleRouter);

const POL_ID = '11111111-1111-1111-1111-111111111111';

const samplePerson = {
  politicianId: POL_ID,
  name: 'John Hamilton',
  headshotUrl: null,
  party: 'Democratic',
  officeTitle: 'Mayor',
  district: null,
  jurisdiction: 'Bloomington',
  meetingCount: 3,
  cities: ['Bloomington'],
  lastSpokeDate: '2026-02-18',
};

beforeEach(() => {
  mockGetPeople.mockReset();
  mockGetPersonById.mockReset();
  mockGetAppearancesById.mockReset();
});

describe('GET /api/people', () => {
  it('200 with the roster', async () => {
    mockGetPeople.mockResolvedValueOnce([samplePerson]);
    const res = await request(app).get('/api/people');
    expect(res.status).toBe(200);
    expect(res.body).toEqual([samplePerson]);
    expect(mockGetPeople).toHaveBeenCalledWith(undefined);
  });

  it('passes the city filter through', async () => {
    mockGetPeople.mockResolvedValueOnce([]);
    const res = await request(app).get('/api/people?city=Bloomington');
    expect(res.status).toBe(200);
    expect(mockGetPeople).toHaveBeenCalledWith({ city: 'Bloomington' });
  });
});

describe('GET /api/people/:id', () => {
  it('422 on a non-UUID id, service not called', async () => {
    const res = await request(app).get('/api/people/not-a-uuid');
    expect(res.status).toBe(422);
    expect(mockGetPersonById).not.toHaveBeenCalled();
  });

  it('404 when the person is unknown', async () => {
    mockGetPersonById.mockResolvedValueOnce(null);
    const res = await request(app).get(`/api/people/${POL_ID}`);
    expect(res.status).toBe(404);
  });

  it('200 with the person detail', async () => {
    mockGetPersonById.mockResolvedValueOnce({ ...samplePerson, bioText: 'Mayor since 2016.' });
    const res = await request(app).get(`/api/people/${POL_ID}`);
    expect(res.status).toBe(200);
    expect(res.body.bioText).toBe('Mayor since 2016.');
    expect(mockGetPersonById).toHaveBeenCalledWith(POL_ID);
  });
});

describe('GET /api/people/:id/appearances', () => {
  it('422 on a non-UUID id, service not called', async () => {
    const res = await request(app).get('/api/people/not-a-uuid/appearances');
    expect(res.status).toBe(422);
    expect(mockGetAppearancesById).not.toHaveBeenCalled();
  });

  it('200 with id and appearances', async () => {
    const appearance = {
      meetingId: '22222222-2222-2222-2222-222222222222',
      city: 'Bloomington',
      meetingType: 'City Council',
      date: '2026-02-18',
      playbackKind: 'youtube',
      segments: [{ segmentIndex: 4, startTime: 120.5, endTime: 150, text: 'Thank you.' }],
    };
    mockGetAppearancesById.mockResolvedValueOnce([appearance]);
    const res = await request(app).get(`/api/people/${POL_ID}/appearances`);
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ id: POL_ID, appearances: [appearance] });
    expect(mockGetAppearancesById).toHaveBeenCalledWith(POL_ID);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/people.test.ts`
Expected: FAIL — the route still imports `getPersonBySlug`/`getAppearancesBySlug` and validates with `SLUG_REGEX` (e.g. `not-a-uuid` passes the slug regex, so the 422 tests fail; the mocked names don't match).

- [ ] **Step 3: Rewrite the route for UUID + id**

Replace the entire contents of `backend/src/routes/people.ts`:

```typescript
/**
 * People routes — people who speak in published meetings.
 *
 * Serves the on-the-record web app's /people pages, and (being the
 * essentials backend) lets essentials render appearance cards from the
 * same endpoints.
 *
 * Public reads only: no auth required (optionalAuth). No write routes —
 * roster is derived from meetings.speakers, written by the pipeline.
 *
 * Keyed on essentials.politicians.id (UUID): politician_slug is NULL for
 * ~99.4% of rows (incl. all candidates), so id is the only viable key.
 *
 * Architecture rules enforced here (same as meetings.ts):
 *   - All DB access via peopleService (pool.query)
 *   - UUID validated before any DB lookup
 *   - Subpath route (/:id/appearances) defined BEFORE /:id
 */

import { Router } from 'express';
import type { Request, Response } from 'express';
import { optionalAuth } from '../middleware/auth.js';
import {
  getPeople,
  getPersonById,
  getAppearancesById,
} from '../lib/peopleService.js';

const router = Router();

const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

// GET /api/people
// Optional query: ?city=Bloomington
router.get('/', optionalAuth, async (req: Request, res: Response): Promise<void> => {
  const filters: { city?: string } = {};
  if (typeof req.query.city === 'string') filters.city = req.query.city;

  try {
    const people = await getPeople(Object.keys(filters).length > 0 ? filters : undefined);
    res.status(200).json(people);
  } catch (err) {
    console.error('[GET /people] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

// GET /api/people/:id/appearances — MUST be before /:id
router.get(
  '/:id/appearances',
  optionalAuth,
  async (req: Request, res: Response): Promise<void> => {
    const id = req.params.id as string;
    if (!UUID_REGEX.test(id)) {
      res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
      return;
    }

    try {
      const appearances = await getAppearancesById(id);
      res.status(200).json({ id, appearances });
    } catch (err) {
      console.error('[GET /people/:id/appearances] error:', err);
      res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
    }
  }
);

// GET /api/people/:id
router.get('/:id', optionalAuth, async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }

  try {
    const person = await getPersonById(id);
    if (!person) {
      res.status(404).json({ code: 'NOT_FOUND', message: 'Person not found' });
      return;
    }
    res.status(200).json(person);
  } catch (err) {
    console.error('[GET /people/:id] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

export default router;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/people.test.ts`
Expected: PASS (7 tests).

- [ ] **Step 5: Typecheck**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck`
Expected: clean (peopleService + routes now agree).

### Task 3: searchService + search route — re-key hits and the `?speaker=` filter (TDD)

**Files:**
- Modify: `backend/src/lib/searchService.ts`
- Modify: `backend/src/routes/search.ts`
- Modify: `backend/src/routes/search.test.ts`

- [ ] **Step 1: Update the search route tests for a UUID speaker**

Three precise edits in `backend/src/routes/search.test.ts`:

**(a)** In `sampleResponse.results[0]`, rename the identity field (currently `politicianSlug: 'john-hamilton',`):
```typescript
      politicianId: '11111111-1111-1111-1111-111111111111',
```

**(b)** The `422 when speaker is not a valid slug` case uses `speaker=Bad!Slug`, which is also not a UUID — keep its body, rename the title:
```typescript
  it('422 when speaker is not a valid politician id', async () => {
    const res = await request(app).get('/api/search?q=housing&speaker=Bad!Slug');
    expect(res.status).toBe(422);
    expect(mockSearchSegments).not.toHaveBeenCalled();
  });
```

**(c)** In `passes city, speaker, and page through`, the speaker `john-hamilton` is no longer valid — make it a UUID. Replace the request URL line and the `speaker:` expectation:
```typescript
    const res = await request(app).get(
      '/api/search?q=housing&city=Bloomington&speaker=33333333-3333-3333-3333-333333333333&page=3'
    );
    expect(res.status).toBe(200);
    expect(mockSearchSegments).toHaveBeenCalledWith({
      q: 'housing',
      city: 'Bloomington',
      speaker: '33333333-3333-3333-3333-333333333333',
      page: 3,
    });
```
(The `mockSearchSegments.mockResolvedValueOnce({ ...sampleResponse, page: 3 })` line above it stays.)

**(d)** Add a NEW discriminating case inside the `GET /api/search validation` describe block. A lowercase UUID coincidentally satisfies the old `SLUG_REGEX`, so without this case the route change wouldn't go red. A valid slug that is *not* a UUID must now be rejected:
```typescript
  it('422 when speaker is a valid slug but not a UUID', async () => {
    const res = await request(app).get('/api/search?q=housing&speaker=john-hamilton');
    expect(res.status).toBe(422);
    expect(mockSearchSegments).not.toHaveBeenCalled();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/search.test.ts`
Expected: FAIL on the new case **(d)** — the route still validates with `SLUG_REGEX`, so `speaker=john-hamilton` passes (200) when the test expects 422. Confirm that specific red before proceeding.

- [ ] **Step 3: Re-key `searchService.ts`**

In `backend/src/lib/searchService.ts`:

Rename the result identity field in the `SearchResult` interface:
```typescript
  politicianId: string | null;
```
(replacing `politicianSlug: string | null;`)

Rename in the `SearchRow` interface:
```typescript
  politician_id: string | null;
```
(replacing `politician_slug: string | null;`)

Update `mapResult`:
```typescript
    politicianId: row.politician_id,
```
(replacing `politicianSlug: row.politician_slug,`)

In `searchSegments`, change the speaker filter condition (currently `s.politician_slug = $${params.length}`):
```typescript
  if (speaker !== undefined) {
    params.push(speaker);
    conditions.push(`sp.politician_id = $${params.length}`);
  }
```

In the `hits` CTE query: add the speakers join and select `sp.politician_id`. Replace the `FROM ... WHERE` block and the inner/outer select of `politician_slug`:

```typescript
      `WITH hits AS (
         SELECT s.meeting_id, s.segment_index, s.start_time, s.end_time,
                s.speaker_name, sp.politician_id, s.text,
                ts_rank(s.tsv, websearch_to_tsquery('english', $1)) AS rank,
                m.city, m.meeting_type, m.date::text AS date
         FROM meetings.segments s
         JOIN meetings.meetings m ON m.id = s.meeting_id
         LEFT JOIN meetings.speakers sp ON sp.id = s.speaker_id
         WHERE ${where}
         ORDER BY rank DESC, m.date DESC, s.meeting_id, s.segment_index
         LIMIT $${limitParam} OFFSET $${offsetParam}
       )
       SELECT meeting_id, city, meeting_type, date, segment_index,
              start_time, end_time, speaker_name, politician_id,
              ts_headline('english', text, websearch_to_tsquery('english', $1),
                          'StartSel=[[[, StopSel=]]], MaxWords=40, MinWords=20') AS snippet
       FROM hits
       ORDER BY rank DESC, date DESC, meeting_id, segment_index`,
```

In the count query, add the same join so the `sp.politician_id` filter resolves:

```typescript
      `SELECT COUNT(*) AS count
       FROM meetings.segments s
       JOIN meetings.meetings m ON m.id = s.meeting_id
       LEFT JOIN meetings.speakers sp ON sp.id = s.speaker_id
       WHERE ${where}`,
```

- [ ] **Step 4: Re-key the search route's `speaker` validation**

In `backend/src/routes/search.ts`, add the UUID regex next to `SLUG_REGEX` (replace the `SLUG_REGEX` line):

```typescript
const UUID_REGEX = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
```

Update the speaker validation block:

```typescript
  let speaker: string | undefined;
  if (req.query.speaker !== undefined) {
    if (typeof req.query.speaker !== 'string' || !UUID_REGEX.test(req.query.speaker)) {
      res.status(422).json({ code: 'VALIDATION_ERROR', message: 'speaker must be a valid politician id' });
      return;
    }
    speaker = req.query.speaker;
  }
```

Update the route's example-URL comment to use a UUID `speaker=`.

- [ ] **Step 5: Run search tests to verify they pass**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/search.test.ts`
Expected: PASS.

- [ ] **Step 6: Typecheck**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck`
Expected: clean.

### Task 4: full suite, smoke test, commit (ev-accounts)

**Files:** none (verification + commit)

- [ ] **Step 1: Typecheck + full test suite**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck && npm test`
Expected: typecheck clean; all tests pass (people + search included, no regressions).

- [ ] **Step 2: Smoke test against the real DB**

```bash
cd ~/Documents/GitHub/ev-accounts/backend && npm run dev
# in another shell (substitute the actual PORT from backend/.env):
curl -s localhost:PORT/api/people | head -c 800
# pick a politicianId from that roster JSON, then:
curl -s localhost:PORT/api/people/<POLITICIAN_UUID> | head -c 800
curl -s localhost:PORT/api/people/<POLITICIAN_UUID>/appearances | head -c 800
curl -s "localhost:PORT/api/search?q=the&speaker=<POLITICIAN_UUID>" | head -c 800
```

Expected: roster is a JSON array; each item has `politicianId` (UUID) and NO `slug`. Person detail includes `bioText`. Appearances grouped by meeting with `segments`. Search with `?speaker=<uuid>` returns hits whose `politicianId` matches. **If the roster is empty**, that is expected until the pipeline links someone with a `politician_id` — verify with `SELECT politician_id, COUNT(*) FROM meetings.speakers WHERE politician_id IS NOT NULL GROUP BY 1` before debugging code.

- [ ] **Step 3: Commit (ev-accounts repo)**

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/lib/peopleService.ts backend/src/routes/people.ts backend/src/routes/people.test.ts backend/src/lib/searchService.ts backend/src/routes/search.ts backend/src/routes/search.test.ts && git commit -m "$(cat <<'EOF'
feat(api): re-key people + search on politician_id

Join essentials.politicians on p.id = sp.politician_id (slug is NULL for
~99.4% of rows). /api/people/:id is now a UUID; appearances and search hits
resolve identity via a join to meetings.speakers (no segments schema change).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

---

# Part B — on-the-record web (worktree, branch `claude/magical-bhaskara-056787`)

### Task 5: web types + queries — add `politician_id` to speakers, re-key people fetchers

**Files:**
- Modify: `web/lib/types.ts`
- Modify: `web/lib/queries.ts`

- [ ] **Step 1: Add `politician_id` to `MeetingSpeaker`**

In `web/lib/types.ts`, in the `MeetingSpeaker` interface, add a `politician_id` field right after `politician_slug`:

```typescript
  politician_slug: string | null;
  politician_id: string | null;
```

- [ ] **Step 2: Re-key the `Person` interface to a single id field**

In `web/lib/types.ts`, replace the `Person` interface's first two fields (`slug: string;` and `politician_id: string | null;`) so there is one identity field:

```typescript
export interface Person {
  politician_id: string;          // essentials.politicians UUID (the key + URL)
  name: string;
  headshot_url: string | null;
  party: string | null;
  office_title: string | null;
  district: string | null;
  jurisdiction: string | null;
  meeting_count: number;
  cities: string[];
  last_spoke_date: string | null; // YYYY-MM-DD
}
```

(`PersonDetail extends Person` is unchanged.)

- [ ] **Step 3: Re-key `SearchResult`**

In `web/lib/types.ts`, in the `SearchResult` interface replace `politician_slug: string | null;` with:

```typescript
  politician_id: string | null;
```

- [ ] **Step 4: Map `politician_id` on meeting speakers**

In `web/lib/queries.ts`, in `mapMeeting`'s speakers map, add the field after `politician_slug`:

```typescript
      politician_slug: sp.politicianSlug ?? null,
      politician_id: sp.politicianId ?? null,
```

- [ ] **Step 5: Drop `slug` from `mapPerson`**

In `web/lib/queries.ts`, replace `mapPerson` so it no longer references `slug`:

```typescript
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapPerson(p: any): Person {
  return {
    politician_id: p.politicianId,
    name: p.name,
    headshot_url: p.headshotUrl ?? null,
    party: p.party ?? null,
    office_title: p.officeTitle ?? null,
    district: p.district ?? null,
    jurisdiction: p.jurisdiction ?? null,
    meeting_count: p.meetingCount ?? 0,
    cities: p.cities ?? [],
    last_spoke_date: p.lastSpokeDate ?? null,
  };
}
```

- [ ] **Step 6: Rename the fetcher params slug → id**

In `web/lib/queries.ts`, replace `fetchPerson` and `fetchAppearances` (the path and param become an id; everything else identical):

```typescript
export async function fetchPerson(id: string): Promise<PersonDetail | null> {
  if (!BASE) return null;
  const res = await fetch(`${BASE}/api/people/${encodeURIComponent(id)}`, BUST);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`person fetch failed: ${res.status}`);
  const p = await res.json();
  return { ...mapPerson(p), bio_text: p.bioText ?? null };
}

export async function fetchAppearances(id: string): Promise<Appearance[]> {
  if (!BASE) return [];
  const res = await fetch(
    `${BASE}/api/people/${encodeURIComponent(id)}/appearances`,
    BUST
  );
  if (!res.ok) throw new Error(`appearances fetch failed: ${res.status}`);
  const { appearances } = (await res.json()) as { appearances: unknown[] };
  return appearances.map(mapAppearance);
}
```

- [ ] **Step 7: Typecheck (expected to surface page-level errors next)**

Run: `cd web && npx tsc --noEmit`
Expected: errors in `app/people/page.tsx`, `app/people/[slug]/page.tsx`, `app/search/*`, `MeetingView.tsx` (they still reference `p.slug` / `hit.politician_slug`) — fixed in Tasks 6–8. The types/queries file itself should be error-free.

- [ ] **Step 8: Commit**

```bash
git add web/lib/types.ts web/lib/queries.ts && git commit -m "$(cat <<'EOF'
feat(web): carry politician_id on speakers; re-key people fetchers to id

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

### Task 6: people roster page + `[slug]` → `[id]` profile route

**Files:**
- Modify: `web/app/people/page.tsx`
- Rename: `web/app/people/[slug]/` → `web/app/people/[id]/`
- Modify: `web/app/people/[id]/page.tsx`

- [ ] **Step 1: Re-key the roster page links/keys**

In `web/app/people/page.tsx`, replace the `<li>`/`<Link>` opener (currently `key={p.slug}` and `href={`/people/${p.slug}`}`):

```tsx
            <li key={p.politician_id}>
              <Link href={`/people/${p.politician_id}`} className="personCard">
```

- [ ] **Step 2: Rename the dynamic route folder**

Run:
```bash
git mv web/app/people/\[slug\] web/app/people/\[id\]
```
Expected: `web/app/people/[id]/page.tsx` now exists; `[slug]` is gone.

- [ ] **Step 3: Re-key the profile page to id + fix the static-export sentinel**

In `web/app/people/[id]/page.tsx`, replace `generateStaticParams` (the sentinel must be a *valid UUID* so the API returns 404→null, not a 422 that would throw at build):

```tsx
export async function generateStaticParams() {
  // Wrap in try/catch so builds succeed when EV_ACCOUNTS_URL is unset or the
  // API is unreachable (fetch throws TypeError: Invalid URL for relative paths).
  let people: Awaited<ReturnType<typeof fetchPeople>> = [];
  try {
    people = await fetchPeople();
  } catch {
    // API unavailable at build time — fall through to sentinel below.
  }
  // output:"export" fails the build when a dynamic route has zero params
  // (e.g., before the first person is linked). Emit one sentinel id — the nil
  // UUID — which is a valid UUID (so the API returns 404, not 422) and renders
  // 404, so empty-data builds still succeed.
  if (people.length === 0) return [{ id: "00000000-0000-0000-0000-000000000000" }];
  return people.map((p) => ({ id: p.politician_id }));
}
```

Replace the component signature + the params/fetch lines (currently destructures `slug` and calls `fetchPerson(slug)`/`fetchAppearances(slug)`):

```tsx
export default async function PersonPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const person = await fetchPerson(id);
  if (!person) notFound();
  const appearances = await fetchAppearances(id);
```

(The `ESSENTIALS_BASE` link already uses `person.politician_id` — unchanged. Everything else in the file is unchanged.)

- [ ] **Step 4: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: `app/people/*` errors gone; remaining errors only in `MeetingView.tsx` and `app/search/*` (Tasks 7–8).

- [ ] **Step 5: Commit**

```bash
git add web/app/people && git commit -m "$(cat <<'EOF'
feat(web): people pages keyed on politician_id; /people/<uuid> routes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

### Task 7: MeetingView — link transcript speakers by `politician_id`

**Files:**
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx`

- [ ] **Step 1: Build a `label → politician_id` map and re-key the local-name filter**

In `MeetingView.tsx`, the existing block builds `statusByLabel` and `localNameByLabel`. Replace the `localNameByLabel` comment+filter to key on `politician_id`, and add a `politicianIdByLabel` map right after `statusByLabel`:

```tsx
  // Build a label → politician_id map from the meeting's speaker list. The
  // meeting endpoint carries politician_id on speakers; segments do not.
  const politicianIdByLabel = new Map(
    (meeting.speakers ?? [])
      .filter((sp) => sp.politician_id)
      .map((sp) => [sp.label, sp.politician_id as string] as const)
  );

  // Build a label → local name map for non-roster speakers (local_slug set, no
  // politician identity). Rendered as plain text (no link) per D-08, D-09.
  const localNameByLabel = new Map(
    (meeting.speakers ?? [])
      .filter((sp) => sp.local_slug && !sp.politician_id)
      .map((sp) => [sp.label, sp.local_name ?? sp.display_name] as const)
  );
```

- [ ] **Step 2: Re-key the speaker link to use the map**

Replace the `<span className="speaker">…</span>` block (currently keyed on `seg.politician_slug`):

```tsx
              <span className="speaker">
                {politicianIdByLabel.get(seg.speaker_label) ? (
                  <Link
                    href={`/people/${politicianIdByLabel.get(seg.speaker_label)}`}
                    className="speakerLink"
                    title="View this person's appearances"
                  >
                    {seg.speaker_name || seg.speaker_label}
                  </Link>
                ) : localNameByLabel.get(seg.speaker_label) ? (
                  localNameByLabel.get(seg.speaker_label)
                ) : (
                  seg.speaker_name || seg.speaker_label
                )}
              </span>
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: `MeetingView.tsx` errors gone; only `app/search/*` remain.

- [ ] **Step 4: Commit**

```bash
git add web/app/meetings/\[meetingId\]/MeetingView.tsx && git commit -m "$(cat <<'EOF'
feat(web): link transcript speakers to /people/<politician_id>

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

### Task 8: search page + SearchView — speaker filter and hit links by `politician_id`

**Files:**
- Modify: `web/app/search/page.tsx`
- Modify: `web/app/search/SearchView.tsx`

- [ ] **Step 1: Build speaker options from `politician_id`**

In `web/app/search/page.tsx`, change the `speakers` type and mapping. Replace the declaration line and the `speakers = ...` block:

```tsx
  let speakers: { id: string; name: string }[] = [];
```
```tsx
    speakers = people
      .map((p) => ({ id: p.politician_id, name: p.name }))
      .sort((a, b) => a.name.localeCompare(b.name));
```

- [ ] **Step 2: Re-key `SpeakerOption`, the dropdown, the result mapper, and the hit link**

In `web/app/search/SearchView.tsx`:

Replace the `SpeakerOption` interface:
```tsx
interface SpeakerOption {
  id: string;
  name: string;
}
```

In `mapResult`, replace the `politician_slug` line:
```tsx
    politician_id: r.politicianId ?? null,
```

Replace the speaker `<select>`'s options map (currently `key={s.slug} value={s.slug}`):
```tsx
          {speakers.map((s) => (
            <option key={s.id} value={s.id}>
              {s.name}
            </option>
          ))}
```

Replace the per-hit speaker link block (currently keyed on `hit.politician_slug`):
```tsx
                    {hit.speaker_name &&
                      (hit.politician_id ? (
                        <Link
                          href={`/people/${hit.politician_id}`}
                          className="speakerLink searchSpeaker"
                        >
                          {hit.speaker_name}
                        </Link>
                      ) : (
                        <span className="searchSpeaker">{hit.speaker_name}</span>
                      ))}
```

(The `urlSpeaker` value now flows to the API `?speaker=` as a UUID — the ev-accounts route validates it. No other SearchView logic changes.)

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: clean — all web references to `politician_slug`/`slug` for linking are gone.

- [ ] **Step 4: Commit**

```bash
git add web/app/search/page.tsx web/app/search/SearchView.tsx && git commit -m "$(cat <<'EOF'
feat(web): search speaker filter + hit links keyed on politician_id

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

### Task 9: web build verification

**Files:** none (verification)

- [ ] **Step 1: Build the static site against the local backend**

With the ev-accounts dev server (Task 4) running, substitute its PORT:

```bash
cd web && EV_ACCOUNTS_URL=http://localhost:PORT npm run build
```

Expected: build succeeds. If the roster has linked people, the route list shows one `/people/<uuid>` per person; if empty, it shows the single nil-UUID sentinel route. Either way the build must not error (the sentinel is a valid UUID → 404, not a 422 throw).

- [ ] **Step 2: Verify built pages (only if roster is non-empty)**

Serve `web/out` (e.g. `npx serve web/out`) and confirm by loading pages:
1. `/people` grid renders; each card links to `/people/<uuid>`.
2. A profile page shows office info, the essentials.city link, and appearances grouped by meeting.
3. On a meeting page, an identified speaker's name links to `/people/<uuid>`.
4. On `/search`, the speaker dropdown filters results, and a hit's speaker name links to `/people/<uuid>`.

(If the roster is empty in the local DB, this step is a no-op — the empty-data path was verified in Step 1. Real-data verification happens post-deploy, Task 11.)

### Task 10 lives in Part C. Web work ends here.

---

# Part C — on-the-record pipeline (worktree, branch `claude/magical-bhaskara-056787`)

### Task 10: enroll.py — re-key the profile to `essentials:<politician_id>` (TDD)

**Files:**
- Modify: `tests/test_profile_v3.py`
- Modify: `src/enroll.py`

The existing tests assert `essentials:<slug>` profile keys; re-keying to id makes them the spec for the new behavior. Update the tests first (red), then change `enroll.py` (green).

- [ ] **Step 1: Update the profile-key assertions to id**

In `tests/test_profile_v3.py`, change every `essentials:<slug>` key literal to its `essentials:<politician_id>` form. The fixtures use `politician_slug="isabel-piedmont-smith"` / `politician_id="uuid-ips"` and `politician_slug="jane-adams"` / `politician_id="uuid-ja"`. Apply these substitutions throughout the file:

- `"essentials:isabel-piedmont-smith"` → `"essentials:uuid-ips"`
- `db.profiles["essentials:isabel-piedmont-smith"]` → `db.profiles["essentials:uuid-ips"]`
- `saved["db"].profiles["essentials:isabel-piedmont-smith"]` → `saved["db"].profiles["essentials:uuid-ips"]`
- `"essentials:jane-adams"` → `"essentials:uuid-ja"`
- `db.profiles["essentials:jane-adams"]` → `db.profiles["essentials:uuid-ja"]`

The identity-field assertions stay (`profile.politician_slug == "isabel-piedmont-smith"`, `profile.politician_id == "uuid-ips"`, etc.) — slug is still *carried*, just not the key. In `test_reenroll_promotes_to_essentials_key`, also update its docstring to say it promotes to `essentials:<politician_id>` keys.

Use a targeted find/replace; then read the file to confirm no `essentials:<word-with-hyphens>` literals remain except inside string-equality checks of `politician_slug`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_profile_v3.py -v`
Expected: FAIL — `enroll.py` still keys on slug, so `essentials:uuid-ips`/`essentials:uuid-ja` are not in `db.profiles` (the old slug keys are).

- [ ] **Step 3: Re-key `resolve_mapping_enrollment`**

In `src/enroll.py`, replace the `essentials:` branch of `resolve_mapping_enrollment` (currently `if mapping.politician_slug:` returning `f"essentials:{mapping.politician_slug}"`):

```python
    if mapping.politician_id:
        # Key on the stable UUID — politician_slug is NULL for ~99.4% of
        # essentials.politicians (incl. all candidates). Slug is still carried
        # for downstream display/publish, just not used as the key.
        return f"essentials:{mapping.politician_id}", mapping.politician_slug, mapping.politician_id
    if mapping.local_slug:
        # Key local people (incl. unidentified handles) by their stable slug, not
        # the typed name — so identical labels in different meetings never merge.
        return f"local:{mapping.local_slug}", None, None
    return resolve_enrollment_key(mapping.speaker_name, roster)
```

- [ ] **Step 4: Re-key the roster branch of `resolve_enrollment_key`**

In `src/enroll.py`, replace the roster-match branch (currently `if member.politician_slug:` returning `f"essentials:{member.politician_slug}"`):

```python
        corrected = correct_speaker_name(display_name, roster)
        for member in roster.members:
            if corrected == member.name:
                if member.politician_id:
                    return (
                        f"essentials:{member.politician_id}",
                        member.politician_slug,
                        member.politician_id,
                    )
                break
```

Also update the function's docstring line `key = 'essentials:<politician_slug>'` → `key = 'essentials:<politician_id>'`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_profile_v3.py -v`
Expected: PASS.

- [ ] **Step 6: Run the broader enrollment/identify suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_profile_v3.py tests/test_speaker_status.py -v`
Expected: PASS. (If `test_speaker_status.py` asserts any `essentials:<slug>` key, apply the same id substitution there and re-run.)

- [ ] **Step 7: Commit**

```bash
git add src/enroll.py tests/test_profile_v3.py tests/test_speaker_status.py && git commit -m "$(cat <<'EOF'
feat(pipeline): key voice profiles on essentials:<politician_id>

The essentials branch in enroll.py now keys on politician_id, not slug, so a
manually-linked or roster-matched candidate (slug NULL) anchors to their voice
profile and propagates via Wire-1. local: and name fallbacks unchanged.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

(If `tests/test_speaker_status.py` was not modified, drop it from the `git add` line.)

### Task 11: migration — re-key the profile DB, then deploy + smoke

**Files:** none (operational)

- [ ] **Step 1: Re-key the profile DB via reenroll**

`reenroll_profiles.py` rebuilds the profile DB from each meeting's
`transcript_named.json`, reconstructing `SpeakerMapping`s (which round-trip
`politician_id`) and enrolling under the new id-keyed `resolve_mapping_enrollment`.
This re-keys every essentials profile from `essentials:<slug>` to
`essentials:<politician_id>` and collapses duplicates for the same id.

Run (per the project's normal reenroll procedure — it re-extracts embeddings, so
expect it to be heavy):
```bash
.venv/bin/python reenroll_profiles.py
```
Expected: completes; the rebuilt DB's essentials profiles are keyed
`essentials:<uuid>`. Spot-check by loading the DB and confirming no remaining
`essentials:<non-uuid>` keys.

- [ ] **Step 2: Open the two PRs**

- ev-accounts: PR from `feat/people-rekey-politician-id` → `master`.
- on-the-record: PR from `claude/magical-bhaskara-056787` → `main` (web + pipeline).

- [ ] **Step 3: Deploy in order and smoke-check**

1. Deploy ev-accounts backend first (id-keyed endpoints + UUID validation must be live before the site builds).
2. Pipeline: deploy `enroll.py`; the reenroll from Step 1 has already re-keyed the profile DB. New review links now anchor on id.
3. Trigger a Render rebuild of the static site (`on-the-record-web`); `generateStaticParams` reads the id-keyed roster and emits `/people/<uuid>` routes.
4. Smoke-check in prod: a candidate with id-but-no-slug (e.g. Steve Hilton) appears in `/people`, his transcript speaker name links through to `/people/<uuid>`, and his profile renders appearances.

---

## Notes / out of scope (per the spec)

- `meetings.local_people.politician_slug` linking is unchanged (separate flow; local people render as plain text).
- `essentialsBodiesService.ts` exposes essentials' own `p.slug` for body rosters — a different feature, untouched.
- `meetings.segments` schema and `publish.py` are untouched (consequence of Approach J).
- `meetingsService.ts` is untouched — it already returns `politicianId` on speakers.
- Pretty (name-derived) URLs are deferred; the UUID is the v1 URL.
