# essentials.quotes — DB reference

Quotes live in **`essentials.quotes`** in the **ev-accounts** Supabase — a *different* database
from the on-the-record civic pipeline. There is no on-the-record quotes table.

## Connection

`ev-accounts/backend/.env` → `DATABASE_URL` (Supabase **session pooler**,
`...pooler.supabase.com:5432`, SSL). The insert script reads it from there by default
(`--env-file` to override, or set `DATABASE_URL` in the environment). Both `psql` and the
on-the-record `.venv/bin/python` (psycopg2) can reach it.

## Schema (`essentials.quotes`)

| column | notes |
|---|---|
| `id` | uuid, `gen_random_uuid()` |
| `politician_id` | uuid → `essentials.politicians` (NOT NULL) |
| `topic_key` | text, must match `inform.compass_topics` (lowercase) |
| `quote_text` | text, the quote as displayed |
| `deidentified_text` | text, nullable — see below |
| `editor_note` | text, nullable — editor rationale: why selected + what was edited & why |
| `source_name` / `source_url` | text, nullable; convention is domain + full URL |
| `readrank_selected` | boolean, NOT NULL default false — the "live" switch |
| `created_at` / `updated_at` | timestamptz |

## The two flags that matter

**`readrank_selected` — the live switch.** A unique index allows **at most one `true` per
`(politician_id, lower(topic_key))`**. Insert new quotes as `false`. The admin tool's select action
clears the others and sets one true in a transaction. Choosing it is a human curation step.

**`deidentified_text` — public text + selection gate.** The public site serves
`COALESCE(deidentified_text, quote_text)`, so whatever is here is what's shown; the migration says
`NULL` means "original is safe to serve verbatim." **But** `selectReadrankQuote` refuses any row
with `NULL` deidentified_text — so to be admin-selectable a row **must** have it populated. House
norm (confirmed in live data): a **verbatim copy** of `quote_text`, lightly genericized only when a
hyper-specific marker would reveal the speaker (e.g. "…to Louisiana" → "…to another state that has
banned abortion").

## Lookups

```sql
-- politician_id (verify the name!)
SELECT id, COALESCE(full_name, TRIM(COALESCE(preferred_name,first_name)||' '||last_name)) AS name
FROM essentials.politicians WHERE name-or-id matches …;

-- canonical topic keys
SELECT topic_key FROM inform.compass_topics ORDER BY topic_key;

-- meeting source URL + slug (deep-link target)
SELECT video_url, source_url, slug FROM meetings.meetings WHERE id = '<uuid>';
```

YouTube source URLs take `&t=<seconds>s` to deep-link the exact moment — the script does this from
each quote's `timestamp_seconds`. The on-the-record meeting page
(`ontherecord.empowered.vote/meetings/<id>`) is the eventual richer deep-link target.

## Admin tool

`/admin/readrank-quotes` (ev-accounts admin) — lists politicians + topics, lets a curator select
the one live quote per topic. Backend: `backend/src/lib/readrankQuotesService.ts`,
`backend/src/routes/readrankQuotesAdmin.ts`.
