# House-Floor Admin Review Panel (Project 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a web admin panel that lists House-floor draft meetings, lets an authenticated operator review a draft (transcript + CREC attributions + streamed CDN audio), and promote it live or archive it.

**Architecture:** Two repos, two PRs. In **ev-accounts** add an authenticated `/api/admin/meetings` router that reads drafts via the existing `includeAllStatuses` bypass and flips meeting status (logged). In **on-the-record `web/`** add `/admin` routes that reuse the existing meeting viewer, gated by a client-side EV login that sends a Bearer token to the admin API. No pipeline, gate, or schema changes.

**Tech Stack:** ev-accounts = Express 5 + TypeScript (ESM), `pg`, Vitest + Supertest. web/ = Next.js 16 static export, plain global CSS, hand-rolled `useApi`/`usePathParam`, Vitest (node env). Playback = `hls.js` via the existing `FilePlayer`.

## Global Constraints

- **GitHub account:** `chrisandrewsedu`. PRs go to `EmpoweredVote/on-the-record` and `EmpoweredVote/ev-accounts`; squash-merge with a `#num`.
- **ev-accounts ESM:** every relative import path ends in `.js` (e.g. `../lib/meetingsService.js`), even though the source is `.ts`.
- **ev-accounts route tests** mock the service layer and the auth middleware with `vi.mock` + `vi.hoisted` (see `backend/src/routes/meetings.test.ts`). **Service tests** mock `./db.js` as `{ pool: { query: mockQuery } }` (see `backend/src/lib/meetingsService.test.ts`).
- **ev-accounts deploy** is automatic on merge to `master`. Migrations are hand-applied; **this project needs none**.
- **ev-accounts route mount order matters:** mount `/api/admin/meetings` BEFORE the bare `app.use('/api/admin', adminRouter)` at `backend/src/index.ts:163` so nothing generic shadows it.
- **web/ tests:** `vitest.config.ts` runs **only `lib/**/*.test.ts` in the `node` environment** — no jsdom, no testing-library. Put all testable logic in `lib/` and mock `fetch`. `.tsx` pages are verified via `next build` + the browser preview, matching the repo's existing no-component-test convention.
- **web/ API base:** browser fetchers read `process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL` (prod: `https://accounts-api.empowered.vote`). Strip a trailing slash.
- **web/ dynamic routes** use the sentinel pattern: `generateStaticParams()` returns one `{ param: "view" }`, plus a `render.yaml` rewrite; the client reads the real id via `usePathParam`.
- **Auth is server-enforced.** Never return draft data or accept a status flip without `requireAuth + requireAdmin`. The static `web/` bundle carries no secret.
- **Commit trailer** on every commit:
  ```
  Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
  ```

## File Structure

**ev-accounts (PR A — branch off `master`, e.g. `feat/admin-meetings-review`):**
- Create `backend/src/lib/adminMeetingsService.ts` — `getSpeakerCountsByMeeting()` aggregate.
- Create `backend/src/lib/adminMeetingsService.test.ts` — its unit test.
- Create `backend/src/routes/adminMeetings.ts` — the `/api/admin/meetings` router (reads + status PATCH).
- Create `backend/src/routes/adminMeetings.test.ts` — router tests.
- Modify `backend/src/index.ts` — mount the router.

**on-the-record `web/` (PR B — branch off `main`; this worktree is already on `claude/clever-shamir-5b50d5`):**
- Create `web/lib/adminAuth.ts` + `web/lib/adminAuth.test.ts` — login, token store, `authedFetch`.
- Create `web/lib/adminQueries.ts` + `web/lib/adminQueries.test.ts` — admin fetchers + mutations.
- Modify `web/lib/queries.ts` — `export` the existing `mapMeeting`, `mapSummary`, `mapSegment` mappers (DRY reuse).
- Modify `web/app/meetings/[meetingId]/MeetingView.tsx` — add an optional `actionBar` prop.
- Create `web/components/AdminGate.tsx` — login-or-children gate.
- Create `web/app/admin/floor/page.tsx` + `web/app/admin/floor/FloorQueueClient.tsx` — the queue.
- Create `web/app/admin/meetings/[meetingId]/page.tsx` + `AdminMeetingClient.tsx` — the review page.
- Modify `web/render.yaml`... (rewrites live in the repo-root `render.yaml`) — add the `/admin/meetings/*` rewrite. (Path: `render.yaml` at the on-the-record repo root, not under `web/`.)

---

## PR A — ev-accounts admin API

### Task A1: Speaker link-count aggregate

**Files:**
- Create: `backend/src/lib/adminMeetingsService.ts`
- Test: `backend/src/lib/adminMeetingsService.test.ts`

**Interfaces:**
- Consumes: `pool` from `./db.js`.
- Produces: `getSpeakerCountsByMeeting(meetingIds: string[]): Promise<Record<string, { named: number; linked: number }>>` — for each meeting id, how many speaker rows have a `display_name` (named) and a `politician_id` (linked).

- [ ] **Step 1: Write the failing test**

```ts
// backend/src/lib/adminMeetingsService.test.ts
import { describe, it, expect, vi, beforeEach } from 'vitest';

const { mockQuery } = vi.hoisted(() => ({ mockQuery: vi.fn() }));
vi.mock('./db.js', () => ({ pool: { query: mockQuery } }));

import { getSpeakerCountsByMeeting } from './adminMeetingsService.js';

describe('getSpeakerCountsByMeeting', () => {
  beforeEach(() => mockQuery.mockReset());

  it('returns {} and does not query when given no ids', async () => {
    expect(await getSpeakerCountsByMeeting([])).toEqual({});
    expect(mockQuery).not.toHaveBeenCalled();
  });

  it('maps named/linked counts to numbers keyed by meeting id', async () => {
    mockQuery.mockResolvedValueOnce({
      rows: [{ meeting_id: 'm1', named: '33', linked: '32' }],
    });
    const out = await getSpeakerCountsByMeeting(['m1']);
    expect(out).toEqual({ m1: { named: 33, linked: 32 } });
    const [, params] = mockQuery.mock.calls[0];
    expect(params).toEqual([['m1']]);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix backend -- adminMeetingsService`
Expected: FAIL — cannot find module `./adminMeetingsService.js`.

- [ ] **Step 3: Write minimal implementation**

```ts
// backend/src/lib/adminMeetingsService.ts
import { pool } from './db.js';

export interface SpeakerLinkCounts {
  named: number;
  linked: number;
}

/**
 * For each meeting id, count speaker rows that carry a display_name (named) and
 * a politician_id (linked). Used by the admin review queue to show, e.g.,
 * "48 speakers · 33 named · 32 linked" without loading every speaker row.
 */
export async function getSpeakerCountsByMeeting(
  meetingIds: string[]
): Promise<Record<string, SpeakerLinkCounts>> {
  if (meetingIds.length === 0) return {};
  const { rows } = await pool.query<{ meeting_id: string; named: string; linked: string }>(
    `SELECT meeting_id,
            COUNT(*) FILTER (WHERE display_name IS NOT NULL) AS named,
            COUNT(*) FILTER (WHERE politician_id IS NOT NULL) AS linked
       FROM meetings.speakers
      WHERE meeting_id = ANY($1::uuid[])
      GROUP BY meeting_id`,
    [meetingIds]
  );
  const out: Record<string, SpeakerLinkCounts> = {};
  for (const r of rows) {
    out[r.meeting_id] = { named: Number(r.named), linked: Number(r.linked) };
  }
  return out;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test --prefix backend -- adminMeetingsService`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/lib/adminMeetingsService.ts backend/src/lib/adminMeetingsService.test.ts
