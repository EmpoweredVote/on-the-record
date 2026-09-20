# Evidence Review Surface — work the `inform.evidence_items` pending queue + capture feedback

**Status:** Draft for review (brainstormed with Chris 2026-09-20)
**Repo:** ev-accounts (backend service + admin routes + React page; one small gated migration). Spec/plan live in on-the-record `docs/superpowers/` for program continuity.
**Follows:** the write-path (PR #247) — evidence now lands in `inform.evidence_items` as `review_status='pending'`. This is the human-in-the-loop surface to accept/reject/re-home those items **and** to capture that feedback as a categorized, always-on signal for improving the pipeline.

## Why (the learning framing)

The human's decisions ARE an always-on gold set: **accept** = pipeline right; **reject + structured reason** = wrong + the failure mode; **re-home** = right quote, wrong tag. Reviewing both green and flagged makes the labelled stream complete. Capturing it *structured* (not just prose) is what lets us measure the pipeline continuously and pinpoint systematic errors to fix — the same signal that moved precision 0.375 → 0.80 in the one-off gold, now accruing as work happens. This build **captures** that signal and makes it **legible** (metrics); the export→re-eval→prompt-tune loop is a deliberate follow-up.

## Goals

- An auth-gated admin page to work the pending `evidence_items` queue **per candidate**, with inline **accept / reject / re-home**.
- **Structured reject reasons** (enum + free note) so every rejection is categorized.
- A **metrics view**: rolling precision by `machine_status` / issue / model, and top reject reasons.
- Backend service + routes mirroring the existing `ResearchReviewPage` / `research-review` pattern; Vitest tests (db mocked).

## Non-goals (deferred)

- The self-improvement loop itself (export accumulated decisions → refresh eval gold → re-run harness → propose prompt/threshold tuning → human approves). This build makes that possible; it doesn't implement it.
- Deriving compass reasoning / read-rank quotes from `accepted` evidence (separate sub-project).
- Auto-accepting green (decided: the human confirms both green and flagged).
- Any change to the on-the-record extraction pipeline.

## Schema — one small gated migration

`inform.evidence_items` already has `review_status`, `reviewed_by`, `reviewed_at`, `review_note`. Add:

- `review_reason text` (nullable) with `CHECK (review_reason IS NULL OR review_reason IN ('off-question','goal-only','not-verbatim','not-primary','not-forward','is-attack','stale','other'))`.

Migration `backend/migrations/<steward-slot>_evidence_review_reason.sql`: idempotent
`ALTER TABLE inform.evidence_items ADD COLUMN IF NOT EXISTS review_reason text;` + the CHECK added
guarded (only if absent) + a `DO $$` verify gate. 🔴 Steward-allocated number; **written, not applied**
(Chris applies). Reason vocabulary aligns with the pipeline failure modes + `audit-quotes/CHECKS.md`;
`wrong-tag` is intentionally NOT a reject reason (it is handled by **re-home**, which fixes the tag
rather than discarding the quote).

## Backend — `backend/src/lib/evidenceReviewService.ts`

Raw node-postgres over `pool` (repo convention), pure-shaping helpers where useful:

- `listCandidatesWithPending() -> {politicianId, name, pendingGreen, pendingFlagged}[]` — politicians with any `review_status='pending'` evidence, with counts (ORDER BY total desc).
- `listPendingEvidence(politicianId, {issue?, machineStatus?}) -> EvidenceRow[]` — that candidate's pending items; each row: `id, issue, topicId, evidenceType, verbatimText, sourceUrl, deepLink, context, sourceType, machineStatus, gateFlags, provenance, createdAt`.
- `acceptEvidence(id, reviewerId) -> void` — `review_status='accepted', reviewed_by, reviewed_at=now(), updated_at=now()`.
- `rejectEvidence(id, reviewerId, reason, note?) -> void` — `review_status='rejected', review_reason, review_note, reviewed_by, reviewed_at`. Validate `reason` against the enum.
- `rehomeEvidence(id, {topicId, issue}, reviewerId) -> void` — update `topic_id` + `issue` (verbatim text untouched); leaves `review_status='pending'` (re-home fixes the tag; a separate accept confirms it) and stamps `reviewed_by`/`updated_at`. Validate `topic_id` exists (or is null) and `issue` non-empty.
- `evidenceReviewMetrics() -> { byStatus, byIssue, byModel, topRejectReasons }` — rolling counts + precision (`accepted / (accepted+rejected)`) grouped by `machine_status`, `issue`, and `provenance->>'judge'` (the model), plus reject-reason frequencies. (Near-empty until decisions accrue — built to grow.)

## Routes — `backend/src/routes/admin.ts` (under `requireAuth, requireAdmin`)

- `GET /admin/evidence/candidates` → `listCandidatesWithPending`
- `GET /admin/evidence?politician_id=&issue=&machine_status=` → `listPendingEvidence`
- `POST /admin/evidence/:id/accept` → `acceptEvidence(id, req.user.id)`
- `POST /admin/evidence/:id/reject` (body `{reason, note?}`) → `rejectEvidence`
- `POST /admin/evidence/:id/rehome` (body `{topic_id, issue}`) → `rehomeEvidence`
- `GET /admin/evidence/metrics` → `evidenceReviewMetrics`

`reviewer_id` comes from `requireAuth` (the admin user), never the client body.

## Frontend — `admin/src/pages/admin/EvidenceReviewPage.tsx`

- Route entry + a nav link (mirror how `ResearchReviewPage` / readrank pages are registered).
- **Candidate list** (from `/candidates`) with pending counts → select a candidate.
- **Per-candidate queue:** each item card shows verbatim quote, context, a source link (`deep_link`), a `machine_status` badge, issue/topic, `source_type`, and the `gate_flags` (reasons + judge/cross-check scores — *why* flagged). Filters: issue, green/flagged.
- **Inline actions:** Accept; Reject (reason dropdown [the enum] + optional note); Re-home (compass-topic picker + free issue text). Optimistic update / refetch on success.
- **Metrics panel** (from `/metrics`): rolling precision by status/issue/model + top reject reasons.
- A pure helper (e.g. `evidenceReviewFormat.ts`: badge/label/precision formatting) is unit-tested, like `coverageGridCell.ts`.

## Tests

- Vitest backend (db mocked via `vi.mock('./db.js')`, repo convention): each service function issues the right SQL/params; `rejectEvidence` validates the reason enum; `rehomeEvidence` leaves `review_status='pending'`; metrics aggregation shapes the expected buckets.
- Admin: the pure format helper unit-tested; `tsc` + build clean.

## Risks / decisions

- **Metrics are sparse initially** (10 pending rows, 0 decided) — the view is correct-but-empty until reviewing happens; that's expected.
- **Re-home leaves the item pending** (fix the tag, then accept) rather than auto-accepting — keeps the two acts distinct and auditable.
- **The reason enum is app-enforced + a DB CHECK** — keep the two lists in sync (the CHECK is the backstop).
- The metrics `model` slice reads `provenance->>'judge'`; if provenance is missing it buckets as unknown.

## Success criteria

- From the admin page, pick a candidate with pending evidence, see their items with the gate flags, and accept / reject-with-reason / re-home — each persists to `inform.evidence_items` (verifiable in the DB).
- The metrics view renders precision + reject-reason breakdowns (growing as decisions accrue).
- Vitest + admin build green; migration written + gated.

## Execution notes

- ev-accounts stack: TS backend (raw pg, Vitest db-mocked) + React admin (`apiFetch`, react-router). Mirror `ResearchReviewPage` + `research-review` routes + `researchEvidenceService`.
- Migration WRITTEN not applied; steward-allocated; Chris applies. ev-accounts commits are gated (no push/merge without Chris).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
