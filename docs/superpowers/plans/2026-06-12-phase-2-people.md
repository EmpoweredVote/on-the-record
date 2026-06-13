# Phase 2 — People Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** People roster (`/people`) and profile pages (`/people/[slug]`) with appearances deep-linking into meeting transcripts, backed by three new public API endpoints in ev-accounts.

**Architecture:** Cross-repo. The **ev-accounts** repo (`~/Documents/GitHub/ev-accounts`, Express + pg) gains `peopleService.ts` + `routes/people.ts` serving `/api/people`, `/api/people/:slug`, `/api/people/:slug/appearances` — roster derived from `meetings.speakers.politician_slug`, enriched from `essentials.politicians` by shared slug. The **on-the-record** repo's `web/` Next.js static-export app gains roster + profile pages that fetch these endpoints at build time, plus speaker-name links from transcripts to profiles.

**Tech Stack:** Express 4/5 + pg pool.query (no PostgREST), vitest + supertest, Next.js 16 App Router static export (`output: "export"`).

**Conventions that MUST be followed (from each codebase):**
- ev-accounts `meetings.*`/`essentials.*` access is `pool.query()` ONLY — never the Supabase service-role client.
- Response objects from explicit field whitelists; NEVER spread DB rows.
- pg returns bigint/numeric as strings — `Number()` on counts, `segment_index`, `start_time`, `end_time`.
- Public reads use `optionalAuth`; subpath routes (`/:slug/appearances`) defined BEFORE `/:slug`.
- Web types are snake_case; `web/lib/queries.ts` maps camelCase API fields → snake_case via explicit mappers.

---

## Part A — ev-accounts API (work in `~/Documents/GitHub/ev-accounts/backend`)

### Task 1: peopleService

**Files:**
- Create: `backend/src/lib/peopleService.ts`

(No unit test for this file — DB-backed services in this codebase are exercised via route tests with the service mocked, plus a manual smoke test in Task 3. This matches `meetingsService.ts`.)

- [ ] **Step 1: Write the service**

```typescript
/**
 * peopleService — people who speak in published meetings.
 *
 * Roster source: DISTINCT politician_slug from meetings.speakers, enriched
 * from essentials.politicians via the shared slug. Speakers without a
 * politician_slug (unidentified) are not listed.
 *
 * Same architecture rules as meetingsService:
 *   - meetings.* and essentials.* are NOT PostgREST-exposed; pool.query() only
 *   - explicit field whitelists, never spread rows
 *   - Number() on bigint/numeric: meeting_count, segment_index, start/end_time
 */

import { pool } from './db.js';

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface Person {
  slug: string;
  politicianId: string | null;
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

export interface PersonDetail extends Person {
  bioText: string | null;
}

export interface AppearanceSegment {
  segmentIndex: number;
  startTime: number;
  endTime: number;
  text: string;
}

export interface Appearance {
  meetingId: string;
  city: string;
  meetingType: string;
  date: string;
  playbackKind: string | null;
  segments: AppearanceSegment[];
}

// ---------------------------------------------------------------------------
// Row types
// ---------------------------------------------------------------------------

interface PersonRow {
  slug: string;
  politician_id: string | null;
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

interface AppearanceRow {
  meeting_id: string;
  segment_index: string;
  start_time: string;
  end_time: string;
  text: string;
  city: string;
  meeting_type: string;
  date: string;
  playback_kind: string | null;
}

// ---------------------------------------------------------------------------
// Mappers (explicit camelCase — NEVER spread rows)
// ---------------------------------------------------------------------------

function mapPerson(row: PersonRow): Person {
  return {
    slug: row.slug,
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

function mapPersonDetail(row: PersonRow): PersonDetail {
  return { ...mapPerson(row), bioText: row.bio_text };
}

// ---------------------------------------------------------------------------
// Queries
// ---------------------------------------------------------------------------

// Shared SELECT for roster and profile. GROUP BY (slug, p.id) is valid:
// p.id is essentials.politicians' PK, so p.* columns are functionally
// dependent; the lateral office columns must be grouped explicitly.
const PERSON_SELECT = `
  SELECT
    sp.politician_slug                                                       AS slug,
    p.id                                                                     AS politician_id,
    COALESCE(p.full_name, MAX(sp.display_name), sp.politician_slug)          AS name,
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
  LEFT JOIN essentials.politicians p ON p.slug = sp.politician_slug
  LEFT JOIN LATERAL (
    SELECT o.title AS office_title, d.label AS district, g.name AS jurisdiction
    FROM essentials.offices o
    LEFT JOIN essentials.districts d ON d.id = o.district_id
    LEFT JOIN essentials.chambers ch ON ch.id = o.chamber_id
    LEFT JOIN essentials.governments g ON g.id = ch.government_id
    WHERE o.politician_id = p.id AND o.is_vacant = false
    LIMIT 1
  ) off ON true
