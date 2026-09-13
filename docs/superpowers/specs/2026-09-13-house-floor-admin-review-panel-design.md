# House-Floor Admin Review Panel (Project 2) — Design

Date: 2026-09-13
Status: approved (brainstorming), ready for planning
Repos: `on-the-record` (`web/`) + `ev-accounts` (`backend/`)
Related:
- Project 1 spec: `docs/superpowers/specs/2026-09-11-house-floor-weekly-automation-design.md`
- ev-cto decision 0017 (host/security context)
- Memory notes: `house-floor-weekly-automation`, `web-publishing-architecture`,
  `review-ui-future-direction`, `ev-accounts-pending-contracts`,
  `meeting-confidence-gate-status`

## Problem

Project 1 ships House-floor sessions to the production database as
`meetings.meetings.status = 'draft'` (not live), for human review before they go
live. There is no good way to review those drafts. The local processing GUI
(`gui/`) builds its library by scanning local meeting folders on the operator's
Mac; the cloud automation writes drafts straight to the DB and never creates
local folders, so the GUI cannot see them. The only current tool is a CLI
stopgap (`run_local.py --list-drafts` / `--promote <slug>`): inspect-and-promote
only, with no audio and no per-segment view.

This project builds the review/approve surface: a web admin panel that lets the
operator, off their Mac, list draft floor meetings, open one and read its
transcript with CREC attributions while streaming the source audio, and then
promote it live or archive it.

Out of scope: the pipeline and the confidence gate (both done). This is purely
the review/approve surface.

## Key facts this design rests on (verified 2026-09-13)

**web/ (on-the-record)** is a static-export Next.js app (`output: "export"`)
with no server and no auth of any kind. Every detail route is a client shell
that fetches the ev-accounts API at runtime.
- `web/lib/queries.ts` fetchers run client-side against
  `NEXT_PUBLIC_EV_ACCOUNTS_URL`, `cache: "no-store"`. `fetchMeetings` filters to
  `status === "published"`; `fetchMeeting` returns `null` for non-published.
  `fetchSegments` / `fetchSummary` / `fetchVotes` do **not** filter by status.
- `web/app/meetings/[meetingId]/` renders the meeting via `MeetingView.tsx`
  (transcript + speakers + `ProvenanceBadge` + playback). Playback is chosen by
  `meeting.playback_kind`; `hls` uses `players/FilePlayer.tsx` (dynamic
  `hls.js`), driven through a `PlayerAdapter` (`seekTo` / `getCurrentTime` /
  `isPlaying`). Click-to-seek and `?t=` / `#seg-N` deep links already work.
- Dynamic routes use the sentinel pattern: `generateStaticParams()` returns one
  `{ param: "view" }`, and `render.yaml` rewrites `/<route>/* ->
  /<route>/view/index.html`. The client reads the real id from the URL via
  `usePathParam`.
- Styling is plain global CSS with design tokens (no Tailwind, no CSS Modules).
  Data fetching is a hand-rolled `useApi` hook (no SWR/react-query). No auth
  library is present.

**ev-accounts (`backend/`, Express 5 + TypeScript)** already anticipates this
panel.
- Auth: `requireAuth` (JWT via `jose`, Supabase or WorkOS issuer, `Authorization:
  Bearer`) then `requireAdmin` (looks `req.userId` up in `public.admin_users`;
  403 otherwise). There is an existing `/api/admin/*` namespace whose routers use
  `router.use(requireAuth, requireAdmin)` and log every mutation via
  `logAdminAction()`.
- `POST /api/auth/login` returns `{ access_token, refresh_token, expires_in,
  token_type: "bearer", user }` and sets the `ev_session` cookie. The
  `access_token` is a usable Bearer for `requireAuth`.
- Meeting visibility: `backend/src/lib/meetingVisibility.ts` defines
  `MeetingViewerOptions { includeAllStatuses?: boolean }` (documented as reserved
  for the Project-2 admin panel) and `PUBLIC_MEETING_STATUSES = ['published',
  'scheduled']`. `backend/src/lib/meetingsService.ts` functions
  (`getMeetings`, `getMeetingById`, `getTranscriptByMeetingId`,
  `getSummaryByMeetingId`, `getVotesByMeetingId`) each accept `opts` and skip the
  public-status clause when `includeAllStatuses` is set. The route layer does not
  wire this yet.
- Mutation: `PATCH /api/meetings/:id` (gated `requireAuth, requireAdmin`) accepts
  `status` and runs `UPDATE meetings.meetings SET status = $N, updated_at = NOW()`
  via `updateMeeting`. `DELETE /api/meetings/:id` also exists (cascades).
