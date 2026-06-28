# Always-current web app via runtime (client-side) data fetching

**Date:** 2026-06-28
**Status:** Approved (design)
**Repos affected:** `on-the-record/web` (primary), `on-the-record` pipeline (`src/publish.py`, one small change)

## Problem

The web app is a Next.js **static export** (`output: "export"`), hosted on Render as a free **Static Site**. Every data-driven page fetches from the ev-accounts API **at build time** and freezes the result into static HTML:

- List pages: `/` (meetings), `/search`, `/people`, `/topics`
- Detail pages: `/meetings/[meetingId]`, `/people/[id]`, `/topics/[key]` (each pre-built per known id via `generateStaticParams`, with `dynamicParams = false`)

So **data that changes independently of code — i.e., new meetings — only appears after a full site rebuild.** That rebuild is triggered by a Render deploy hook fired from `publish.py`. When Render dedups/races rapid hook calls (as happened with the Pod Save America meeting), the static list goes stale: the meeting's detail page worked, but the homepage list omitted it until a manual rebuild.

## Goal

Make the **entire site always reflect current data with no rebuild required** ("B" from brainstorming), at **zero added hosting cost**, accepting weaker SEO for now.

Non-goals (explicitly parked):
- **SEO / server-side rendering.** Runtime client fetching means content is not in the initial HTML. Accepted for now; SSR (a Render Web Service, ~$7/mo) remains a clean future upgrade if discoverability becomes a priority.
- Converting the API or anything in ev-accounts. CORS is already enabled for `https://ontherecord.empowered.vote` (verified), so browser fetches work today with no API change.

## Approach (chosen: "Full client-side fetch + SPA-fallback rewrites")

Keep `output: "export"` (stays a free Static Site). Move all data fetching from build time into the **browser at runtime**. Reuse the existing presentational components and the `queries.ts` mapping functions unchanged — only the data source and timing change.

Rejected alternatives:
- **Hybrid (keep SSG + client-refresh):** retains SEO/instant content but is two rendering paths per route and still leans on periodic rebuilds. More complexity for SEO we've deprioritized.
- **SSR/ISR (Render Web Service):** the "right" SEO answer but ~$7/mo + a running service. Parked as the future upgrade.

## Architecture

### Data layer (`web/lib/queries.ts`)
- Change `BASE` from `process.env.EV_ACCOUNTS_URL` (server-only) to `process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL` (already defined; inlined into the browser bundle so the same functions run client-side).
- Fetch with `cache: "no-store"` so the browser always gets current data. Remove the `BUST` build-id header (a server-build concept, irrelevant client-side).
- The 9 existing fetch functions (`fetchMeetings`, `fetchMeeting`, `fetchSegments`, `fetchSummary`, `fetchPeople`, `fetchPerson`, `fetchAppearances`, `fetchTopics`, `fetchTopic`) and their `mapX` mappers are reused as-is.

### Shared data hook (`web/lib/useApi.ts`, new)
A small, isolated, unit-testable hook:
```
useApi(fetchFn, deps?) -> { data, loading, error }
```
- Runs `fetchFn` on mount (and when `deps` change), tracks loading/error, ignores results from stale calls (cancellation guard via an `ignore` flag in the effect cleanup).
- One clear responsibility: turn an async fetch into render-ready `{data, loading, error}`. Every page uses it so loading/error handling is uniform and not duplicated.

### Shared UI states (`web/components/`, new small components)
- `<Loading />` — spinner/skeleton shown while `loading`.
- `<ErrorState message? />` — tidy "couldn't load" with a retry affordance.
- `<EmptyState message />` — "no meetings yet" / "no results".
- `<NotFound />` — for detail pages whose id returns 404 from the API.

These are presentational; pages compose them with `useApi`.