git commit -m "feat(admin): speaker named/linked count aggregate

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task A2: Admin meetings router — reads + mount

**Files:**
- Create: `backend/src/routes/adminMeetings.ts`
- Test: `backend/src/routes/adminMeetings.test.ts`
- Modify: `backend/src/index.ts` (mount before the bare `/api/admin` router)

**Interfaces:**
- Consumes: `getMeetings`, `getMeetingById`, `getTranscriptByMeetingId`, `getSummaryByMeetingId`, `getVotesByMeetingId` from `../lib/meetingsService.js` (all accept a second arg `{ includeAllStatuses: true }`); `getSpeakerCountsByMeeting` from `../lib/adminMeetingsService.js`; `requireAuth`, `requireAdmin` middleware.
- Produces: router mounted at `/api/admin/meetings` with `GET /` (queue), `GET /:id`, `GET /:id/transcript`, `GET /:id/summary`, `GET /:id/votes`. The queue returns each `MeetingListItem` plus `named` and `linked` numbers. (The `PATCH /:id` mutation is added in Task A3.)

- [ ] **Step 1: Write the failing test**

```ts
// backend/src/routes/adminMeetings.test.ts
import { beforeEach, describe, expect, it, vi } from 'vitest';
import express from 'express';
import request from 'supertest';

const MEETING_ID = '11111111-1111-1111-1111-111111111111';

const {
  mockGetMeetings,
  mockGetMeetingById,
  mockGetTranscriptByMeetingId,
  mockGetSummaryByMeetingId,
  mockGetVotesByMeetingId,
  mockUpdateMeeting,
  mockGetSpeakerCountsByMeeting,
  mockLogAdminAction,
} = vi.hoisted(() => ({
  mockGetMeetings: vi.fn(),
  mockGetMeetingById: vi.fn(),
  mockGetTranscriptByMeetingId: vi.fn(),
  mockGetSummaryByMeetingId: vi.fn(),
  mockGetVotesByMeetingId: vi.fn(),
  mockUpdateMeeting: vi.fn(),
  mockGetSpeakerCountsByMeeting: vi.fn(),
  mockLogAdminAction: vi.fn(),
}));

vi.mock('../lib/meetingsService.js', () => ({
  getMeetings: mockGetMeetings,
  getMeetingById: mockGetMeetingById,
  getTranscriptByMeetingId: mockGetTranscriptByMeetingId,
  getSummaryByMeetingId: mockGetSummaryByMeetingId,
  getVotesByMeetingId: mockGetVotesByMeetingId,
  updateMeeting: mockUpdateMeeting,
}));
vi.mock('../lib/adminMeetingsService.js', () => ({
  getSpeakerCountsByMeeting: mockGetSpeakerCountsByMeeting,
}));
vi.mock('../lib/adminService.js', () => ({ logAdminAction: mockLogAdminAction }));

// Mutable middleware impls so a test can simulate 401/403.
let authImpl = (req: any, _res: any, next: any) => { req.userId = 'admin-1'; next(); };
let adminImpl = (_req: any, _res: any, next: any) => next();
vi.mock('../middleware/auth.js', () => ({
  requireAuth: (req: any, res: any, next: any) => authImpl(req, res, next),
}));
vi.mock('../middleware/requireAdmin.js', () => ({
  requireAdmin: (req: any, res: any, next: any) => adminImpl(req, res, next),
}));

import adminMeetingsRouter from './adminMeetings.js';

const app = express();
app.use(express.json());
app.use('/api/admin/meetings', adminMeetingsRouter);

beforeEach(() => {
  vi.clearAllMocks();
  authImpl = (req: any, _res: any, next: any) => { req.userId = 'admin-1'; next(); };
  adminImpl = (_req: any, _res: any, next: any) => next();
});

describe('GET /api/admin/meetings', () => {
  it('lists drafts with includeAllStatuses and merged named/linked counts', async () => {
    mockGetMeetings.mockResolvedValueOnce([
      { id: MEETING_ID, title: 'US House Floor', status: 'draft', speakerCount: 48 },
    ]);
    mockGetSpeakerCountsByMeeting.mockResolvedValueOnce({ [MEETING_ID]: { named: 33, linked: 32 } });

    const res = await request(app).get('/api/admin/meetings?status=draft');

    expect(res.status).toBe(200);
    expect(mockGetMeetings).toHaveBeenCalledWith({ status: 'draft' }, { includeAllStatuses: true });
    expect(res.body).toEqual([
      { id: MEETING_ID, title: 'US House Floor', status: 'draft', speakerCount: 48, named: 33, linked: 32 },
    ]);
  });

  it('defaults to status=draft when no status query is given', async () => {
    mockGetMeetings.mockResolvedValueOnce([]);
    mockGetSpeakerCountsByMeeting.mockResolvedValueOnce({});
    await request(app).get('/api/admin/meetings');
    expect(mockGetMeetings).toHaveBeenCalledWith({ status: 'draft' }, { includeAllStatuses: true });
  });

  it('returns 401 when not authenticated', async () => {
    authImpl = (_req: any, res: any) => res.status(401).json({ code: 'UNAUTHENTICATED' });
    const res = await request(app).get('/api/admin/meetings');
    expect(res.status).toBe(401);
    expect(mockGetMeetings).not.toHaveBeenCalled();
  });

  it('returns 403 when authenticated but not an admin', async () => {
    adminImpl = (_req: any, res: any) => res.status(403).json({ error: 'Admin access required' });
    const res = await request(app).get('/api/admin/meetings');
    expect(res.status).toBe(403);
    expect(mockGetMeetings).not.toHaveBeenCalled();
  });
});

describe('GET /api/admin/meetings/:id', () => {
  it('returns a draft meeting with includeAllStatuses', async () => {
    mockGetMeetingById.mockResolvedValueOnce({ id: MEETING_ID, status: 'draft', speakers: [] });
    const res = await request(app).get(`/api/admin/meetings/${MEETING_ID}`);
    expect(res.status).toBe(200);
    expect(mockGetMeetingById).toHaveBeenCalledWith(MEETING_ID, { includeAllStatuses: true });
  });

  it('404s a missing meeting', async () => {
    mockGetMeetingById.mockResolvedValueOnce(null);
    const res = await request(app).get(`/api/admin/meetings/${MEETING_ID}`);
    expect(res.status).toBe(404);
  });

  it('422s a malformed id', async () => {
    const res = await request(app).get('/api/admin/meetings/not-a-uuid');
    expect(res.status).toBe(422);
    expect(mockGetMeetingById).not.toHaveBeenCalled();
  });
});

describe('GET /api/admin/meetings/:id/transcript', () => {
  it('passes page and includeAllStatuses', async () => {
    mockGetTranscriptByMeetingId.mockResolvedValueOnce({ segments: [], page: 2, totalCount: 0 });
    const res = await request(app).get(`/api/admin/meetings/${MEETING_ID}/transcript?page=2`);
    expect(res.status).toBe(200);
    expect(mockGetTranscriptByMeetingId).toHaveBeenCalledWith(MEETING_ID, 2, { includeAllStatuses: true });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix backend -- adminMeetings.test`
