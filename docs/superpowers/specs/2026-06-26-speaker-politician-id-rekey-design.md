# Re-key Speaker → Politician Connection on `politician_id` — Design

**Date:** 2026-06-26
**Status:** Approved for planning
**Repos:** on-the-record (`web/` + pipeline) and ev-accounts (`backend/`)
**Evolves:** `docs/superpowers/specs/2026-06-12-speaker-politician-linking-design.md`
(pipeline linking) and `docs/superpowers/plans/2026-06-12-phase-2-people.md`
(the slug-keyed people layer this re-keys).

## Problem

On the on-the-record meeting site, speakers don't connect to politician
profiles — e.g. Steve Hilton renders as plain unlinked text. The connection is
keyed on `politician_slug` end-to-end (pipeline → `meetings.speakers`/`segments`
→ ev-accounts people API joins `essentials.politicians ON p.slug =
sp.politician_slug` → web `/people/<slug>`).

Verified against prod: **82,426 of 82,953 `essentials.politicians` rows (99.4%)
have a NULL slug** — including essentially all candidates. Every politician has a
stable `politician_id` (UUID). So the slug key is dead-by-data: a candidate like
Steve Hilton has an id but no slug, and the slug-keyed connection silently drops
him. The original linking design already noted that "automation comes from the
embeddings ↔ `politician_id` link, not the UI." `politician_id` is the correct
key; this design stops the connection from depending on slugs at all.

## Why this is mostly a key-swap (not new infrastructure)

`politician_id` already rides almost the entire pipeline:

- `SpeakerMapping` carries `politician_slug` **and** `politician_id`
  (`src/models.py`); both round-trip through `transcript_named.json`.
- `StoredProfile` (voice profile) carries both (`src/enroll.py`).
- Wire-1 voice match copies both onto the mapping
  (`identify.match_voice_profiles`); `_carry_link` preserves both on override.
- `roster.correct_mappings()` sets both from the council roster member.
- `essentials_client.search_politicians()` returns `politician_id` for every
  result, **even when slug is NULL** — so the manual-link flow can already
  attach an id.
- `publish.py` already writes **both** `politician_slug` and `politician_id` to
  `meetings.speakers`.
- ev-accounts `meetingsService.ts` already SELECTs and returns
  `politicianId` on the meeting's speakers array.

Two layers are still hard-keyed on slug, and they are the entire bug:

1. **The public people layer (ev-accounts API + web).** Roster/profile/
   appearances/search all key on `politician_slug`; `/people/<slug>` routes and
   transcript/search links use the slug. A null-slug person is excluded and
   never links.
2. **The pipeline enrollment key (`enroll.py`).** The `essentials:` branch
   builds `essentials:<politician_slug>` only in the slug path; when slug is
   NULL it falls back to a **name-based** key and `resolve_mapping_enrollment`
   returns `(name_key, None, None)` — **dropping `politician_id` even when the
   mapping carries it**. So a manually-linked candidate's id never anchors to
   their voice profile, and Wire-1 carries nothing forward. The voice-
   propagation engine — the whole point of automation — can't reach candidates.

## Key simplifier: the public layer has no live prod data

Existing published speakers have **neither** `politician_slug` nor
`politician_id` populated (nobody has been linked), so the slug-keyed `/people`
roster is empty in prod. **There are no live `/people/<slug>` URLs to preserve.**
This means the public-layer re-key needs no migration, no redirects, and no
dual-read — we switch an empty pipe to the correct key *before* data flows
through it. The only state needing migration is the pipeline's voice-profile DB
(see Migration), handled by the existing rebuildable-cache mechanism.

## Decisions (resolved in brainstorming)

- **Public URLs become the raw UUID:** `/people/<politician_id>`. Zero new
  infrastructure, always present, no collisions, trivial static generation.
  Pretty (name-derived) slugs are a deferred enhancement, not v1.
- **The enrollment key re-keys too:** `essentials:<politician_id>`. Required for
  voice-propagation to anchor candidates — the automation goal. Local and
  name-fallback keys are unchanged.
- **Per-segment identity is resolved by join, not denormalization (Approach J).**
  `meetings.segments` keeps only `politician_slug` and is left untouched; the two
  read paths that need per-segment identity (appearances, search) join
  `meetings.speakers ON sp.id = s.speaker_id` and use `sp.politician_id`. This
  keeps the pipeline change surgical (enrollment key only), avoids a migration /
  `publish.py` change / backfill, and contains the rest in the ev-accounts query
  layer. The extra join is on tables already FK-related and already joined to
  `meetings`; cost is negligible at current scale. (Denormalizing
  `segments.politician_id` was the rejected alternative — faster reads and
  pattern-consistent, but real cross-repo coordination cost for a theoretical
  win while prod segment identity is empty.)

