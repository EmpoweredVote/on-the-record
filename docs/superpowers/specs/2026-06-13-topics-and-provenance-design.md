# Topics & Provenance (Phase 6, reordered ahead of 4–5) — Design

Approved in brainstorming 2026-06-13. Supersedes the roadmap's original Phase 6 ("Topics & issues") with a richer model, and absorbs the unbuilt **web display** half of Phase 4 (summaries) because the chosen presentation requires it.

**Depends on** the Event Kinds & Flexible Titling precursor (`2026-06-13-event-kinds-and-titling-design.md`), built first — the outline below keys off the meeting's `event_kind` / deliberative grouping and the `meetingTitle()` helper introduced there.

## What we're building

1. **Meeting outline ("items").** A meeting renders as the list of substantive things it deliberated — each item is a summarizer **section** (discussion / public comment / consent agenda / vote) with its title/bill name, time range, outcome, jump link, and **topic label(s)**. Topics are labels *on* items, not free-floating chips. (Chosen over chips in the visual brainstorm.) **The outline adapts by event kind** (from the precursor's deliberative grouping): *deliberative* kinds (`council`, `school_board`) get the full bill/vote/section outline described here; *non-deliberative* kinds (`debate`, `forum`, `community_meeting`) get the same section list minus the vote/roll-call affordances (just titled, topic-tagged, time-ranged sections); `news_clip` gets no outline (topics may still be tagged and the exec summary still shown). The branch is on deliberative-vs-not, not per-kind.
2. **Topics = Compass issues.** A topic is an `inform.compass_topics` row, referenced by its stable `topic_key` — the same key `essentials.quotes` already uses. Tagging a meeting item with a `topic_key` puts meeting discussion on the same spine as politician quotes and Compass stances. Topic pages aggregate every item with a given `topic_key` across meetings.
3. **Provenance badge.** A CalMatters-style "AI-predicted vs human-verified" indicator on (a) speaker attributions and (b) topic tags. Transparency is a core value. In **this** build everything ships **predicted**; the promotion-to-verified workflow is deferred (see Deferred).
4. **Finish Phase 4's web display.** Render the executive-summary block on meeting pages and a one-line summary preview on index cards. Required anyway by the outline model; the data already exists.

## What already exists (discovered during brainstorming)

- **Summarizer (`src/summarize.py`)** runs in the pipeline (`run_local.py` Stage 5): Haiku section classification → Sonnet section summaries + executive summary + key decisions + vote extraction. Output is a `MeetingSummary` with `sections[]` (each has `section_type`, `title`, `content`, `start_time`/`end_time`, `start_segment`/`end_segment`).
- **`publish.py` persists the summary as a JSONB column** `meetings.meetings.summary` (sections nested inside). It does **not** write any normalized summary tables.
- **`meetings.speakers` already carries provenance**: `id_method` and `confidence`. The review step sets `id_method = "human_review"` on human-confirmed speakers (`src/review.py`); automated paths set `llm` / `roster` / etc. ev-accounts already exposes `idMethod`/`confidence` on speakers via `getMeetingById`.
- **Compass topics** live in `inform.compass_topics` with a unique, stable `topic_key` (e.g. `homelessness`, `data-centers`), `title`, `short_title`, `question_text`, `is_live`, `version`. A full admin **topic-rewrite workflow** (`/api/admin/topic-rewrites`, migration 061) already creates/edits/version-controls topics with human gates — so topic *creation* that propagates across apps is existing infrastructure, not something this phase builds.

## Discovered prerequisite to fix (in scope)

**The summary read path in ev-accounts is broken.** `meetingsService.getSummaryByMeetingId` reads `meetings.meeting_summaries` / `meetings.summary_sections` — tables **no migration ever creates**. The real data is `meetings.meetings.summary` (JSONB). This phase replaces that read path to read the JSONB, so the meeting outline and exec summary can render. The dead `MeetingSummary`/`SummarySection` row-mapping code in `meetingsService.ts` is removed.

## Architecture (cross-repo, same split as Phases 2–3)

### Pipeline (`src/`, this repo) — topic classification stage

- **New stage after summarization.** Input: the meeting's substantive sections (types `discussion`, `public_comment`, `consent_agenda`, `vote`). Procedural sections (`opening`, `roll_call`, `procedural`, `closing`) are skipped.
- **Vocabulary fetched live at publish time.** Query `inform.compass_topics` (via the existing `DATABASE_URL` psycopg2 connection) for `topic_key`, `short_title`, `question_text` `WHERE is_live = true`. This tracks Compass automatically, including rewrites.
- **One Haiku call per meeting.** Prompt carries the candidate topic list (key + short_title + question_text) and each substantive section's title + condensed text; the model returns, per section, 0–N `topic_key`s drawn **only** from the candidate list. Sections it can't confidently place get no topic → surface as **Uncategorized**. Config: `TOPIC_CLASSIFY_MODEL` (Haiku). Cost note: one call per meeting; cheap.
- **Re-publish-safe checkpoint.** Results saved to a `topics.json` checkpoint (like `summary.json`) so re-runs don't re-bill. Carried on the `Meeting`/`MeetingSummary` model as a per-section `topic_keys` list.
- **No verification in this build.** Every tag is emitted with `status = 'predicted'`, `confidence` from the model where available, `model` = the classifier id.

### Database (`meetings.*`) — topic tags as the queryable spine

New table written by `publish.py` (delete-then-insert per meeting, mirroring segment handling):

```
meetings.meeting_topics (
  meeting_id     uuid     not null references meetings.meetings(id),
  section_index  int      not null,   -- position in summary.sections[]
  topic_key      text     not null,   -- references inform.compass_topics.topic_key (live version)
  status         text     not null default 'predicted',  -- 'predicted' | 'verified'
  confidence     real,
  model          text,
  -- denormalized for single-query topic pages (mirrors segments.speaker_name):
  section_title  text,
  section_type   text,
  start_time     real,
  end_time       real,
  created_at     timestamptz default now()
)
```

Indexes: `(topic_key)`, `(meeting_id)`. `meeting_topics` is the **single source of truth** for topic tags — topics are NOT duplicated into the summary JSONB (no drift). ev-accounts attaches them to sections at read time by `section_index`.

`topic_key` is a soft reference (text), not an FK, because `inform.compass_topics` is in another schema/domain and topics get re-versioned; the live topic is resolved by join at read time. Tags whose `topic_key` no longer matches a live topic are simply omitted from topic listings (defensive).

### ev-accounts — read path fix + topic API

- **Fix `getSummaryByMeetingId`** to read `meetings.meetings.summary` JSONB and shape the existing `MeetingSummary`/`SummarySection` response from it. Remove the dead normalized-table queries.
- **Attach topics to sections.** When returning a meeting's summary/sections, LEFT JOIN `meeting_topics` by `(meeting_id, section_index)` so each section carries `topicKeys: [{key, title, status}]` (title resolved from live `inform.compass_topics`).
- **`GET /api/topics`** — every `topic_key` with ≥1 tagged item, joined to live `inform.compass_topics` for `title`/`short_title`, with `itemCount` and `meetingCount`. Sorted by item count desc. Plus an `uncategorizedCount` (substantive sections with zero tags) surfaced separately.
- **`GET /api/topics/[key]`** — topic title + every tagged item across meetings (newest first): `meetingId`, `city`, `meetingType`, `date`, `sectionTitle`, `sectionType`, `startTime`, `status`, and meeting `playbackKind`. 422 on malformed key; 404 if the key matches no live topic AND has no tags.
- Conventions identical to people/search services: `pool.query` only for `meetings.*`; `inform.compass_topics` is PostgREST-exposed (the codebase reads it via `supabaseAnon`) — either client is acceptable, prefer one `pool.query` join for the topic endpoints to keep it single-round-trip. Explicit mappers, `Number()` on numerics, `optionalAuth`, 422-before-DB, 500 `INTERNAL_ERROR`.

### web (`web/`, static export) — outline, topics, badges

- **Types/fetchers**: extend `Meeting`/summary types to carry `sections[]` (with `topic_keys`) and the meeting's `speakers[]` (with `id_method`/`confidence`); add `Topic`, `TopicItem`, and fetchers `fetchTopics`, `fetchTopic(key)`. Build-time fetches (static export), except nothing here is runtime — topic pages are statically generated like people pages.
- **Meeting page**: the existing synced transcript stays as-is; the new content sits **above** it — executive-summary block at top, then (for kinds that get one) the **outline** of substantive sections (title/bill · topic label(s) · time range · outcome · jump link to `?t=…#seg-…`) acting as a clickable table of contents into the transcript below. Outcome/vote affordances show only for deliberative kinds; `news_clip` shows no outline. Topic labels link to `/topics/[key]` and carry the **predicted** badge. Speaker attributions in the transcript carry the provenance badge derived from `id_method` (see below). Meeting/page headings use the precursor's `meetingTitle()` helper, not the old `{city} {meeting_type}` render.
- **Index**: one-line executive-summary preview on each meeting card.
- **`/topics`** index (the vocabulary in use, with counts + an Uncategorized row) and **`/topics/[key]`** (items across meetings), per the approved mockups. `generateStaticParams` over `fetchTopics`; sentinel fallback for the empty-DB build case (same pattern as `/people/[slug]` and `/meetings/[id]`).
- **Provenance badge component** (`web/components` or co-located): two states.
  - *Predicted*: subtle "✦ AI predicted" pill, tooltip "Automated — pending human review."
  - *Verified*: "✓ Verified" pill, tooltip "Confirmed by a human reviewer."
  - **Speaker mapping**: `verified` iff `id_method === 'human_review'`; otherwise `predicted` (including null/unknown). Topic tags: always `predicted` this build (the component already supports `verified` for when the curation phase lands).
  - To avoid noise, the speaker badge renders once per consecutive same-speaker run in the transcript, not on every segment.
- Nav: add a "Topics" link alongside People/Search.
- Styles: flat classes in `globals.css` using existing variables; badge uses `--muted`/`--accent`.

## Error handling & edge cases

| Case | Behavior |
|---|---|
| Meeting has no summary (e.g. `--skip-summary`, no API key) | No outline/topics; meeting page falls back to current flat transcript. No topic tags written. |
| Section matches no live topic | No tag; counts toward Uncategorized. |
| `topic_key` tag whose topic is no longer live | Omitted from topic listings (resolved by join to `is_live`). |
| Empty DB at build | Sentinel static params; pages render empty states (same as Phases 2–3). |
| Classifier returns a key not in the candidate list | Dropped (pipeline validates against the fetched vocabulary before writing). |

## Testing

- **Pipeline**: unit-test the classifier's output validation (drops out-of-vocab keys; substantive-only filtering; empty-summary path). Mock the Anthropic client.
- **ev-accounts**: TDD route tests (mocked services) for `/api/topics`, `/api/topics/[key]` (validation, shape, empty, key passthrough) and the rewritten summary read path (JSONB shaping; topics attached by section_index).
- **web**: `tsc --noEmit`, lint, static build against the local backend; browser verification of the outline, topic labels + badge, a topic page, deep links, and the speaker badge states.

## Deferred (explicitly out of this build)

- **Post-publish curation web app** — the mutable surface to verify/correct AI tags, clear the Uncategorized backlog, and promote predicted→verified after publish. This is the architectural next step (auth, write API, static-vs-dynamic story) and gets its own brainstorm. The `status` field and the badge's `verified` state exist now so this can land without a data migration.
- **Pre-publish topic review** in the CLI (extending `review.py` to confirm topic tags as it does speakers).
- **Official agenda ingestion** (PDF/HTML/Granicus) for authoritative item lists/numbering.
- **LA council-file linking** (`meetings.council_file_details`) on items.
- **Scope-aware vocabulary** (filtering Compass topics by local/state/federal or community).
- **Unified topic page** merging meeting items with essentials quotes and Compass stances on the shared `topic_key`; and linking out to the essentials issue view (mirror of the people→essentials link).
- GIN/perf indexing beyond the basic indexes, if scale grows.

## Open inconsistency resolved here

The summary JSONB-vs-normalized-tables split (above) is fixed in the JSONB direction. No other repo touched beyond ev-accounts + web + pipeline.
