# Meeting URLs key on the immutable UUID; slugs are frozen at creation

## Status

accepted

## Context & Decision

Public meeting URLs are built on `meetings.meetings.id` — a `gen_random_uuid()` value minted once at first publish (`src/publish.py`) and surfaced to the frontend as `meeting_id` (`web/lib/types.ts`). The human-readable [Source key](../../CONTEXT.md)-adjacent identifier derived from `date` + `meeting_type` lives in the separate `slug` column and is **not** currently used in any URL.

We decided that **editing display metadata (title, meeting_type, city, date, event_kind) must never regenerate the slug or the local meeting directory name.** The slug and directory are frozen at creation; metadata edits touch display columns only. This holds whether or not a URL scheme ever uses the slug.

## Considered Options

- **UUID URLs + frozen slug (chosen).** URLs are ugly but permanently stable; freezing the slug costs nothing and keeps a future pretty-URL migration safe.
- **Pretty slug-based URLs now.** Better SEO, but a slug derived from mutable `meeting_type` would move whenever a title is corrected — and on a static-export + Render site, redirects are fiddly host-level rewrites. Deferred, not rejected outright.

## Consequences

- A metadata correction (e.g. fixing a wrong title) is safe: the UUID and every inbound link are unaffected.
- No redirect infrastructure is built now. If the site later adopts slug-based URLs for SEO, the freeze rule means those slugs are already stable — revisit redirects only if a published slug ever genuinely needs to change.
- The GUI's metadata editor is the enforcement point: it must write `title`/`meeting_type`/`city`/`date`/`event_kind` to local files + Supabase, but never rename the directory or rewrite the `slug` column.
