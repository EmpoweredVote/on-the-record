# Editor rationale for quotes — design

**Date:** 2026-07-07
**Status:** Approved design, pre-implementation

## Problem

Every published quote should carry a defensible reason: **why the quote was
selected** and, **if it was edited, what changed and why**. Today nothing
captures this in a way that survives to the backend:

- `essentials.quotes` (ev-accounts) has **no rationale column** at all.
- The on-the-record curation page (`web/app/people/[id]/PersonDetailClient.tsx`)
  already has a per-candidate `note` field — but it is framed "note to self",
  lives only in localStorage / the Markdown export, and **is never sent to the
  DB**.
- The ev-accounts Read & Rank admin (`ReadRankQuotesPage.tsx`) shows no note.
- Publishing is manual and effectively single-quote-per-topic: curators grab and
  star in the web UI, then a curator/Claude runs `insert_quotes.py` by hand. The
  curation "pick" (star) is single-select per topic label.

The user wants: a single defensible rationale on **every** quote, captured at
curation time, carried to the backend, and visible/editable in the Read & Rank
admin. Separately, the curation page should be able to publish **multiple** draft
quotes per topic, not just the one starred pick.

## Decisions

- **One combined freeform field** — `editor_note` — covers both selection
  rationale and edit justification. Not two separate fields.
- **Repurpose the existing curation `note` field** into this rationale (relabel;
  no second field, no localStorage migration). Widen its input to a multi-line
  textarea.
- **Publish sends all curated candidates** for the person as drafts (not only the
  starred one). The star stays a curator *hint*; choosing the live quote remains a
  human step in the ev-accounts admin.
- **The rationale is required at the curation page before export**, and the
  publish script re-enforces a non-empty `editor_note` as a backstop. Together
  these guarantee every published quote has reasoning. The DB column is nullable
  and the admin does not force one, so existing rows and quick admin text fixes
  are not blocked.
- **No new server / HTTP push** — publishing stays script-driven, human-in-the-
  loop, matching the current architecture (`web/` is a static-export public site
  with no DB creds or admin auth in the browser).
- **No backfill** of existing quotes — rationale can't be reconstructed after the
  fact; they display as "—" and can be filled in by hand in the admin.

## Scope of this pass

In scope: DB column, curation page (repurpose + JSON export), publish path
(`insert_quotes.py` + skill docs), ev-accounts Read & Rank admin (read/write).

Explicitly **out** of scope (deferred follow-ups):

- Public Read & Rank surfacing (the info-icon on `readrank.empowered.vote`) and
  exposure of `editor_note` in `/api/essentials/quotes` or the reveal payload.
- Any one-click HTTP publish button / import endpoint.
- Star auto-setting the live quote.

## Components & changes

### A. Database (ev-accounts)

New migration after the current highest (the table is externally created; this
repo already owns the `070` / `293` alters):

```sql
ALTER TABLE essentials.quotes ADD COLUMN editor_note text;
COMMENT ON COLUMN essentials.quotes.editor_note IS
  'Editor rationale: why this quote was selected and, if edited, what changed and why. Freeform.';
```

Nullable. No index. No backfill.

### B. Curation page (`web/app/people/[id]/PersonDetailClient.tsx`)

> Note: this `web/` app runs a non-standard Next.js — per `web/AGENTS.md`, read
> `node_modules/next/dist/docs/` before writing code here.

- **Repurpose `Candidate.note`** (`web/lib/types.ts:183`) as the editor rationale.
  Keep the field name/shape; update its comment.
- **Relabel the input** in `CandidateCard` (currently single-line, ~lines 661-666)
  and **change `<input>` → `<textarea>`** (a few rows / auto-grow). New placeholder
  along the lines of *"why this quote — and what you edited & why"*.
- **Require the rationale before export.** The "Export publish batch (JSON)"
  action is disabled (or blocks with an inline message) while any candidate has an
  empty/whitespace `note`; the offending cards are flagged so the curator can fill
  them in. This is the primary gate; the script check is a backstop.
- **New "Export publish batch (JSON)" action** in `CurateView` alongside the
  existing Markdown export. It emits **all candidates** for the person (not just
  starred), each as:

  ```json
  {
    "politician_id": "<uuid>",
    "quotes": [
      {
        "text": "<edit_text or orig_text>",
        "topic_label": "<free-text label>",
        "source_url": "<meeting source url>",
        "timestamp_seconds": 919,
        "editor_note": "<the rationale>",
        "starred": true
      }
    ]
  }
  ```

  `text` uses `edit_text` when present, else `orig_text`. `deidentified` is left
  to the script's verbatim default unless the curator has genuinely blinded it.
  `starred` is carried as a hint only.

### C. Publish path (`.claude/skills/publish-quotes/`)

- **`insert_quotes.py`** accepts a richer batch:
  - per-quote `topic_key`, `source_url`, and `editor_note`, each falling back to
    batch-level defaults where present (existing single-topic batches still work).
  - **errors if any quote's `editor_note` is empty/absent.**
  - writes `editor_note` into the new column; shows it in the dry-run preview.
- **`SKILL.md` / `EDITORIAL.md`** gain steps:
  - Capture/confirm a rationale per quote before insert.
  - **Reconcile each `topic_label` → canonical `topic_key`** (Claude proposes from
    `inform.compass_topics`, user confirms) and set per-quote `topic_key`.
  - **Warn if a topic exceeds 2 drafts** (house cap).
  - Dry-run → `--commit` unchanged; still inserts as `readrank_selected = false`.

### D. Read & Rank admin (ev-accounts)

- **`backend/src/lib/readrankQuotesService.ts`**: `listReadrankQuotes` selects
  `editor_note`; `AdminQuote` gains `editorNote: string | null`;
  `updateReadrankQuote` writes it; `ReadrankQuoteUpdate` gains the field.
- **`backend/src/routes/readrankQuotesAdmin.ts`**: PATCH body schema accepts
  `editor_note: string | null`.
- **`admin/src/pages/admin/ReadRankQuotesPage.tsx`**: read-only mode shows the
  note (labeled); edit mode adds a textarea. Not required to save.

## Data flow

```
curation page (grab + edit + rationale, all candidates)
      │  Export publish batch (JSON)
      ▼
insert_quotes.py  ──(label→topic_key reconcile, editor_note gate)──▶ essentials.quotes
      │                                                               (drafts, editor_note set)
      ▼
Read & Rank admin  ── shows/edits editor_note; human selects the one live quote per topic
```

## Testing

- `insert_quotes.py`: dry-run rejects a batch with any empty `editor_note`;
  per-quote `topic_key`/`source_url` override batch defaults; verbatim
  `deidentified` default still applies; `editor_note` appears in preview and in
  the committed row. (Existing single-topic batch still parses.)
- Curation page: rationale textarea persists to localStorage; export is blocked
  while any candidate note is empty/whitespace (offending cards flagged); once all
  are filled, JSON export includes every candidate with `editor_note` and
  `topic_label`.
- Admin: `editor_note` round-trips through GET → PATCH; a null note renders as
  "—" and is not required to save or select.

## Open items / risks

- Free-text `topic_label` → `topic_key` reconciliation is a manual step in the
  skill; ambiguous labels need a human call. Acceptable for now.
- House cap is 2 drafts per (politician, topic); publishing "all candidates" can
  exceed it — hence the skill warning rather than a hard block.