`;

const PERSON_GROUP_BY = `
  GROUP BY sp.politician_slug, p.id, off.office_title, off.district, off.jurisdiction
`;

export async function getPeople(filters?: { city?: string }): Promise<Person[]> {
  const params: string[] = [];
  let cityClause = '';
  if (filters?.city !== undefined) {
    params.push(filters.city);
    cityClause = `AND m.city = $${params.length}`;
  }

  const { rows } = await pool.query<PersonRow>(
    `${PERSON_SELECT}
     WHERE sp.politician_slug IS NOT NULL
     ${cityClause}
     ${PERSON_GROUP_BY}
     ORDER BY name`,
    params
  );

  return rows.map(mapPerson);
}

export async function getPersonBySlug(slug: string): Promise<PersonDetail | null> {
  const { rows } = await pool.query<PersonRow>(
    `${PERSON_SELECT}
     WHERE sp.politician_slug = $1
     ${PERSON_GROUP_BY}`,
    [slug]
  );

  return rows.length > 0 ? mapPersonDetail(rows[0]) : null;
}

export async function getAppearancesBySlug(slug: string): Promise<Appearance[]> {
  const { rows } = await pool.query<AppearanceRow>(
    `SELECT s.meeting_id, s.segment_index, s.start_time, s.end_time, s.text,
            m.city, m.meeting_type, m.date::text AS date, m.playback_kind
     FROM meetings.segments s
     JOIN meetings.meetings m ON m.id = s.meeting_id
     WHERE s.politician_slug = $1
     ORDER BY m.date DESC, s.meeting_id, s.segment_index`,
    [slug]
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

- [ ] **Step 2: Typecheck**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck`
Expected: no errors (route + tests come next; service compiles standalone).

### Task 2: people routes (TDD)