Expected: FAIL — cannot find module `./adminMeetings.js`.

- [ ] **Step 3: Write minimal implementation**

```ts
// backend/src/routes/adminMeetings.ts
/**
 * adminMeetings.ts — /api/admin/meetings/* handlers for the Project-2 review panel.
 *
 * requireAuth + requireAdmin gate the whole router. Reads use the meetings
 * service with { includeAllStatuses: true } so drafts are visible here (and only
 * here). The status PATCH is logged via logAdminAction (ADMN-05).
 */
import { Router, type Request, type Response } from 'express';
import { requireAuth, type AuthenticatedRequest } from '../middleware/auth.js';
import { requireAdmin } from '../middleware/requireAdmin.js';
import {
  getMeetings,
  getMeetingById,
  getTranscriptByMeetingId,
  getSummaryByMeetingId,
  getVotesByMeetingId,
  updateMeeting,
} from '../lib/meetingsService.js';
import { getSpeakerCountsByMeeting } from '../lib/adminMeetingsService.js';
import { logAdminAction } from '../lib/adminService.js';

const UUID_REGEX =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ADMIN_STATUSES = new Set(['draft', 'published', 'archived']);
const VIEW = { includeAllStatuses: true } as const;

function actorId(req: Request): string {
  return (req as AuthenticatedRequest).userId ?? 'unknown';
}

const router = Router();
router.use(requireAuth, requireAdmin);

// GET /api/admin/meetings?status=draft — the review queue.
router.get('/', async (req: Request, res: Response): Promise<void> => {
  const status = typeof req.query.status === 'string' ? req.query.status : 'draft';
  try {
    const meetings = await getMeetings({ status }, VIEW);
    const counts = await getSpeakerCountsByMeeting(meetings.map((m) => m.id));
    res.status(200).json(
      meetings.map((m) => ({
        ...m,
        named: counts[m.id]?.named ?? 0,
        linked: counts[m.id]?.linked ?? 0,
      }))
    );
  } catch (err) {
    console.error('[GET /admin/meetings] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

// GET /api/admin/meetings/:id/transcript?page=N — registered before /:id.
router.get('/:id/transcript', async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }
  const page = Math.max(1, Number(req.query.page) || 1);
  try {
    res.status(200).json(await getTranscriptByMeetingId(id, page, VIEW));
  } catch (err) {
    console.error('[GET /admin/meetings/:id/transcript] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

// GET /api/admin/meetings/:id/summary
router.get('/:id/summary', async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }
  try {
    const summary = await getSummaryByMeetingId(id, VIEW);
    if (!summary) { res.status(404).json({ code: 'NOT_FOUND', message: 'No summary' }); return; }
    res.status(200).json(summary);
  } catch (err) {
    console.error('[GET /admin/meetings/:id/summary] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

// GET /api/admin/meetings/:id/votes
router.get('/:id/votes', async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }
  try {
    res.status(200).json(await getVotesByMeetingId(id, VIEW));
  } catch (err) {
    console.error('[GET /admin/meetings/:id/votes] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

// GET /api/admin/meetings/:id — meeting detail (+ speakers).
router.get('/:id', async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }
  try {
    const meeting = await getMeetingById(id, VIEW);
    if (!meeting) { res.status(404).json({ code: 'NOT_FOUND', message: 'Meeting not found' }); return; }
    res.status(200).json(meeting);
  } catch (err) {
    console.error('[GET /admin/meetings/:id] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});

export default router;
```

Note: the `updateMeeting` / `logAdminAction` imports and `actorId` / `ADMIN_STATUSES` are used by Task A3's `PATCH` handler, added in the same file next. They are imported now so A3 only adds the route block.

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test --prefix backend -- adminMeetings.test`
Expected: PASS.

- [ ] **Step 5: Mount the router in `index.ts`**

In `backend/src/index.ts`, add the import near the other route imports (by `import meetingsRouter from './routes/meetings.js';` at line 53):

```ts
import adminMeetingsRouter from './routes/adminMeetings.js';
```

Then mount it **before** the bare `/api/admin` routers. Immediately after the seasons mount (`app.use('/api/admin/seasons', seasonsAdminRouter);`, ~line 149) add:

```ts
// Project-2 House-floor review panel (authenticated). Mounted before the bare
// /api/admin routers so the specific prefix is not shadowed.
app.use('/api/admin/meetings', adminMeetingsRouter);
```

- [ ] **Step 6: Verify the build and full backend test suite**

Run: `npm run build --prefix backend && npm test --prefix backend`
Expected: build OK; suite green (existing tests + the new file).

- [ ] **Step 7: Commit**

```bash
git add backend/src/routes/adminMeetings.ts backend/src/routes/adminMeetings.test.ts backend/src/index.ts
git commit -m "feat(admin): /api/admin/meetings read routes (draft-visible)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task A3: Status mutation (promote / archive)

**Files:**
- Modify: `backend/src/routes/adminMeetings.ts` (add `PATCH /:id`)
- Modify: `backend/src/routes/adminMeetings.test.ts` (add mutation tests)

**Interfaces:**
- Consumes: `getMeetingById` (resolve current status / existence), `updateMeeting({ status })`, `logAdminAction` (all already imported in A2).
- Produces: `PATCH /api/admin/meetings/:id { status }` where `status ∈ {draft, published, archived}`; returns the updated meeting; logs `meeting_status_change` with `{ meetingId, from, to }`.

- [ ] **Step 1: Write the failing test** (append to `adminMeetings.test.ts`)