### Page conversions (all become `"use client"`)
| Route | Fetch | States to handle |
|---|---|---|
| `/` | `fetchMeetings()` | loading / list / empty / error |
| `/search` | already client (`SearchView`) — ensure no build-time fetch remains | (existing) |
| `/people` | `fetchPeople()` | loading / list / empty / error |
| `/topics` | `fetchTopics()` | loading / list / empty / error |
| `/meetings/[meetingId]` | id from `useParams()` → `fetchMeeting` + `fetchSegments` + `fetchSummary` | loading / detail / **not-found** / error |
| `/people/[id]` | id from `useParams()` → `fetchPerson` + `fetchAppearances` | loading / detail / **not-found** / error |
| `/topics/[key]` | key from `useParams()` → `fetchTopic` | loading / detail / **not-found** / error |

Existing presentational components (`MeetingCard`, `MeetingView`, `SearchView`, etc.) are reused; only their data now arrives via `useApi` instead of server props.

### Serving arbitrary detail ids on a static host (the load-bearing piece)
A static host serves pre-made files by path; a brand-new id has no file. To make **any** id resolve without a rebuild:

1. Each dynamic route keeps a minimal `generateStaticParams` returning a single placeholder so the `output: "export"` build still emits one shell file per dynamic route. (Static export errors if a dynamic route has zero params.)
2. The detail page is a **client component** that reads the real id from the URL via `useParams()` (Next's client router parses `window.location` against the `[meetingId]` route pattern at runtime) and fetches by it.
3. Add **Render rewrite rules** so any unmatched path under each dynamic route serves that route's shell file:
   - `/meetings/*` → the `[meetingId]` shell
   - `/people/*` → the `[id]` shell
   - `/topics/*` → the `[key]` shell

   The served shell is identical regardless of id; the client reads the actual id from the address bar and fetches live. (Enable `trailingSlash: true` if needed to make built shell paths predictable for the rewrite destinations — to be confirmed during implementation.)

**Risk / verification:** this rewrite + client-`useParams` behavior is the one piece that cannot be unit-tested and depends on Render's static-rewrite semantics. The implementation plan MUST verify it end-to-end on a real Render preview deploy (publish a meeting, then load its detail URL and a list, with no rebuild) before this is considered done.

### Remove the fragile rebuild dependency (`on-the-record/src/publish.py`)
Because the site now reads data live, publishing a meeting no longer needs to trigger a website rebuild. Remove the automatic deploy-hook firing from the publish path (`_trigger_deploy_hook()` call after a successful publish). This eliminates the dedup/race that caused the original stale-list bug. `RENDER_DEPLOY_HOOK_URL` may remain available for manual/code deploys, but it is no longer fired per publish. Code changes still deploy via the normal Render auto-deploy on push.

> Note: this is a change in the **pipeline repo**, not `web`. It should ship together with (or just after) the web change so that between the two, data freshness is never worse than today.

## Data flow (after)
```
Browser opens /meetings/<id>
  -> Render serves the [meetingId] shell (via rewrite)
  -> client reads <id> from useParams()
  -> useApi(fetchMeeting/​fetchSegments/​fetchSummary) -> NEXT_PUBLIC_EV_ACCOUNTS_URL (CORS OK)
  -> Loading -> MeetingView (or NotFound / ErrorState)
Publishing a meeting writes to the DB only; the API serves it immediately; no rebuild.
```

## Testing
- **Unit:** `useApi` hook — loading→data, loading→error, deps change re-fetches, stale-call cancellation. (`web/lib/useApi.test.ts`)
- **Component:** a representative list page (`/`) and a detail page (`/meetings/[meetingId]`) with a mocked `fetch`/queries module — assert loading, success, empty, not-found, and error renders. Reuse the existing web test setup (e.g. `web/lib/thumbnail.test.ts` patterns).
- **Manual / preview (required):** on a Render preview deploy — (a) load the homepage and confirm it lists a just-published meeting with no rebuild; (b) load that meeting's detail URL directly and confirm the catch-all shell + client fetch render it; (c) load a non-existent id and confirm the NotFound state; (d) confirm `/people/[id]` and `/topics/[key]` behave the same.

## Out of scope / future
- SSR/ISR upgrade for SEO (Model 3).
- Client-side caching/prefetch libraries (SWR/react-query) — start with plain `useApi` + `cache: "no-store"`; revisit only if request volume or UX warrants.