**Files:**
- Create: `backend/src/routes/people.test.ts`
- Create: `backend/src/routes/people.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
import { vi, describe, it, expect, beforeEach } from 'vitest';
import express from 'express';
import request from 'supertest';

const { mockGetPeople, mockGetPersonBySlug, mockGetAppearancesBySlug } = vi.hoisted(() => ({
  mockGetPeople: vi.fn(),
  mockGetPersonBySlug: vi.fn(),
  mockGetAppearancesBySlug: vi.fn(),
}));
vi.mock('../lib/peopleService.js', () => ({
  getPeople: mockGetPeople,
  getPersonBySlug: mockGetPersonBySlug,
  getAppearancesBySlug: mockGetAppearancesBySlug,
}));
vi.mock('../middleware/auth.js', () => ({
  optionalAuth: (_req: unknown, _res: unknown, next: () => void) => next(),
}));

import peopleRouter from './people.js';

const app = express();
app.use('/api/people', peopleRouter);

const samplePerson = {
  slug: 'john-hamilton',
  politicianId: '11111111-1111-1111-1111-111111111111',
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
  mockGetPersonBySlug.mockReset();
  mockGetAppearancesBySlug.mockReset();
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

describe('GET /api/people/:slug', () => {
  it('422 on an invalid slug, service not called', async () => {
    const res = await request(app).get('/api/people/Bad!Slug');
    expect(res.status).toBe(422);
    expect(mockGetPersonBySlug).not.toHaveBeenCalled();
  });

  it('404 when the person is unknown', async () => {
    mockGetPersonBySlug.mockResolvedValueOnce(null);
    const res = await request(app).get('/api/people/nobody-here');
    expect(res.status).toBe(404);
  });

  it('200 with the person detail', async () => {
    mockGetPersonBySlug.mockResolvedValueOnce({ ...samplePerson, bioText: 'Mayor since 2016.' });
    const res = await request(app).get('/api/people/john-hamilton');
    expect(res.status).toBe(200);
    expect(res.body.bioText).toBe('Mayor since 2016.');
    expect(mockGetPersonBySlug).toHaveBeenCalledWith('john-hamilton');
  });
});

describe('GET /api/people/:slug/appearances', () => {
  it('422 on an invalid slug, service not called', async () => {
    const res = await request(app).get('/api/people/Bad!Slug/appearances');
    expect(res.status).toBe(422);
    expect(mockGetAppearancesBySlug).not.toHaveBeenCalled();
  });

  it('200 with slug and appearances', async () => {
    const appearance = {
      meetingId: '22222222-2222-2222-2222-222222222222',
      city: 'Bloomington',
      meetingType: 'City Council',
      date: '2026-02-18',
      playbackKind: 'youtube',
      segments: [{ segmentIndex: 4, startTime: 120.5, endTime: 150, text: 'Thank you.' }],
    };
    mockGetAppearancesBySlug.mockResolvedValueOnce([appearance]);
    const res = await request(app).get('/api/people/john-hamilton/appearances');
    expect(res.status).toBe(200);
    expect(res.body).toEqual({ slug: 'john-hamilton', appearances: [appearance] });
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/people.test.ts`
Expected: FAIL — `Cannot find module './people.js'`

- [ ] **Step 3: Write the route**

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
 * Architecture rules enforced here (same as meetings.ts):
 *   - All DB access via peopleService (pool.query)
 *   - Slug validated before any DB lookup
 *   - Subpath route (/:slug/appearances) defined BEFORE /:slug
 */

import { Router } from 'express';
import type { Request, Response } from 'express';
import { optionalAuth } from '../middleware/auth.js';
import {
  getPeople,
  getPersonBySlug,
  getAppearancesBySlug,
} from '../lib/peopleService.js';

const router = Router();

// Pipeline slugs are kebab-case; cap length defensively.
const SLUG_REGEX = /^[a-z0-9][a-z0-9_-]{0,99}$/;

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

// GET /api/people/:slug/appearances — MUST be before /:slug
router.get(
  '/:slug/appearances',
  optionalAuth,
  async (req: Request, res: Response): Promise<void> => {
    const slug = req.params.slug as string;
    if (!SLUG_REGEX.test(slug)) {
      res.status(422).json({ code: 'INVALID_SLUG', message: 'Invalid slug format' });
      return;
    }

    try {
      const appearances = await getAppearancesBySlug(slug);
      res.status(200).json({ slug, appearances });
    } catch (err) {
      console.error('[GET /people/:slug/appearances] error:', err);
      res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
    }
  }
);

