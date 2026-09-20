# Evidence Write-Path Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist the trust-core pipeline's green + flagged evidence items to a new `inform.evidence_items` table via a standalone, dry-run→`--commit` committer, idempotently.

**Architecture:** A pure row-builder (`src/evidence/commit.py`) maps a run's `evidence_items.json` to table rows; a thin CLI (`scripts/commit_evidence.py`) reads the artifact, resolves the compass-topic map from the DB, previews (dry-run), and on `--commit` inserts with `ON CONFLICT DO NOTHING`. A gated ev-accounts migration creates the table. The online runner is unchanged (still DB-read-only); the committer is the only writer.

**Tech Stack:** Python 3.14 (main-checkout `.venv`), psycopg2, pytest (offline), reuse of `src/evidence/data.py::database_url`. ev-accounts migration = raw SQL, steward-allocated, hand-applied.

## Global Constraints

- Spec: `docs/superpowers/specs/2026-09-20-evidence-write-path-design.md`.
- Persist **green + flagged** only (drop `dropped`); each row gets `machine_status` = the item's status and `review_status = 'pending'`. Season-agnostic (no `season_id`).
- Dedup / idempotency key: `(politician_id, source_url, md5(lower(verbatim_text)))`; committer writes `ON CONFLICT … DO NOTHING` (never clobbers a human `review_status`).
- The committer is the ONLY DB writer; **dry-run by default**, `--commit` writes. Env resolution: env `DATABASE_URL` > `--env-file` > ev-accounts `backend/.env` (via `src.evidence.data.database_url`).
- ev-accounts migration is **written, not applied** — Chris applies it. Allocate the number with the STEWARD (`cd backend && npm run steward -- slot shared --purpose "…"`), never the git-only `check-migration-numbers` guard. Idempotent SQL + a `DO $$` post-verify gate.
- Run/test with the MAIN checkout `~/Documents/GitHub/on-the-record/.venv/bin/python`. Offline tests only (no DB, no network).
- `evidence_items.json` item shape (verified): top-level `politician_id, issue, evidence_type, verbatim_text, source_url, cited_via, context, deep_link, source_type, gates, status, status_reasons, provenance`; `gates` = `{verbatim, own_words, in_context, primary, tag_agree, judge_tag_ok, judge_context_sufficient, judge_dispute_risk, judge_mechanism}`; `provenance` = `{extractor, crosschecker, judge, batch}`. (No `source_cycle_year` today → maps to NULL defensively.)
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- Create `src/evidence/commit.py` — pure `build_rows(items, topic_key_to_id) -> list[dict]` + helpers. No DB/IO.
- Create `scripts/commit_evidence.py` — the CLI (read JSON, DB read for topic map, dry-run/`--commit` insert).
- Create `tests/test_evidence_commit.py` — offline tests for `build_rows` + CLI arg-parse.
- Create ev-accounts `backend/migrations/<steward-slot>_evidence_items.sql` — the gated migration.

---

### Task 1: Pure row-builder `src/evidence/commit.py`

**Files:**
- Create: `src/evidence/commit.py`
- Test: `tests/test_evidence_commit.py`

**Interfaces:**
- Produces: `WRITE_STATUSES = ("green", "flagged")`; `build_rows(items: list[dict], topic_key_to_id: dict) -> list[dict]` — filters to green+flagged, resolves `topic_id` from a `lower(topic_key)->id` map (free label → None), and shapes each row dict with keys: `politician_id, topic_id, issue, evidence_type, verbatim_text, source_url, deep_link, context, source_type, source_cycle_year, machine_status, gate_flags, provenance, batch_id, review_status`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_commit.py
from src.evidence.commit import build_rows, WRITE_STATUSES

TOPICS = {"housing": "topic-housing-uuid", "homelessness": "topic-homeless-uuid"}

def _item(**kw):
    base = dict(politician_id="p1", issue="housing", evidence_type="quote",
                verbatim_text="We will triple housing construction.",
                source_url="https://ex.com/a", cited_via=None, context="…ctx…",
                deep_link="https://ex.com/a", source_type="primary",
                gates={"verbatim": True, "judge_mechanism": 0.9}, status="green",
                status_reasons=[], provenance={"extractor": "haiku-or", "batch": "b1"})
    base.update(kw)
    return base

def test_drops_non_green_flagged():
    rows = build_rows([_item(status="dropped"), _item(status="green"),
                       _item(status="flagged", verbatim_text="Enforce the ordinance.")], TOPICS)
    assert len(rows) == 2
    assert {r["machine_status"] for r in rows} == {"green", "flagged"}