```ts
describe('PATCH /api/admin/meetings/:id', () => {
  it('promotes a draft to published and logs the action', async () => {
    mockGetMeetingById.mockResolvedValueOnce({ id: MEETING_ID, status: 'draft' });
    mockUpdateMeeting.mockResolvedValueOnce({ id: MEETING_ID, status: 'published' });

    const res = await request(app)
      .patch(`/api/admin/meetings/${MEETING_ID}`)
      .send({ status: 'published' });

    expect(res.status).toBe(200);
    expect(res.body.status).toBe('published');
    expect(mockUpdateMeeting).toHaveBeenCalledWith(MEETING_ID, { status: 'published' });
    expect(mockLogAdminAction).toHaveBeenCalledWith(
      'admin-1',
      'meeting_status_change',
      null,
      { meetingId: MEETING_ID, from: 'draft', to: 'published' }
    );
  });

  it('archives a draft', async () => {
    mockGetMeetingById.mockResolvedValueOnce({ id: MEETING_ID, status: 'draft' });
    mockUpdateMeeting.mockResolvedValueOnce({ id: MEETING_ID, status: 'archived' });
    const res = await request(app)
      .patch(`/api/admin/meetings/${MEETING_ID}`)
      .send({ status: 'archived' });
    expect(res.status).toBe(200);
    expect(mockUpdateMeeting).toHaveBeenCalledWith(MEETING_ID, { status: 'archived' });
  });

  it('422s an unsupported status', async () => {
    const res = await request(app)
      .patch(`/api/admin/meetings/${MEETING_ID}`)
      .send({ status: 'deleted' });
    expect(res.status).toBe(422);
    expect(mockUpdateMeeting).not.toHaveBeenCalled();
  });

  it('404s when the meeting does not exist', async () => {
    mockGetMeetingById.mockResolvedValueOnce(null);
    const res = await request(app)
      .patch(`/api/admin/meetings/${MEETING_ID}`)
      .send({ status: 'published' });
    expect(res.status).toBe(404);
    expect(mockUpdateMeeting).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test --prefix backend -- adminMeetings.test`
Expected: FAIL — PATCH returns 404 (no route) so status assertions fail.

- [ ] **Step 3: Write minimal implementation** — add before `export default router;` in `adminMeetings.ts`

```ts
// PATCH /api/admin/meetings/:id — promote (published) / archive (archived) / back to draft.
router.patch('/:id', async (req: Request, res: Response): Promise<void> => {
  const id = req.params.id as string;
  if (!UUID_REGEX.test(id)) {
    res.status(422).json({ code: 'INVALID_ID', message: 'Invalid UUID format' });
    return;
  }
  const status = (req.body ?? {}).status;
  if (typeof status !== 'string' || !ADMIN_STATUSES.has(status)) {
    res.status(422).json({
      code: 'VALIDATION_ERROR',
      message: 'status must be one of: draft, published, archived',
    });
    return;
  }
  try {
    const current = await getMeetingById(id, VIEW);
    if (!current) { res.status(404).json({ code: 'NOT_FOUND', message: 'Meeting not found' }); return; }
    const updated = await updateMeeting(id, { status });
    await logAdminAction(actorId(req), 'meeting_status_change', null, {
      meetingId: id, from: current.status, to: status,
    });
    res.status(200).json(updated);
  } catch (err) {
    console.error('[PATCH /admin/meetings/:id] error:', err);
    res.status(500).json({ code: 'INTERNAL_ERROR', message: 'An unexpected error occurred' });
  }
});
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test --prefix backend -- adminMeetings.test`
Expected: PASS (all read + mutation tests).

- [ ] **Step 5: Commit**

```bash
git add backend/src/routes/adminMeetings.ts backend/src/routes/adminMeetings.test.ts
git commit -m "feat(admin): PATCH status flip (promote/archive) with audit log

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

- [ ] **Step 6: Open PR A**

```bash
git push -u origin feat/admin-meetings-review
gh pr create --repo EmpoweredVote/ev-accounts --base master \
  --title "Admin meetings review API (Project 2)" \
  --body "Authenticated /api/admin/meetings reads (draft-visible via includeAllStatuses) + status PATCH (promote/archive) with audit logging. No schema change.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## PR B — on-the-record `web/` admin UI

> Run all `web/` commands from the `web/` directory. Tests: `npm test` (runs `lib/**/*.test.ts`).

### Task B1: Admin auth module

**Files:**
- Create: `web/lib/adminAuth.ts`
- Test: `web/lib/adminAuth.test.ts`

**Interfaces:**
- Produces:
  - `login(email: string, password: string): Promise<void>` — POST `/api/auth/login`; stores the returned `access_token`; throws `AuthError('INVALID_CREDENTIALS')` on 401/403.
  - `getToken(): string | null`, `logout(): void`.
  - `authedFetch(path: string, init?: RequestInit): Promise<Response>` — adds `Authorization: Bearer`; throws `AuthError('UNAUTHORIZED')` with no token or on a 401 (and clears the token).
  - `class AuthError extends Error` with `code: 'INVALID_CREDENTIALS' | 'UNAUTHORIZED' | 'NETWORK'`.

- [ ] **Step 1: Write the failing test**

```ts
// web/lib/adminAuth.test.ts
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { login, getToken, logout, authedFetch, AuthError } from "./adminAuth";

const fetchMock = vi.fn();

beforeEach(() => {
  vi.stubGlobal("fetch", fetchMock);
  vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", "https://api.test");
  fetchMock.mockReset();
  logout();
});
afterEach(() => vi.unstubAllGlobals());

describe("login", () => {
  it("stores the access token on success", async () => {
    fetchMock.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({ access_token: "tok-123", token_type: "bearer" }),
    });
    await login("chris@empowered.vote", "pw");
    expect(getToken()).toBe("tok-123");
    const [url, init] = fetchMock.mock.calls[0];
    expect(url).toBe("https://api.test/api/auth/login");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ email: "chris@empowered.vote", password: "pw" });
  });

  it("throws INVALID_CREDENTIALS on 401", async () => {
    fetchMock.mockResolvedValueOnce({ ok: false, status: 401, json: async () => ({}) });
    await expect(login("x", "y")).rejects.toMatchObject({ code: "INVALID_CREDENTIALS" });
    expect(getToken()).toBeNull();
  });
});

describe("authedFetch", () => {
  it("throws UNAUTHORIZED when there is no token", async () => {
    await expect(authedFetch("/api/admin/meetings")).rejects.toBeInstanceOf(AuthError);
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("adds the Bearer header when a token is present", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: "tok-9" }) });
    await login("a", "b");
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => [] });
    await authedFetch("/api/admin/meetings?status=draft");
    const [url, init] = fetchMock.mock.calls[1];
    expect(url).toBe("https://api.test/api/admin/meetings?status=draft");
    expect(init.headers.Authorization).toBe("Bearer tok-9");
  });

  it("clears the token and throws on a 401 response", async () => {
    fetchMock.mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ access_token: "tok-9" }) });
    await login("a", "b");
    fetchMock.mockResolvedValueOnce({ ok: false, status: 401 });
    await expect(authedFetch("/api/admin/meetings")).rejects.toMatchObject({ code: "UNAUTHORIZED" });
    expect(getToken()).toBeNull();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- adminAuth`
Expected: FAIL — cannot find module `./adminAuth`.

- [ ] **Step 3: Write minimal implementation**

