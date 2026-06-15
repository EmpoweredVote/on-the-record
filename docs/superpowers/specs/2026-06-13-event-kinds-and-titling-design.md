# Event Kinds & Flexible Titling — Design

Approved in brainstorming 2026-06-13. A thin **precursor** to the Topics & Provenance phase (`2026-06-13-topics-and-provenance-design.md`), built first so the topic/outline work is event-kind-aware from the start. Pulls the data-model + display slice of the roadmap's Phase 7 forward; the deep per-kind pipeline/summarizer adaptation stays deferred.

## Why

The system was built for city-council meetings and hardcodes it: the site renders every meeting as `{city} {meeting_type} — {date}`, `meetings.meetings.city` is `NOT NULL`, and there's no notion of event format. We're broadening to debates, candidate forums, community meetings, and news clips — which need a real human title, may have no single city, and shouldn't be forced into a council-shaped presentation.

## Scope

Add two fields and make naming flexible across pipeline, DB, API, and site. Make the event kind available to drive presentation (consumed by the Topics phase's outline). **Not** in scope: bespoke per-kind page designs, per-kind summarization prompts, kind-filter UI — all deferred to Phase 7. A debate published after this lands will be *named and typed* correctly; the *quality* of its auto-outline (the summarizer is still council-shaped) is a separate later concern.

## Data model

`meetings.meetings` (ev-accounts migration):

- `ADD COLUMN title TEXT` — optional human display name (e.g. "Mayoral Debate: Smith vs. Jones", "News: Council passes housing ordinance"). Null ⇒ compose from fields.
- `ADD COLUMN event_kind TEXT NOT NULL DEFAULT 'council'` — controlled set (below). Default backfills existing rows as `council`.
- `ALTER COLUMN city DROP NOT NULL` — non-municipal events (statewide debate, news clip) may have no city.

**event_kind controlled set** (validated in the pipeline + ev-accounts write routes, not a DB CHECK — matches how `meeting_type`/`status` are handled and lets the set grow without a migration):

`council`, `school_board`, `debate`, `forum`, `community_meeting`, `news_clip`, `other`

**Deliberative grouping** (drives outline behavior, defined once in code, not per-kind branches): `council` and `school_board` are *deliberative* (agenda/votes/roll-call structure); the rest are *non-deliberative*. The Topics phase keys its outline on this grouping, not on individual kinds, so adding a kind later doesn't touch the outline logic.

## Display

A single helper resolves the display name everywhere:

```
meetingTitle(m) = m.title?.trim() || [m.city, m.meeting_type].filter(Boolean).join(" ")
```

- `title` wins when present; otherwise compose from `city` + `meeting_type`, tolerating a null city (so "Mayoral Debate" alone works if `meeting_type` carries it).
- web: a `meetingTitle()` helper in `web/lib/` (sits next to `formatTime`); used by the index cards and the meeting-page header, replacing the two hardcoded `{city} {meeting_type}` renders.
- A subtle event-kind label (e.g. a small "Debate" / "Forum" tag) renders near the title where it adds clarity; full per-kind styling is deferred.

## Pipeline (`src/`)

- `Meeting` model: add `title: Optional[str] = None` and `event_kind: str = "council"`, with `to_dict`/`from_dict` handling (default `council` on load for older checkpoints).
- The metadata input path that already sets `city`/`meeting_type`/`date` gains optional `title` and `event_kind` (validated against the controlled set; invalid ⇒ error with the allowed list).
- `publish.py` `_upsert_meeting`: write `title` and `event_kind` columns on both INSERT and UPDATE.

## ev-accounts

- Migration as above (additive; safe to run before data exists).
- `meetingsService`: add `title`, `eventKind` to the `Meeting` interface, `MeetingRow`, `mapMeeting`, and `MEETING_COLS`. Returned by `/api/meetings` and `/api/meetings/:id`.
- Write routes (`createMeeting`/`updateMeeting` zod schemas): accept optional `title` and `eventKind`; validate `eventKind` against the shared allowed set; `city` becomes optional. (Pipeline writes directly via psycopg2, but the admin write path must validate consistently.)
- Keep the allowed-kind list in one module so pipeline and API don't drift (document the canonical list; the pipeline has its own copy in Python — keep them in sync, noted in both).

## web

- `Meeting` type + `mapMeeting` in `web/lib`: add `title`, `event_kind`.
- `meetingTitle()` helper; update index (`app/page.tsx`) and meeting page (`app/meetings/[meetingId]/page.tsx`) to use it.
- Render the event-kind label subtly where helpful.

## Error handling

| Case | Behavior |
|---|---|
| `title` null | Compose from city + meeting_type. |
| `city` null and `title` null | Display `meeting_type` alone (operator should set a `title` for cityless events; documented). |
| Unknown `event_kind` on write | 422 / pipeline error listing allowed values. |
| Existing rows pre-migration | Backfilled `event_kind='council'`, `title=null` → render unchanged. |

## Testing

- **Pipeline**: model round-trip (`to_dict`/`from_dict` defaults), event_kind validation, publish writes the columns.
- **ev-accounts**: migration applies; `/api/meetings/:id` returns `title`/`eventKind`; write-route validation (valid kind, invalid kind → 422, null city allowed).
- **web**: `meetingTitle()` unit cases (title set; null title; null city); build renders titles correctly.

## Deferred (Phase 7)

Per-kind page treatments and layouts; per-kind summarization (debate Q&A structure, news-clip single-speaker handling); kind-filter UI on the index; non-roster participants (candidates/moderators) as people rows without `politician_slug`.