def test_resolves_mapped_topic_id_and_review_pending():
    r = build_rows([_item(issue="Housing")], TOPICS)[0]
    assert r["topic_id"] == "topic-housing-uuid"      # case-insensitive map
    assert r["issue"] == "Housing" and r["review_status"] == "pending"
    assert r["machine_status"] == "green" and r["evidence_type"] == "quote"

def test_free_issue_leaves_topic_id_none():
    r = build_rows([_item(issue="immigrant entrepreneurship")], TOPICS)[0]
    assert r["topic_id"] is None and r["issue"] == "immigrant entrepreneurship"

def test_gate_flags_and_provenance_shaped():
    r = build_rows([_item(status="flagged", status_reasons=["judge:no-mechanism"],
                          gates={"verbatim": True, "judge_mechanism": 0.2})], TOPICS)[0]
    assert r["gate_flags"] == {"reasons": ["judge:no-mechanism"],
                               "gates": {"verbatim": True, "judge_mechanism": 0.2}}
    assert r["provenance"]["extractor"] == "haiku-or" and r["batch_id"] == "b1"
    assert r["source_cycle_year"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_commit.py -v`
Expected: FAIL with `ModuleNotFoundError: src.evidence.commit`.

- [ ] **Step 3: Write minimal implementation**

```python
# src/evidence/commit.py
"""Pure row-builder: a run's evidence_items.json -> inform.evidence_items rows.

No DB or I/O. scripts/commit_evidence.py resolves the compass-topic map and
performs the dry-run/--commit write.
"""
from __future__ import annotations

WRITE_STATUSES = ("green", "flagged")


def _resolve_topic(issue: str, topic_key_to_id: dict):
    return topic_key_to_id.get((issue or "").strip().lower())


def build_rows(items: list, topic_key_to_id: dict) -> list:
    rows = []
    for it in items:
        if it.get("status") not in WRITE_STATUSES:
            continue
        issue = (it.get("issue") or "").strip()
        prov = it.get("provenance") or {}
        rows.append({
            "politician_id": it["politician_id"],
            "topic_id": _resolve_topic(issue, topic_key_to_id),
            "issue": issue,
            "evidence_type": it.get("evidence_type") or "quote",
            "verbatim_text": it["verbatim_text"],
            "source_url": it["source_url"],
            "deep_link": it.get("deep_link"),
            "context": it.get("context"),
            "source_type": it.get("source_type"),
            "source_cycle_year": it.get("source_cycle_year"),
            "machine_status": it["status"],
            "gate_flags": {"reasons": it.get("status_reasons") or [],
                           "gates": it.get("gates") or {}},
            "provenance": prov,
            "batch_id": prov.get("batch"),
            "review_status": "pending",
        })
    return rows
```

- [ ] **Step 4: Run test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_commit.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/commit.py tests/test_evidence_commit.py
git commit -m "feat(evidence): pure row-builder for the evidence_items write path

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Gated ev-accounts migration `inform.evidence_items`

**Files:**
- Create: `~/Documents/GitHub/ev-accounts/backend/migrations/<steward-slot>_evidence_items.sql`

**Interfaces:**
- Produces: the `inform.evidence_items` table + indexes exactly as the spec's schema section defines. Not applied; not unit-tested (ev-accounts convention: DB mocked, migrations hand-applied + DO $$ gate). Reviewed structurally.

- [ ] **Step 1: Allocate the migration number via the steward**

Run: `cd ~/Documents/GitHub/ev-accounts/backend && npm run steward -- slot shared --purpose "inform.evidence_items — evidence write path"`
Record the slot number it reserves; use it as `<slot>` in the filename. (The steward fails closed; do NOT hand-pick a number.)

- [ ] **Step 2: Write the migration SQL**

Create `~/Documents/GitHub/ev-accounts/backend/migrations/<slot>_evidence_items.sql`:

```sql
-- <slot>_evidence_items.sql
-- New evidence store for the on-the-record trust-core pipeline. Standalone verbatim
-- evidence items (quotes now; votes/actions later), season-agnostic, with a human
-- review state. Idempotent; safe to re-run. Applied by hand (no runner).

CREATE TABLE IF NOT EXISTS inform.evidence_items (
  id                uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  politician_id     uuid NOT NULL REFERENCES essentials.politicians(id) ON DELETE CASCADE,
  topic_id          uuid REFERENCES inform.compass_topics(id),
  issue             text NOT NULL,
  evidence_type     text NOT NULL DEFAULT 'quote' CHECK (evidence_type IN ('quote')),
  verbatim_text     text NOT NULL,
  source_url        text NOT NULL,
  deep_link         text,
  context           text,
  source_type       text NOT NULL,
  source_cycle_year text,
  machine_status    text NOT NULL CHECK (machine_status IN ('green','flagged')),
  gate_flags        jsonb NOT NULL DEFAULT '{}'::jsonb,
  provenance        jsonb NOT NULL DEFAULT '{}'::jsonb,
  batch_id          text,
  review_status     text NOT NULL DEFAULT 'pending'
                      CHECK (review_status IN ('pending','accepted','rejected')),
  reviewed_by       uuid,
  reviewed_at       timestamptz,
  review_note       text,
  created_at        timestamptz NOT NULL DEFAULT now(),
  updated_at        timestamptz NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_evidence_pol_src_text
  ON inform.evidence_items (politician_id, source_url, md5(lower(verbatim_text)));
CREATE INDEX IF NOT EXISTS idx_evidence_politician_issue
  ON inform.evidence_items (politician_id, issue);
CREATE INDEX IF NOT EXISTS idx_evidence_topic
  ON inform.evidence_items (topic_id) WHERE topic_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_evidence_review_status
  ON inform.evidence_items (review_status);

-- Post-verify gate (house style): fail loudly if the shape isn't what we expect.
DO $$
BEGIN
  IF to_regclass('inform.evidence_items') IS NULL THEN
    RAISE EXCEPTION 'inform.evidence_items missing after migration';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_indexes
                 WHERE schemaname='inform' AND indexname='uq_evidence_pol_src_text') THEN
    RAISE EXCEPTION 'uq_evidence_pol_src_text missing after migration';
  END IF;
END $$;
```

- [ ] **Step 3: Structural self-check (no apply)**

Confirm the file parses as SQL (e.g. read it back; optionally `psql --version` only — do NOT connect/apply). Confirm the filename uses the steward-reserved number. Report the slot + path. Do NOT apply to any database.

- [ ] **Step 4: Commit (in the ev-accounts repo)**

```bash
cd ~/Documents/GitHub/ev-accounts && git add backend/migrations/<slot>_evidence_items.sql
git commit -m "feat(inform): evidence_items table for on-the-record evidence write path (gated)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```
(Do NOT push or apply — Chris reviews, applies, and merges ev-accounts changes.)

---

### Task 3: The committer CLI `scripts/commit_evidence.py`

**Files:**
- Create: `scripts/commit_evidence.py`
- Test: extend `tests/test_evidence_commit.py`

**Interfaces:**
- Consumes: `src.evidence.commit.build_rows`; `src.evidence.data.database_url`; psycopg2.
- Produces: `build_parser()` (args: `artifact` positional, `--commit`, `--env-file`); `load_topic_map(conn) -> dict`; `main(argv=None)`.

- [ ] **Step 1: Write the failing test (CLI arg-parse only; no DB)**

```python
# add to tests/test_evidence_commit.py
import importlib

def test_commit_cli_parses_args():
    mod = importlib.import_module("scripts.commit_evidence")
    args = mod.build_parser().parse_args(["run.json"])
    assert args.artifact == "run.json" and args.commit is False
    args2 = mod.build_parser().parse_args(["run.json", "--commit", "--env-file", "/tmp/e"])
    assert args2.commit is True and args2.env_file == "/tmp/e"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_commit.py::test_commit_cli_parses_args -v`
Expected: FAIL (`ModuleNotFoundError: scripts.commit_evidence`). If it fails instead on `scripts` not importable, mirror the runner: `scripts` resolves as a namespace package under pytest (as `scripts.evidence_slice` already does) — no `__init__.py` needed.

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
# scripts/commit_evidence.py
"""Write a trust-core run's green+flagged evidence items to inform.evidence_items.

Dry-run by default; --commit writes (ON CONFLICT DO NOTHING). The migration
<slot>_evidence_items.sql must be applied first.

  ~/Documents/GitHub/on-the-record/.venv/bin/python scripts/commit_evidence.py \
      docs/superpowers/spikes/2026-09-19-evidence-trust-core/evidence_items.json \
      [--commit] [--env-file PATH]
"""
from __future__ import annotations
import argparse
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import psycopg2
import psycopg2.extras

from src.evidence.commit import build_rows
from src.evidence.data import database_url

_INSERT = """
INSERT INTO inform.evidence_items
  (politician_id, topic_id, issue, evidence_type, verbatim_text, source_url, deep_link,
   context, source_type, source_cycle_year, machine_status, gate_flags, provenance,
   batch_id, review_status)
VALUES
  (%(politician_id)s, %(topic_id)s, %(issue)s, %(evidence_type)s, %(verbatim_text)s,
   %(source_url)s, %(deep_link)s, %(context)s, %(source_type)s, %(source_cycle_year)s,
   %(machine_status)s, %(gate_flags)s, %(provenance)s, %(batch_id)s, %(review_status)s)
ON CONFLICT (politician_id, source_url, md5(lower(verbatim_text))) DO NOTHING
"""


def build_parser():
    ap = argparse.ArgumentParser()
    ap.add_argument("artifact", help="Path to a run's evidence_items.json")
    ap.add_argument("--commit", action="store_true", help="Write (default: dry run)")
    ap.add_argument("--env-file", default=None)
    return ap


def load_topic_map(conn) -> dict:
    cur = conn.cursor()
    cur.execute("SELECT lower(topic_key), id::text FROM inform.compass_topics")
    return {k: v for k, v in cur.fetchall()}


def main(argv=None):
    args = build_parser().parse_args(argv)
    items = json.loads(pathlib.Path(args.artifact).read_text())
    conn = psycopg2.connect(database_url(args.env_file))
    try:
        rows = build_rows(items, load_topic_map(conn))
        mapped = sum(1 for r in rows if r["topic_id"])
        print(f"{len(rows)} rows to write "
              f"({sum(r['machine_status']=='green' for r in rows)} green / "
              f"{sum(r['machine_status']=='flagged' for r in rows)} flagged; "
              f"{mapped} compass-mapped, {len(rows)-mapped} free-issue)")
        for r in rows:
            print(f"  [{r['machine_status']}] {r['issue']}  {r['verbatim_text'][:70]}")
        if not args.commit:
            print("\nDRY RUN — nothing written. Re-run with --commit.")
            return
        cur = conn.cursor()
        for r in rows:
            p = dict(r, gate_flags=psycopg2.extras.Json(r["gate_flags"]),
                     provenance=psycopg2.extras.Json(r["provenance"]))
            cur.execute(_INSERT, p)
        conn.commit()
        print(f"\nCommitted. Inserted {cur.rowcount if False else 'up to'} {len(rows)} "
              f"(existing rows skipped by dedup).")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_commit.py -v`
Expected: all PASS. Then confirm no import-time DB/network: `~/Documents/GitHub/on-the-record/.venv/bin/python -c "import importlib; importlib.import_module('scripts.commit_evidence')"` returns cleanly.

- [ ] **Step 5: Commit**

```bash
git add scripts/commit_evidence.py tests/test_evidence_commit.py
git commit -m "feat(evidence): commit_evidence CLI (dry-run/--commit) for evidence_items

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Dry-run + apply + commit (manual, human-gated)

**This task touches the production DB and needs Chris.** No new code unless a bug surfaces.

- [ ] **Step 1: Dry-run against the LA-Mayor run**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python scripts/commit_evidence.py docs/superpowers/spikes/2026-09-19-evidence-trust-core/evidence_items.json --env-file ~/Documents/GitHub/ev-accounts/backend/.env`
Expected: previews the green+flagged rows with counts (green/flagged, mapped/free-issue); writes nothing. Confirm the numbers match the run's baseline.

- [ ] **Step 2: Chris applies the migration**

Chris applies `<slot>_evidence_items.sql` to prod (agent prod-DB writes are blocked by default). Verify the table + `uq_evidence_pol_src_text` exist.

- [ ] **Step 3: Commit for real**

Run the Step-1 command with `--commit`. Expected: inserts the rows. Re-run with `--commit` again → a no-op (all skipped by the dedup key). Spot-check a row in `inform.evidence_items`.

- [ ] **Step 4: Record + commit the write-up**

Note the row count + any observations in the spike README; commit.

---

## Self-Review

**1. Spec coverage:** new table (Task 2) ✔; green+flagged with review_status=pending + machine_status (Task 1) ✔; nullable topic_id + free issue (Task 1 `_resolve_topic`, Task 2 schema) ✔; gate_flags/provenance jsonb (Task 1/3) ✔; standalone dry-run→--commit committer, ON CONFLICT DO NOTHING (Task 3) ✔; gated steward-allocated migration (Task 2) ✔; persist-only, no derivation ✔; offline tests (Task 1/3) ✔.

**2. Placeholder scan:** `<slot>`/`<steward-slot>` is a real allocation step (Task 2 Step 1), not a placeholder defect. All code steps carry full code. One cosmetic wart to fix in Task 3: the print line `cur.rowcount if False else 'up to'` is deliberately conservative wording (psycopg2 `rowcount` on a loop of single INSERTs reflects only the last statement) — the implementer may simplify to a plain "up to N (existing skipped)" message; do not report a false exact inserted count.

**3. Type consistency:** `build_rows(items, topic_key_to_id)` and the row-dict keys match between Task 1 (producer), the Task 1 tests, and the Task 3 `_INSERT` named params. `database_url(env_file)` matches `src/evidence/data.py`. The `ON CONFLICT` target matches `uq_evidence_pol_src_text`'s expression exactly.