```ts
// web/lib/adminAuth.ts
// Client-side auth for the /admin review panel. The static bundle holds no
// secret; real protection is the ev-accounts requireAuth+requireAdmin gate.
const TOKEN_KEY = "otr_admin_token";
let memoryToken: string | null = null;

function apiBase(): string {
  return (process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");
}

export class AuthError extends Error {
  code: "INVALID_CREDENTIALS" | "UNAUTHORIZED" | "NETWORK";
  constructor(code: "INVALID_CREDENTIALS" | "UNAUTHORIZED" | "NETWORK") {
    super(code);
    this.name = "AuthError";
    this.code = code;
  }
}

export function getToken(): string | null {
  if (memoryToken) return memoryToken;
  try {
    if (typeof sessionStorage !== "undefined") memoryToken = sessionStorage.getItem(TOKEN_KEY);
  } catch {
    /* private mode / storage disabled — memory only */
  }
  return memoryToken;
}

function setToken(token: string): void {
  memoryToken = token;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.setItem(TOKEN_KEY, token);
  } catch {
    /* ignore */
  }
}

export function logout(): void {
  memoryToken = null;
  try {
    if (typeof sessionStorage !== "undefined") sessionStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore */
  }
}

export async function login(email: string, password: string): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${apiBase()}/api/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
  } catch {
    throw new AuthError("NETWORK");
  }
  if (res.status === 401 || res.status === 403) throw new AuthError("INVALID_CREDENTIALS");
  if (!res.ok) throw new AuthError("NETWORK");
  const data = await res.json();
  if (!data?.access_token) throw new AuthError("NETWORK");
  setToken(data.access_token);
}

export async function authedFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const token = getToken();
  if (!token) throw new AuthError("UNAUTHORIZED");
  const res = await fetch(`${apiBase()}${path}`, {
    ...init,
    cache: "no-store",
    headers: { ...(init.headers ?? {}), Authorization: `Bearer ${token}` },
  });
  if (res.status === 401) {
    logout();
    throw new AuthError("UNAUTHORIZED");
  }
  return res;
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- adminAuth`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web/lib/adminAuth.ts web/lib/adminAuth.test.ts
git commit -m "feat(admin): client-side EV login + authedFetch for the review panel

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task B2: Admin fetchers + mutations

**Files:**
- Modify: `web/lib/queries.ts` (add `export` to `mapMeeting`, `mapSummary`, `mapSegment`)
- Create: `web/lib/adminQueries.ts`
- Test: `web/lib/adminQueries.test.ts`

**Interfaces:**
- Consumes: `authedFetch` (B1); the existing `mapMeeting`, `mapSummary`, `mapSegment` from `./queries`; types `Meeting`, `Segment`, `MeetingSummary`, `Vote` from `./types`.
- Produces:
  - `fetchDraftMeetings(status?: string): Promise<DraftListItem[]>` where `DraftListItem = Meeting & { named: number; linked: number }`.
  - `fetchAdminMeeting(id: string): Promise<Meeting | null>` (no published-only filter).
  - `fetchAdminSegments(id: string): Promise<Segment[]>` (paginated).
  - `fetchAdminSummary(id: string): Promise<MeetingSummary | null>`.
  - `fetchAdminVotes(id: string): Promise<Vote[]>`.
  - `promoteMeeting(id: string): Promise<void>` / `archiveMeeting(id: string): Promise<void>`.

- [ ] **Step 1: Export the mappers in `queries.ts`**

Add the `export` keyword to the three existing internal mapper declarations (names confirmed at `web/lib/queries.ts:28,69,99`):

```ts
export function mapMeeting(m: any): Meeting {   // was: function mapMeeting
export function mapSummary(s: any): MeetingSummary {  // was: function mapSummary
export function mapSegment(s: any): Segment {   // was: function mapSegment
```

Do not change their bodies. (Leave `mapPerson`/`mapAppearance`/`mapTopicEntry` untouched.)

- [ ] **Step 2: Write the failing test**

```ts
// web/lib/adminQueries.test.ts
import { describe, it, expect, vi, beforeEach } from "vitest";

const authedFetchMock = vi.fn();
vi.mock("./adminAuth", () => ({ authedFetch: (...a: unknown[]) => authedFetchMock(...a) }));

import {
  fetchDraftMeetings, fetchAdminMeeting, promoteMeeting, archiveMeeting,
} from "./adminQueries";

beforeEach(() => authedFetchMock.mockReset());

describe("fetchDraftMeetings", () => {
  it("requests the draft queue and returns the rows", async () => {
    authedFetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => [{ id: "m1", status: "draft", named: 33, linked: 32 }],
    });
    const rows = await fetchDraftMeetings();
    expect(authedFetchMock).toHaveBeenCalledWith("/api/admin/meetings?status=draft");
    expect(rows[0]).toMatchObject({ id: "m1", named: 33, linked: 32 });
  });
});

describe("fetchAdminMeeting", () => {
  it("returns a draft meeting (no published-only filter)", async () => {
    authedFetchMock.mockResolvedValueOnce({
      ok: true, status: 200,
      json: async () => ({ id: "m1", status: "draft", speakers: [] }),
    });
    const m = await fetchAdminMeeting("m1");
    expect(authedFetchMock).toHaveBeenCalledWith("/api/admin/meetings/m1");
    expect(m?.status).toBe("draft");
  });

  it("returns null on 404", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: false, status: 404 });
    expect(await fetchAdminMeeting("m1")).toBeNull();
  });
});

describe("mutations", () => {
  it("promoteMeeting PATCHes status=published", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: true, status: 200 });
    await promoteMeeting("m1");
    const [path, init] = authedFetchMock.mock.calls[0];
    expect(path).toBe("/api/admin/meetings/m1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ status: "published" });
  });

  it("archiveMeeting PATCHes status=archived", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: true, status: 200 });
    await archiveMeeting("m1");
    const [, init] = authedFetchMock.mock.calls[0];
    expect(JSON.parse(init.body)).toEqual({ status: "archived" });
  });

  it("throws when the PATCH fails", async () => {
    authedFetchMock.mockResolvedValueOnce({ ok: false, status: 500 });
    await expect(promoteMeeting("m1")).rejects.toThrow();
  });
});
```

- [ ] **Step 3: Run test to verify it fails**

Run: `npm test -- adminQueries`
Expected: FAIL — cannot find module `./adminQueries`.

- [ ] **Step 4: Write minimal implementation**