// GET /api/people/:slug
router.get('/:slug', optionalAuth, async (req: Request, res: Response): Promise<void> => {
  const slug = req.params.slug as string;
  if (!SLUG_REGEX.test(slug)) {
    res.status(422).json({ code: 'INVALID_SLUG', message: 'Invalid slug format' });
    return;
  }

  try {
    const person = await getPersonBySlug(slug);
    if (!person) {
      res.status(404).json({ code: 'NOT_FOUND', message: 'Person not found' });
      return;
    }
    res.status(200).json(person);
  } catch (err) {
    console.error('[GET /people/:slug] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

export default router;
```

Note: `SLUG_REGEX` is lowercase-only (no `i` flag) — `Bad!Slug` must fail it both for the `!` and the capitals.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/people.test.ts`
Expected: PASS (7 tests)

### Task 3: register router, smoke test, commit

**Files:**
- Modify: `backend/src/index.ts` (import block ~line 48; mount block ~line 161)

- [ ] **Step 1: Register the router**

In `backend/src/index.ts`, next to the meetings import (line ~48):

```typescript
import peopleRouter from './routes/people.js';
```

Next to the meetings mount (line ~161):

```typescript
app.use('/api/people', peopleRouter);
```

- [ ] **Step 2: Typecheck + full test suite**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck && npm test`
Expected: typecheck clean; all tests pass (people tests included, no regressions).

- [ ] **Step 3: Smoke test against the real DB**

The backend dev server uses the repo's existing `.env` (port = `PORT` env var; check `backend/.env`).

```bash
cd ~/Documents/GitHub/ev-accounts/backend && npm run dev
# in another shell (substitute the actual port):
curl -s localhost:PORT/api/people | head -c 600
curl -s localhost:PORT/api/people/SOME-SLUG-FROM-ROSTER | head -c 600
curl -s localhost:PORT/api/people/SOME-SLUG-FROM-ROSTER/appearances | head -c 600
```

Expected: roster JSON array with at least one person (the pipeline has published meetings with `politician_slug` set); person detail includes `bioText`; appearances grouped by meeting with `segments`. If the roster is empty, check `SELECT politician_slug, COUNT(*) FROM meetings.speakers GROUP BY 1` before debugging code.

- [ ] **Step 4: Commit (ev-accounts repo)**

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/lib/peopleService.ts backend/src/routes/people.ts backend/src/routes/people.test.ts backend/src/index.ts && git commit -m "feat(api): people endpoints — roster, profile, appearances"
```

---

## Part B — web pages (work in `~/Documents/GitHub/on-the-record`)

> **Before starting:** this repo has uncommitted in-flight changes (the ev-accounts API migration touching `web/lib/queries.ts`, `web/lib/types.ts`, `src/publish.py`, etc.). Commit that work first as its own commit so people-page changes don't mix with it:
> `git add -A && git commit -m "feat(web): switch site to ev-accounts API + static export"` — review `git status` / `git diff` before committing; if anything looks unfinished, ask the user instead of committing blind.

### Task 4: types + queries

**Files:**
- Modify: `web/lib/types.ts` (append)
- Modify: `web/lib/queries.ts` (append)

- [ ] **Step 1: Add types** (append to `web/lib/types.ts`)

```typescript
export interface Person {
  slug: string;
  politician_id: string | null;   // essentials.politicians UUID
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

export interface PersonDetail extends Person {
  bio_text: string | null;
}

export interface AppearanceSegment {
  segment_id: number;             // segmentIndex from ev-accounts
  start_time: number;
  end_time: number;
  text: string;
}

export interface Appearance {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;           // YYYY-MM-DD
  playback_kind: string | null;
  segments: AppearanceSegment[];
}
```

- [ ] **Step 2: Add fetchers** (append to `web/lib/queries.ts`; extend the existing type import to `import type { Meeting, Segment, Person, PersonDetail, Appearance } from "./types";`)

```typescript
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapPerson(p: any): Person {
  return {
    slug: p.slug,
    politician_id: p.politicianId ?? null,
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

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapAppearance(a: any): Appearance {
  return {
    meeting_id: a.meetingId,
    city: a.city,
    meeting_type: a.meetingType,
    meeting_date: a.date,
    playback_kind: a.playbackKind ?? null,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    segments: (a.segments as any[]).map((s) => ({
      segment_id: s.segmentIndex,
      start_time: s.startTime,
      end_time: s.endTime,
      text: s.text,
    })),
  };
}

export async function fetchPeople(): Promise<Person[]> {
  const res = await fetch(`${BASE}/api/people`);
  if (!res.ok) throw new Error(`people fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapPerson);
}

export async function fetchPerson(slug: string): Promise<PersonDetail | null> {
  const res = await fetch(`${BASE}/api/people/${slug}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`person fetch failed: ${res.status}`);
  const p = await res.json();
  return { ...mapPerson(p), bio_text: p.bioText ?? null };
}

export async function fetchAppearances(slug: string): Promise<Appearance[]> {
  const res = await fetch(`${BASE}/api/people/${slug}/appearances`);
  if (!res.ok) throw new Error(`appearances fetch failed: ${res.status}`);
  const { appearances } = (await res.json()) as { appearances: unknown[] };
  return appearances.map(mapAppearance);
}
```

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 4: Commit**

```bash
git add web/lib/types.ts web/lib/queries.ts && git commit -m "feat(web): people types and ev-accounts fetchers"
```

### Task 5: /people roster page

**Files:**
- Create: `web/app/people/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
import Link from "next/link";
import { fetchPeople } from "@/lib/queries";
import type { Person } from "@/lib/types";

export const metadata = { title: "People — On the Record" };

export default async function PeoplePage() {
  let people: Person[] = [];
  let loadError = false;
  try {
    people = await fetchPeople();
  } catch {
    // Don't take the whole site down (or fail CI builds) on an API hiccup.
    loadError = true;
  }

  return (
    <main className="indexPage">
      <h1>People</h1>
      <p className="tagline">
        Everyone identified speaking in published meetings, linked to every
        moment they spoke.
      </p>
      {loadError ? (
        <p>People are temporarily unavailable. Please try again shortly.</p>
      ) : people.length === 0 ? (
        <p>No identified speakers yet.</p>
      ) : (
        <ul className="peopleGrid">
          {people.map((p) => (
            <li key={p.slug}>
              <Link href={`/people/${p.slug}`} className="personCard">
                {p.headshot_url ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img className="personPhoto" src={p.headshot_url} alt="" />
                ) : (
                  <span className="personPhoto personPhotoFallback" aria-hidden>
                    {p.name.charAt(0)}
                  </span>
                )}
                <span className="personName">{p.name}</span>
                {p.office_title && (
                  <span className="personOffice">
                    {p.office_title}
                    {p.district ? `, ${p.district}` : ""}
                  </span>
                )}
                <span className="personMeta">
                  {p.meeting_count} meeting{p.meeting_count === 1 ? "" : "s"}
                  {p.cities.length > 0 ? ` · ${p.cities.join(", ")}` : ""}
                </span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
```

- [ ] **Step 2: Add cross-nav from the home page**

In `web/app/page.tsx`, directly after the `<p className="tagline">…</p>` element, add:

```tsx
      <nav className="siteNav">
        <Link href="/people">People →</Link>
      </nav>
```

(`Link` is already imported there.)

- [ ] **Step 3: Commit**

```bash
git add web/app/people/page.tsx web/app/page.tsx && git commit -m "feat(web): people roster page"
```

### Task 6: /people/[slug] profile page

**Files:**
- Create: `web/app/people/[slug]/page.tsx`

- [ ] **Step 1: Write the page**

```tsx
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchAppearances, fetchPeople, fetchPerson } from "@/lib/queries";

export const dynamicParams = false;

export async function generateStaticParams() {
  const people = await fetchPeople();
  return people.map((p) => ({ slug: p.slug }));
}

// essentials.city politician profiles are /politician/<uuid>
const ESSENTIALS_BASE = "https://essentials.city";

function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}

export default async function PersonPage({
  params,
}: {
  params: Promise<{ slug: string }>;
}) {
  const { slug } = await params;
  const person = await fetchPerson(slug);
  if (!person) notFound();
  const appearances = await fetchAppearances(slug);

  return (
    <main className="indexPage personPage">
      <Link href="/people" className="backLink">
        ← All people
      </Link>
      <header className="personHeader">
        {person.headshot_url ? (
          // eslint-disable-next-line @next/next/no-img-element
          <img className="personPhoto large" src={person.headshot_url} alt="" />
        ) : (
          <span className="personPhoto large personPhotoFallback" aria-hidden>
            {person.name.charAt(0)}
          </span>
        )}
        <div>
          <h1>{person.name}</h1>
          <p className="personOffice">
            {[person.office_title, person.district, person.jurisdiction]
              .filter(Boolean)
              .join(" · ")}
            {person.party ? ` · ${person.party}` : ""}
          </p>
          {person.politician_id && (
            <a
              className="sourceLink"
              href={`${ESSENTIALS_BASE}/politician/${person.politician_id}`}
              target="_blank"
              rel="noreferrer"
            >
              Full profile on essentials.city ↗
            </a>
          )}
        </div>
      </header>
      {person.bio_text && <p className="personBio">{person.bio_text}</p>}

      <h2>Appearances</h2>
      {appearances.length === 0 ? (
        <p>No appearances on record.</p>
      ) : (
        appearances.map((a) => (
          <section key={a.meeting_id} className="appearance">
            <h3>
              <Link href={`/meetings/${a.meeting_id}`}>
                {a.city} {a.meeting_type} — {a.meeting_date}
              </Link>
              <span className="personMeta">
                {" "}
                · {a.segments.length} segment
                {a.segments.length === 1 ? "" : "s"}
              </span>
            </h3>
            <ul className="appearanceSegments">
              {a.segments.map((seg) => (
                <li key={seg.segment_id}>
                  <Link
                    href={`/meetings/${a.meeting_id}?t=${Math.floor(seg.start_time)}#seg-${seg.segment_id}`}
                    className="timestampLink"
                  >
                    {formatTime(seg.start_time)}
                  </Link>{" "}
                  <span className="appearanceText">{seg.text}</span>
                </li>
              ))}
            </ul>
          </section>
        ))
      )}
    </main>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: clean.

