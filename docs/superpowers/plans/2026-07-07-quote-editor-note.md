# Quote editor rationale (`editor_note`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Capture one defensible editor rationale (why selected + what was edited & why) on every quote, from the on-the-record curation page through the publish script into `essentials.quotes`, and make it visible/editable in the ev-accounts Read & Rank admin.

**Architecture:** A single nullable `editor_note` column on `essentials.quotes` (ev-accounts DB). The on-the-record curation page repurposes its existing per-candidate `note` field into this rationale, requires it before a new JSON "publish batch" export, and emits **all** curated candidates (not just the starred pick). The publish script (`insert_quotes.py`) gains per-quote `topic_key`/`source_url` overrides plus a hard non-empty `editor_note` gate, and writes the column. The ev-accounts admin reads and edits the note. No new server; publishing stays human-in-the-loop.

**Tech Stack:** Postgres (Supabase), Node/TypeScript + Express + zod (ev-accounts backend), React + Vite + Tailwind + vitest (ev-accounts admin), Python 3 + psycopg2 + pytest (on-the-record publish script), Next.js (non-standard) + React + vitest (on-the-record `web/`).

**Cross-repo ordering:** Task 1 (DB migration) MUST be applied to the target DB before Task 2/3 write to the column and before the script (Task 5) commits `editor_note` in production. Tasks in `on-the-record` (`web/`, curation) do not depend on the DB and can proceed in parallel, but the JSON export (Tasks 7–8) feeds the script (Task 5), so Task 5's batch format is the contract.

**Repos:**
- ev-accounts: `/Users/chrisandrews/Documents/GitHub/ev-accounts`
- on-the-record: `/Users/chrisandrews/Documents/GitHub/on-the-record`

**Note on `web/`:** per `web/AGENTS.md`, this is a non-standard Next.js — read `node_modules/next/dist/docs/` before writing app code. The changes here are plain React/TS in existing client components, so risk is low, but honor that rule if anything Next-specific comes up.

---

## Task 1: DB migration — add `editor_note` column (ev-accounts)

**Files:**
- Create: `ev-accounts/backend/migrations/1230_quotes_editor_note.sql`
- Create: `ev-accounts/backend/scripts/_apply-migration-1230.ts`

- [ ] **Step 1: Write the migration SQL**

Create `ev-accounts/backend/migrations/1230_quotes_editor_note.sql`:

```sql
-- 1230_quotes_editor_note.sql
-- Adds a freeform editor rationale to essentials.quotes: why the quote was
-- selected and, if edited, what changed and why. Nullable; no backfill.
BEGIN;

ALTER TABLE essentials.quotes
  ADD COLUMN IF NOT EXISTS editor_note text;

COMMENT ON COLUMN essentials.quotes.editor_note IS
  'Editor rationale: why this quote was selected and, if edited, what changed and why. Freeform.';

COMMIT;
```

- [ ] **Step 2: Write the apply script**