```ts
// web/lib/adminQueries.ts
// Authenticated fetchers for the /admin review panel. Kept separate from
// queries.ts so the public app stays unauthenticated and published-only.
import { authedFetch } from "./adminAuth";
import { mapMeeting, mapSummary, mapSegment } from "./queries";
import type { Meeting, Segment, MeetingSummary, Vote } from "./types";

export type DraftListItem = Meeting & { named: number; linked: number };

export async function fetchDraftMeetings(status = "draft"): Promise<DraftListItem[]> {
  const res = await authedFetch(`/api/admin/meetings?status=${encodeURIComponent(status)}`);
  if (!res.ok) return [];
  const raw = (await res.json()) as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  return raw.map((r) => ({ ...mapMeeting(r), named: Number(r.named ?? 0), linked: Number(r.linked ?? 0) }));
}

export async function fetchAdminMeeting(id: string): Promise<Meeting | null> {
  const res = await authedFetch(`/api/admin/meetings/${id}`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`admin meeting fetch failed: ${res.status}`);
  return mapMeeting(await res.json());
}

export async function fetchAdminSegments(id: string): Promise<Segment[]> {
  const all: Segment[] = [];
  for (let page = 1; ; page++) {
    const res = await authedFetch(`/api/admin/meetings/${id}/transcript?page=${page}`);
    if (!res.ok) throw new Error(`admin transcript fetch failed: ${res.status}`);
    const { segments, totalCount } = (await res.json()) as {
      segments: unknown[]; page: number; totalCount: number;
    };
    all.push(...segments.map(mapSegment));
    if (all.length >= totalCount) break;
  }
  return all;
}

export async function fetchAdminSummary(id: string): Promise<MeetingSummary | null> {
  const res = await authedFetch(`/api/admin/meetings/${id}/summary`);
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`admin summary fetch failed: ${res.status}`);
  return mapSummary(await res.json());
}

export async function fetchAdminVotes(id: string): Promise<Vote[]> {
  const res = await authedFetch(`/api/admin/meetings/${id}/votes`);
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`admin votes fetch failed: ${res.status}`);
  const raw = (await res.json()) as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  return raw.map((v) => ({
    id: v.id,
    resolution: v.resolution ?? null,
    description: v.description ?? null,
    result: v.result ?? "",
    voteType: v.voteType ?? null,
    timestamp: v.timestamp ?? null,
  }));
}

async function patchStatus(id: string, status: string): Promise<void> {
  const res = await authedFetch(`/api/admin/meetings/${id}`, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (!res.ok) throw new Error(`status change to ${status} failed: ${res.status}`);
}

export async function promoteMeeting(id: string): Promise<void> { await patchStatus(id, "published"); }
export async function archiveMeeting(id: string): Promise<void> { await patchStatus(id, "archived"); }
```

- [ ] **Step 5: Run test to verify it passes**

Run: `npm test -- adminQueries`
Expected: PASS.

If the `Vote` field names above do not match `web/lib/types.ts`, use the field names in that file — the shape must equal what `fetchVotes` in `queries.ts` returns (that function is the reference).

- [ ] **Step 6: Commit**

```bash
git add web/lib/queries.ts web/lib/adminQueries.ts web/lib/adminQueries.test.ts
git commit -m "feat(admin): draft-visible admin fetchers + promote/archive mutations

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task B3: `MeetingView` accepts an action bar

**Files:**
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx`

**Interfaces:**
- Consumes: nothing new.
- Produces: `MeetingView` gains an optional prop `actionBar?: React.ReactNode`, rendered at the top of the view. The existing public caller (`MeetingDetailClient`) passes nothing and is unchanged.

- [ ] **Step 1: Add the prop**

In `web/app/meetings/[meetingId]/MeetingView.tsx`, extend the props destructure + type (currently `{ meeting, segments, outline = [], votes = [] }`):

```tsx
export default function MeetingView({
  meeting,
  segments,
  outline = [],
  votes = [],
  actionBar,
}: {
  meeting: Meeting;
  segments: Segment[];
  outline?: SummarySection[];
  votes?: Vote[];
  actionBar?: React.ReactNode;
}) {
```

- [ ] **Step 2: Render it**

Find the top of the returned JSX (the outermost wrapper of the view) and render the action bar first when present. Add, immediately inside the outermost returned element:

```tsx
{actionBar ? <div className="adminActionBar">{actionBar}</div> : null}
```

- [ ] **Step 3: Verify the build still passes**

Run (from `web/`): `npm run build`
Expected: build succeeds; the public meeting page is unchanged (no caller passes `actionBar`).

- [ ] **Step 4: Add the action-bar style**

Append to `web/app/globals.css`:

```css
.adminActionBar {
  display: flex;
  gap: 0.75rem;
  align-items: center;
  padding: 0.75rem 1rem;
  margin-bottom: 1rem;
  border: 1px solid var(--accent);
  border-radius: 8px;
  background: var(--background);
}
```

- [ ] **Step 5: Commit**

```bash
git add web/app/meetings/[meetingId]/MeetingView.tsx web/app/globals.css
git commit -m "feat(admin): optional actionBar slot on MeetingView

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task B4: Admin gate + draft queue page

**Files:**
- Create: `web/components/AdminGate.tsx`
- Create: `web/app/admin/floor/page.tsx`
- Create: `web/app/admin/floor/FloorQueueClient.tsx`
- Test: `web/lib/adminRow.ts` + `web/lib/adminRow.test.ts` (pure row-formatting helper, so the queue has real test coverage)

**Interfaces:**
- Consumes: `login`, `getToken`, `logout`, `AuthError` (B1); `fetchDraftMeetings`, `promoteMeeting`, `archiveMeeting`, `DraftListItem` (B2).
- Produces: `AdminGate` (renders a login form until a token exists, then its children); `/admin/floor` static page; `draftRowView(item)` formatting helper.

- [ ] **Step 1: Write the failing test for the row helper**

```ts
// web/lib/adminRow.test.ts
import { describe, it, expect } from "vitest";
import { draftRowView } from "./adminRow";