- [ ] **Step 3: Commit**

```bash
git add web/app/people/\[slug\]/page.tsx && git commit -m "feat(web): person profile page with appearances"
```

### Task 7: link transcript speaker names to profiles

**Files:**
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx` (~line 239)

- [ ] **Step 1: Make the speaker name a link when the speaker is identified**

Add to the import block at the top of `MeetingView.tsx`:

```tsx
import Link from "next/link";
```

Replace (around line 239):

```tsx
              <span className="speaker">
                {seg.speaker_name || seg.speaker_label}
              </span>
```

with:

```tsx
              <span className="speaker">
                {seg.politician_slug ? (
                  <Link
                    href={`/people/${seg.politician_slug}`}
                    className="speakerLink"
                    title="View this person's appearances"
                  >
                    {seg.speaker_name || seg.speaker_label}
                  </Link>
                ) : (
                  seg.speaker_name || seg.speaker_label
                )}
              </span>
```

- [ ] **Step 2: Typecheck + commit**

Run: `cd web && npx tsc --noEmit`

```bash
git add web/app/meetings/\[meetingId\]/MeetingView.tsx && git commit -m "feat(web): link identified speakers to people pages"
```

### Task 8: styles

**Files:**
- Modify: `web/app/globals.css` (append)

- [ ] **Step 1: Append people styles** (uses the existing CSS variables; matches the flat-class convention)

```css
/* ---------- People ---------- */

