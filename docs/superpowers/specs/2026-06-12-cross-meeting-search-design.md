# Cross-Meeting Search (Phase 3) — Design

Approved in brainstorming 2026-06-12. Implements Phase 3 of `docs/web-roadmap.md`.

## Goal

Full-text search across every published meeting transcript, with optional city and speaker filters, from a `/search` page on the static site. Each result deep-links to the exact moment in the meeting page.

## Architecture

Two repos, same split as Phase 2 (People):

- **ev-accounts** (`~/Documents/GitHub/ev-accounts/backend`) owns the search endpoint: `searchService.ts` (SQL via `pool.query`) + `routes/search.ts` (validation + HTTP), mounted at `/api/search`.
- **web** (`~/Documents/GitHub/on-the-record/web`, Next.js static export) owns the `/search` page. Because the site is statically exported, the search request happens **in the browser at runtime** — unlike every other fetch in the app, which runs at build time.

Chosen approach: flat ranked segment hits from the API, grouped by meeting client-side. Rejected: server-side per-meeting aggregation (complex SQL, no v1 gain) and external search engines (operational overkill; the `tsv` GIN index from migration 364 is sufficient).

## API — `GET /api/search`

### Request

| Param | Required | Validation | Meaning |
|---|---|---|---|
| `q` | yes | non-empty after trim, ≤ 200 chars | search query, `websearch_to_tsquery` syntax (plain words, quoted phrases, `-exclusion`, `OR`) |
| `city` | no | non-empty string | exact match on `meetings.city` |
| `speaker` | no | `/^[a-z0-9][a-z0-9_-]{0,99}$/` | `politician_slug` filter |
| `page` | no, default 1 | positive integer | 25 results per page |

Violations → 422 `{code: 'VALIDATION_ERROR', message}` before any DB access. Page size 25 is a server-side constant (`SEARCH_PAGE_SIZE`).

### Response (200)

```json
{
  "query": "affordable housing",
  "page": 1,
  "totalCount": 137,
  "results": [
    {
      "meetingId": "uuid",
      "city": "Bloomington",
      "meetingType": "City Council",
      "date": "2026-02-18",
      "segmentIndex": 42,
      "startTime": 1843.2,
      "endTime": 1851.0,
      "speakerName": "John Hamilton",
      "politicianSlug": "john-hamilton",
      "snippet": "we have to talk about [[[affordable]]] [[[housing]]] before the"
    }
  ]
}
```

A query with no matches is a 200 with `totalCount: 0, results: []` (not an error).

### SQL shape

Inner query matches, ranks, paginates; outer query computes `ts_headline` over only the returned page (keeps headline generation off the full match set):

```sql
WITH hits AS (
  SELECT s.id, s.meeting_id, s.segment_index, s.start_time, s.end_time,
         s.speaker_name, s.politician_slug, s.text,
         ts_rank(s.tsv, websearch_to_tsquery('english', $1)) AS rank,
         m.city, m.meeting_type, m.date::text AS date
  FROM meetings.segments s
  JOIN meetings.meetings m ON m.id = s.meeting_id
  WHERE s.tsv @@ websearch_to_tsquery('english', $1)
    -- optional: AND s.politician_slug = $n
    -- optional: AND m.city = $n
  ORDER BY rank DESC, m.date DESC, s.segment_index
  LIMIT 25 OFFSET <(page-1)*25>
)
SELECT meeting_id, segment_index, start_time, end_time, speaker_name,
       politician_slug, city, meeting_type, date,
       ts_headline('english', text, websearch_to_tsquery('english', $1),
                   'StartSel=[[[, StopSel=]]], MaxWords=40, MinWords=20') AS snippet
FROM hits
ORDER BY rank DESC, date DESC, segment_index;
```

Plus a parallel `SELECT COUNT(*)` with the same WHERE for `totalCount` (same `Promise.all` pattern as `getTranscriptByMeetingId`).

**Snippet safety:** `ts_headline` does not HTML-escape its input, so the API never emits HTML. The `[[[`/`]]]` sentinels are converted to `<mark>` React elements client-side by string splitting — no `dangerouslySetInnerHTML` anywhere. `websearch_to_tsquery` never throws on malformed input, so no query-syntax error handling is needed.

### Conventions (same as people/meetings routes)