- DB access is direct `pg` (`pool.query`); the `meetings` schema is not
  PostgREST-exposed. Deploys auto on `master` merge (Render). Migrations are
  hand-applied, numbered `NNNN_` or per-author `CA_NNNN_`.
- person/topic/search services hardcode the public-status clause with **no**
  `includeAllStatuses` bypass. The panel does not use them, so they stay as-is.

**Promote goes live with no rebuild.** The site reads live from the API; the
deploy hook is dead. Promote is a pure DB status flip. `run_local.py --promote`
does the same flip locally, but it is a Mac CLI and cannot back a browser panel.

## Decisions (resolved in brainstorming)

1. **Auth model — EV user login + admin gate.** The panel authenticates the
   operator with EV email/password (`POST /api/auth/login`), stores the returned
   Bearer token client-side, and sends it on admin calls. The API enforces
   access with `requireAuth + requireAdmin`. Rationale: reuses the existing
   security boundary for every other admin action, gives per-user attribution and
   a free audit trail (`logAdminAction`), is cleanly revocable, and adds no new
   secret. WorkOS can be added later if EV standardizes on it.
2. **Relabel scope — none in v1.** CREC is authoritative for floor sessions.
   Attributions are shown read-only (name, linked/unlinked, confidence, method).
   A draft with a real attribution problem is held and fixed on the Mac pipeline.
3. **Reject semantics — archive.** "Reject" sets `status = 'archived'`: never
   public (only `published`/`scheduled` are), drops out of the review queue,
   keeps the full record + processing metadata, and is reversible (flip back to
   `draft`). "Hold" = take no action; the draft stays a draft. `archived` is a
   new text value; no schema migration (pending a `CHECK`-constraint check).
4. **Mutation home — ev-accounts API.** Forced by the auth choice and the off-Mac
   goal. Reuse the existing admin-gated `PATCH /api/meetings/:id` for the status
   flip. `run_local --promote` is not used.
5. **UI home — web/ (`/admin` routes).** Reuse the existing meeting viewer rather
   than rebuild it in the ev-accounts frontend.

## Architecture

Two repos, two PRs, no shared state beyond the DB and the HTTP contract.

```
  weekly cron (Project 1)                operator's browser (off-Mac)
        |                                        |
        v                                        v
  meetings.meetings                     web/ /admin/* (static bundle)
   status='draft'  <----- reads -----   admin fetchers (Bearer token)
        ^                                        |
        |                                        v
        +------ PATCH status ------  ev-accounts /api/admin/meetings
                                     (requireAuth + requireAdmin)
                                     + PATCH /api/meetings/:id
```

### Component A — ev-accounts admin API

A new router mounted at `/api/admin/meetings`, `router.use(requireAuth,
requireAdmin)`, following `backend/src/routes/admin.ts` as the template
(mutations logged via `logAdminAction`). Endpoints:

- `GET /api/admin/meetings?status=draft` — the queue. Returns each meeting with
  its counts and stored `gate_verdict` + `gate_coverage` (from
  `processing_metadata`). Calls `getMeetings` with `{ includeAllStatuses: true }`.
  Must honor a `status` filter of `draft` (and `archived`) under the bypass — the
  planner verifies how `getMeetings` intersects the `?status` param with the
  public allowlist and, if needed, filters in the admin route so `draft` is not
  dropped.
- `GET /api/admin/meetings/:id` — meeting detail, `{ includeAllStatuses: true }`.
- `GET /api/admin/meetings/:id/transcript?page=` — transcript (paginated),
  `{ includeAllStatuses: true }`.
- `GET /api/admin/meetings/:id/summary` — summary, `{ includeAllStatuses: true }`.
- `GET /api/admin/meetings/:id/votes` — votes, `{ includeAllStatuses: true }`.

**Mutation:** reuse the existing admin-gated `PATCH /api/meetings/:id { status }`.
Promote → `published`; Archive → `archived`. Constrain the accepted status set for
this flow and confirm no DB `CHECK` constraint blocks `archived`.

The read endpoints are the only genuinely new server code; the services already
support the bypass, and the mutation already exists.

### Component B — web/ admin UI

Three routes, using the static-export sentinel pattern + `render.yaml` rewrites,
exactly as the public routes do:

- `/admin/floor` — the **draft queue**. A table with: date, title, duration,
  gate verdict + effective coverage, and speakers / named / linked counts. Per
  row: Review · Promote · Archive.