.siteNav {
  margin-bottom: 1.5rem;
  font-size: 0.9rem;
}

.siteNav a {
  color: var(--accent);
}

.peopleGrid {
  list-style: none;
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
  gap: 1rem;
}

.personCard {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 0.35rem;
  padding: 1.25rem 1rem;
  border: 1px solid var(--border);
  border-radius: 8px;
  text-align: center;
}

.personCard:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
}

.personPhoto {
  width: 72px;
  height: 72px;
  border-radius: 50%;
  object-fit: cover;
  background: var(--accent-soft);
}

.personPhoto.large {
  width: 96px;
  height: 96px;
}

.personPhotoFallback {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  font-size: 1.75rem;
  color: var(--accent);
}

.personName {
  font-weight: 600;
}

.personOffice {
  color: var(--muted);
  font-size: 0.85rem;
}

.personMeta {
  color: var(--muted);
  font-size: 0.8rem;
}

.personPage .personHeader {
  display: flex;
  align-items: center;
  gap: 1.25rem;
  margin: 1rem 0;
}

.personBio {
  color: var(--muted);
  max-width: 60ch;
  margin-bottom: 1.5rem;
}

.personPage h2 {
  margin: 1.5rem 0 0.75rem;
}

.appearance {
  margin-bottom: 1.5rem;
}

