# Phase 3 — Cross-Meeting Search Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Full-text search across all published meeting transcripts via a `/api/search` endpoint in ev-accounts and a `/search` page on the static site, per the approved spec `docs/superpowers/specs/2026-06-12-cross-meeting-search-design.md`.

**Architecture:** Cross-repo, same split as Phase 2. **ev-accounts** gains `searchService.ts` (Postgres FTS: `websearch_to_tsquery` + `ts_rank` against `meetings.segments.tsv`, `ts_headline` snippets with `[[[`/`]]]` sentinels computed only on the returned page) and `routes/search.ts`. **web** gains a `/search` page — server component fetches dropdown data at build time; a client `SearchView` does the actual search **from the browser at runtime** via the new `NEXT_PUBLIC_EV_ACCOUNTS_URL` env var, with URL-as-state and submit-to-search.

**Tech Stack:** Express + pg pool.query, vitest + supertest; Next.js 16 App Router static export, `useSearchParams` in a Suspense boundary.

**Conventions (identical to Phase 2 — see that plan's header):** pool.query only, explicit row types + camelCase mappers (never spread rows), `Number()` on pg numerics, `optionalAuth`, validation before DB access, web types snake_case. Work directly on `master` (ev-accounts) / `main` (web); do NOT push. End every commit message with `Co-Authored-By: Claude Fable 5 <noreply@anthropic.com>`.

---

## Part A — ev-accounts API (work in `~/Documents/GitHub/ev-accounts/backend`)

### Task 1: searchService

**Files:**
- Create: `backend/src/lib/searchService.ts`

(No unit test — DB-backed services here are exercised via route tests with the service mocked, plus the smoke test in Task 3. Matches `meetingsService.ts`/`peopleService.ts`.)

- [ ] **Step 1: Write the service**

```typescript
/**
 * searchService — full-text search across published meeting transcripts.
 *
 * Uses the tsv tsvector column on meetings.segments (GIN-indexed, maintained
 * by trigger — migration 364). websearch_to_tsquery parses user input and
 * never throws on malformed queries.
 *
 * Snippets: ts_headline does NOT HTML-escape, so we never emit HTML. The
 * [[[ / ]]] sentinels are converted to <mark> elements client-side.
 * ts_headline runs in an outer query over only the returned page.
 *
 * Same architecture rules as meetingsService/peopleService:
 *   - meetings.* is NOT PostgREST-exposed; pool.query() only
 *   - explicit field whitelists, never spread rows
 *   - Number() on bigint/numeric: segment_index, start_time, end_time, count
 */

import { pool } from './db.js';

export const SEARCH_PAGE_SIZE = 25;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

export interface SearchResult {
  meetingId: string;
  city: string;
  meetingType: string;
  date: string;
  segmentIndex: number;
  startTime: number;
  endTime: number;
  speakerName: string | null;
  politicianSlug: string | null;
  snippet: string;
}

export interface SearchResponse {
  query: string;
  page: number;
  totalCount: number;
  results: SearchResult[];
}

interface SearchRow {
  meeting_id: string;
  city: string;
  meeting_type: string;
  date: string;
  segment_index: string;
  start_time: string;
  end_time: string;
  speaker_name: string | null;
  politician_slug: string | null;
  snippet: string;
}

// ---------------------------------------------------------------------------
// Mapper (explicit camelCase — NEVER spread rows)
// ---------------------------------------------------------------------------

function mapResult(row: SearchRow): SearchResult {
  return {
    meetingId: row.meeting_id,
    city: row.city,
    meetingType: row.meeting_type,
    date: row.date,
    segmentIndex: Number(row.segment_index),
    startTime: Number(row.start_time),
    endTime: Number(row.end_time),
    speakerName: row.speaker_name,
    politicianSlug: row.politician_slug,
    snippet: row.snippet,
  };
}

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------

export async function searchSegments(opts: {
  q: string;
  city?: string;
  speaker?: string;
  page: number;
}): Promise<SearchResponse> {
  const { q, city, speaker, page } = opts;
  const offset = (page - 1) * SEARCH_PAGE_SIZE;

  const conditions: string[] = [`s.tsv @@ websearch_to_tsquery('english', $1)`];
  const params: unknown[] = [q];
  if (speaker !== undefined) {
    params.push(speaker);
    conditions.push(`s.politician_slug = $${params.length}`);
  }
  if (city !== undefined) {
    params.push(city);
    conditions.push(`m.city = $${params.length}`);
  }
  const where = conditions.join(' AND ');

  const limitParam = params.length + 1;
  const offsetParam = params.length + 2;

  const [{ rows }, { rows: countRows }] = await Promise.all([
    pool.query<SearchRow>(
      `WITH hits AS (
         SELECT s.meeting_id, s.segment_index, s.start_time, s.end_time,
                s.speaker_name, s.politician_slug, s.text,
                ts_rank(s.tsv, websearch_to_tsquery('english', $1)) AS rank,
                m.city, m.meeting_type, m.date::text AS date
         FROM meetings.segments s
         JOIN meetings.meetings m ON m.id = s.meeting_id
         WHERE ${where}
         ORDER BY rank DESC, m.date DESC, s.segment_index
         LIMIT $${limitParam} OFFSET $${offsetParam}
       )
       SELECT meeting_id, city, meeting_type, date, segment_index,
              start_time, end_time, speaker_name, politician_slug,
              ts_headline('english', text, websearch_to_tsquery('english', $1),
                          'StartSel=[[[, StopSel=]]], MaxWords=40, MinWords=20') AS snippet
       FROM hits
       ORDER BY rank DESC, date DESC, segment_index`,
      [...params, SEARCH_PAGE_SIZE, offset]
    ),
    pool.query<{ count: string }>(
      `SELECT COUNT(*) AS count
       FROM meetings.segments s
       JOIN meetings.meetings m ON m.id = s.meeting_id
       WHERE ${where}`,
      params
    ),
  ]);

  return {
    query: q,
    page,
    totalCount: Number(countRows[0].count),
    results: rows.map(mapResult),
  };
}
```

- [ ] **Step 2: Typecheck**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck`
Expected: clean.

### Task 2: search route (TDD)

**Files:**
- Create: `backend/src/routes/search.test.ts`
- Create: `backend/src/routes/search.ts`

- [ ] **Step 1: Write the failing tests**

```typescript
import { vi, describe, it, expect, beforeEach } from 'vitest';
import express from 'express';
import request from 'supertest';

const { mockSearchSegments } = vi.hoisted(() => ({ mockSearchSegments: vi.fn() }));
vi.mock('../lib/searchService.js', () => ({
  searchSegments: mockSearchSegments,
  SEARCH_PAGE_SIZE: 25,
}));
vi.mock('../middleware/auth.js', () => ({
  optionalAuth: (_req: unknown, _res: unknown, next: () => void) => next(),
}));

import searchRouter from './search.js';

const app = express();
app.use('/api/search', searchRouter);

const sampleResponse = {
  query: 'housing',
  page: 1,
  totalCount: 1,
  results: [
    {
      meetingId: '22222222-2222-2222-2222-222222222222',
      city: 'Bloomington',
      meetingType: 'City Council',
      date: '2026-02-18',
      segmentIndex: 42,
      startTime: 1843.2,
      endTime: 1851,
      speakerName: 'John Hamilton',
      politicianSlug: 'john-hamilton',
      snippet: 'we have to talk about [[[housing]]] before the',
    },
  ],
};

beforeEach(() => mockSearchSegments.mockReset());

describe('GET /api/search validation', () => {
  it('422 when q is missing', async () => {
    const res = await request(app).get('/api/search');
    expect(res.status).toBe(422);
    expect(mockSearchSegments).not.toHaveBeenCalled();
  });

  it('422 when q is only whitespace', async () => {
    const res = await request(app).get('/api/search?q=%20%20');
    expect(res.status).toBe(422);
    expect(mockSearchSegments).not.toHaveBeenCalled();
  });

  it('422 when q exceeds 200 characters', async () => {
    const res = await request(app).get(`/api/search?q=${'a'.repeat(201)}`);
    expect(res.status).toBe(422);
    expect(mockSearchSegments).not.toHaveBeenCalled();
  });

  it('422 when page is not a positive integer', async () => {
    const res = await request(app).get('/api/search?q=housing&page=0');
    expect(res.status).toBe(422);
    expect(mockSearchSegments).not.toHaveBeenCalled();
  });

  it('422 when speaker is not a valid slug', async () => {
    const res = await request(app).get('/api/search?q=housing&speaker=Bad!Slug');
    expect(res.status).toBe(422);
    expect(mockSearchSegments).not.toHaveBeenCalled();
  });
});

describe('GET /api/search results', () => {
  it('200 with the search response, defaults applied', async () => {
    mockSearchSegments.mockResolvedValueOnce(sampleResponse);
    const res = await request(app).get('/api/search?q=housing');
    expect(res.status).toBe(200);
    expect(res.body).toEqual(sampleResponse);
    expect(mockSearchSegments).toHaveBeenCalledWith({
      q: 'housing',
      city: undefined,
      speaker: undefined,
      page: 1,
    });
  });

  it('passes city, speaker, and page through', async () => {
    mockSearchSegments.mockResolvedValueOnce({ ...sampleResponse, page: 3 });
    const res = await request(app).get(
      '/api/search?q=housing&city=Bloomington&speaker=john-hamilton&page=3'
    );
    expect(res.status).toBe(200);
    expect(mockSearchSegments).toHaveBeenCalledWith({
      q: 'housing',
      city: 'Bloomington',
      speaker: 'john-hamilton',
      page: 3,
    });
  });

  it('200 with zero results is not an error', async () => {
    mockSearchSegments.mockResolvedValueOnce({
      query: 'zzzz',
      page: 1,
      totalCount: 0,
      results: [],
    });
    const res = await request(app).get('/api/search?q=zzzz');
    expect(res.status).toBe(200);
    expect(res.body.totalCount).toBe(0);
    expect(res.body.results).toEqual([]);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/search.test.ts`
Expected: FAIL — cannot find module './search.js'

- [ ] **Step 3: Write the route**

```typescript
/**
 * Search routes — full-text search across published meeting transcripts.
 *
 * Public read only (optionalAuth). Called cross-origin by the on-the-record
 * static site's browser (origin must be in the CORS_ORIGIN allowlist).
 *
 * Architecture rules (same as meetings.ts / people.ts):
 *   - All DB access via searchService (pool.query)
 *   - All params validated before any DB work
 */

import { Router } from 'express';
import type { Request, Response } from 'express';
import { optionalAuth } from '../middleware/auth.js';
import { searchSegments } from '../lib/searchService.js';

const router = Router();

const SLUG_REGEX = /^[a-z0-9][a-z0-9_-]{0,99}$/;
const MAX_QUERY_LENGTH = 200;

// GET /api/search?q=affordable+housing&city=Bloomington&speaker=john-hamilton&page=1
router.get('/', optionalAuth, async (req: Request, res: Response): Promise<void> => {
  const q = typeof req.query.q === 'string' ? req.query.q.trim() : '';
  if (q.length === 0 || q.length > MAX_QUERY_LENGTH) {
    res.status(422).json({
      code: 'VALIDATION_ERROR',
      message: `q is required and must be at most ${MAX_QUERY_LENGTH} characters`,
    });
    return;
  }

  let page = 1;
  if (req.query.page !== undefined) {
    const parsed = Number(req.query.page);
    if (!Number.isInteger(parsed) || parsed < 1) {
      res.status(422).json({ code: 'VALIDATION_ERROR', message: 'page must be a positive integer' });
      return;
    }
    page = parsed;
  }

  let speaker: string | undefined;
  if (req.query.speaker !== undefined) {
    if (typeof req.query.speaker !== 'string' || !SLUG_REGEX.test(req.query.speaker)) {
      res.status(422).json({ code: 'VALIDATION_ERROR', message: 'speaker must be a valid slug' });
      return;
    }
    speaker = req.query.speaker;
  }

  let city: string | undefined;
  if (typeof req.query.city === 'string' && req.query.city.length > 0) {
    city = req.query.city;
  }

  try {
    const response = await searchSegments({ q, city, speaker, page });
    res.status(200).json(response);
  } catch (err) {
    console.error('[GET /search] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

export default router;
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npx vitest run src/routes/search.test.ts`
Expected: PASS (8 tests)

### Task 3: register, smoke test, commit

**Files:**
- Modify: `backend/src/index.ts` (import next to peopleRouter; mount next to `/api/people`)

- [ ] **Step 1: Register the router**

```typescript
import searchRouter from './routes/search.js';
```

```typescript
app.use('/api/search', searchRouter);
```

- [ ] **Step 2: Typecheck + full suite**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run typecheck && npm test`
Expected: typecheck clean; no NEW test failures (9 pre-existing failing files are known — architecture/compass/env tests unrelated to search).

- [ ] **Step 3: Smoke test**

Start `npm run dev` in the background (port 3000 unless `backend/.env` overrides `PORT`), then:

```bash
curl -s "localhost:3000/api/search?q=housing" | head -c 400        # 200 {query, page, totalCount, results}
curl -s -o /dev/null -w "%{http_code}" "localhost:3000/api/search"  # 422
curl -s "localhost:3000/api/search?q=council&speaker=john-hamilton" | head -c 400
```

If the DB has published segments, verify snippets contain `[[[`/`]]]` sentinels and ranking looks sane. If the DB is still empty (data not yet re-published), `totalCount: 0, results: []` confirms the wiring; note it in your report. Kill the dev server when done.

- [ ] **Step 4: Commit (ev-accounts repo)**

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/lib/searchService.ts backend/src/routes/search.ts backend/src/routes/search.test.ts backend/src/index.ts && git commit -m "feat(api): full-text transcript search endpoint"
```

Do NOT stage anything else (the repo may have unrelated uncommitted changes).

---

## Part B — web `/search` page (work in `~/Documents/GitHub/on-the-record`)

### Task 4: extract formatTime to web/lib/format.ts

**Files:**
- Create: `web/lib/format.ts`
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx` (delete local formatTime ~lines 15-22, add import)
- Modify: `web/app/people/[slug]/page.tsx` (delete local formatTime ~lines 19-26, add import)

- [ ] **Step 1: Create `web/lib/format.ts`**

```typescript
export function formatTime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  const mm = String(m).padStart(2, "0");
  const ss = String(s).padStart(2, "0");
  return h > 0 ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
}
```

- [ ] **Step 2: Update both call sites**

In `web/app/meetings/[meetingId]/MeetingView.tsx` AND `web/app/people/[slug]/page.tsx`: delete the identical local `function formatTime(seconds: number): string {...}` definition and add `import { formatTime } from "@/lib/format";` to the imports. (In MeetingView it goes with the other `@/lib` import; in the person page below the `@/lib/queries` import.)

- [ ] **Step 3: Verify + commit**

Run: `cd ~/Documents/GitHub/on-the-record/web && npx tsc --noEmit && npm run lint`
Expected: both clean.

```bash
cd ~/Documents/GitHub/on-the-record && git add web/lib/format.ts "web/app/meetings/[meetingId]/MeetingView.tsx" "web/app/people/[slug]/page.tsx" && git commit -m "refactor(web): extract shared formatTime helper"
```

### Task 5: search types + env plumbing

**Files:**
- Modify: `web/lib/types.ts` (append)
- Modify: `web/.env.local.example`
- Modify: `render.yaml` (envVars list)
- Modify: `web/.env.local` (local only, NOT committed — it is gitignored)

- [ ] **Step 1: Append to `web/lib/types.ts`**

```typescript
export interface SearchResult {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;          // YYYY-MM-DD
  segment_id: number;            // segmentIndex from ev-accounts
  start_time: number;
  end_time: number;
  speaker_name: string | null;
  politician_slug: string | null;
  snippet: string;               // [[[match]]] sentinels, rendered as <mark>
}
```

- [ ] **Step 2: Replace the contents of `web/.env.local.example` with:**

```bash
# Base URL of the ev-accounts API (no trailing slash).
# Build-time only — not exposed to the browser.
EV_ACCOUNTS_URL=https://ev-accounts.onrender.com

# Same URL, baked into the client bundle for the /search page's
# runtime requests. Must also be in ev-accounts' CORS_ORIGIN allowlist
# (the SITE origin, not this URL) for production.
NEXT_PUBLIC_EV_ACCOUNTS_URL=https://ev-accounts.onrender.com
```

- [ ] **Step 3: In `render.yaml`, add to the `envVars:` list (after the `EV_ACCOUNTS_URL` entry):**

```yaml
      - key: NEXT_PUBLIC_EV_ACCOUNTS_URL
        sync: false
```

- [ ] **Step 4: Add `NEXT_PUBLIC_EV_ACCOUNTS_URL=http://localhost:3000` to `web/.env.local`** (gitignored; needed for local builds/dev — match whatever `EV_ACCOUNTS_URL` is set to there).

- [ ] **Step 5: Commit**

```bash
git add web/lib/types.ts web/.env.local.example render.yaml && git commit -m "feat(web): search result type and NEXT_PUBLIC_EV_ACCOUNTS_URL plumbing"
```

### Task 6: /search page

**Files:**
- Create: `web/app/search/page.tsx`
- Create: `web/app/search/SearchView.tsx`

- [ ] **Step 1: Create `web/app/search/page.tsx`** (server component — dropdown data at build time)

```tsx
import Link from "next/link";
import { Suspense } from "react";
import { fetchMeetings, fetchPeople } from "@/lib/queries";
import SearchView from "./SearchView";

export const metadata = { title: "Search — On the Record" };

export default async function SearchPage() {
  let cities: string[] = [];
  let speakers: { slug: string; name: string }[] = [];
  try {
    const [meetings, people] = await Promise.all([
      fetchMeetings(),
      fetchPeople(),
    ]);
    cities = [...new Set(meetings.map((m) => m.city))].sort();
    speakers = people
      .map((p) => ({ slug: p.slug, name: p.name }))
      .sort((a, b) => a.name.localeCompare(b.name));
  } catch {
    // Dropdowns degrade to empty lists; search itself is a runtime request.
  }

  return (
    <main className="indexPage searchPage">
      <Link href="/" className="backLink">
        ← All meetings
      </Link>
      <h1>Search</h1>
      <p className="tagline">
        Search every word spoken across all published meetings.
      </p>
      {/* useSearchParams requires a Suspense boundary under static export */}
      <Suspense fallback={null}>
        <SearchView cities={cities} speakers={speakers} />
      </Suspense>
    </main>
  );
}
```

- [ ] **Step 2: Create `web/app/search/SearchView.tsx`** (client component — runtime search)

```tsx
"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { formatTime } from "@/lib/format";
import type { SearchResult } from "@/lib/types";

// Runtime requests from the browser — needs the NEXT_PUBLIC_ env var,
// baked in at build time. The build-time EV_ACCOUNTS_URL is not visible here.
const API_BASE = (process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");
const PAGE_SIZE = 25; // keep in sync with SEARCH_PAGE_SIZE in ev-accounts

interface SpeakerOption {
  slug: string;
  name: string;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
function mapResult(r: any): SearchResult {
  return {
    meeting_id: r.meetingId,
    city: r.city,
    meeting_type: r.meetingType,
    meeting_date: r.date,
    segment_id: r.segmentIndex,
    start_time: r.startTime,
    end_time: r.endTime,
    speaker_name: r.speakerName ?? null,
    politician_slug: r.politicianSlug ?? null,
    snippet: r.snippet ?? "",
  };
}

// ts_headline emits [[[match]]] sentinels (never HTML) — split into <mark>
// React nodes so nothing goes through dangerouslySetInnerHTML.
function renderSnippet(snippet: string) {
  const parts = snippet.split(/\[\[\[|\]\]\]/);
  return parts.map((part, i) =>
    i % 2 === 1 ? <mark key={i}>{part}</mark> : <span key={i}>{part}</span>
  );
}

interface MeetingGroup {
  meeting_id: string;
  city: string;
  meeting_type: string;
  meeting_date: string;
  hits: SearchResult[];
}

type Status = "idle" | "loading" | "done" | "error";

export default function SearchView({
  cities,
  speakers,
}: {
  cities: string[];
  speakers: SpeakerOption[];
}) {
  const router = useRouter();
  const searchParams = useSearchParams();

  const urlQ = searchParams.get("q") ?? "";
  const urlCity = searchParams.get("city") ?? "";
  const urlSpeaker = searchParams.get("speaker") ?? "";
  const urlPage = Math.max(1, Number(searchParams.get("page") ?? "1") || 1);

  const [input, setInput] = useState(urlQ);
  const [city, setCity] = useState(urlCity);
  const [speaker, setSpeaker] = useState(urlSpeaker);
  const [status, setStatus] = useState<Status>("idle");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [totalCount, setTotalCount] = useState(0);

  // Keep the form in sync with the URL (back/forward navigation).
  useEffect(() => {
    setInput(urlQ);
    setCity(urlCity);
    setSpeaker(urlSpeaker);
  }, [urlQ, urlCity, urlSpeaker]);

  // The URL is the source of truth: fetch whenever ?q= is present.
  useEffect(() => {
    if (!urlQ) {
      setStatus("idle");
      setResults([]);
      setTotalCount(0);
      return;
    }
    const controller = new AbortController();
    const params = new URLSearchParams({ q: urlQ, page: String(urlPage) });
    if (urlCity) params.set("city", urlCity);
    if (urlSpeaker) params.set("speaker", urlSpeaker);
    setStatus("loading");
    fetch(`${API_BASE}/api/search?${params}`, { signal: controller.signal })
      .then((res) => {
        if (!res.ok) throw new Error(`search failed: ${res.status}`);
        return res.json();
      })
      .then((data) => {
        setResults((data.results as unknown[]).map(mapResult));
        setTotalCount(data.totalCount);
        setStatus("done");
      })
      .catch((err: unknown) => {
        if ((err as Error).name !== "AbortError") setStatus("error");
      });
    return () => controller.abort();
  }, [urlQ, urlCity, urlSpeaker, urlPage]);

  const navigate = useCallback(
    (q: string, c: string, s: string, page: number) => {
      const params = new URLSearchParams();
      if (q) params.set("q", q);
      if (c) params.set("city", c);
      if (s) params.set("speaker", s);
      if (page > 1) params.set("page", String(page));
      router.replace(`/search?${params}`);
    },
    [router]
  );

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    navigate(input.trim(), city, speaker, 1);
  }

  // Group by meeting, preserving API rank order (first hit wins position).
  const groups: MeetingGroup[] = [];
  for (const r of results) {
    const existing = groups.find((g) => g.meeting_id === r.meeting_id);
    if (existing) {
      existing.hits.push(r);
    } else {
      groups.push({
        meeting_id: r.meeting_id,
        city: r.city,
        meeting_type: r.meeting_type,
        meeting_date: r.meeting_date,
        hits: [r],
      });
    }
  }

  const totalPages = Math.max(1, Math.ceil(totalCount / PAGE_SIZE));

  return (
    <div className="searchView">
      <form className="searchForm" onSubmit={onSubmit}>
        <input
          type="search"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          placeholder='e.g. affordable housing, "parking garage"'
          aria-label="Search transcripts"
          maxLength={200}
        />
        <select
          value={city}
          onChange={(e) => setCity(e.target.value)}
          aria-label="Filter by city"
        >
          <option value="">All cities</option>
          {cities.map((c) => (
            <option key={c} value={c}>
              {c}
            </option>
          ))}
        </select>
        <select
          value={speaker}
          onChange={(e) => setSpeaker(e.target.value)}
          aria-label="Filter by speaker"
        >
          <option value="">All speakers</option>
          {speakers.map((s) => (
            <option key={s.slug} value={s.slug}>
              {s.name}
            </option>
          ))}
        </select>
        <button type="submit">Search</button>
      </form>

      {status === "idle" && (
        <p className="searchHint">
          Try a phrase in quotes, or exclude words with a leading dash.
        </p>
      )}
      {status === "loading" && <p className="searchHint">Searching…</p>}
      {status === "error" && (
        <p className="searchHint">
          Search is temporarily unavailable. Please try again shortly.
        </p>
      )}
      {status === "done" && totalCount === 0 && (
        <p className="searchHint">No results for “{urlQ}”.</p>
      )}

      {status === "done" && totalCount > 0 && (
        <>
          <p className="searchCount">
            {totalCount} result{totalCount === 1 ? "" : "s"}
          </p>
          {groups.map((g) => (
            <section key={g.meeting_id} className="searchGroup">
              <h2>
                <Link href={`/meetings/${g.meeting_id}`}>
                  {g.city} {g.meeting_type} — {g.meeting_date}
                </Link>
              </h2>
              <ul className="searchHits">
                {g.hits.map((hit) => (
                  <li key={hit.segment_id} className="searchHit">
                    <Link
                      href={`/meetings/${hit.meeting_id}?t=${Math.floor(hit.start_time)}#seg-${hit.segment_id}`}
                      className="timestampLink"
                    >
                      {formatTime(hit.start_time)}
                    </Link>{" "}
                    {hit.speaker_name &&
                      (hit.politician_slug ? (
                        <Link
                          href={`/people/${hit.politician_slug}`}
                          className="speakerLink searchSpeaker"
                        >
                          {hit.speaker_name}
                        </Link>
                      ) : (
                        <span className="searchSpeaker">{hit.speaker_name}</span>
                      ))}
                    <p className="searchSnippet">{renderSnippet(hit.snippet)}</p>
                  </li>
                ))}
              </ul>
            </section>
          ))}
          {totalPages > 1 && (
            <div className="searchPager">
              <button
                disabled={urlPage <= 1}
                onClick={() => navigate(urlQ, urlCity, urlSpeaker, urlPage - 1)}
              >
                ← Previous
              </button>
              <span>
                Page {urlPage} of {totalPages}
              </span>
              <button
                disabled={urlPage >= totalPages}
                onClick={() => navigate(urlQ, urlCity, urlSpeaker, urlPage + 1)}
              >
                Next →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 3: Typecheck + commit**

Run: `cd ~/Documents/GitHub/on-the-record/web && npx tsc --noEmit && npm run lint`
Expected: clean.

```bash
git add web/app/search/ && git commit -m "feat(web): cross-meeting search page"
```

### Task 7: nav links + styles

**Files:**
- Modify: `web/app/page.tsx` (siteNav block, ~line 28)
- Modify: `web/app/people/page.tsx` (after tagline)
- Modify: `web/app/globals.css` (append)

- [ ] **Step 1: Home page nav.** In `web/app/page.tsx`, replace:

```tsx
      <nav className="siteNav">
        <Link href="/people">People →</Link>
      </nav>
```

with:

```tsx
      <nav className="siteNav">
        <Link href="/people">People →</Link>
        <Link href="/search">Search →</Link>
      </nav>
```

- [ ] **Step 2: People page nav.** In `web/app/people/page.tsx`, directly after the `<p className="tagline">…</p>` element, add:

```tsx
      <nav className="siteNav">
        <Link href="/search">Search →</Link>
      </nav>
```

- [ ] **Step 3: Append to `web/app/globals.css`:**

```css
/* ---------- Search ---------- */

.siteNav {
  display: flex;
  gap: 1.25rem;
}

.searchForm {
  display: flex;
  flex-wrap: wrap;
  gap: 0.5rem;
  margin: 1rem 0 1.5rem;
}

.searchForm input[type="search"] {
  flex: 1 1 240px;
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--background);
  color: var(--foreground);
  font-size: 1rem;
}

.searchForm select,
.searchForm button {
  padding: 0.5rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--background);
  color: var(--foreground);
  font-size: 0.9rem;
}

.searchForm button {
  background: var(--accent);
  border-color: var(--accent);
  color: #ffffff;
  cursor: pointer;
}

.searchHint,
.searchCount {
  color: var(--muted);
  font-size: 0.9rem;
  margin-bottom: 1rem;
}

.searchGroup {
  margin-bottom: 1.75rem;
}

.searchGroup h2 {
  font-size: 1rem;
  margin-bottom: 0.5rem;
}

.searchGroup h2 a {
  color: var(--accent);
}

.searchHits {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.searchHit {
  padding-left: 0.75rem;
  border-left: 2px solid var(--border);
}

.searchSpeaker {
  font-weight: 600;
  font-size: 0.85rem;
  margin-left: 0.5rem;
}

.searchSnippet {
  font-size: 0.95rem;
  margin-top: 0.2rem;
}

.searchSnippet mark {
  background: var(--active);
  color: inherit;
  padding: 0 2px;
  border-radius: 2px;
}

.searchPager {
  display: flex;
  align-items: center;
  gap: 1rem;
  color: var(--muted);
  font-size: 0.9rem;
}

.searchPager button {
  padding: 0.4rem 0.75rem;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: var(--background);
  color: var(--foreground);
  cursor: pointer;
}

.searchPager button:disabled {
  opacity: 0.45;
  cursor: default;
}
```

Note: `.siteNav` already exists in the People block of globals.css with `margin-bottom`/`font-size`; the new `display:flex; gap` rule here extends it for two links — that's intentional, keep both rules.

- [ ] **Step 4: Commit**

```bash
git add web/app/page.tsx web/app/people/page.tsx web/app/globals.css && git commit -m "feat(web): search nav links and styles"
```

### Task 8: end-to-end verification + roadmap

- [ ] **Step 1: Build against the local backend**

With the ev-accounts dev server running (Task 3 setup, port 3000):

```bash
cd ~/Documents/GitHub/on-the-record/web && EV_ACCOUNTS_URL=http://localhost:3000 NEXT_PUBLIC_EV_ACCOUNTS_URL=http://localhost:3000 npm run build
```

Expected: build succeeds; route list includes `○ /search`.

- [ ] **Step 2: Browser verification**

Serve `web/out` (e.g. `npx serve web/out`) and verify in a browser:
1. `/search` renders the form with city/speaker dropdowns (empty dropdowns are OK if the DB has no data).
2. Submitting a query updates the URL to `/search?q=...` and shows results / "No results" / the error state as appropriate.
3. With data: a timestamp link lands on the meeting page, seeks the player, scrolls to the highlighted segment; snippet `<mark>` highlights render; pagination appears for >25 results.
4. Without data (DB still empty): the empty state renders and the request returns 200 `{totalCount: 0}` — confirm in the network tab; note in the report that data-dependent checks are pending re-publish.

- [ ] **Step 3: Roadmap update**

In `docs/web-roadmap.md`: change `## Phase 3 — Cross-meeting search  ← next up` to `## Phase 3 — Cross-meeting search ✅ (built YYYY-MM-DD)` (actual date), and append ` ← next up` to the Phase 4 heading.

- [ ] **Step 4: Final commit**

```bash
git add docs/web-roadmap.md && git commit -m "docs: mark Phase 3 (search) built; Phase 4 next"
```

---

## Deployment notes (after both repos are pushed)

1. Deploy ev-accounts first; **append the production site origin to its `CORS_ORIGIN` env list** (exact origin, e.g. `https://on-the-record-web.onrender.com` — scheme + host, no path).
2. Set `NEXT_PUBLIC_EV_ACCOUNTS_URL` on the Render static site (same value as `EV_ACCOUNTS_URL`).
3. Trigger the static site rebuild; smoke-check `/search?q=council` in production.

## Consciously deferred (per spec)

Live search-as-you-type, server-side grouping, rate limiting, fuzzy matching, date filters, search analytics.
