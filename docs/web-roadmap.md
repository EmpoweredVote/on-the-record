# On the Record Web — Roadmap

Reference product: [CalMatters Digital Democracy](https://calmatters.digitaldemocracy.org).

## How it works now

Three pieces, two repos:

1. **Pipeline (this repo)** — `src/publish.py` writes meetings, speakers, and segments over direct Postgres (`DATABASE_URL`) into the `meetings.*` schema of the E.V Backend database. Idempotent by meeting slug; `python run_local.py --publish` / `--publish-meeting`.
2. **ev-accounts (essentials.city backend repo)** — serves the public API the site consumes: `/api/meetings`, `/api/meetings/[id]`, `/api/meetings/[id]/transcript` (paginated, 200 segments/page). **All new server-side features land here**, since it owns the database and is shared with essentials.city.
3. **Site (`web/`, this repo)** — Next.js **static export** (`output: "export"`) deployed as a Render static site. Fetches ev-accounts at build time via `EV_ACCOUNTS_URL` (build-time only, not exposed to the browser). Player adapters for YouTube + direct file/HLS, transcript sync, click-to-seek, `?t=` deep links, in-meeting search.

Two consequences of static export worth remembering:

- Anything dynamic at request time (cross-meeting search, future interactivity) must call ev-accounts **from the browser**, so those endpoints need CORS for the site origin.
- New published meetings only appear after a rebuild — see the deploy-hook item in Phase 10.

Each phase below tags work by where it lives: **[pipeline]**, **[ev-accounts]**, or **[web]**.

## Phase 2 — People ✅ (built 2026-06-12; live once data is re-published and both repos deploy)

- **[ev-accounts]** `/api/people` (roster, filterable by city/body), `/api/people/[slug]` (profile), `/api/people/[slug]/appearances` (every meeting where the person spoke, with segments and timestamps — `segments where politician_slug = ? order by meeting_id, start_time`).
- **[web]** `/people` roster page (card grid, like digitaldemocracy.org/people) and `/people/[slug]` profile: name, district, essentials.city link, appearances with deep links into meetings.
- **[ev-accounts]** Enrich people with photos/bios already in the essentials data (shared `politician_slug`/`politician_id`).
- Free byproduct: because ev-accounts *is* the essentials backend, essentials.city can render an "appearances" card on politician profiles straight from the same endpoint — the old "essentials integration" phase collapses into this one. Remaining leftover: meeting pages here link speaker names to essentials profiles.

## Phase 3 — Cross-meeting search ✅ (built 2026-06-12; live once data is re-published and both repos deploy)

- **[ev-accounts]** `/api/search?q=&speaker=&city=`: `websearch_to_tsquery('english', $q)` against the segments FTS index + `ts_headline` snippets, joined to meeting metadata. CORS-allowlisted for the site origin (static export ⇒ browser calls it directly).
- **[web]** `/search` page (client component): results grouped by meeting, each deep-linking to `/meetings/[id]?t=...#seg-...`.

## Phase 4 — AI summaries & key moments  ← next up

- **[pipeline]** During publish, generate per-meeting artifacts with an LLM pass over the named transcript: short summary, topic list, and notable moments (segment ranges + one-line description). Deterministic re-publish: store alongside the transcript so re-runs don't re-bill.
- **[ev-accounts]** Columns + API: summary/topics/moments on the meeting payload.
- **[web]** Summary block at the top of meeting pages; summary preview text on the index cards; "key moments" as jump links into the transcript.

## Phase 5 — Clips & sharing

- **[web]** Select a transcript range → shareable clip URL: a page showing the quoted text, speaker, meeting context, and the player cued to the range, plus OpenGraph card so links unfurl well.
- **Prerequisite (pulled forward from ops): stable segment hashes** — e.g. hash of meeting_id + speaker + text prefix — so clip and `#seg-` links survive re-publishes that renumber segments. **[pipeline]** computes them, **[ev-accounts]** serves them, **[web]** uses them as anchors.

## Phase 6 — Topics & issues

- **[pipeline]** Topic tagging falls out of the Phase 4 summarization pass — tag segments/meetings with a controlled topic vocabulary (zoning, budget, policing, ...).
- **[ev-accounts]** `/api/topics`, `/api/topics/[slug]` (discussions across meetings).
- **[web]** Topic pages collecting every discussion of an issue across meetings and bodies; topic chips on meeting pages.

## Phase 7 — Beyond council meetings

- Debates, candidate forums, school boards. Schema is already body-agnostic (`body_slug`, `meeting_type`); consider an `event_kind` column ('council' | 'debate' | 'forum' | ...) for filtering and per-kind page treatments.
- Non-roster participants (candidates, moderators) may deserve people rows without `politician_slug` — would require a site-local slug PK with `politician_slug` as a nullable link.

## Phase 8 — More playback providers

- `resolve_playback()` in `src/publish.py` is the single extension point on the pipeline side; one player adapter component in `web/app/meetings/[meetingId]/players/` on the site side.
- Candidates: Vimeo (Player SDK), Granicus, Swagit, Cablecast VOD generally (many expose direct MP4/HLS like CATS TV does), IBM Video.

## Phase 9 — Self-hosting public-domain footage

For sources that are neither embeddable nor direct-file, when links rot, or if hotlinking a community station's bandwidth becomes impolite:

- Upload the already-downloaded `source.mp4` (or audio-only) to object storage during publish (`--self-host-media` flag).
- Publish with `playback_kind='file'` pointing at our bucket — zero schema or player changes needed.
- Rights call per source: city-owned public-record footage yes; TV-station debate footage no (keep embed/link).

## Phase 10 — Ops & polish

- **Rebuild on publish**: Render deploy hook called at the end of `--publish` so new meetings appear without manual redeploys.
- `sitemap.xml` + per-meeting OpenGraph metadata for SEO/sharing (clip OG cards arrive in Phase 5).
- Nightly consistency check: re-publish drift detection between `transcript_named.json` and DB.
- Word-level karaoke highlighting if ever wanted: word timestamps already live in `transcript.json` on disk; would need a publish path and player support.

## Deferred / not planned

- **Alerts & saved searches** ("email me when X is mentioned") — deliberately out for now; revisit once accounts exist in ev-accounts.

## Setup notes

- Publisher env (`.env.local` at repo root): `DATABASE_URL` (Supabase dashboard → Project Settings → Database → connection string, URI mode, port 5432).
- Site env (`web/.env.local`): `EV_ACCOUNTS_URL` (no trailing slash) — see `web/.env.local.example`.
- Render: static site from this repo, root dir `web`, build `npm install && npm run build`, publish path `out`, env vars `NODE_VERSION=20` and `EV_ACCOUNTS_URL` (see `render.yaml`).