## Components

### ev-accounts (`backend/`, branch off `master`) — the core re-key

**1. `lib/peopleService.ts`** — flip roster, profile, and appearances to id.

- Roster/profile shared `PERSON_SELECT`:
  - `LEFT JOIN essentials.politicians p ON p.slug = sp.politician_slug`
    → `... ON p.id = sp.politician_id`.
  - Selected key field: `sp.politician_id` (the value that becomes the URL),
    not `sp.politician_slug`.
  - Name: `COALESCE(p.full_name, MAX(sp.display_name), sp.politician_slug)`
    → `COALESCE(p.full_name, MAX(sp.display_name))` (the slug fallback is dead).
  - `GROUP BY sp.politician_slug, p.id, ...` → `GROUP BY p.id, ...`
    (group on the id, no longer the slug).
- `getPeople`: `WHERE sp.politician_slug IS NOT NULL`
  → `WHERE sp.politician_id IS NOT NULL`.
- `getPersonBySlug(slug)` → `getPersonById(id)`: `WHERE sp.politician_id = $1`.
- `getAppearancesBySlug(slug)` → `getAppearancesById(id)`: add
  `JOIN meetings.speakers sp ON sp.id = s.speaker_id`,
  `WHERE sp.politician_id = $1`.
- The people payload has **one identity field**, the UUID: rename `slug` →
  `politicianId` (API) / `politician_id` (web) end-to-end and drop the old
  `slug` field. The people layer no longer has a slug concept. (The existing
  `Person` interface already had a separate `politicianId`/`politician_id`
  field for the outbound essentials.city link; these collapse into the one id.)

**2. `routes/people.ts`** — param `:slug` → `:id`.

- Replace `SLUG_REGEX` validation with a UUID-format check; reject non-UUID
  with 422 before any DB call.
- `/:id/appearances` stays defined **before** `/:id`.
- Response of `/:id/appearances` keys the wrapper on `id` (was `slug`).

**3. `lib/searchService.ts`** — re-key per-hit identity and the person filter.

- Both the `hits` CTE and the count query gain
  `JOIN meetings.speakers sp ON sp.id = s.speaker_id`.
- Select `sp.politician_id` (return it per result alongside / instead of slug).
- `?speaker=`/`?person=` filter: `s.politician_slug = $X`
  → `sp.politician_id = $X`.
- `SearchResult.politicianSlug` → `politicianId`.

**4. `lib/meetingsService.ts`** — **no change.** The speakers array already
SELECTs and returns `politicianId`; the web derives transcript links from it.

### on-the-record `web/` (branch off `main`)

**5. Route folder `app/people/[slug]/` → `app/people/[id]/`.**

- `generateStaticParams` returns `{ id: p.politician_id }` for each roster
  person (keep the existing empty-roster sentinel that emits one 404 slug so
  `output: "export"` builds with zero data).
- `fetchPerson(id)` / `fetchAppearances(id)` take the UUID.
- The outbound `essentials.city` link already uses `politician_id`; unchanged.

**6. `app/meetings/[meetingId]/MeetingView.tsx`** — link by id from the speaker
list, not the segment slug.

- Build a `label → politicianId` map from `meeting.speakers` (already carries
  `politician_id` via the meeting endpoint).
- The speaker-name link: `seg.politician_slug ? /people/<slug>` →
  `politicianIdByLabel.get(seg.speaker_label) ? /people/<id>`.
- The existing `localNameByLabel` plain-text branch (non-roster local people)
  is preserved.

**7. `app/search/SearchView.tsx`** — link each hit to `/people/<politician_id>`
(was `/people/<politician_slug>`).

**8. `lib/types.ts` + `lib/queries.ts`.**

- `Person`/`PersonDetail`: identity field `slug` → `politician_id` (UUID);
  drop the separate `politician_id` duplication — there is one id field now.
- `Segment`/`SearchResult`: add `politician_id`; mappers read `politicianId`.
  (`politician_slug` may remain in the payload harmlessly but is no longer used
  for linking; remove from the web types if it has no remaining consumer.)
- `fetchPerson`/`fetchAppearances` hit `/api/people/<id>` /
  `/api/people/<id>/appearances`.

### on-the-record pipeline (`main`) — the enrollment re-key

**9. `src/enroll.py`** — anchor the profile on `politician_id`.

- `resolve_mapping_enrollment`: when `mapping.politician_id` is set, return
  `(f"essentials:{politician_id}", mapping.politician_slug, mapping.politician_id)`
  — keyed on id, regardless of whether slug is present. (Previously the
  `essentials:` branch required `mapping.politician_slug`.)