.appearance h3 {
  font-size: 1rem;
  margin-bottom: 0.5rem;
}

.appearance h3 a {
  color: var(--accent);
}

.appearanceSegments {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.appearanceSegments li {
  padding-left: 0.75rem;
  border-left: 2px solid var(--border);
}

.timestampLink {
  color: var(--accent);
  font-variant-numeric: tabular-nums;
  font-size: 0.85rem;
}

.appearanceText {
  font-size: 0.95rem;
}

.speakerLink {
  color: var(--accent);
}

.speakerLink:hover {
  text-decoration: underline;
}
```

- [ ] **Step 2: Commit**

```bash
git add web/app/globals.css && git commit -m "feat(web): people page styles"
```

### Task 9: end-to-end build verification + roadmap update

- [ ] **Step 1: Build the static site against the local backend**

With the ev-accounts dev server from Task 3 still running (substitute the port):

```bash
cd web && EV_ACCOUNTS_URL=http://localhost:PORT npm run build
```

Expected: build succeeds; output lists `/people` and one `/people/<slug>` route per roster person (`generateStaticParams`).

- [ ] **Step 2: Verify the built pages in the preview**

Serve `web/out` (e.g. `npx serve web/out`) and check, by loading the pages and reading the HTML/behavior:
1. `/people` shows the roster grid with names and meeting counts.
2. A profile page shows office info, the essentials.city link (when `politician_id` is set), and appearances grouped by meeting.
3. A timestamp link from an appearance lands on the meeting page, seeks the player to the right moment, and scrolls to the highlighted segment.
4. On a meeting page, an identified speaker's name links back to their profile.

- [ ] **Step 3: Mark Phase 2 progress in the roadmap**

In `docs/web-roadmap.md`, change the Phase 2 heading from `## Phase 2 — People  ← next up` to `## Phase 2 — People ✅ (shipped YYYY-MM-DD)` with the actual date, and move the `← next up` marker to Phase 3.

- [ ] **Step 4: Final commit**

```bash
git add docs/web-roadmap.md && git commit -m "docs: mark Phase 2 (people) shipped"
```

---

## Consciously deferred (so reviewers don't think it was missed)

- **Roster filter UI**: the API supports `?city=`, but the roster page renders everyone — with one city published, filter chips are noise. Add a city/body filter UI when a second city ships. (Body-level filtering would also need `body_slug` joined into the roster query.)
- **Speaker links target our own people pages, not essentials**: the roadmap's leftover item said meeting pages link speakers to essentials profiles. With people pages existing, in-site profiles are the better landing (they have the appearances), and each profile links onward to essentials. Revisit if essentials wants direct links.

## Deployment notes (after both repos are merged/pushed)

1. Deploy ev-accounts backend first (its normal deploy path) — the endpoints must be live before the site builds.
2. Trigger a Render rebuild of the static site (`on-the-record-web`); `EV_ACCOUNTS_URL` is already configured there.
3. Smoke-check `https://<site>/people` in production.