- `/admin/meetings/[id]` — the **review page**. Reuses `MeetingView` (transcript +
  speakers + `ProvenanceBadge` + HLS `FilePlayer`); audio streams from the House
  Clerk CDN via the meeting's `playback_url`; click-to-seek works. Attributions
  are read-only. Adds an action bar (Promote / Archive), each behind a confirm.
- `/admin` — the login gate; renders the email/password form when there is no
  valid token, otherwise routes to `/admin/floor`.

Supporting pieces:

- **Auth module** (`web/lib/adminAuth.ts`, new): email/password form posts to
  `/api/auth/login`, stores `access_token` in `sessionStorage` + memory, exposes
  the current token and a `logout`. On any admin call returning 401, clear the
  token and show the login form. No silent refresh in v1 (weekly cadence; worst
  case is a re-login).
- **Admin fetchers** (`web/lib/adminQueries.ts`, new): mirror the reads the
  review page needs, hitting `/api/admin/*` with the `Authorization: Bearer`
  header. These are **isolated from `web/lib/queries.ts`** so the public app stays
  unauthenticated and published-only.
- **`MeetingView` refactor** (small): accept its data (meeting, segments,
  summary, votes) and an optional action bar as props, so both the existing
  public client (`MeetingDetailClient`) and a new admin client feed the same
  component. No fork of the viewer.
- `render.yaml`: add rewrites `/admin/floor -> /admin/floor/index.html` (static)
  and `/admin/meetings/* -> /admin/meetings/view/index.html` (sentinel).

## Data flow

1. Weekly cron (Project 1) writes House-floor sessions as `status='draft'`.
2. Operator opens `/admin/floor` and logs in (email/password → Bearer token).
3. The queue lists drafts via `GET /api/admin/meetings?status=draft`, showing
   gate verdict, effective coverage, and named/linked counts.
4. Operator opens one → the review page streams the CDN audio and shows each
   segment's CREC attribution (read-only).
5. Operator clicks **Promote** → `PATCH status=published`. It is live at once
   (people/search read live from the API; no rebuild).
6. Or **Archive** → `PATCH status=archived`. It drops from the queue and stays
   hidden. Reversible by setting `draft` again.
7. Or **Hold** → no action; the draft reappears next time.

## Safety — draft-leak prevention

- Admin reads are gated server-side (`requireAuth + requireAdmin`). Without a
  valid admin token the endpoints return 401. The static `/admin` bundle carries
  no secret and no draft data.
- Public endpoints are unchanged (published/scheduled only). `draft` and
  `archived` are never public.
- person/topic/search keep their hardcoded public-status clause. The review page
  links to normal person pages; a not-yet-live draft's appearances do not surface
  there — correct behavior, no leak.
- The new admin fetchers are isolated from the public `queries.ts`.

## Error handling

- 401 → show the login form (token expired or missing).
- 403 → "this account is not an admin" message (authenticated but not in
  `admin_users`).
- 500 / network → inline error with retry, reusing the `useApi` pattern.
- Promote / Archive → confirm dialog; disable the control during the call; on
  success update the row / badge; on failure show the error and keep state.
- Pre-fix drafts show stale `gate_coverage = 0%` — display as-is with a small
  note. Cosmetic; a re-run would recompute but is not worth the Modal cost.

## Testing

- **ev-accounts** (Vitest + Supertest, colocated `*.test.ts`): admin router
  returns 401 without a token, 403 for a non-admin, 200 with drafts for an admin;
  the reads pass `includeAllStatuses`; `PATCH` flips status and logs the action;
  an archived meeting is absent from the public endpoints.
- **web/** (Vitest): admin fetchers attach the Bearer header and clear the token
  on 401; the queue renders rows and fires the right mutation; the `MeetingView`
  refactor is covered by the existing viewer tests.
- Any on-the-record Python tests run under `.venv/bin/python` (none expected here;
  this work is TypeScript/web).

## Go-live steps (no schema migration)

1. Insert the operator's EV user id into `public.admin_users` (one row).
2. Confirm no DB `CHECK` constraint on `meetings.meetings.status` blocks
   `archived`.
3. Deploy ev-accounts (auto on `master` merge) **before** web points at the new
   routes.
4. Confirm the web site origin is in the ev-accounts `CORS_ORIGIN` allowlist.
5. Rebuild the web static site (merge to `main`, trigger the Render build).

## Out of scope (v1)

Speaker relabel and per-segment editing; draft visibility in person/topic/search;
silent token refresh; bulk/multi-select promote; notifications. Each can be added
later without reworking this design.