describe("draftRowView", () => {
  it("formats gate verdict and coverage from processingMetadata", () => {
    const v = draftRowView({
      id: "m1", date: "2026-09-02", title: "US House Floor", durationSeconds: 33660,
      speakerCount: 67, named: 52, linked: 42,
      processingMetadata: { gate_verdict: "review", gate_coverage: 0.63 },
    } as any);
    expect(v.gateVerdict).toBe("review");
    expect(v.coverage).toBe("63%");
    expect(v.counts).toBe("67 speakers · 52 named · 42 linked");
    expect(v.duration).toBe("9h 21m");
  });

  it("handles missing metadata (pre-fix drafts)", () => {
    const v = draftRowView({
      id: "m2", date: "2026-09-10", title: "Pro forma", durationSeconds: 180,
      speakerCount: 3, named: 0, linked: 0, processingMetadata: null,
    } as any);
    expect(v.gateVerdict).toBe("—");
    expect(v.coverage).toBe("—");
    expect(v.duration).toBe("3m");
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm test -- adminRow`
Expected: FAIL — cannot find `./adminRow`.

- [ ] **Step 3: Implement the row helper**

```ts
// web/lib/adminRow.ts
import type { DraftListItem } from "./adminQueries";

export interface DraftRowView {
  id: string;
  date: string;
  title: string;
  duration: string;
  gateVerdict: string;
  coverage: string;
  counts: string;
}

function fmtDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "—";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}

export function draftRowView(item: DraftListItem): DraftRowView {
  const meta = (item.processingMetadata ?? {}) as { gate_verdict?: string; gate_coverage?: number };
  const cov = typeof meta.gate_coverage === "number" ? `${Math.round(meta.gate_coverage * 100)}%` : "—";
  return {
    id: item.id,
    date: item.date ?? "—",
    title: item.title ?? "Untitled",
    duration: fmtDuration(item.durationSeconds ?? null),
    gateVerdict: meta.gate_verdict ?? "—",
    coverage: cov,
    counts: `${item.speakerCount ?? 0} speakers · ${item.named} named · ${item.linked} linked`,
  };
}
```

If `processingMetadata` / `durationSeconds` / `speakerCount` are named differently on the `Meeting` type, use the actual names from `web/lib/types.ts` (they are the fields `mapMeeting` populates).

- [ ] **Step 4: Run test to verify it passes**

Run: `npm test -- adminRow`
Expected: PASS.

- [ ] **Step 5: Implement `AdminGate`**

```tsx
// web/components/AdminGate.tsx
"use client";
import { useEffect, useState } from "react";
import { login, getToken, logout, AuthError } from "@/lib/adminAuth";

export default function AdminGate({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [authed, setAuthed] = useState(false);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    setAuthed(!!getToken());
    setReady(true);
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(email, password);
      setAuthed(true);
    } catch (err) {
      setError(
        err instanceof AuthError && err.code === "INVALID_CREDENTIALS"
          ? "Invalid email or password."
          : "Could not reach the server. Try again."
      );
    } finally {
      setBusy(false);
    }
  }

  if (!ready) return null;

  if (!authed) {
    return (
      <div className="adminLogin">
        <h1>Admin sign in</h1>
        <form onSubmit={onSubmit}>
          <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
          <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} required /></label>
          {error ? <p className="adminError">{error}</p> : null}
          <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        </form>
      </div>
    );
  }

  return (
    <>
      <div className="adminBar">
        <span>Admin</span>
        <button onClick={() => { logout(); setAuthed(false); }}>Sign out</button>
      </div>
      {children}
    </>
  );
}
```

- [ ] **Step 6: Implement the queue client**

```tsx
// web/app/admin/floor/FloorQueueClient.tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AdminGate from "@/components/AdminGate";
import { fetchDraftMeetings, promoteMeeting, archiveMeeting, type DraftListItem } from "@/lib/adminQueries";
import { draftRowView } from "@/lib/adminRow";

function Queue() {
  const [rows, setRows] = useState<DraftListItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setError(null);
    try {
      setRows(await fetchDraftMeetings("draft"));
    } catch {
      setError("Could not load drafts.");
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  async function act(id: string, fn: (id: string) => Promise<void>, verb: string) {
    if (!confirm(`${verb} this meeting?`)) return;
    setBusyId(id);
    try {
      await fn(id);
      await load();
    } catch {
      setError(`Could not ${verb.toLowerCase()} the meeting.`);
    } finally {
      setBusyId(null);
    }
  }

  if (error) return <div className="adminError"><p>{error}</p><button onClick={load}>Retry</button></div>;
  if (!rows) return <p>Loading drafts…</p>;
  if (rows.length === 0) return <p>No drafts awaiting review.</p>;

  return (
    <table className="adminQueue">
      <thead><tr><th>Date</th><th>Meeting</th><th>Duration</th><th>Gate</th><th>Coverage</th><th>Speakers</th><th>Actions</th></tr></thead>
      <tbody>
        {rows.map((r) => {
          const v = draftRowView(r);
          return (
            <tr key={v.id}>
              <td>{v.date}</td>
              <td><Link href={`/admin/meetings/${v.id}`}>{v.title}</Link></td>
              <td>{v.duration}</td>
              <td>{v.gateVerdict}</td>
              <td>{v.coverage}</td>
              <td>{v.counts}</td>
              <td>
                <Link href={`/admin/meetings/${v.id}`}>Review</Link>{" "}
                <button disabled={busyId === v.id} onClick={() => act(v.id, promoteMeeting, "Promote")}>Promote</button>{" "}
                <button disabled={busyId === v.id} onClick={() => act(v.id, archiveMeeting, "Archive")}>Archive</button>
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export default function FloorQueueClient() {
  return <AdminGate><h1>House-floor draft review</h1><Queue /></AdminGate>;
}
```

```tsx
// web/app/admin/floor/page.tsx
import FloorQueueClient from "./FloorQueueClient";
export default function AdminFloorPage() {
  return <FloorQueueClient />;
}
```

- [ ] **Step 7: Verify the build**

Run (from `web/`): `npm run build`
Expected: build succeeds; `/admin/floor/index.html` is emitted.

- [ ] **Step 8: Commit**

```bash
git add web/components/AdminGate.tsx web/app/admin/floor web/lib/adminRow.ts web/lib/adminRow.test.ts
git commit -m "feat(admin): login gate + House-floor draft queue

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task B5: Review page + render.yaml rewrite

**Files:**
- Create: `web/app/admin/meetings/[meetingId]/page.tsx`
- Create: `web/app/admin/meetings/[meetingId]/AdminMeetingClient.tsx`
- Modify: `render.yaml` (repo root) — add the `/admin/meetings/*` rewrite

**Interfaces:**
- Consumes: `AdminGate` (B4); `fetchAdminMeeting`, `fetchAdminSegments`, `fetchAdminSummary`, `fetchAdminVotes`, `promoteMeeting`, `archiveMeeting` (B2); `MeetingView` with `actionBar` (B3); `usePathParam` (existing), `buildOutline` (existing, used by the public client).
- Produces: `/admin/meetings/[meetingId]` review page (sentinel export).

- [ ] **Step 1: The sentinel page**

```tsx
// web/app/admin/meetings/[meetingId]/page.tsx
import AdminMeetingClient from "./AdminMeetingClient";
// One sentinel so output:"export" emits a single shell; render.yaml rewrites
// /admin/meetings/* to this shell and the client reads the id from the URL.
export function generateStaticParams() {
  return [{ meetingId: "view" }];
}
export default function AdminMeetingPage() {
  return <AdminMeetingClient />;
}
```

- [ ] **Step 2: The review client**

Mirror the public `MeetingDetailClient` (`web/app/meetings/[meetingId]/MeetingDetailClient.tsx`) for id resolution, outline building, and loading/error handling, but use the admin fetchers and pass an `actionBar`. Read that file first and match its `usePathParam` index and `buildOutline` import.

```tsx
// web/app/admin/meetings/[meetingId]/AdminMeetingClient.tsx
"use client";
import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import AdminGate from "@/components/AdminGate";
import MeetingView from "@/app/meetings/[meetingId]/MeetingView";
import { usePathParam } from "@/lib/usePathParam";
import { buildOutline } from "@/lib/outline";
import {
  fetchAdminMeeting, fetchAdminSegments, fetchAdminSummary, fetchAdminVotes,
  promoteMeeting, archiveMeeting,
} from "@/lib/adminQueries";
import type { Meeting, Segment, MeetingSummary, Vote } from "@/lib/types";

function Review({ id }: { id: string }) {
  const [meeting, setMeeting] = useState<Meeting | null>(null);
  const [segments, setSegments] = useState<Segment[]>([]);
  const [summary, setSummary] = useState<MeetingSummary | null>(null);
  const [votes, setVotes] = useState<Vote[]>([]);
  const [state, setState] = useState<"loading" | "ready" | "notfound" | "error">("loading");
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setState("loading");
    try {
      const m = await fetchAdminMeeting(id);
      if (!m) { setState("notfound"); return; }
      const [seg, sum, vts] = await Promise.all([
        fetchAdminSegments(id), fetchAdminSummary(id), fetchAdminVotes(id),
      ]);
      setMeeting(m); setSegments(seg); setSummary(sum); setVotes(vts);
      setState("ready");
    } catch {
      setState("error");
    }
  }, [id]);

  useEffect(() => { load(); }, [load]);

  async function act(fn: (id: string) => Promise<void>, verb: string) {
    if (!confirm(`${verb} this meeting?`)) return;
    setBusy(true);
    try {
      await fn(id);
      window.location.href = "/admin/floor";
    } catch {
      alert(`Could not ${verb.toLowerCase()} the meeting.`);
      setBusy(false);
    }
  }

  if (state === "loading") return <p>Loading…</p>;
  if (state === "notfound") return <p>Draft not found. <Link href="/admin/floor">Back to queue</Link></p>;
  if (state === "error") return <div className="adminError"><p>Could not load this draft.</p><button onClick={load}>Retry</button></div>;

  const actionBar = (
    <>
      <Link href="/admin/floor">← Queue</Link>
      <span>Status: {meeting!.status}</span>
      <button disabled={busy} onClick={() => act(promoteMeeting, "Promote")}>Promote to live</button>
      <button disabled={busy} onClick={() => act(archiveMeeting, "Archive")}>Archive</button>
    </>
  );

  return (
    <MeetingView
      meeting={meeting!}
      segments={segments}
      outline={buildOutline(summary?.sections)}
      votes={votes}
      actionBar={actionBar}
    />
  );
}

export default function AdminMeetingClient() {
  const id = usePathParam(2); // /admin/meetings/<id> — confirm index vs public client
  return <AdminGate>{id ? <Review id={id} /> : <p>Loading…</p>}</AdminGate>;
}
```

**Confirm before finishing:** open the public `MeetingDetailClient.tsx` and match (a) the exact `usePathParam(n)` index for this deeper path (`/admin/meetings/<id>` has one more segment than `/meetings/<id>`), and (b) the exact `buildOutline` import path and argument. Fix the two lines flagged above to match.

- [ ] **Step 3: Add the render.yaml rewrite**

In the repo-root `render.yaml`, under the static site's `routes:`, add (next to the existing `/meetings/*` rewrite):

```yaml
      - { type: rewrite, source: /admin/meetings/*, destination: /admin/meetings/view/index.html }
```

`/admin/floor` is a plain static path (`trailingSlash: true` emits `/admin/floor/index.html`) and needs no rewrite.

- [ ] **Step 4: Verify the build**

Run (from `web/`): `npm run build`
Expected: build succeeds; `/admin/meetings/view/index.html` and `/admin/floor/index.html` are emitted. Then run the full web test suite: `npm test` — all green.

- [ ] **Step 5: Browser smoke test**

Create `.claude/launch.json` if absent with a `web` dev-server entry (`npm run dev`, port 3000), then `preview_start` it. With `NEXT_PUBLIC_EV_ACCOUNTS_URL` set to the prod API in `web/.env.local`, load `/admin/floor`:
- Expected: the login form renders (no token yet).
- `read_console_messages` / `read_network_requests`: no unexpected errors; no `/api/admin/*` call fires before login (the gate blocks it).

Full authenticated end-to-end (real login → queue populated with the 5 prod drafts → promote) is a **go-live verification** for the operator, since it needs real admin credentials (see below).

- [ ] **Step 6: Commit and open PR B**

```bash
git add "web/app/admin/meetings" render.yaml
git commit -m "feat(admin): draft review page (reuses MeetingView) + admin route rewrite

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
git push -u origin claude/clever-shamir-5b50d5
gh pr create --repo EmpoweredVote/on-the-record --base main \
  --title "Admin review panel UI for House-floor drafts (Project 2)" \
  --body "Adds /admin/floor (draft queue) and /admin/meetings/[id] (review) behind a client-side EV login. Reuses MeetingView for transcript + speakers + HLS playback. Depends on ev-accounts /api/admin/meetings.

🤖 Generated with [Claude Code](https://claude.com/claude-code)"
```

---

## Go-live checklist (operator; after both PRs merge)

1. **Deploy order:** merge and deploy **PR A (ev-accounts)** first (auto-deploys on `master` merge), then PR B.
2. **Admin user:** insert the operator's EV user id into `public.admin_users` (one row).
3. **Status value:** confirm no `CHECK` constraint on `meetings.meetings.status` blocks `archived` (`\d meetings.meetings`, or query `information_schema.check_constraints`). None is expected.
4. **CORS:** confirm the web site origin (`https://otr.empowered.vote`) is in ev-accounts `CORS_ORIGIN`. It already is for the public app; no change expected.
5. **Env:** confirm `NEXT_PUBLIC_EV_ACCOUNTS_URL` is set on the Render static site.
6. **Rebuild web:** merge PR B to `main` and trigger the Render static build.
7. **End-to-end check:** sign in at `/admin/floor`, confirm the 5 prod drafts list with gate + counts, open one, play audio, promote a pro-forma test draft to `published`, confirm it appears publicly, then set it back to `draft` (or archive) if it was only a test.

---

## Self-Review

**Spec coverage:**
- List drafts + gate verdict + coverage + named/linked → Task A2 (queue endpoint) + B4 (queue UI + `draftRowView`). ✓
- Open a draft, transcript with CREC attribution (name, linked, confidence, method), read-only → Task A2 (detail/transcript reads) + B5 (review page reuses `MeetingView`, which already renders `ProvenanceBadge` + speaker maps). ✓
- Play source audio at any segment (HLS, no local files) → reused `FilePlayer` in `MeetingView` (B5); playback_url comes from the meeting row. ✓
- Promote (→published) / archive (→archived) / hold (no-op) → Task A3 + B2 mutations + B4/B5 buttons. ✓
- Auth (EV login + requireAdmin, server-enforced, never expose drafts/promote unauth) → Task A2/A3 (`requireAuth+requireAdmin`) + B1 (login/token) + B4 (`AdminGate`). ✓
- Mutation in ev-accounts API, not run_local → Task A3. ✓
- UI in web/, reuse viewer → Tasks B3–B5. ✓
- No relabel; no person/topic/search draft visibility; no migration → nothing in the plan adds them. ✓
- Safety (public endpoints unchanged; admin fetchers isolated from queries.ts) → B2 creates a separate module; queries.ts only gains `export` on 3 mappers. ✓

**Placeholder scan:** No TBD/TODO. Two spots ask the implementer to confirm a name/index against an existing file (`Vote` fields in B2 Step 5; `usePathParam` index + `buildOutline` in B5 Step 2) — each names the reference file and the exact lines to adjust, with working default code, not a blank. Acceptable.

**Type consistency:** `getSpeakerCountsByMeeting` return shape `{named,linked}` matches A2's merge and B2's `DraftListItem`. `authedFetch(path, init)` signature is identical across B1/B2. `AuthError.code` union is consistent. Mutation names `promoteMeeting`/`archiveMeeting` match across B2/B4/B5. `MeetingView` `actionBar` prop matches B3↔B5. Status set `{draft,published,archived}` matches A3 server + B2 client.
