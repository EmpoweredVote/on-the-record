# Always-current Web App (Runtime Client-Side Data Fetching) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every data-driven page of the `web/` app fetch from the ev-accounts API in the browser at runtime, so new meetings (and people/topics) appear instantly with no rebuild, at zero added hosting cost.

**Architecture:** Keep `output: "export"` (free Render Static Site). Convert each data page to a client component that fetches via `NEXT_PUBLIC_EV_ACCOUNTS_URL` through a shared `useApi` hook. Dynamic detail routes keep a one-shell `generateStaticParams` and rely on Render rewrites + client-side `useParams()` so any id resolves without a prebuilt page. Remove the per-publish deploy-hook firing from the pipeline (the source of the stale-list race). Full design: `docs/superpowers/specs/2026-06-28-web-runtime-data-fetching-design.md`.

**Tech Stack:** Next.js **16.2.9** (App Router, `output: "export"`), React client components, vitest (node env), Render Static Site, ev-accounts REST API (CORS already enabled).

---

## Standing instructions (read before every task)

1. **This is NOT the Next.js you know (v16.2.9).** Per `web/AGENTS.md`, before writing any web code read the relevant guide under `web/node_modules/next/dist/docs/01-app/` — specifically the pages on **"use client" / client components**, **static exports (`output: export`)**, **dynamic routes & `generateStaticParams`**, and the **`useParams`** hook. Adjust the reference code below if the v16 API differs; the `npm run build` step is the correctness gate.
2. **Testing reality:** `web` uses **vitest in `environment: "node"`** and only collects **`lib/**/*.test.ts`**. There is **no DOM/React test infra** and component tests are not part of this codebase. Therefore: **TDD the data layer (`lib/queries.ts`) by mocking `fetch`**, and verify hooks/components/pages via **`npm run build`** (a clean static-export build is a strong gate) plus the **preview-deploy verification** in the final task. Do **not** add jsdom/testing-library (out of scope).
3. Run web commands from `web/`. Build: `npm run build`. Tests: `npm test` (vitest run) or `npx vitest run lib/<file>.test.ts`.
4. All commits end with the trailer `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` (omitted from sample messages below — add it).
5. Branch is `web-runtime-data` (already created). The one pipeline-repo change (Task 10) is committed on the same branch.

---

## File Structure

**web/ (primary):**
- Modify: `web/lib/queries.ts` — base URL → `NEXT_PUBLIC_EV_ACCOUNTS_URL`, runtime `cache: "no-store"`, drop build-id `BUST`. Add a `base()` accessor for testability.
- Create: `web/lib/queries.test.ts` — unit tests for the data layer (mock `fetch`).
- Create: `web/lib/useApi.ts` — shared client hook `{data, loading, error}`.
- Create: `web/components/Loading.tsx`, `web/components/ErrorState.tsx`, `web/components/EmptyState.tsx`, `web/components/NotFound.tsx` — shared UI states.
- Modify (server→client data flow): `web/app/page.tsx`, `web/app/people/page.tsx`, `web/app/topics/page.tsx`, `web/app/search/page.tsx`.
- Modify + Create client child: `web/app/meetings/[meetingId]/page.tsx` (+ `MeetingDetailClient.tsx`), `web/app/people/[id]/page.tsx` (+ `PersonDetailClient.tsx`), `web/app/topics/[key]/page.tsx` (+ `TopicDetailClient.tsx`).
- Modify: `web/next.config.*` — `trailingSlash: true` (predictable shell paths for rewrites).
- Modify: `render.yaml` (repo root) — add `routes` rewrites for the three dynamic groups.

**on-the-record pipeline:**
- Modify: `src/publish.py` — stop auto-firing the Render deploy hook on publish.

---

## Task 1: Data layer → runtime client fetch (TDD)

**Files:**
- Modify: `web/lib/queries.ts:14-19` (the `BASE`/`BUST` block) and the 9 `fetch(...)` call sites
- Create: `web/lib/queries.test.ts`