Create `ev-accounts/backend/scripts/_apply-migration-1230.ts` (mirrors the repo's `_apply-migration-*.ts` pattern; reads from `backend/migrations/`, runs against `DATABASE_URL`, smoke-checks the column exists):

```ts
import 'dotenv/config';
import { Pool } from 'pg';
import { readFileSync } from 'fs';
import path from 'path';

const pool = new Pool({
  connectionString: process.env['DATABASE_URL'],
  ssl: { rejectUnauthorized: false },
});

const sql = readFileSync(
  path.join(process.cwd(), 'migrations', '1230_quotes_editor_note.sql'),
  'utf8',
);

try {
  await pool.query(sql);
  console.log('Migration 1230 applied.');

  const r = await pool.query(
    `SELECT 1 FROM information_schema.columns
      WHERE table_schema = 'essentials' AND table_name = 'quotes'
        AND column_name = 'editor_note'`,
  );
  console.log(`editor_note column present: ${r.rowCount === 1}`);
  if (r.rowCount !== 1) process.exitCode = 1;
} catch (e) {
  console.error('Migration 1230 failed:', e);
  process.exitCode = 1;
} finally {
  await pool.end();
}
```

- [ ] **Step 3: Apply the migration and verify**

Run (from the backend dir, where `.env` with `DATABASE_URL` lives):

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend && npx tsx scripts/_apply-migration-1230.ts
```

Expected output:
```
Migration 1230 applied.
editor_note column present: true
```

(`ADD COLUMN IF NOT EXISTS` is idempotent — safe to re-run.)

- [ ] **Step 4: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/migrations/1230_quotes_editor_note.sql backend/scripts/_apply-migration-1230.ts
git commit -m "feat(quotes): add essentials.quotes.editor_note column + apply script"
```

---

## Task 2: Backend service — read & write `editor_note` (ev-accounts)

**Files:**
- Modify: `ev-accounts/backend/src/lib/readrankQuotesService.ts`

No DB test harness exists for these pool-backed functions, so verification is `tsc --noEmit` (types must line up end to end) plus the live smoke query in Step 5.

- [ ] **Step 1: Add `editorNote` to the `AdminQuote` interface**

In `readrankQuotesService.ts`, change the `AdminQuote` interface (currently lines 45–52):

```ts
export interface AdminQuote {
  id: string;
  quoteText: string;
  deidentifiedText: string | null;
  sourceUrl: string | null;
  sourceName: string | null;
  editorNote: string | null;
  readrankSelected: boolean;
}
```

- [ ] **Step 2: Select and map `editor_note` in `listReadrankQuotes`**

Update the query's row type, the `SELECT` column list, and the object built per row (currently lines 58–79):

```ts
export async function listReadrankQuotes(politicianId: string): Promise<AdminTopicQuotes[]> {
  const { rows } = await pool.query<{
    id: string; topic_key: string; quote_text: string; deidentified_text: string | null;
    source_url: string | null; source_name: string | null; editor_note: string | null;
    readrank_selected: boolean;
  }>(
    `SELECT id, lower(topic_key) AS topic_key, quote_text, deidentified_text,
            source_url, source_name, editor_note, readrank_selected
       FROM essentials.quotes
      WHERE politician_id = $1
      ORDER BY lower(topic_key) ASC, readrank_selected DESC, created_at ASC NULLS LAST, id ASC`,
    [politicianId],
  );
  const byTopic = new Map<string, AdminTopicQuotes>();
  const order: string[] = [];
  for (const r of rows) {
    if (!byTopic.has(r.topic_key)) { byTopic.set(r.topic_key, { topicKey: r.topic_key, quotes: [] }); order.push(r.topic_key); }
    byTopic.get(r.topic_key)!.quotes.push({
      id: r.id, quoteText: r.quote_text, deidentifiedText: r.deidentified_text,
      sourceUrl: r.source_url, sourceName: r.source_name, editorNote: r.editor_note,
      readrankSelected: r.readrank_selected,
    });
  }
  return order.map((k) => byTopic.get(k)!);
}
```

- [ ] **Step 3: Add `editorNote` to `ReadrankQuoteUpdate` and write it**

Update the interface (currently lines 82–87) and the `UPDATE` in `updateReadrankQuote` (currently lines 89–108):

```ts
export interface ReadrankQuoteUpdate {
  quoteText: string;
  deidentifiedText: string | null;
  sourceUrl: string | null;
  sourceName: string | null;
  editorNote: string | null;
}

export async function updateReadrankQuote(quoteId: string, fields: ReadrankQuoteUpdate): Promise<void> {
  const { rows } = await pool.query<{ readrank_selected: boolean }>(
    `SELECT readrank_selected FROM essentials.quotes WHERE id = $1`,
    [quoteId],
  );
  if (rows.length === 0) throw new Error('Quote not found');

  const deidentifiedText = fields.deidentifiedText;
  // Mirror the selectReadrankQuote guard: a selected quote must keep de-identified text.
  if (rows[0].readrank_selected && !deidentifiedText?.trim()) {
    throw new Error('Cannot remove de-identified text from a selected quote');
  }

  await pool.query(
    `UPDATE essentials.quotes
        SET quote_text = $2, deidentified_text = $3, source_url = $4, source_name = $5, editor_note = $6
      WHERE id = $1`,
    [quoteId, fields.quoteText, deidentifiedText, fields.sourceUrl, fields.sourceName, fields.editorNote],
  );
}
```

- [ ] **Step 4: Typecheck**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend && npm run typecheck
```
Expected: exits 0 (no errors). It WILL error at the route call site until Task 3 passes `editorNote` — do Task 3 before relying on a clean typecheck, or expect a single "missing property editorNote" error here that Task 3 resolves.

- [ ] **Step 5: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/src/lib/readrankQuotesService.ts
git commit -m "feat(quotes): service reads/writes editor_note"
```

---

## Task 3: Backend PATCH route — accept `editor_note` (ev-accounts)

**Files:**
- Modify: `ev-accounts/backend/src/routes/readrankQuotesAdmin.ts`

- [ ] **Step 1: Add `editor_note` to the PATCH zod schema**

Change `updateBody` (currently lines 67–73):

```ts
const updateBody = z.object({
  quote_id: z.string().uuid(),
  quote_text: z.string().min(1),
  deidentified_text: z.string().nullable(),
  source_url: z.string().nullable(),
  source_name: z.string().nullable(),
  editor_note: z.string().nullable(),
});
```

- [ ] **Step 2: Pass `editorNote` through and into the admin log**

Update the PATCH handler body (currently lines 82–95):

```ts
  const { quote_id, quote_text, deidentified_text, source_url, source_name, editor_note } = parsed.data;
  try {
    await updateReadrankQuote(quote_id, {
      quoteText: quote_text,
      deidentifiedText: deidentified_text,
      sourceUrl: source_url,
      sourceName: source_name,
      editorNote: editor_note,
    });
    await logAdminAction(
      (req as AuthenticatedRequest).userId,
      'readrank_quote.update',
      null,
      { quote_id, quote_text, deidentified_text, source_url, source_name, editor_note },
    );
    res.status(200).json({ ok: true });
```

- [ ] **Step 3: Typecheck the whole backend**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/backend && npm run typecheck
```
Expected: exits 0 with no errors (Task 2 + Task 3 now consistent).

- [ ] **Step 4: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add backend/src/routes/readrankQuotesAdmin.ts
git commit -m "feat(quotes): PATCH readrank-quotes accepts editor_note"
```

---

## Task 4: Admin UI — show & edit `editor_note` (ev-accounts)

**Files:**
- Modify: `ev-accounts/admin/src/pages/admin/ReadRankQuotesPage.tsx`

- [ ] **Step 1: Add `editorNote` to the local `AdminQuote` interface**

Change the interface (currently lines 12–19):

```ts
interface AdminQuote {
  id: string;
  quoteText: string;
  deidentifiedText: string | null;
  sourceUrl: string | null;
  sourceName: string | null;
  editorNote: string | null;
  readrankSelected: boolean;
}
```

- [ ] **Step 2: Add `editorNote` to the edit-form state**

Change the `editForm` initial state (currently line 35):

```ts
  const [editForm, setEditForm] = useState({ quoteText: '', deidentifiedText: '', sourceUrl: '', sourceName: '', editorNote: '' });
```

- [ ] **Step 3: Seed `editorNote` when starting an edit**

Change `startEdit` (currently lines 94–103):

```ts
  function startEdit(q: AdminQuote) {
    setEditingId(q.id);
    setTopicsError(null);
    setEditForm({
      quoteText: q.quoteText,
      deidentifiedText: q.deidentifiedText ?? '',
      sourceUrl: q.sourceUrl ?? '',
      sourceName: q.sourceName ?? '',
      editorNote: q.editorNote ?? '',
    });
  }
```

- [ ] **Step 4: Send `editor_note` in the PATCH body**

In `saveEdit`, add `editor_note` to the JSON body (currently lines 112–118). It uses the existing `nullIfBlank` helper:

```ts
        body: JSON.stringify({
          quote_id: quoteId,
          quote_text: editForm.quoteText,
          deidentified_text: nullIfBlank(editForm.deidentifiedText),
          source_url: nullIfBlank(editForm.sourceUrl),
          source_name: nullIfBlank(editForm.sourceName),
          editor_note: nullIfBlank(editForm.editorNote),
        }),
```

- [ ] **Step 5: Add the editor-note textarea to edit mode**

In the edit-mode block, insert a new field after the De-identified text `<div>` and before the source-url/name row (i.e. between the block ending at line 251 and the `<div className="flex gap-2">` at line 252):

```tsx
                                <div>
                                  <label className="block text-xs font-medium text-gray-500 dark:text-gray-400 mb-0.5">Editor note (why selected / what edited)</label>
                                  <textarea
                                    className={fieldClass}
                                    rows={2}
                                    value={editForm.editorNote}
                                    onChange={(e) => setEditForm((f) => ({ ...f, editorNote: e.target.value }))}
                                  />
                                </div>
```

- [ ] **Step 6: Show the note in read mode**

In the read-mode block, add a line after the `verbatim:` paragraph (currently line 290) and before the `<div className="flex items-center gap-3 mt-1">` (line 291):

```tsx
                                {q.editorNote && (
                                  <p className="text-xs text-gray-500 dark:text-gray-400 mt-1">
                                    <span className="font-medium">editor note:</span> {q.editorNote}
                                  </p>
                                )}
```

- [ ] **Step 7: Build the admin app to verify types + compile**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts/admin && npm run build
```
Expected: `tsc && vite build` completes with no errors.

- [ ] **Step 8: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/ev-accounts
git add admin/src/pages/admin/ReadRankQuotesPage.tsx
git commit -m "feat(quotes): admin shows & edits editor_note"
```

---

## Task 5: Publish script — `editor_note` gate + per-quote overrides (on-the-record)

**Files:**
- Modify: `on-the-record/.claude/skills/publish-quotes/scripts/insert_quotes.py`
- Create: `on-the-record/tests/test_insert_quotes.py`

Approach: extract a pure `build_insert_rows(batch, politician_id)` function (no DB) so the gate and override logic are unit-testable, then wire it into `main()`.

- [ ] **Step 1: Write the failing test**

Create `on-the-record/tests/test_insert_quotes.py`:

```python
import importlib.util
from pathlib import Path

import pytest

# Load the script module by path (it lives under .claude/skills, not on sys.path).
_SPEC_PATH = Path(__file__).resolve().parents[1] / ".claude/skills/publish-quotes/scripts/insert_quotes.py"
_spec = importlib.util.spec_from_file_location("insert_quotes", _SPEC_PATH)
insert_quotes = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(insert_quotes)
build_insert_rows = insert_quotes.build_insert_rows

PID = "9a60d603-194d-410f-ae01-85bd6293f1a7"


def _batch(**over):
    b = {
        "topic_key": "abortion",
        "source_url": "https://www.youtube.com/watch?v=VIZ1h4OaImU",
        "quotes": [{"text": "A quote.", "editor_note": "Clearest statement of the stance."}],
    }
    b.update(over)
    return b


def test_row_shape_and_verbatim_deidentified_default():
    rows = build_insert_rows(_batch(), PID)
    assert len(rows) == 1
    pid, topic_key, quote_text, deid, source_name, source_url, editor_note = rows[0]
    assert pid == PID
    assert topic_key == "abortion"
    assert quote_text == "A quote."
    assert deid == "A quote."  # verbatim default
    assert source_name == "www.youtube.com"
    assert source_url == "https://www.youtube.com/watch?v=VIZ1h4OaImU"
    assert editor_note == "Clearest statement of the stance."


def test_empty_editor_note_is_rejected():
    with pytest.raises(ValueError, match="editor_note"):
        build_insert_rows(_batch(quotes=[{"text": "x", "editor_note": "   "}]), PID)


def test_missing_editor_note_is_rejected():
    with pytest.raises(ValueError, match="editor_note"):
        build_insert_rows(_batch(quotes=[{"text": "x"}]), PID)


def test_per_quote_topic_and_source_override_batch_defaults():
    rows = build_insert_rows(
        _batch(quotes=[{
            "text": "y",
            "editor_note": "note",
            "topic_key": "housing",
            "source_url": "https://www.youtube.com/watch?v=OTHER",
            "timestamp_seconds": 90,
        }]),
        PID,
    )
    _, topic_key, _, _, source_name, source_url, _ = rows[0]
    assert topic_key == "housing"
    assert source_url == "https://www.youtube.com/watch?v=OTHER&t=90s"
    assert source_name == "www.youtube.com"


def test_missing_topic_key_anywhere_is_rejected():
    b = {"source_url": "https://x.test", "quotes": [{"text": "z", "editor_note": "n"}]}
    with pytest.raises(ValueError, match="topic_key"):
        build_insert_rows(b, PID)
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && .venv/bin/python -m pytest tests/test_insert_quotes.py -v
```
Expected: FAIL — `AttributeError: module 'insert_quotes' has no attribute 'build_insert_rows'`.

- [ ] **Step 3: Add the `build_insert_rows` function**

In `insert_quotes.py`, add this function above `main()` (after `with_timestamp`, i.e. after line 63):

```python
def build_insert_rows(batch, politician_id):
    """Assemble (politician_id, topic_key, quote_text, deidentified_text, source_name,
    source_url, editor_note) tuples for insertion. Pure — no DB access.

    Each quote requires a non-empty editor_note (why selected / what edited & why).
    Per-quote topic_key and source_url override the batch-level defaults. Raises
    ValueError on a missing/blank editor_note, or a quote with no topic_key or
    source_url from either level.
    """
    default_topic = (batch.get("topic_key") or "").strip().lower()
    default_source = (batch.get("source_url") or "").strip()
    rows = []
    for i, q in enumerate(batch["quotes"], 1):
        note = (q.get("editor_note") or "").strip()
        if not note:
            raise ValueError(f"quote #{i} is missing a non-empty editor_note")
        topic_key = (q.get("topic_key") or default_topic).strip().lower()
        if not topic_key:
            raise ValueError(f"quote #{i} has no topic_key (per-quote or batch-level)")
        base_url = (q.get("source_url") or default_source).strip()
        if not base_url:
            raise ValueError(f"quote #{i} has no source_url (per-quote or batch-level)")
        text = q["text"].strip()
        deid = (q.get("deidentified") or text).strip()  # verbatim by default
        source_name = urlparse(base_url).netloc
        url = with_timestamp(base_url, q.get("timestamp_seconds"))
        rows.append((politician_id, topic_key, text, deid, source_name, url, note))
    return rows
```

- [ ] **Step 4: Run the test to verify it passes**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && .venv/bin/python -m pytest tests/test_insert_quotes.py -v
```
Expected: 5 passed.

- [ ] **Step 5: Wire `build_insert_rows` into `main()` and add `editor_note` to the INSERT**

In `main()`, replace the topic-validation + row-building + INSERT + preview so the script validates every distinct topic and inserts `editor_note`.

Replace the block that currently reads (lines 110–142, from the topic validation through the INSERT) with:

```python
    # 2. Build rows (enforces the editor_note gate + per-quote overrides).
    rows_to_insert = build_insert_rows(batch, pid)

    # 3. Validate every distinct topic_key against the canonical compass spine.
    for tk in sorted({r[1] for r in rows_to_insert}):
        cur.execute("SELECT 1 FROM inform.compass_topics WHERE lower(topic_key) = %s", (tk,))
        if not cur.fetchone():
            sys.exit(f"topic_key '{tk}' is not in inform.compass_topics. Pick a canonical key.")
    print(f"Topics: {', '.join(sorted({r[1] for r in rows_to_insert}))}")

    # House cap: at most 2 drafts per (politician, topic). Warn, don't block.
    from collections import Counter
    per_topic = Counter(r[1] for r in rows_to_insert)
    for tk, n in per_topic.items():
        if n > 2:
            print(f"  WARNING: {n} quotes for topic '{tk}' — house cap is 2 drafts per topic.")

    print(f"\n{'DRY RUN — nothing written' if not args.commit else 'COMMITTING'}: "
          f"{len(rows_to_insert)} quote(s), readrank_selected = FALSE\n")
    for i, (_, tk, text, deid, _, url, note) in enumerate(rows_to_insert, 1):
        print(f"  #{i} [{tk}] {url}")
        print(f"     text:  {text[:90]}{'…' if len(text) > 90 else ''}")
        print(f"     deid:  {'(verbatim)' if deid == text else deid[:90]}")
        print(f"     note:  {note[:90]}{'…' if len(note) > 90 else ''}")

    if not args.commit:
        print("\nRe-run with --commit to write.")
        return

    try:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO essentials.quotes "
            "(politician_id, topic_key, quote_text, deidentified_text, source_name, source_url, "
            " editor_note, readrank_selected, created_at, updated_at) VALUES %s",
            rows_to_insert,
            template="(%s, %s, %s, %s, %s, %s, %s, false, now(), now())",
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    print(f"\nInserted {len(rows_to_insert)} row(s).")
```

Also DELETE the now-dead lines that read the old single `topic_key` / `source_url` / `source_name` / `quotes` locals at the top of `main()` IF they are no longer referenced. Specifically the old lines:
```python
    topic_key = batch["topic_key"].strip().lower()
    source_url = batch["source_url"].strip()
    source_name = urlparse(source_url).netloc
    quotes = batch["quotes"]
    if not quotes:
        sys.exit("No quotes in batch.")
```
Replace them with just the empty-check (topic/source are now optional at batch level):
```python
    if not batch.get("quotes"):
        sys.exit("No quotes in batch.")
```

- [ ] **Step 6: Verify the module still imports and the verify-query block references a valid topic**

The final "verify" block in `main()` (currently ~lines 150–160) selects by a single `topic_key`; it now must use one of the inserted topics. Change its query to iterate the distinct topics, or scope it to the batch's politician across all inserted topics. Replace the verify block with:

```python
    # 4. Verify: show all quotes for this politician across the inserted topics.
    topics = sorted({r[1] for r in rows_to_insert})
    cur.execute(
        "SELECT lower(topic_key) AS topic_key, readrank_selected, "
        "       (deidentified_text = quote_text) AS verbatim, source_url, "
        "       left(quote_text,55) AS preview, (editor_note IS NOT NULL) AS has_note "
        "FROM essentials.quotes "
        "WHERE politician_id = %s AND lower(topic_key) = ANY(%s) "
        "ORDER BY lower(topic_key), created_at NULLS FIRST, id",
        (pid, topics))
    print(f"\nInserted quotes for this politician ({', '.join(topics)}):")
    for r in cur.fetchall():
        live = "LIVE" if r["readrank_selected"] else "draft"
        print(f"  [{live}] {r['topic_key']}  verbatim={r['verbatim']} note={r['has_note']}\n        {r['preview']}")
    print("\nNext: pick the single live quote per topic in /admin/readrank-quotes.")
```

- [ ] **Step 7: Re-run the unit tests + import check**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && .venv/bin/python -m pytest tests/test_insert_quotes.py -v && .venv/bin/python -c "import importlib.util,pathlib; p=pathlib.Path('.claude/skills/publish-quotes/scripts/insert_quotes.py'); s=importlib.util.spec_from_file_location('iq',p); m=importlib.util.module_from_spec(s); s.loader.exec_module(m); print('import OK')"
```
Expected: 5 passed, then `import OK`.

- [ ] **Step 8: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add tests/test_insert_quotes.py .claude/skills/publish-quotes/scripts/insert_quotes.py
git commit -m "feat(publish-quotes): editor_note gate + per-quote topic/source overrides"
```

---

## Task 6: Update the publish-quotes skill docs (on-the-record)

**Files:**
- Modify: `on-the-record/.claude/skills/publish-quotes/SKILL.md`
- Modify: `on-the-record/.claude/skills/publish-quotes/REFERENCE.md`
- Modify: `on-the-record/.claude/skills/publish-quotes/EDITORIAL.md`

No automated test — verification is a read-through against the spec. Keep edits factual and matched to Task 5's batch format.

- [ ] **Step 1: Update `SKILL.md` batch format + workflow**

In `SKILL.md`, add an `editor_note` to each quote in the `batch.json` example and note the per-quote `topic_key`/`source_url` overrides. Replace the `batch.json` code block (lines 41–51) with:

```json
{
  "politician_id": "9a60d603-194d-410f-ae01-85bd6293f1a7",
  "topic_key": "abortion",
  "source_url": "https://www.youtube.com/watch?v=VIZ1h4OaImU",
  "quotes": [
    {"text": "First single-claim quote …", "timestamp_seconds": 919,
     "editor_note": "Why this quote was picked; note any edits and why."},
    {"text": "Second quote …", "timestamp_seconds": 974,
     "editor_note": "Verbatim, no edits — clearest line on the topic.",
     "topic_key": "housing", "source_url": "https://www.youtube.com/watch?v=OTHER"}
  ]
}
```

Below the example, add these two lines:
```markdown
Every quote **requires** a non-empty `editor_note` — the script refuses the batch otherwise.
Per-quote `topic_key` / `source_url` override the batch-level defaults, so one batch can
span multiple topics and sources (e.g. straight from a curation-page export).
```

In the Workflow checklist, add a step after "Pick the topic_key" (line 24–25):
```markdown
- [ ] **Reconcile curation labels → topic keys.** A curation-page publish export uses free-text
      `topic_label`s. Map each to a canonical `inform.compass_topics` key and set it as the quote's
      `topic_key`. Confirm the mapping with the user.
- [ ] **Write the editor note.** For every quote capture why it was selected and, if edited, what
      changed and why. Confirm with the user before insert. The script enforces a non-empty note.
```

In "Non-negotiables", add:
```markdown
- Every quote carries an **`editor_note`** (selection rationale + edit justification). No blank notes.
- House cap: **≤ 2 drafts per (politician, topic)** — the script warns when exceeded.
```

- [ ] **Step 2: Update `REFERENCE.md` schema table**

In `REFERENCE.md`, add a row to the schema table (after the `deidentified_text` row):
```markdown
| `editor_note` | text, nullable — editor rationale: why selected + what was edited & why |
```

- [ ] **Step 3: Update `EDITORIAL.md`**

In `EDITORIAL.md`, add a short section:
```markdown
## Editor note (required)

Every quote needs a one-to-few-sentence `editor_note`: **why this quote** (what stance it
captures, why it's the clearest evidence) and, **if you edited it**, exactly what you changed
and why (trimmed filler with `…`, bracketed an inserted word, condensed two sentences). If it's
verbatim, say "verbatim, no edits." This is the defense of the wording — write it for a skeptical
reader, not as a note-to-self.
```

- [ ] **Step 4: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add .claude/skills/publish-quotes/SKILL.md .claude/skills/publish-quotes/REFERENCE.md .claude/skills/publish-quotes/EDITORIAL.md
git commit -m "docs(publish-quotes): document editor_note gate, per-quote overrides, label reconcile"
```

---

## Task 7: Curation JSON publish-batch export helper (on-the-record web)

**Files:**
- Create: `on-the-record/web/lib/candidatePublish.ts`
- Create: `on-the-record/web/lib/candidatePublish.test.ts`
- Modify: `on-the-record/web/lib/types.ts:183` (comment only)

The export emits **all** candidates as a handoff batch: base `source_url` + `timestamp_seconds` (the script appends `&t=Ns`), free-text `topic_label` (the skill reconciles to `topic_key`), the `editor_note`, and `starred` as a hint.

- [ ] **Step 1: Write the failing test**

Create `on-the-record/web/lib/candidatePublish.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { candidatesToPublishBatch, candidatesMissingNotes } from "./candidatePublish";
import type { Candidate } from "./types";

function cand(over: Partial<Candidate>): Candidate {
  return {
    id: "1", politician_id: "p1", meeting_id: "m", meeting_title: "Council",
    meeting_date: "2026-04-15", segment_id: 3, start_time: 640,
    source_url: "https://www.youtube.com/watch?v=abc", playback_kind: "youtube",
    orig_text: "orig verbatim", edit_text: "edited text",
    label: "housing insurance", note: "why it matters", starred: true,
    created_at: 0, ...over,
  };
}

describe("candidatesToPublishBatch", () => {
  it("emits politician_id and one entry per candidate with base url + timestamp", () => {
    const batch = candidatesToPublishBatch("p1", [cand({})]);
    expect(batch.politician_id).toBe("p1");
    expect(batch.quotes).toHaveLength(1);
    const q = batch.quotes[0];
    expect(q.text).toBe("edited text");
    expect(q.topic_label).toBe("housing insurance");
    expect(q.source_url).toBe("https://www.youtube.com/watch?v=abc"); // base, no &t=
    expect(q.timestamp_seconds).toBe(640);
    expect(q.editor_note).toBe("why it matters");
    expect(q.starred).toBe(true);
  });

  it("falls back to verbatim text when edit_text is blank, and trims", () => {
    const batch = candidatesToPublishBatch("p1", [cand({ edit_text: "   ", orig_text: " raw " })]);
    expect(batch.quotes[0].text).toBe("raw");
  });

  it("includes ALL candidates, starred or not", () => {
    const batch = candidatesToPublishBatch("p1", [
      cand({ id: "a", starred: true }),
      cand({ id: "b", starred: false, note: "second" }),
    ]);
    expect(batch.quotes).toHaveLength(2);
  });
});

describe("candidatesMissingNotes", () => {
  it("returns ids of candidates whose note is empty/whitespace", () => {
    const missing = candidatesMissingNotes([
      cand({ id: "a", note: "ok" }),
      cand({ id: "b", note: "   " }),
      cand({ id: "c", note: "" }),
    ]);
    expect(missing).toEqual(["b", "c"]);
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/web && npm run test -- candidatePublish
```
Expected: FAIL — cannot resolve `./candidatePublish`.

- [ ] **Step 3: Write the helper**

Create `on-the-record/web/lib/candidatePublish.ts`:

```ts
import type { Candidate } from "./types";

// The publish-batch handoff the curation page hands to the publish-quotes skill.
// It carries EVERY candidate (not just the starred pick). Topic labels stay
// free-text (reconciled to a compass topic_key at publish); source_url is the
// base meeting url and timestamp_seconds is separate (the script appends &t=Ns).
export interface PublishQuote {
  text: string;
  topic_label: string;
  source_url: string | null;
  timestamp_seconds: number;
  editor_note: string;
  starred: boolean;
}
export interface PublishBatch {
  politician_id: string;
  quotes: PublishQuote[];
}

export function candidatesToPublishBatch(politicianId: string, cands: Candidate[]): PublishBatch {
  return {
    politician_id: politicianId,
    quotes: cands.map((c) => ({
      text: (c.edit_text.trim() || c.orig_text.trim()),
      topic_label: c.label.trim(),
      source_url: c.source_url,
      timestamp_seconds: c.start_time,
      editor_note: c.note.trim(),
      starred: c.starred,
    })),
  };
}

// Candidate ids whose editor note is empty/whitespace — the export gate blocks
// until this is empty.
export function candidatesMissingNotes(cands: Candidate[]): string[] {
  return cands.filter((c) => !c.note.trim()).map((c) => c.id);
}
```

- [ ] **Step 4: Update the `note` comment in `types.ts`**

Change `on-the-record/web/lib/types.ts:183` from:
```ts
  note: string;               // note-to-self
```
to:
```ts
  note: string;               // editor rationale: why selected + what was edited & why (required before publish export)
```

- [ ] **Step 5: Run the test to verify it passes**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/web && npm run test -- candidatePublish
```
Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add web/lib/candidatePublish.ts web/lib/candidatePublish.test.ts web/lib/types.ts
git commit -m "feat(web): publish-batch export helper + missing-note detector"
```

---

## Task 8: Curation page UI — bigger required note + JSON export (on-the-record web)

**Files:**
- Modify: `on-the-record/web/app/people/[id]/PersonDetailClient.tsx`

- [ ] **Step 1: Import the helpers**

At the top of `PersonDetailClient.tsx`, immediately after the existing `import { candidatesToMarkdown } from "@/lib/candidateMarkdown";` line (line 20), add:

```ts
import { candidatesToPublishBatch, candidatesMissingNotes } from "@/lib/candidatePublish";
```
(This file uses the `@/*` path alias — confirmed in `web/tsconfig.json` — so match it exactly as shown.)

- [ ] **Step 2: Turn the note input into a required textarea**

In `CandidateCard` replace the note `<input>` (lines 661–666) with a textarea plus an inline "required" hint when empty:

```tsx
        <textarea
          className="skimNoteIn"
          rows={2}
          value={c.note}
          placeholder="why this quote — and what you edited & why (required to publish)"
          onChange={(e) => collection.update(c.id, { note: e.target.value })}
        />
      </div>
      {!c.note.trim() && (
        <div style={{ fontSize: "0.72rem", color: "#b91c1c", marginTop: "0.25rem" }}>
          Editor note required before publish export.
        </div>
      )}
```

Note: the closing `</div>` shown is the existing `skimCandFoot` closer — keep the structure so the warning renders just below the footer. Verify the JSX still balances after the edit.

- [ ] **Step 3: Add JSON export state + gating to `CurateView`**

In `CurateView`, after the existing `md` line (line 533), add:

```tsx
  const [jsonOpen, setJsonOpen] = useState(false);
  const [jsonCopied, setJsonCopied] = useState(false);
  const missingNotes = candidatesMissingNotes(collection.cands);
  const publishJson = JSON.stringify(
    candidatesToPublishBatch(collection.cands[0]?.politician_id ?? "", collection.cands),
    null,
    2,
  );
  const copyJson = () => {
    navigator.clipboard.writeText(publishJson).then(() => {
      setJsonCopied(true);
      setTimeout(() => setJsonCopied(false), 1400);
    });
  };
```

- [ ] **Step 4: Add the JSON export button next to the Markdown button**

In the `skimCurateHead` block, after the existing "Export Markdown" button (line 552), add:

```tsx
        <button
          className="skimExportBtn"
          onClick={() => setJsonOpen(true)}
          disabled={collection.cands.length === 0 || missingNotes.length > 0}
          title={missingNotes.length > 0
            ? `${missingNotes.length} quote(s) still need an editor note`
            : "Export a publish batch for the publish-quotes skill"}
        >
          Export publish batch
        </button>
```

- [ ] **Step 5: Add a gate hint under the header**

Right after the `skimHint` paragraph (line 558), add:

```tsx
      {missingNotes.length > 0 && (
        <p className="skimHint" style={{ color: "#b91c1c" }}>
          {missingNotes.length} quote{missingNotes.length === 1 ? "" : "s"} need an editor note
          before you can export a publish batch.
        </p>
      )}
```

- [ ] **Step 6: Add the JSON export modal**

After the existing Markdown modal block (the `{exportOpen && (…)}` ending at line 602), add a sibling modal:

```tsx
      {jsonOpen && (
        <div className="skimModal" onClick={() => setJsonOpen(false)}>
          <div className="skimModalCard" onClick={(e) => e.stopPropagation()}>
            <header>
              <h3>Export — publish batch (JSON)</h3>
              <button className="skimModalX" onClick={() => setJsonOpen(false)}>×</button>
            </header>
            <pre>{publishJson}</pre>
            <footer>
              <button className="skimBtnSoft" onClick={() => setJsonOpen(false)}>Close</button>
              <button className="skimBtnPrimary" onClick={copyJson}>
                {jsonCopied ? "Copied ✓" : "Copy to clipboard"}
              </button>
            </footer>
          </div>
        </div>
      )}
```

- [ ] **Step 7: Build the web app to verify it compiles**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/web && npm run build
```
Expected: `next build` completes with no type/lint errors.

- [ ] **Step 8: Visually verify in the dev server**

Start the dev server and confirm: the note field is a multi-line textarea; a card with an empty note shows the red "required" hint; the "Export publish batch" button is disabled while any note is empty and enabled once all are filled; clicking it shows JSON containing `editor_note` and `topic_label` for every candidate. (Use the preview tooling; grab a couple of quotes on a person page, leave one note blank, then fill it.)

- [ ] **Step 9: Run the full web test suite (guard against regressions)**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/web && npm run test
```
Expected: all tests pass (including the existing `candidateMarkdown` tests and the new `candidatePublish` tests).

- [ ] **Step 10: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add web/app/people/[id]/PersonDetailClient.tsx
git commit -m "feat(web): require editor note; add publish-batch JSON export to curation page"
```

---

## Task 9: End-to-end smoke (manual, cross-repo)

**Files:** none (verification only)

- [ ] **Step 1: Curation → JSON**

On a person page, grab 2 quotes under one topic and 1 under another, fill an editor note on each, and export the publish batch. Confirm the JSON has 3 quotes with `topic_label`, `editor_note`, base `source_url`, and `timestamp_seconds`.

- [ ] **Step 2: JSON → batch → dry run**

Reconcile `topic_label`s to real `topic_key`s (set per-quote `topic_key`), save as `batch.json`, and dry-run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && .venv/bin/python .claude/skills/publish-quotes/scripts/insert_quotes.py batch.json
```
Expected: preview shows each quote's `[topic]`, text, deid `(verbatim)`, and `note:`; a topic with >2 drafts prints the house-cap WARNING; no write happens.

- [ ] **Step 3: Commit (with the user's OK) + admin check**

After the user approves, re-run with `--commit`. Then open `/admin/readrank-quotes`, expand the politician, and confirm each new draft shows its `editor note:` line in read mode and that editing a quote surfaces the note in a textarea that saves. Selecting the live quote per topic still works.

---

## Notes for the executor

- **Do not** create the migration in `supabase/migrations/` — the quotes-table alters (`070`, `293`) live in `backend/migrations/`, and `1230` follows that convention. The apply script is how DDL actually reaches the DB here.
- The backend has no live-DB test harness for the pool-backed service functions; `npm run typecheck` + the Task 9 live check are the verification. Don't invent a DB-dependent unit test.
- Keep `deidentified_text` behavior unchanged (verbatim default; the admin/select guards still hold). This feature only adds `editor_note`.
- Star stays a curator hint; nothing in this plan auto-sets `readrank_selected`.