- `resolve_enrollment_key` (roster path): when a roster member has a
  `politician_id`, key `essentials:<politician_id>` and return its id; a member
  with id-but-no-slug now anchors correctly.
- `local:<local_slug>` and the `_name_to_slug` fallback are **unchanged**.
- `StoredProfile` already stores both id and slug; no field change.

**10. Migration — one `reenroll_profiles.py` run.** It rebuilds the profile DB
from each meeting's `transcript_named.json` by reconstructing `SpeakerMapping`s
(which round-trip `politician_id`) and calling the shared enroll path. Under the
new id-keyed `resolve_mapping_enrollment`, the rebuild re-keys every profile to
`essentials:<politician_id>` and collapses any duplicate slug/name profiles for
the same id. This is the established rebuildable-cache mechanism from the
2026-06-12 design — no per-profile manual migration needed.

## Data flow (after the change)

```
Review: name speaker ──▶ link (search_politicians → pick) sets
                          SpeakerMapping.politician_id (slug may be NULL)
                              │
            ┌─────────────────┼───────────────────────────┐
            ▼                 ▼                            ▼
 transcript_named.json   enroll: profile keyed     publish: meetings.speakers
 (round-trips id)        essentials:<politician_id>  .politician_id (already wired)
                              │
                              ▼
   Next meeting: Wire-1 voice match copies profile.politician_id ──▶ pre-linked
                              │
                              ▼
 ev-accounts people API: roster/profile/appearances JOIN essentials ON p.id =
   sp.politician_id  ──▶  web /people/<politician_id>  +  transcript/search links
```

## Out of scope (deferred, not missed)

- **`local_people` linking** still uses `politician_slug`
  (`meetings.local_people.politician_slug`, nullable). Non-roster local people
  are a separate flow with no `politician_id` column; re-keying it is a separate
  fix. Local people continue to render as plain text (unchanged).
- **`essentialsBodiesService.ts`** exposes essentials' *own* `p.slug` for body
  rosters — an unrelated feature, untouched.
- **`meetings.segments` schema and `publish.py`** are untouched — a direct
  consequence of choosing Approach J.
- **Pretty / name-derived URLs** — UUID is v1; a deterministic `name-shortid`
  slug is a possible later enhancement, needing a slug build/parse helper.
- **Back-link pass over already-published meetings** — not needed: prod public
  data is empty; new/re-published meetings carry ids forward.

## Testing

- **peopleService**: roster includes a person with `politician_id` but NULL slug
  (the candidate case); excludes a speaker with NULL `politician_id`; name falls
  back to `display_name` when `p.full_name` is NULL; appearances filter by id via
  the speakers join. (DB-backed service exercised via route tests + manual smoke,
  matching the existing pattern.)
- **routes/people**: 422 on a non-UUID `:id` (service not called); 404 on
  unknown id; 200 with detail; `/:id/appearances` ordered before `/:id`.
- **searchService**: per-hit `politicianId` populated via the join; `?person=`
  filter restricts by id.
- **enroll (Wire 2 re-key)**: a mapping with `politician_id` and **NULL slug**
  keys `essentials:<politician_id>` and carries the id; a pre-existing
  name-keyed profile for the same id merges in (embeddings/meetings/counts
  preserved); `local:`/name fallbacks unchanged.
- **web**: build emits one `/people/<uuid>` route per roster person; a
  transcript identified speaker links to `/people/<id>`; a search hit links to
  `/people/<id>`; empty-roster build still succeeds via the sentinel.

## Deployment order (after both repos merge)

1. Deploy ev-accounts backend first (new id-keyed endpoints + UUID route
   validation must be live before the site builds).
2. Pipeline: deploy the `enroll.py` re-key and run `reenroll_profiles.py` once
   to re-key the profile DB; new review links now anchor on id.
3. Trigger a Render rebuild of the static site; `generateStaticParams` reads the
   id-keyed roster and emits `/people/<uuid>` routes.
4. Smoke-check: a known candidate (e.g. Steve Hilton, id-but-no-slug) appears in
   `/people`, his transcript name links through, and his profile renders
   appearances.

## Cross-repo PR plan

- **ev-accounts** — one PR off `master`: peopleService + routes + searchService.
- **on-the-record** — off `main`: web re-key (routes, MeetingView, SearchView,
  types/queries) and the `enroll.py` re-key. May be one PR or split web/pipeline;
  the pipeline change is independent of the web change. Default: one PR unless
  the pipeline change wants separate review.