The current module reads `BASE` from the server-only `EV_ACCOUNTS_URL` at import time and adds a build-id `BUST` header. Switch to the browser-available `NEXT_PUBLIC_EV_ACCOUNTS_URL`, read it at call time via a `base()` helper (testable + still inlined by Next), and fetch with `cache: "no-store"` for always-current data.

- [ ] **Step 1: Write the failing tests**

```ts
// web/lib/queries.test.ts
import { afterEach, describe, expect, it, vi } from "vitest";

const API = "https://api.test";

function mockFetch(status: number, body: unknown) {
  return vi.fn(async () => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  })) as unknown as typeof fetch;
}

async function load() {
  // queries reads the env at call time via base(); set it before importing.
  vi.stubEnv("NEXT_PUBLIC_EV_ACCOUNTS_URL", API);
  vi.resetModules();
  return await import("./queries");
}

afterEach(() => {
  vi.unstubAllEnvs();
  vi.restoreAllMocks();
});

describe("queries data layer", () => {
  it("fetchMeetings hits the public API with no-store and maps results", async () => {
    const f = mockFetch(200, [{ id: "m1", date: "2026-01-01", meetingType: "X" }]);
    vi.stubGlobal("fetch", f);
    const { fetchMeetings } = await load();
    const out = await fetchMeetings();
    const [url, init] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${API}/api/meetings`);
    expect((init as RequestInit).cache).toBe("no-store");
    expect(out).toHaveLength(1);
    expect(out[0].meeting_id).toBe("m1");
  });

  it("fetchMeeting returns null on 404", async () => {
    vi.stubGlobal("fetch", mockFetch(404, {}));
    const { fetchMeeting } = await load();
    expect(await fetchMeeting("missing")).toBeNull();
  });

  it("fetchMeeting throws on a non-404 error", async () => {
    vi.stubGlobal("fetch", mockFetch(500, {}));
    const { fetchMeeting } = await load();
    await expect(fetchMeeting("x")).rejects.toThrow();
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web && npx vitest run lib/queries.test.ts`
Expected: FAIL — current `queries.ts` reads `EV_ACCOUNTS_URL` (so `base()` doesn't exist / URL mismatch) and sends the `BUST` header, not `cache: "no-store"`.

- [ ] **Step 3: Edit `web/lib/queries.ts`**

Replace the `BASE`/`BUST` block (lines 14-19) with:

```ts
// Read at call time (not import time) so it's testable and still inlined by Next.
function base(): string {
  return (process.env.NEXT_PUBLIC_EV_ACCOUNTS_URL ?? "").replace(/\/$/, "");
}

// Always fetch current data in the browser; no build-time cache.
const FETCH_INIT: RequestInit = { cache: "no-store" };
```

Then update **every** fetch call site in the file: replace `BASE` with `base()` and replace the `BUST` argument with `FETCH_INIT`. The 9 functions and their lines (from the current file): `fetchPeople` (137), `fetchPerson` (145), `fetchAppearances` (154-156), `fetchMeetings` (165), `fetchMeeting` (173), `fetchSegments` (184-186), `fetchSummary` (202), `fetchTopics` (210), `fetchTopic` (218). Also replace the `if (!BASE)` guards with `if (!base())`.

Example (fetchMeetings):
```ts
export async function fetchMeetings(): Promise<Meeting[]> {
  if (!base()) return [];
  const res = await fetch(`${base()}/api/meetings`, FETCH_INIT);
  if (!res.ok) throw new Error(`meetings fetch failed: ${res.status}`);
  const data = await res.json();
  return (data as unknown[]).map(mapMeeting);
}
```

Apply the identical transformation (`BASE`→`base()`, `BUST`→`FETCH_INIT`) to all 9 functions. Do not change the mapping functions.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web && npx vitest run lib/queries.test.ts`
Expected: PASS (3 tests). Also run the full lib suite: `npx vitest run` → existing `thumbnail.test.ts` still passes.

- [ ] **Step 5: Commit**

```bash
git add web/lib/queries.ts web/lib/queries.test.ts
git commit -m "feat(web): fetch API data at runtime via NEXT_PUBLIC base + no-store"
```

---

## Task 2: Shared `useApi` hook

**Files:**
- Create: `web/lib/useApi.ts`

A client hook that runs an async fetcher on mount (and when `deps` change), with a stale-call guard. Not unit-tested (no DOM infra) — verified via the pages that use it + build.

- [ ] **Step 1: Write the hook**

```tsx
// web/lib/useApi.ts
"use client";

import { useEffect, useState } from "react";

export interface ApiState<T> {
  data: T | null;
  loading: boolean;
  error: boolean;
}

/** Runs `fetcher` on mount and whenever `deps` change. Ignores results from
 *  superseded calls so a fast re-render can't apply stale data. */
export function useApi<T>(fetcher: () => Promise<T>, deps: unknown[] = []): ApiState<T> {
  const [state, setState] = useState<ApiState<T>>({ data: null, loading: true, error: false });

  useEffect(() => {
    let ignore = false;
    setState({ data: null, loading: true, error: false });
    fetcher()
      .then((data) => { if (!ignore) setState({ data, loading: false, error: false }); })
      .catch(() => { if (!ignore) setState({ data: null, loading: false, error: true }); });
    return () => { ignore = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);

  return state;
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no new errors (pre-existing `.next/` validator artifact errors, if any, are unrelated).

- [ ] **Step 3: Commit**

```bash
git add web/lib/useApi.ts
git commit -m "feat(web): add useApi client hook for runtime data fetching"
```

---

## Task 3: Shared UI state components

**Files:**
- Create: `web/components/Loading.tsx`, `web/components/ErrorState.tsx`, `web/components/EmptyState.tsx`, `web/components/NotFound.tsx`

- [ ] **Step 1: Create the components**

```tsx
// web/components/Loading.tsx
export default function Loading({ label = "Loading…" }: { label?: string }) {
  return <p className="loadingState" role="status" aria-live="polite">{label}</p>;
}
```

```tsx
// web/components/ErrorState.tsx
export default function ErrorState({ message = "Couldn’t load this right now. Please try again shortly." }: { message?: string }) {
  return <p className="errorState" role="alert">{message}</p>;
}
```

```tsx
// web/components/EmptyState.tsx
export default function EmptyState({ message }: { message: string }) {
  return <p className="emptyState">{message}</p>;
}
```

```tsx
// web/components/NotFound.tsx
import Link from "next/link";
export default function NotFound({ message = "Not found." }: { message?: string }) {
  return (
    <main className="notFoundPage">
      <p>{message}</p>
      <Link href="/">← Back to meetings</Link>
    </main>
  );
}
```

- [ ] **Step 2: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 3: Commit**

```bash
git add web/components/Loading.tsx web/components/ErrorState.tsx web/components/EmptyState.tsx web/components/NotFound.tsx
git commit -m "feat(web): add shared Loading/Error/Empty/NotFound UI components"
```

---

## Task 4: Convert the homepage `/` to runtime fetch (LIST TEMPLATE)

**Files:**
- Modify: `web/app/page.tsx`

This is the reference template for list pages. The page already renders the client `MeetingListClient`; we move the data fetch into the browser.

- [ ] **Step 1: Replace `web/app/page.tsx` with the client version**

```tsx
"use client";

import Link from "next/link";
import { fetchMeetings } from "@/lib/queries";
import { useApi } from "@/lib/useApi";
import MeetingListClient from "./MeetingListClient";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import EmptyState from "@/components/EmptyState";

export default function HomePage() {
  const { data: meetings, loading, error } = useApi(fetchMeetings);

  return (
    <main className="indexPage">
      <h1>Meetings</h1>
      <p className="tagline">
        Searchable, speaker-attributed transcripts of public meetings, synced
        to the original video.
      </p>
      <nav className="siteNav">
        <Link href="/people">People →</Link>
        <Link href="/search">Search →</Link>
        <Link href="/topics">Topics →</Link>
      </nav>
      {loading ? (
        <Loading label="Loading meetings…" />
      ) : error ? (
        <ErrorState message="Meetings are temporarily unavailable. Please try again shortly." />
      ) : !meetings || meetings.length === 0 ? (
        <EmptyState message="No meetings published yet." />
      ) : (
        <MeetingListClient meetings={meetings} />
      )}
    </main>
  );
}
```

- [ ] **Step 2: Build to verify the static export still succeeds**

Run: `cd web && npm run build`
Expected: build succeeds; `/` is emitted as a static client page. (If the build complains about a v16-specific client-component rule, consult `node_modules/next/dist/docs/01-app` and adjust, keeping the same data flow.)

- [ ] **Step 3: Commit**

```bash
git add web/app/page.tsx
git commit -m "feat(web): homepage fetches meetings at runtime"
```

---

## Task 5: Convert `/people` and `/topics` list pages (apply the LIST TEMPLATE)

**Files:**
- Modify: `web/app/people/page.tsx`, `web/app/topics/page.tsx`

For **each** file: first read its current contents to preserve its exact markup and the presentational component(s) it renders. Then apply the **exact pattern shown in Task 4**: add `"use client"`, replace the server `await fetch…` with `const { data, loading, error } = useApi(<fetcher>)`, and render `Loading` / `ErrorState` / `EmptyState` / the existing list markup. Use these fetchers and empty messages:

- `web/app/people/page.tsx` → `fetchPeople` (from `@/lib/queries`); empty message `"No people yet."` Preserve the page's existing people-list rendering (read the file).
- `web/app/topics/page.tsx` → `fetchTopics`; empty message `"No topics yet."` Preserve the existing topics rendering.

> Why a recipe instead of verbatim code: these files haven't been read in this plan and must keep their current per-page markup. The transformation is mechanically identical to Task 4 — copy that structure, swap the fetcher, keep the page's own JSX for the loaded state.

- [ ] **Step 1:** Convert `web/app/people/page.tsx` per the template above.
- [ ] **Step 2:** Convert `web/app/topics/page.tsx` per the template above.
- [ ] **Step 3: Build**

Run: `cd web && npm run build`
Expected: build succeeds; `/people` and `/topics` are static client pages.

- [ ] **Step 4: Commit**

```bash
git add web/app/people/page.tsx web/app/topics/page.tsx
git commit -m "feat(web): people and topics lists fetch at runtime"
```

---

## Task 6: Ensure `/search` fetches at runtime

**Files:**
- Modify: `web/app/search/page.tsx` (and confirm `web/app/search/SearchView.tsx`)

`SearchView` is already a client component. Read `web/app/search/page.tsx`: if it does any build-time `await fetch…` and passes data as props, move that fetch into the client (either into `SearchView` via `useApi`, or make `page.tsx` a thin client wrapper following Task 4). If `page.tsx` does no data fetching (just renders `SearchView`), leave it and confirm `SearchView` fetches via `fetch`/`useApi` against `base()` at runtime.

- [ ] **Step 1:** Inspect `web/app/search/page.tsx` and `SearchView.tsx`; remove any build-time data fetch so search results come from runtime requests.
- [ ] **Step 2: Build**

Run: `cd web && npm run build`
Expected: build succeeds.

- [ ] **Step 3: Commit**

```bash
git add web/app/search
git commit -m "feat(web): search fetches results at runtime only"
```

---

## Task 7: Convert `/meetings/[meetingId]` (DETAIL TEMPLATE)

**Files:**
- Modify: `web/app/meetings/[meetingId]/page.tsx`
- Create: `web/app/meetings/[meetingId]/MeetingDetailClient.tsx`

This is the reference template for dynamic detail routes. Next server-only exports (`generateStaticParams`) must stay in a server file, so the page stays a thin **server** shell that keeps a one-sentinel `generateStaticParams` and renders a **client** component that reads the id from the URL and fetches.

- [ ] **Step 1: Replace `web/app/meetings/[meetingId]/page.tsx` with a thin server shell**

```tsx
import MeetingDetailClient from "./MeetingDetailClient";

// One sentinel so output:"export" emits a single shell file for this route.
// Render rewrites (see render.yaml) serve this shell for ANY /meetings/* id;
// the client reads the real id from the URL and fetches it at runtime.
export function generateStaticParams() {
  return [{ meetingId: "view" }];
}

export default function MeetingPage() {
  return <MeetingDetailClient />;
}
```

(Drop `dynamicParams`, `generateMetadata`, and the server data fetch — SEO is out of scope per the design. If the v16 build requires `dynamicParams`, set `export const dynamicParams = false;` per the docs.)

- [ ] **Step 2: Create `web/app/meetings/[meetingId]/MeetingDetailClient.tsx`**

Move the existing rendering (header, exec summary, `MeetingView`) into this client component, fed by runtime fetches. Read the current `page.tsx` (already captured in the plan context) to preserve the exact header/summary markup.

```tsx
"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { fetchMeeting, fetchSegments, fetchSummary } from "@/lib/queries";
import { eventKindLabel, formatMeetingDate, meetingTitle } from "@/lib/format";
import { useApi } from "@/lib/useApi";
import MeetingView from "./MeetingView";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";

const SUBSTANTIVE = new Set(["discussion", "public_comment", "consent_agenda", "vote"]);

export default function MeetingDetailClient() {
  const params = useParams<{ meetingId: string }>();
  const id = params.meetingId;

  const meetingQ = useApi(() => fetchMeeting(id), [id]);
  const segmentsQ = useApi(() => fetchSegments(id), [id]);
  const summaryQ = useApi(() => fetchSummary(id).catch(() => null), [id]);

  if (meetingQ.loading) return <main className="meetingPage"><Loading label="Loading meeting…" /></main>;
  if (meetingQ.error) return <main className="meetingPage"><ErrorState /></main>;
  if (!meetingQ.data) return <NotFound message="Meeting not found." />;

  const meeting = meetingQ.data;
  const segments = segmentsQ.data ?? [];
  const summary = summaryQ.data ?? null;
  const outline = (summary?.sections ?? []).filter((s) => SUBSTANTIVE.has(s.section_type));

  return (
    <main className="meetingPage">
      <header className="meetingHeader">
        <Link href="/" className="backLink">← All meetings</Link>
        <h1>{meetingTitle(meeting)}</h1>
        <span className="eventKind">{eventKindLabel(meeting.event_kind)}</span>
        {meeting.event_orgs.length > 0 && (
          <p className="eventOrgs">{meeting.event_orgs.join(" · ")}</p>
        )}
        <p className="meetingDate">{formatMeetingDate(meeting.meeting_date)}</p>
        {meeting.source_url && (
          <a className="sourceLink" href={meeting.source_url} target="_blank" rel="noreferrer">
            Original source ↗
          </a>
        )}
      </header>

      {summary?.executive_summary && (
        <section className="execSummary">
          <h2>Summary</h2>
          <p>{summary.executive_summary}</p>
          {summary.highlights.length > 0 && (
            <ul className="highlights">
              {summary.highlights.map((d, i) => <li key={i}>{d}</li>)}
            </ul>
          )}
        </section>
      )}

      <MeetingView meeting={meeting} segments={segments} outline={outline} />
    </main>
  );
}
```

- [ ] **Step 3: Build**

Run: `cd web && npm run build`
Expected: build succeeds and emits exactly one shell for the route. Confirm the output path: `ls -d out/meetings/*/` (note the built directory name — it'll be the `view` sentinel; you'll need it for the rewrite in Task 9).

- [ ] **Step 4: Commit**

```bash
git add web/app/meetings/[meetingId]/page.tsx web/app/meetings/[meetingId]/MeetingDetailClient.tsx
git commit -m "feat(web): meeting detail renders any id at runtime via client fetch"
```

---

## Task 8: Convert `/people/[id]` and `/topics/[key]` (apply the DETAIL TEMPLATE)

**Files:**
- Modify: `web/app/people/[id]/page.tsx`, `web/app/topics/[key]/page.tsx`
- Create: `web/app/people/[id]/PersonDetailClient.tsx`, `web/app/topics/[key]/TopicDetailClient.tsx`

For **each** route, read the current `page.tsx` to preserve its markup, then apply the **exact pattern from Task 7**: a thin server shell with a one-sentinel `generateStaticParams` rendering a client detail component that reads the param via `useParams()` and fetches.

- `web/app/people/[id]/`:
  - Shell `generateStaticParams()` → `[{ id: "view" }]`; render `<PersonDetailClient />`.
  - `PersonDetailClient.tsx`: `const { id } = useParams<{ id: string }>()`; `useApi(() => fetchPerson(id), [id])` and `useApi(() => fetchAppearances(id), [id])`; render the page's existing person + appearances markup; `if (!person) return <NotFound message="Person not found." />`.
- `web/app/topics/[key]/`:
  - Shell `generateStaticParams()` → `[{ key: "view" }]`; render `<TopicDetailClient />`.
  - `TopicDetailClient.tsx`: `const { key } = useParams<{ key: string }>()`; `useApi(() => fetchTopic(key), [key])`; render the existing topic markup; `if (!topic) return <NotFound message="Topic not found." />`.

- [ ] **Step 1:** Convert `web/app/people/[id]/` per the template (shell + `PersonDetailClient.tsx`).
- [ ] **Step 2:** Convert `web/app/topics/[key]/` per the template (shell + `TopicDetailClient.tsx`).
- [ ] **Step 3: Build + note shell paths**

Run: `cd web && npm run build && ls -d out/people/*/ out/topics/*/`
Expected: build succeeds; note the built sentinel directory names for the rewrites in Task 9.

- [ ] **Step 4: Commit**

```bash
git add web/app/people/[id] web/app/topics/[key]
git commit -m "feat(web): people and topic detail pages render any id at runtime"
```

---

## Task 9: Render rewrites + predictable shell paths

**Files:**
- Modify: `web/next.config.*` (add `trailingSlash: true`)
- Modify: `render.yaml` (repo root — add `routes`)

Make built shell paths predictable, then route any dynamic-detail URL to its shell.

- [ ] **Step 1: Enable trailing slash** in `web/next.config.*` (so each route exports to `<route>/index.html`):

```js
const nextConfig = {
  output: "export",
  trailingSlash: true,
  // ...keep any existing config (images, etc.)
};
```

- [ ] **Step 2: Rebuild and confirm exact shell paths**

Run: `cd web && npm run build && ls out/meetings/ out/people/ out/topics/`
Expected: directories like `out/meetings/view/index.html`, `out/people/view/index.html`, `out/topics/view/index.html`. Use the actual names you see in the next step.

- [ ] **Step 3: Add rewrites to `render.yaml`** under the `on-the-record-web` static service (Render serves an existing static file when one matches; these rewrites catch the *unmatched* detail ids and serve the shell). Use the paths confirmed in Step 2:

```yaml
    routes:
      - type: rewrite
        source: /meetings/*
        destination: /meetings/view/index.html
      - type: rewrite
        source: /people/*
        destination: /people/view/index.html
      - type: rewrite
        source: /topics/*
        destination: /topics/view/index.html
```

> Verification of this rule's exact syntax/behavior on Render is part of Task 11 (it can't be checked locally). If Render requires a different `source`/`destination` form or a single SPA-style fallback, adjust there.

- [ ] **Step 4: Commit**

```bash
git add web/next.config.* render.yaml
git commit -m "feat(web): trailing-slash export + Render rewrites for dynamic detail routes"
```

---

## Task 10: Stop auto-firing the deploy hook on publish (pipeline)

**Files:**
- Modify: `src/publish.py` (`publish_meeting`, ~line 570 where `_trigger_deploy_hook()` is called)

With the web app reading data live, publishing no longer needs to rebuild the site. Remove the per-publish hook firing (the dedup/race source). Code deploys still happen via Render auto-deploy on git push.

- [ ] **Step 1: Edit `publish_meeting` in `src/publish.py`** — remove the post-publish hook call. Change:

```python
    if trigger_deploy:
        _trigger_deploy_hook()
```
to:
```python
    # The web app now reads data live from the API, so publishing no longer
    # needs to rebuild the static site. (Code deploys happen via git push.)
    # _trigger_deploy_hook() intentionally not called here anymore.
```

Leave the `_trigger_deploy_hook` function and the `trigger_deploy` parameter in place (harmless; callers unaffected) so any explicit/manual deploy path still works.

- [ ] **Step 2: Verify nothing broke**

Run: `.venv/bin/python -c "import src.publish"` → no error.
Run: `.venv/bin/python -m pytest tests/test_publish.py tests/test_republish_all.py -q` → PASS (publish DB-bound code isn't unit-tested here, consistent with the existing suite; this is a thin removal).

- [ ] **Step 3: Commit**

```bash
git add src/publish.py
git commit -m "feat(web): stop auto-rebuilding the site on publish (site reads data live)"
```

---

## Task 11: End-to-end preview verification (required)

No code; this validates the load-bearing rewrite + runtime fetch on a real deploy. The static-export build cannot prove the Render rewrite behavior.

- [ ] **Step 1:** Push the branch and open a PR so Render builds a **preview** for `web` (or trigger a preview deploy of the branch).
- [ ] **Step 2:** On the preview URL, verify:
  - Homepage lists meetings (loads via spinner → list).
  - A meeting detail URL `/(preview)/meetings/<a-real-id>` renders via the shell + client fetch.
  - A non-existent id renders the **NotFound** state (not a hard 404 / blank).
  - `/people/<id>` and `/topics/<key>` behave the same.
  - `/search` returns results from runtime requests.
- [ ] **Step 3:** Confirm the freshness goal directly: with the preview build untouched, publish a test meeting (or note one already published after the build) and confirm it appears in the homepage list **without any rebuild**.
- [ ] **Step 4:** If any rewrite/path detail was wrong, fix `render.yaml` / `next.config` per what the preview shows, rebuild, re-verify. Then the feature is done.

---

## Self-Review (completed during authoring)

- **Spec coverage:** runtime client fetch for all data pages → Tasks 1,4,5,6,7,8. Shared hook/UI → Tasks 2,3. Arbitrary-id detail via sentinel + rewrites + `useParams` → Tasks 7,8,9. Remove deploy-hook race → Task 10. CORS dependency → already verified (noted in spec; no task needed). Preview verification of the load-bearing rewrite → Task 11. SEO/SSR explicitly out of scope.
- **Testing posture:** only the data layer is unit-tested (Task 1), matching the repo's `lib/`-only, node-env vitest setup; everything else is build- + preview-verified. This is a deliberate, codebase-consistent exception (documented in Standing Instructions), analogous to the pipeline's untested DB-bound code — not skipped TDD.
- **Deferred/recipe tasks:** Tasks 5, 6, 8 are recipe-style because they apply the verbatim templates from Tasks 4 and 7 to files whose existing per-page markup must be preserved (and read at implementation time). The template code they reference is fully present in this plan, not hand-waved.
- **Type consistency:** `useApi` returns `{data, loading, error}` and is consumed identically in Tasks 4–8; `base()` / `FETCH_INIT` names match between Task 1's implementation and tests.
- **Next 16 risk:** every web task ends in `npm run build`, and Standing Instruction #1 mandates checking `node_modules/next/dist/docs/01-app` for v16 client-component / static-export / dynamic-route / `useParams` specifics before writing.