`pool.query()` only; explicit row types + camelCase mappers, never spread rows; `Number()` on numerics (`rank` is discarded, `start_time`/`end_time`/`segment_index` are mapped); `optionalAuth`; 500 `{code: 'INTERNAL_ERROR'}` with `console.error` on failure; route file does validation only, service file does SQL only.

### CORS (ops, no code)

The static site's browser calls this endpoint cross-origin. ev-accounts already allowlists origins via the `CORS_ORIGIN` env list (exact match, `backend/src/index.ts:73-87`). Deploy step: append the production site origin (e.g. `https://on-the-record-web.onrender.com`) to `CORS_ORIGIN` on the ev-accounts deployment. Dev allows all origins already.

## Web — `/search` page

### Structure

- `web/app/search/page.tsx` — **server component**, build-time: fetches `fetchMeetings()` + `fetchPeople()` to derive dropdown options (distinct cities sorted; speakers as `{slug, name}` sorted by name). Renders `<SearchView cities={...} speakers={...} />` inside `<Suspense>` (required by `useSearchParams` under static export).
- `web/app/search/SearchView.tsx` — **client component**: form + results. No new lib fetcher: the runtime fetch lives here, using `NEXT_PUBLIC_EV_ACCOUNTS_URL`.
- Types `SearchResult`/`SearchResponse` (snake_case) in `web/lib/types.ts`; the camelCase→snake_case mapping happens in `SearchView`.

### Behavior

- Form: text input, city `<select>` ("All cities" default), speaker `<select>` ("All speakers" default), Search button. Submit-to-search (no live search).
- URL is the source of truth: submitting updates `?q=&city=&speaker=` (omit empty params, reset page) via `router.replace`; arriving with `?q=` present triggers an immediate search. Shareable/bookmarkable.
- Results grouped by meeting (insertion order — API rank order determines which meeting appears first): meeting header links to `/meetings/[id]`; each hit shows a timestamp link (`/meetings/[id]?t=<floor(start_time)>#seg-<segment_index>`, same format as people profiles), speaker name (linked to `/people/[slug]` when `politician_slug` present), and the snippet with `<mark>` highlights.
- Pagination: Prev/Next buttons driven by `?page=` (25/page, hidden when not applicable). Result count shown ("137 results").
- States: idle (prompt to search), loading, empty ("No results for …"), error ("Search is temporarily unavailable…"). Build does NOT fail if the API is down at build time — dropdown fetches are wrapped in try/catch with empty fallbacks (matching the roster page pattern).
- Env: `NEXT_PUBLIC_EV_ACCOUNTS_URL` added to `web/.env.local.example` and `render.yaml` (same value as `EV_ACCOUNTS_URL`; the `NEXT_PUBLIC_` prefix bakes it into the client bundle).
- Nav: "Search" link added next to the existing "People →" link on the home page, and a link on the people page; search page links back home.
- Styles: flat classes in `globals.css` using existing variables (`.searchForm`, `.searchResults`, `.searchHit`, `mark` styling, etc.).

### formatTime

Phase 2 left identical `formatTime` helpers in `MeetingView.tsx` and `people/[slug]/page.tsx`. The search page needs it too — third copy is too many: extract to `web/lib/format.ts` and update both existing call sites to import it.

## Error handling summary

| Failure | Behavior |
|---|---|
| Missing/long `q`, bad `page`/`speaker` | 422 before DB |
| DB error | 500 `INTERNAL_ERROR`, logged |
| No matches | 200, empty results |
| API unreachable from browser | error state on page |
| API unreachable at build | dropdowns fall back to empty lists; build succeeds |

## Testing

- **ev-accounts**: TDD route tests (vitest + supertest, service mocked): 422 on missing q / long q / bad page / bad speaker slug; 200 happy path with response shape; filter passthrough (city, speaker, page); 200 empty results. Service follows the no-unit-test convention (covered by route tests + smoke).
- **Smoke**: curl against local backend with real data once published; verify snippet sentinels, ranking sanity, filters.
- **Web**: `tsc --noEmit`, `npm run lint`, full static build against local backend, browser verification of search flow (submit, deep link lands on the right segment, filters, pagination, empty/error states).

## Out of scope (deferred)

- Live search-as-you-type (revisit if users ask).
- Per-meeting server-side grouping/aggregation.
- Search analytics, query suggestions, fuzzy/typo tolerance.
- Rate limiting (public endpoint; revisit if abused — note in ops).
- Date-range filters.
