# Evidence Review Surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An ev-accounts admin surface to work the `inform.evidence_items` pending queue per candidate (accept / reject-with-reason / re-home), capturing categorized feedback and a rolling-metrics view.

**Architecture:** Mirror the existing `ResearchReviewPage` pattern — a backend service (`evidenceReviewService.ts`, raw pg over `pool`, lazy `await import('./db.js')`) + auth-gated routes in `admin.ts` + a React page (`EvidenceReviewPage.tsx`, `apiFetch`). One small gated migration adds `review_reason`.

**Tech Stack:** ev-accounts — TypeScript/ESM backend (node-postgres, Vitest with db mocked), React admin (react-router, `apiFetch`). Raw SQL migration, steward-allocated, hand-applied.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-20-evidence-review-surface-design.md` (in the on-the-record repo).
- All work is in **ev-accounts** (`~/Documents/GitHub/ev-accounts`). Commit locally; **do NOT push/merge**, and **do NOT apply** the migration — Chris does. Trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
- Auth: routes under the existing `router.use(requireAuth, requireAdmin)`; reviewer id = `actorId(req)` (= `(req as AuthenticatedRequest).userId`) — never from the client body.
- Reject-reason enum (app + DB CHECK, keep in sync): `off-question`, `goal-only`, `not-verbatim`, `not-primary`, `not-forward`, `is-attack`, `stale`, `other`. `wrong-tag` is NOT a reject reason — it is handled by **re-home**.
- Re-home updates `topic_id` + `issue` (verbatim text untouched) and leaves `review_status='pending'` (accept confirms separately).
- Service DB access: `const { pool } = await import('./db.js');` then `pool.query(sql, params)` (lazy import so `vi.mock('./db.js')` works). Vitest db-mock convention: `const { mockQuery } = vi.hoisted(() => ({ mockQuery: vi.fn() })); vi.mock('./db.js', () => ({ pool: { query: mockQuery } }));`.
- Run backend tests: `cd ~/Documents/GitHub/ev-accounts/backend && npm test` (or the repo's vitest invocation). Admin: `cd ~/Documents/GitHub/ev-accounts/admin && npm run build` + `npx tsc --noEmit`.

## File Structure (ev-accounts)

- Create `backend/migrations/<steward-slot>_evidence_review_reason.sql` — add `review_reason` + CHECK + DO $$ gate.
- Create `backend/src/lib/evidenceReviewService.ts` — the service.
- Create `backend/src/lib/evidenceReviewService.test.ts` — Vitest (db mocked).
- Modify `backend/src/routes/admin.ts` — import the service + add the 6 routes.
- Create `admin/src/pages/admin/EvidenceReviewPage.tsx` — the page.
- Create `admin/src/pages/admin/evidenceReviewFormat.ts` + `evidenceReviewFormat.test.ts` — pure helpers + unit test.
- Modify `admin/src/App.tsx` — import + `<Route>` + nav link.

---

### Task 1: Migration — add `review_reason` (gated)

**Files:** Create `~/Documents/GitHub/ev-accounts/backend/migrations/<slot>_evidence_review_reason.sql`

- [ ] **Step 1: Allocate the slot**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run steward -- slot shared --purpose "evidence_items.review_reason"` (if `slot shared` isn't right, `npm run steward -- --help`; report what you used). Use the reserved number as `<slot>`.

- [ ] **Step 2: Write the SQL**

```sql
-- <slot>_evidence_review_reason.sql
-- Structured reject reason for the evidence review surface. Idempotent; hand-applied.
ALTER TABLE inform.evidence_items ADD COLUMN IF NOT EXISTS review_reason text;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'evidence_items_review_reason_check'
      AND conrelid = 'inform.evidence_items'::regclass
  ) THEN
    ALTER TABLE inform.evidence_items
      ADD CONSTRAINT evidence_items_review_reason_check
      CHECK (review_reason IS NULL OR review_reason IN
        ('off-question','goal-only','not-verbatim','not-primary','not-forward',
         'is-attack','stale','other'));
  END IF;
  IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                 WHERE table_schema='inform' AND table_name='evidence_items'
                   AND column_name='review_reason') THEN
    RAISE EXCEPTION 'review_reason column missing after migration';
  END IF;
END $$;
```

- [ ] **Step 3: Structural self-check (no apply)** — read the file back; confirm the filename uses the reserved slot. Do NOT connect/apply.

- [ ] **Step 4: Commit locally in ev-accounts** (no push, no apply):

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/migrations/<slot>_evidence_review_reason.sql
git commit -m "feat(inform): evidence_items.review_reason for the review surface (gated)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Backend service `evidenceReviewService.ts`

**Files:**
- Create: `backend/src/lib/evidenceReviewService.ts`
- Test: `backend/src/lib/evidenceReviewService.test.ts`

**Interfaces (Produces):** `REJECT_REASONS: readonly string[]`; `listCandidatesWithPending()`, `listPendingEvidence(politicianId, filters)`, `acceptEvidence(id, reviewerId)`, `rejectEvidence(id, reviewerId, reason, note?)`, `rehomeEvidence(id, {topicId, issue}, reviewerId)`, `evidenceReviewMetrics()`.

- [ ] **Step 1: Write the failing test (db mocked)**

```typescript
// backend/src/lib/evidenceReviewService.test.ts
import { describe, it, expect, beforeEach, vi } from 'vitest';
const { mockQuery } = vi.hoisted(() => ({ mockQuery: vi.fn() }));
vi.mock('./db.js', () => ({ pool: { query: mockQuery } }));
import {
  acceptEvidence, rejectEvidence, rehomeEvidence, listPendingEvidence, REJECT_REASONS,
} from './evidenceReviewService.js';

beforeEach(() => { mockQuery.mockReset(); mockQuery.mockResolvedValue({ rows: [] }); });

describe('evidenceReviewService', () => {
  it('acceptEvidence sets accepted + reviewer + timestamps', async () => {
    await acceptEvidence('e1', 'admin1');
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/review_status\s*=\s*'accepted'/);
    expect(sql).toMatch(/reviewed_by/); expect(sql).toMatch(/reviewed_at\s*=\s*NOW\(\)/i);
    expect(params).toEqual(['e1', 'admin1']);
  });

  it('rejectEvidence stores reason + note and rejects', async () => {
    await rejectEvidence('e1', 'admin1', 'goal-only', 'no lever');
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/review_status\s*=\s*'rejected'/);
    expect(params).toEqual(['e1', 'admin1', 'goal-only', 'no lever']);
  });

  it('rejectEvidence rejects an invalid reason before querying', async () => {
    await expect(rejectEvidence('e1', 'admin1', 'bogus')).rejects.toThrow(/reason/i);
    expect(mockQuery).not.toHaveBeenCalled();
  });

  it('rehomeEvidence updates topic_id + issue and stays pending', async () => {
    await rehomeEvidence('e1', { topicId: 't1', issue: 'housing' }, 'admin1');
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/topic_id\s*=\s*\$2/); expect(sql).toMatch(/issue\s*=\s*\$3/);
    expect(sql).not.toMatch(/review_status\s*=\s*'accepted'/);
    expect(params).toEqual(['e1', 't1', 'housing', 'admin1']);
  });

  it('rehomeEvidence requires a non-empty issue', async () => {
    await expect(rehomeEvidence('e1', { topicId: null, issue: '' }, 'admin1')).rejects.toThrow(/issue/i);
  });

  it('listPendingEvidence filters by politician and optional issue/status', async () => {
    mockQuery.mockResolvedValueOnce({ rows: [{ id: 'e1' }] });
    const rows = await listPendingEvidence('p1', { issue: 'housing', machineStatus: 'flagged' });
    const [sql, params] = mockQuery.mock.calls[0];
    expect(sql).toMatch(/review_status\s*=\s*'pending'/);
    expect(params).toContain('p1'); expect(params).toContain('housing'); expect(params).toContain('flagged');
    expect(rows).toEqual([{ id: 'e1' }]);
  });

  it('exposes the reason enum', () => {
    expect(REJECT_REASONS).toContain('goal-only'); expect(REJECT_REASONS).not.toContain('wrong-tag');
  });
});
```

- [ ] **Step 2: Run it — FAIL** (`cd ~/Documents/GitHub/ev-accounts/backend && npm test evidenceReviewService`), expect module-not-found.

- [ ] **Step 3: Implement**

```typescript
// backend/src/lib/evidenceReviewService.ts
export const REJECT_REASONS = [
  'off-question', 'goal-only', 'not-verbatim', 'not-primary', 'not-forward',
  'is-attack', 'stale', 'other',
] as const;

export interface PendingCandidate {
  politicianId: string; name: string; pendingGreen: number; pendingFlagged: number;
}
export interface EvidenceRow {
  id: string; issue: string; topicId: string | null; evidenceType: string;
  verbatimText: string; sourceUrl: string; deepLink: string | null; context: string | null;
  sourceType: string; machineStatus: string; gateFlags: unknown; provenance: unknown; createdAt: string;
}

export async function listCandidatesWithPending(): Promise<PendingCandidate[]> {
  const { pool } = await import('./db.js');
  const { rows } = await pool.query(
    `SELECT e.politician_id AS "politicianId",
            COALESCE(p.full_name, TRIM(COALESCE(p.preferred_name,p.first_name)||' '||p.last_name)) AS name,
            COUNT(*) FILTER (WHERE e.machine_status='green')   AS "pendingGreen",
            COUNT(*) FILTER (WHERE e.machine_status='flagged') AS "pendingFlagged"
       FROM inform.evidence_items e
       JOIN essentials.politicians p ON p.id = e.politician_id
      WHERE e.review_status='pending'
      GROUP BY e.politician_id, name
      ORDER BY COUNT(*) DESC`);
  return rows;
}

export async function listPendingEvidence(
  politicianId: string, filters: { issue?: string; machineStatus?: string } = {},
): Promise<EvidenceRow[]> {
  const { pool } = await import('./db.js');
  const params: any[] = [politicianId];
  let sql =
    `SELECT id, issue, topic_id AS "topicId", evidence_type AS "evidenceType",
            verbatim_text AS "verbatimText", source_url AS "sourceUrl", deep_link AS "deepLink",
            context, source_type AS "sourceType", machine_status AS "machineStatus",
            gate_flags AS "gateFlags", provenance, created_at AS "createdAt"
       FROM inform.evidence_items
      WHERE review_status='pending' AND politician_id=$1`;
  if (filters.issue) { params.push(filters.issue); sql += ` AND issue=$${params.length}`; }
  if (filters.machineStatus) { params.push(filters.machineStatus); sql += ` AND machine_status=$${params.length}`; }
  sql += ` ORDER BY issue, created_at`;
  const { rows } = await pool.query(sql, params);
  return rows;
}

export async function acceptEvidence(id: string, reviewerId: string): Promise<void> {
  const { pool } = await import('./db.js');
  await pool.query(
    `UPDATE inform.evidence_items
       SET review_status='accepted', reviewed_by=$2, reviewed_at=NOW(), updated_at=NOW()
     WHERE id=$1`, [id, reviewerId]);
}

export async function rejectEvidence(
  id: string, reviewerId: string, reason: string, note?: string,
): Promise<void> {
  if (!REJECT_REASONS.includes(reason as any)) throw new Error(`invalid reject reason: ${reason}`);
  const { pool } = await import('./db.js');
  await pool.query(
    `UPDATE inform.evidence_items
       SET review_status='rejected', review_reason=$3, review_note=COALESCE($4, review_note),
           reviewed_by=$2, reviewed_at=NOW(), updated_at=NOW()
     WHERE id=$1`, [id, reviewerId, reason, note ?? null]);
}

export async function rehomeEvidence(
  id: string, target: { topicId: string | null; issue: string }, reviewerId: string,
): Promise<void> {
  if (!target.issue || !target.issue.trim()) throw new Error('re-home requires a non-empty issue');
  const { pool } = await import('./db.js');
  await pool.query(
    `UPDATE inform.evidence_items
       SET topic_id=$2, issue=$3, reviewed_by=$4, updated_at=NOW()
     WHERE id=$1`, [id, target.topicId, target.issue.trim(), reviewerId]);
}

export async function evidenceReviewMetrics(): Promise<{
  byStatus: any[]; byIssue: any[]; byModel: any[]; topRejectReasons: any[];
}> {
  const { pool } = await import('./db.js');
  const decided = `review_status IN ('accepted','rejected')`;
  const prec = `ROUND(AVG((review_status='accepted')::int)::numeric, 3) AS precision,
                COUNT(*) FILTER (WHERE review_status='accepted') AS accepted,
                COUNT(*) FILTER (WHERE review_status='rejected') AS rejected`;
  const [byStatus, byIssue, byModel, topRejectReasons] = await Promise.all([
    pool.query(`SELECT machine_status AS "machineStatus", ${prec} FROM inform.evidence_items WHERE ${decided} GROUP BY 1 ORDER BY 1`),
    pool.query(`SELECT issue, ${prec} FROM inform.evidence_items WHERE ${decided} GROUP BY 1 ORDER BY rejected DESC NULLS LAST LIMIT 25`),
    pool.query(`SELECT provenance->>'judge' AS model, ${prec} FROM inform.evidence_items WHERE ${decided} GROUP BY 1 ORDER BY 1`),
    pool.query(`SELECT review_reason AS reason, COUNT(*) AS n FROM inform.evidence_items WHERE review_status='rejected' AND review_reason IS NOT NULL GROUP BY 1 ORDER BY n DESC`),
  ]);
  return { byStatus: byStatus.rows, byIssue: byIssue.rows, byModel: byModel.rows, topRejectReasons: topRejectReasons.rows };
}
```

- [ ] **Step 4: Run — PASS** (`npm test evidenceReviewService`, all green).

- [ ] **Step 5: Commit** (ev-accounts local):

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/lib/evidenceReviewService.ts backend/src/lib/evidenceReviewService.test.ts
git commit -m "feat(evidence): evidence review service (accept/reject/re-home/metrics)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Backend routes in `admin.ts`

**Files:** Modify `backend/src/routes/admin.ts`

**Interfaces:** Consumes the Task-2 service + the existing `actorId(req)`.

- [ ] **Step 1: Add the import** (with the other service imports near the top):

```typescript
import {
  listCandidatesWithPending, listPendingEvidence, acceptEvidence, rejectEvidence,
  rehomeEvidence, evidenceReviewMetrics,
} from '../lib/evidenceReviewService.js';
```

- [ ] **Step 2: Add the routes** (below the existing research-review routes; already under `requireAuth, requireAdmin`):

```typescript
router.get('/evidence/candidates', async (_req, res) => {
  try { res.json(await listCandidatesWithPending()); }
  catch (err) { console.error('[admin/evidence/candidates]', err); res.status(500).json({ error: 'failed' }); }
});
router.get('/evidence/metrics', async (_req, res) => {
  try { res.json(await evidenceReviewMetrics()); }
  catch (err) { console.error('[admin/evidence/metrics]', err); res.status(500).json({ error: 'failed' }); }
});
router.get('/evidence', async (req, res) => {
  try {
    const { politician_id, issue, machine_status } = req.query as Record<string, string>;
    if (!politician_id) return res.status(400).json({ error: 'politician_id required' });
    res.json(await listPendingEvidence(politician_id, { issue, machineStatus: machine_status }));
  } catch (err) { console.error('[admin/evidence]', err); res.status(500).json({ error: 'failed' }); }
});
router.post('/evidence/:id/accept', async (req: any, res) => {
  try { await acceptEvidence(req.params.id, actorId(req)); res.json({ ok: true }); }
  catch (err) { console.error('[admin/evidence/accept]', err); res.status(500).json({ error: 'failed' }); }
});
router.post('/evidence/:id/reject', async (req: any, res) => {
  try {
    const { reason, note } = req.body ?? {};
    await rejectEvidence(req.params.id, actorId(req), reason, note);
    res.json({ ok: true });
  } catch (err: any) {
    if (/invalid reject reason/.test(err?.message)) return res.status(400).json({ error: err.message });
    console.error('[admin/evidence/reject]', err); res.status(500).json({ error: 'failed' });
  }
});
router.post('/evidence/:id/rehome', async (req: any, res) => {
  try {
    const { topic_id, issue } = req.body ?? {};
    await rehomeEvidence(req.params.id, { topicId: topic_id ?? null, issue }, actorId(req));
    res.json({ ok: true });
  } catch (err: any) {
    if (/issue/.test(err?.message)) return res.status(400).json({ error: err.message });
    console.error('[admin/evidence/rehome]', err); res.status(500).json({ error: 'failed' });
  }
});
```

- [ ] **Step 3: Typecheck** — `cd ~/Documents/GitHub/ev-accounts/backend && npx tsc --noEmit` clean; run `npm test` (no regressions).

- [ ] **Step 4: Commit** (ev-accounts local):

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/src/routes/admin.ts
git commit -m "feat(evidence): admin routes for the evidence review surface

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Frontend `EvidenceReviewPage.tsx` + helper + route/nav

**Files:**
- Create: `admin/src/pages/admin/evidenceReviewFormat.ts` + `admin/src/pages/admin/evidenceReviewFormat.test.ts`
- Create: `admin/src/pages/admin/EvidenceReviewPage.tsx`
- Modify: `admin/src/App.tsx`

- [ ] **Step 1: Pure helper + failing test**

```typescript
// admin/src/pages/admin/evidenceReviewFormat.ts
export const REJECT_REASONS = ['off-question','goal-only','not-verbatim','not-primary','not-forward','is-attack','stale','other'] as const;
export function statusBadge(machineStatus: string): { label: string; kind: 'green' | 'flagged' } {
  return machineStatus === 'green' ? { label: 'green', kind: 'green' } : { label: 'flagged', kind: 'flagged' };
}
export function flagReasons(gateFlags: any): string[] {
  return Array.isArray(gateFlags?.reasons) ? gateFlags.reasons : [];
}
export function pct(v: number | null | undefined): string {
  return v === null || v === undefined ? 'n/a' : Number(v).toFixed(2);
}
```

```typescript
// admin/src/pages/admin/evidenceReviewFormat.test.ts
import { describe, it, expect } from 'vitest';
import { statusBadge, flagReasons, pct, REJECT_REASONS } from './evidenceReviewFormat';
describe('evidenceReviewFormat', () => {
  it('statusBadge maps green/flagged', () => {
    expect(statusBadge('green').kind).toBe('green');
    expect(statusBadge('flagged').kind).toBe('flagged');
  });
  it('flagReasons reads gate_flags.reasons defensively', () => {
    expect(flagReasons({ reasons: ['judge:no-mechanism'] })).toEqual(['judge:no-mechanism']);
    expect(flagReasons(null)).toEqual([]);
  });
  it('pct formats or n/a', () => { expect(pct(0.8)).toBe('0.80'); expect(pct(null)).toBe('n/a'); });
  it('reason enum excludes wrong-tag', () => { expect(REJECT_REASONS).not.toContain('wrong-tag'); });
});
```

Run (admin vitest): expect FAIL → implement the helper above → PASS.

- [ ] **Step 2: The page** — create `EvidenceReviewPage.tsx` mirroring `ResearchReviewPage.tsx`'s data-fetch style (`apiFetch`, loading/error state). Structure (write real code; the JSX may be refined with the frontend-design skill, but the data flow + actions below are required):
  - On mount: `apiFetch('/admin/evidence/candidates')` → candidate list (name + pendingGreen/pendingFlagged) and `apiFetch('/admin/evidence/metrics')` → metrics panel.
  - Select a candidate → `apiFetch('/admin/evidence?politician_id=' + id + issue/status filters)` → item cards.
  - Each card renders: `verbatimText`, `context`, a `deep_link` anchor (`target="_blank" rel="noopener noreferrer"`, only if it starts with http), `statusBadge(machineStatus)`, `issue` (+ topic), `sourceType`, and `flagReasons(gateFlags)`.
  - Actions per card:
    - Accept → `apiFetch('/admin/evidence/'+id+'/accept', { method:'POST' })`.
    - Reject → a reason `<select>` (`REJECT_REASONS`) + optional note → `apiFetch(..., { method:'POST', body: JSON.stringify({reason, note}) })`.
    - Re-home → a compass-topic picker (fetch topics once; reuse an existing topics endpoint if present, else a free issue text) + issue text → `POST /rehome` with `{topic_id, issue}`.
    - On success remove/refetch the item and refresh metrics.
  - Filters: issue text + green/flagged toggle.

- [ ] **Step 3: Register the route + nav in `admin/src/App.tsx`**

Add `import { EvidenceReviewPage } from './pages/admin/EvidenceReviewPage';` and a route beside the research one:
```tsx
<Route path="review/evidence" element={<EvidenceReviewPage />} />
```
Add a nav link to it wherever the admin nav lists review/research pages (mirror that link's markup).

- [ ] **Step 4: Verify** — `cd ~/Documents/GitHub/ev-accounts/admin && npx tsc --noEmit` clean; `npm run build` succeeds; the helper test passes.

- [ ] **Step 5: Commit** (ev-accounts local):

```bash
cd ~/Documents/GitHub/ev-accounts && git add admin/src/pages/admin/EvidenceReviewPage.tsx admin/src/pages/admin/evidenceReviewFormat.ts admin/src/pages/admin/evidenceReviewFormat.test.ts admin/src/App.tsx
git commit -m "feat(evidence): admin EvidenceReviewPage (accept/reject/re-home + metrics)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Manual verification (human-gated)

**Needs Chris** (migration apply + a running admin). No new code unless a bug surfaces.

- [ ] **Step 1:** Chris applies `<slot>_evidence_review_reason.sql` to prod (or authorizes me, as with 1884). Verify the column + CHECK exist.
- [ ] **Step 2:** Run the admin locally (or against the deploy) as an admin; open `/admin/review/evidence`. Confirm a candidate with pending evidence lists their items with gate flags.
- [ ] **Step 3:** Accept one, reject one (with a reason), re-home one; verify each persisted in `inform.evidence_items` (review_status / review_reason / topic_id+issue), and that the metrics panel updates.
- [ ] **Step 4:** Note results in the spike README / a short write-up; the ev-accounts branch is ready for Chris to push/merge.

---

## Self-Review

**1. Spec coverage:** review_reason migration (Task 1) ✔; per-candidate list + counts, pending items with gate flags (Task 2 list fns, Task 4 page) ✔; accept / reject-with-reason / re-home leaving pending (Task 2 + 3 + 4) ✔; reason enum app+DB, wrong-tag excluded (Tasks 1/2/4) ✔; metrics by status/issue/model + top reject reasons (Task 2 `evidenceReviewMetrics`, Task 4 panel) ✔; auth via requireAuth+requireAdmin + `actorId` (Task 3) ✔; no on-the-record change ✔; loop deferred ✔.

**2. Placeholder scan:** `<slot>` is a steward allocation step. The Task-4 JSX is described as structured requirements with the pure helper + all apiFetch calls given concretely; the visual layout may be refined with frontend-design, but every data path + action + endpoint is specified. No "add error handling" hand-waves — routes have explicit try/catch + status codes.

**3. Type/name consistency:** service fn names + params match between Task 2 (impl+test), Task 3 (routes), and Task 4 (apiFetch URLs/bodies). `REJECT_REASONS` duplicated in backend + admin deliberately (two packages) — both exclude `wrong-tag`; the DB CHECK is the backstop. `actorId(req)=req.userId` per admin.ts. The `/evidence/candidates` and `/evidence/metrics` routes are declared BEFORE `/evidence/:id/...` and the parameterless `/evidence` uses a query param, so there is no route-collision with `/evidence/:id`.
