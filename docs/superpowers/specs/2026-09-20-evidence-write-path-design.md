# Evidence Write-Path — persist accepted evidence to `inform.evidence_items`

**Status:** Draft for review (brainstormed with Chris 2026-09-20)
**Repo:** on-the-record (committer + tests); ev-accounts (gated migration, written not applied)
**Follows:** `2026-09-19-evidence-trust-core-slice-design.md` (slice 1, merged in PR #246). That slice
was artifacts-only; this is the deferred **write path** — sub-project 5's first half: persist the
pipeline's output so the evidence is real, queryable, and reviewable. *Deriving* compass reasoning
and read-rank quotes **from** the evidence is a separate, later sub-project (out of scope here).

## Context (verified against the live DB, 2026-09-20)

The trust-core pipeline emits evidence items to `evidence_items.json` but writes nothing. The
existing `inform.politician_context_evidence` is not a fit to evolve: `topic_id` is NOT NULL and FKs
`inform.compass_topics` (no non-compass issues); a composite FK
(`politician_id, topic_id, season_id`) requires a `politician_context` row to exist (evidence can't
stand alone); it is season-scoped; and it is snippet-only (no status / provenance / gates / verbatim
distinction / deep-link). So this spec adds a **new** table.

The evidence-item atom (from the slice spec) is: `politician_id`, `issue` (a compass `topic_key` when
it maps, else a free label), `evidence_type`, `verbatim_text`, `source_url`, `cited_via`, `context`,
`deep_link`, `source_type`, `gates`, `status`, `provenance`.

## Goals

- A new **`inform.evidence_items`** table that holds the full atom + a human review state, supports
  non-compass issues, and stands alone (no required `politician_context` row).
- A **standalone committer** that writes a run's **green + flagged** items to it, dry-run by default,
  `--commit` to write, idempotent (dedup), reading the run's `evidence_items.json` artifact.
- A **gated ev-accounts migration** (written, not applied) creating the table + indexes.
- Offline tests for the pure row-builder + the CLI.

## Non-goals (explicitly out)

- Deriving compass reasoning display or read-rank quotes **from** `evidence_items` (later sub-project).
- The review UI / surface for working the `pending` queue (later).
- Applying the migration (Chris applies it).
- Writing `dropped` items (kept only in the run's drop log), and `vote`/`action` evidence types.
- Any change to the online run's read-only DB posture (the runner still never writes).

## Schema — `inform.evidence_items` (new)

Season-agnostic: a verbatim quote is evidence regardless of compass season; season/compass coupling
is a derivation concern handled later.

| column | type / notes |
|---|---|
| `id` | uuid PK, `gen_random_uuid()` |
| `politician_id` | uuid NOT NULL → `essentials.politicians(id)` ON DELETE CASCADE |
| `topic_id` | uuid **NULL** → `inform.compass_topics(id)` — set only when `issue` maps to a canonical compass topic |
| `issue` | text NOT NULL — the issue label (a compass `topic_key` when `topic_id` set; a free label otherwise) |
| `evidence_type` | text NOT NULL, `CHECK (evidence_type IN ('quote'))` (room to extend later) |
| `verbatim_text` | text NOT NULL |
| `source_url` | text NOT NULL |
| `deep_link` | text NULL |
| `context` | text NULL |
| `source_type` | text NOT NULL (`primary` / `pointer` / … from the atom) |
| `source_cycle_year` | text NULL (prior-cycle attribution) |
| `machine_status` | text NOT NULL, `CHECK (machine_status IN ('green','flagged'))` — the pipeline disposition |
| `gate_flags` | jsonb NOT NULL default `'{}'` — status_reasons + judge/cross-check scores, for the reviewer |
| `provenance` | jsonb NOT NULL default `'{}'` — extractor / crosschecker / judge model keys + batch |
| `batch_id` | text NULL |
| `review_status` | text NOT NULL default `'pending'`, `CHECK (review_status IN ('pending','accepted','rejected'))` |
| `reviewed_by` | uuid NULL; `reviewed_at` timestamptz NULL; `review_note` text NULL |
| `created_at` / `updated_at` | timestamptz NOT NULL default `now()` |

**Indexes:**
- `CREATE UNIQUE INDEX uq_evidence_pol_src_text ON inform.evidence_items (politician_id, source_url, md5(lower(verbatim_text)))` — the same verbatim quote from the same source for the same politician is one row (dedup / idempotency key).
- `CREATE INDEX idx_evidence_politician_issue ON inform.evidence_items (politician_id, issue)`.
- `CREATE INDEX idx_evidence_topic ON inform.evidence_items (topic_id) WHERE topic_id IS NOT NULL`.
- `CREATE INDEX idx_evidence_review_status ON inform.evidence_items (review_status)`.

**Migration** `backend/migrations/<steward-slot>_evidence_items.sql`: idempotent
(`CREATE TABLE IF NOT EXISTS` + `CREATE INDEX IF NOT EXISTS`) with a `DO $$ … $$` post-verify gate
(house style). 🔴 Allocate the number via the STEWARD (`cd backend && npm run steward -- slot shared
--purpose "inform.evidence_items"`), never the git-only `check-migration-numbers` guard. Gated:
Chris applies it.

## The committer

`scripts/commit_evidence.py <evidence_items.json> [--commit] [--env-file PATH]`:

1. Load the artifact JSON; keep items whose `status` is `green` or `flagged` (drop `dropped`).
2. Load the compass-topic map (`lower(topic_key) → id`) and validate the politician ids exist
   (read-only query). Resolve each item's `topic_id`: if `issue` matches a canonical `topic_key`,
   set `topic_id`; else leave NULL and keep `issue` as the free label.
3. Build rows (pure, in `src/evidence/commit.py::build_rows`): map atom → table columns, carry
   `gate_flags` (= status_reasons + the numeric gate scores) and `provenance`, set
   `machine_status=status`, `review_status='pending'`.
4. **Dry-run (default):** print a per-item preview + counts (green/flagged, mapped/free-issue, new
   vs already-present) and stop.
5. **`--commit`:** open a read-write connection and `INSERT … ON CONFLICT (politician_id, source_url,
   md5(lower(verbatim_text))) DO NOTHING` (idempotent; never overwrites a human `review_status`).
   Report inserted vs skipped.

Mirrors `insert_quotes.py`: env resolution (env `DATABASE_URL` > `--env-file` > ev-accounts
`.env`), psycopg2, dry-run-first discipline. This is the **only** component that writes to the DB;
the online runner stays read-only.

## Components / file structure (on-the-record)

- `src/evidence/commit.py` — pure `build_rows(items: list[dict], topic_key_to_id: dict) -> list[dict]`
  (+ small helpers: filter, resolve topic, shape). No DB, no I/O.
- `scripts/commit_evidence.py` — the CLI (read JSON, DB read for the topic map + politician check,
  dry-run/`--commit`, the write). Thin.
- `tests/test_evidence_commit.py` — offline: green+flagged kept / dropped excluded; topic_id resolved
  for a mapped key, NULL + issue kept for a free label; gate_flags + provenance shaped; the
  dedup-key value computed as the DB expects (`md5(lower(text))`); CLI `build_parser().parse_args`
  defaults + dry-run path. No DB, no network (mirror `insert_quotes` tests).
- ev-accounts `backend/migrations/<steward-slot>_evidence_items.sql` — the gated migration.

## Risks / decisions

- **Idempotency vs refresh:** `ON CONFLICT DO NOTHING` never updates an existing row, so a re-run with
  improved gate scores won't refresh a row already written. Accepted for v1 (protects human
  `review_status`); a future `--refresh` could `DO UPDATE` the machine columns while preserving the
  review columns.
- **Dedup key** keys on `verbatim_text` (normalized via `lower`), not on `issue` — the same quote
  re-tagged to a different issue does not duplicate; the first write's issue/topic stands (a re-home
  is a human/review action, not a re-insert).
- **Free-issue rows** (`topic_id` NULL) are intentional (expansive beyond the compass); the derivation
  sub-project decides how/whether to surface them under compass.
- **Read-write connection** is confined to `--commit` in the committer; the migration must be applied
  first (writing to a missing table fails — the original_vs_clip lesson).

## Success criteria

- `commit_evidence.py <la-mayor run>.json` dry-run previews the green+flagged rows with resolved
  topics and counts, writing nothing.
- After Chris applies the migration, `--commit` inserts those rows; a second `--commit` is a no-op
  (all skipped by the dedup key).
- Offline tests pass; full suite stays green.

## Execution notes

- Build in on-the-record; run with the MAIN checkout `.venv/bin/python`. Offline tests only in CI.
- ev-accounts migration WRITTEN not applied; steward-allocated number; Chris applies.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
