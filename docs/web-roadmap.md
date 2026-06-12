# CouncilScribe Web — Roadmap

The walking skeleton (meetings index + one synced meeting page) shipped with:

- **Data**: `civic` schema in the E.V Backend Supabase project (`supabase/migrations/0001_initial.sql`) — `meetings`, `people`, `meeting_speakers`, `segments` (FTS-indexed), public-read RLS.
- **Publisher**: `src/publish.py` + `python run_local.py --publish` / `--publish-meeting MEETING_ID`.
- **Site**: `web/` Next.js app — YouTube + direct-file (CATS TV) player adapters, transcript sync, click-to-seek, `?t=` deep links, in-meeting search.

Reference product: [CalMatters Digital Democracy](https://calmatters.digitaldemocracy.org).

## Phase 2 — People

- `/people` roster page (like digitaldemocracy.org/people): card grid from `civic.people`, filterable by city/body.
- `/people/[slug]` profile: name, district, link to essentials.city profile, and an **appearances** section — every meeting where the person spoke, with their segments and deep links. Query: `segments where politician_slug = ? order by meeting_id, start_time` (the `segments_person_idx` index already covers this).
- Enrich `people` rows with photos/bios from essentials (shared `politician_slug`/`politician_id`).

## Phase 3 — Cross-meeting search

- `/search` page backed by a Postgres RPC: `websearch_to_tsquery('english', $q)` against `segments.tsv` (GIN index in place) + `ts_headline` for snippets, joined to meeting metadata, optionally filtered by speaker or city.
- Each result deep-links to `/meetings/[id]?t=...#seg-...`.

## Phase 4 — essentials.city integration

- Public JSON endpoint `/api/people/[slug]/appearances` (CORS-allowlisted for essentials origins) so essentials can render an appearances card on politician profiles — mirroring digitaldemocracy's "hearings" card.
- Optional reverse links: meeting pages link speaker names to essentials profiles.

## Phase 5 — Beyond council meetings

- Debates, candidate forums, school boards. Schema is already body-agnostic (`body_slug`, `meeting_type`); consider an `event_kind` column ('council' | 'debate' | 'forum' | ...) for filtering and per-kind page treatments.
- Non-roster participants (candidates, moderators) may deserve `people` rows without `politician_slug` — would require switching the people PK to a site-local slug with `politician_slug` as a nullable link.

## Phase 6 — More playback providers

- `resolve_playback()` in `src/publish.py` is the single extension point on the pipeline side; one player adapter component in `web/app/meetings/[meetingId]/players/` on the site side.
- Candidates: Vimeo (Player SDK), Granicus, Swagit, Cablecast VOD generally (many expose direct MP4/HLS like CATS TV does), IBM Video.

## Phase 7 — Self-hosting public-domain footage

For sources that are neither embeddable nor direct-file, when links rot, or if hotlinking a community station's bandwidth becomes impolite:

- Upload the already-downloaded `source.mp4` (or audio-only) to Supabase Storage / Cloudflare R2 during publish (`--self-host-media` flag).
- Publish with `playback_kind='file'` pointing at our bucket — **zero schema or player changes needed**.
- Rights call per source: city-owned public-record footage yes; TV-station debate footage no (keep embed/link).

## Phase 8 — Ops & polish

- Render → AWS migration if/when needed (Next.js standalone output).
- `sitemap.xml` + per-meeting OpenGraph metadata for SEO/sharing.
- Stable segment hashes (e.g., hash of meeting_id + speaker + text prefix) so `#seg-` deep links survive re-publishes that renumber segments.
- Nightly consistency check: re-publish drift detection between `transcript_named.json` and DB.
- Word-level karaoke highlighting if ever wanted: add nullable `words jsonb` to `segments`.

## Manual setup notes

- PostgREST must expose the `civic` schema: Supabase dashboard → Project Settings → Data API → Exposed schemas → add `civic`.
- Publisher env (`.env.local` at repo root): `SUPABASE_URL`, `SUPABASE_SECRET_KEY`.
- Site env (`web/.env.local`): `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` (see `web/.env.local.example`).
- Render: create a Web Service from this repo with Root Directory `web`, build `npm run build`, start `npm run start`, plus the two `NEXT_PUBLIC_*` env vars.
