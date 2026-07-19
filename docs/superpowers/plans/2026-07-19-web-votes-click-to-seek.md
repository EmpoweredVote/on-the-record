# Web — Votes click-to-seek Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** On the meeting page, show a list of the meeting's roll-call votes; clicking a timestamped vote seeks the video to that moment.

**Architecture:** Add a `Vote` type + `fetchVotes(meetingId)` to the `web/` data layer (`/api/meetings/:id/votes`, which is already live). Render a "Votes" section in `MeetingView` mirroring the existing "Discussed" outline — each vote is a button that calls the existing `seekToTime(seconds)` (which drives `PlayerAdapter.seekTo` + scrolls the transcript). Wire the fetch through `MeetingDetailClient` like `fetchSegments`.

**Tech Stack:** Next.js (`web/`), TypeScript, React, vitest. Commands run from `web/`: `npm test` (vitest), `npm run build` (Next typecheck+compile).

**Grounding (verified 2026-07-19):** API returns `Vote { id, meetingId, resolution: string|null, description: string|null, result: string, voteType: string|null, timestamp: number|null, createdAt, records[] }` (camelCase, ordered by timestamp NULLS-LAST). `MeetingView` already exposes `seekToTime(seconds)` and `formatTime`, and renders a `<section className="outline">` list of clickable seek buttons (the pattern to mirror). `MeetingDetailClient` fetches via `useApi(() => fetchSegments(id))` and passes props. `queries.ts` has `base()` + `FETCH_INIT`. No component-test infra (vitest lib tests only).

**Scope:** the votes panel + click-to-seek. OUT of scope: the real pass/fail outcome (API returns the tally string in `result`), per-member positions, and cross-linking votes to politician profiles. **Live votes require a meeting published with `meetings.votes` rows** (deferred prod write); this slice is verified by unit test + build + a stubbed-fetch browser check.

---

## File Structure

- Modify `web/lib/types.ts` — add `Vote`.
- Modify `web/lib/queries.ts` — add `fetchVotes`.
- Modify `web/lib/queries.test.ts` — test `fetchVotes`.
- Modify `web/app/meetings/[meetingId]/MeetingView.tsx` — `votes?` prop + Votes section.
- Modify `web/app/meetings/[meetingId]/MeetingDetailClient.tsx` — fetch + pass `votes`.
- Modify `web/app/globals.css` — minimal `.voteResult` / `.voteNoSeek`.

---

## Task 1: `Vote` type + `fetchVotes` data layer

**Files:**
- Modify: `web/lib/types.ts`, `web/lib/queries.ts`
- Test: `web/lib/queries.test.ts`

- [ ] **Step 1: Write the failing test**

Append inside the `describe("queries data layer", ...)` block in `web/lib/queries.test.ts`:

```ts
  it("fetchVotes hits the votes API and preserves null timestamps", async () => {
    const f = mockFetch(200, [
      { id: "v1", resolution: "Roll No. 438", description: "On the Smith amendment",
        result: "Yea 236, Nay 193", voteType: "recorded", timestamp: 14702.64 },
      { id: "v2", resolution: "Roll No. 443", description: "On the Connolly amendment",
        result: "Yea 247, Nay 182", voteType: "recorded", timestamp: null },
    ]);
    vi.stubGlobal("fetch", f);
    const { fetchVotes } = await load();
    const out = await fetchVotes("m1");
    const [url] = (f as unknown as ReturnType<typeof vi.fn>).mock.calls[0];
    expect(url).toBe(`${API}/api/meetings/m1/votes`);
    expect(out).toHaveLength(2);
    expect(out[0].resolution).toBe("Roll No. 438");
    expect(out[0].timestamp).toBe(14702.64);
    expect(out[1].timestamp).toBeNull();
  });

  it("fetchVotes returns [] on 404 (meeting has no votes)", async () => {
    vi.stubGlobal("fetch", mockFetch(404, {}));
    const { fetchVotes } = await load();
    expect(await fetchVotes("m1")).toEqual([]);
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npm test -- queries`
Expected: FAIL — `fetchVotes` is not exported.

- [ ] **Step 3: Add the type and the fetch function**

In `web/lib/types.ts`, add:

```ts
export interface Vote {
  id: string;
  resolution: string | null;
  description: string | null;
  result: string;
  voteType: string | null;
  timestamp: number | null;
}
```

In `web/lib/queries.ts`, add `Vote` to the `import type { ... } from "./types";` list, and add this function (e.g. after `fetchSegments`):

```ts
// Meeting roll-call votes. Empty for meetings without a published vote record;
// unmatched votes carry a null timestamp (not click-to-seekable).
export async function fetchVotes(meetingId: string): Promise<Vote[]> {
  const res = await fetch(`${base()}/api/meetings/${meetingId}/votes`, FETCH_INIT);
  if (res.status === 404) return [];
  if (!res.ok) throw new Error(`votes fetch failed: ${res.status}`);
  const raw = (await res.json()) as any[]; // eslint-disable-line @typescript-eslint/no-explicit-any
  return raw.map((v) => ({
    id: v.id,
    resolution: v.resolution ?? null,
    description: v.description ?? null,
    result: v.result ?? "",
    voteType: v.voteType ?? null,
    timestamp: v.timestamp ?? null,
  }));
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test -- queries`
Expected: PASS (existing query tests + the two new ones).

- [ ] **Step 5: Commit**

```bash
git add web/lib/types.ts web/lib/queries.ts web/lib/queries.test.ts
git commit -m "feat(web): Vote type + fetchVotes data layer"
```

---

## Task 2: Votes panel + wiring + CSS

**Files:**
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx`, `web/app/meetings/[meetingId]/MeetingDetailClient.tsx`, `web/app/globals.css`

- [ ] **Step 1: Add the `votes` prop to MeetingView**

In `MeetingView.tsx`:
1. Add `Vote` to the type import on line 11: change `import type { Meeting, Segment, SummarySection } from "@/lib/types";` to `import type { Meeting, Segment, SummarySection, Vote } from "@/lib/types";`.
2. In the props type (the block declaring `segments: Segment[]; ... outline?: SummarySection[];`), add: `votes?: Vote[];`.
3. In the component's destructured params (where `segments`, `outline` etc. are pulled), add `votes = [],`.

- [ ] **Step 2: Render the Votes section (mirror the outline)**

In `MeetingView.tsx`, immediately AFTER the existing `<section className="outline">…</section>` block (the "Discussed" list) and BEFORE the `<div className="transcriptPane"` element, insert:

```tsx
        {votes.length > 0 && (
          <section className="outline votes">
            <h2>Votes</h2>
            <ul>
              {votes.map((v) => (
                <li key={v.id} className="outlineItem">
                  {v.timestamp != null ? (
                    <button
                      type="button"
                      className="outlineLink"
                      title={v.description ?? undefined}
                      onClick={() => seekToTime(Math.floor(v.timestamp as number))}
                    >
                      <span className="outlineTitle">{v.resolution ?? "Vote"}</span>
                      <span className="outlineTime">{formatTime(v.timestamp as number)}</span>
                    </button>
                  ) : (
                    <span className="outlineLink voteNoSeek" title={v.description ?? undefined}>
                      <span className="outlineTitle">{v.resolution ?? "Vote"}</span>
                    </span>
                  )}
                  <span className="voteResult">{v.result}</span>
                </li>
              ))}
            </ul>
          </section>
        )}
```

(`seekToTime` and `formatTime` are already defined/imported in this file — the outline section uses both.)

- [ ] **Step 3: Fetch votes in MeetingDetailClient and pass them**

In `MeetingDetailClient.tsx`:
1. Add `fetchVotes` to the import: `import { fetchMeeting, fetchSegments, fetchSummary, fetchVotes } from "@/lib/queries";`.
2. Add a query alongside `segmentsQ`: `const votesQ = useApi(() => (ready ? fetchVotes(id).catch(() => []) : Promise.resolve([])), [id]);`.
3. After `const segments = segmentsQ.data ?? [];`, add: `const votes = votesQ.data ?? [];`.
4. Change the render to pass votes: `<MeetingView meeting={meeting} segments={segments} outline={outline} votes={votes} />`.

- [ ] **Step 4: Add minimal CSS**

Append to `web/app/globals.css`:

```css
/* Roll-call votes list (reuses .outline / .outlineItem / .outlineLink layout) */
.voteResult {
  display: block;
  font-size: 0.85rem;
  opacity: 0.7;
  margin-top: 0.15rem;
}
.voteNoSeek {
  cursor: default;
}
```

- [ ] **Step 5: Typecheck + build**

Run: `cd web && npm run build`
Expected: build succeeds (TypeScript compiles; the new prop + JSX integrate).

- [ ] **Step 6: Commit**

```bash
git add "web/app/meetings/[meetingId]/MeetingView.tsx" "web/app/meetings/[meetingId]/MeetingDetailClient.tsx" web/app/globals.css
git commit -m "feat(web): votes panel with click-to-seek on the meeting page"
```

---

## Task 3: Verify (controller runs)

**Files:** none.

- [ ] **Step 1: Unit + build gates**

Run: `cd web && npm test` (all vitest green, incl. the new `fetchVotes` tests) and `cd web && npm run build` (compiles).

- [ ] **Step 2: Browser render + click-to-seek check (stubbed votes)**

Because no meeting has `meetings.votes` rows yet (prod write deferred), verify the UI against a stubbed votes response:
1. `preview_start` the web dev server (`web` via `.claude/launch.json`, or `npm run dev` in `web/` on its port) and open a real meeting page (one with a video + transcript).
2. In the page, override the votes fetch to return mock rows and re-render — e.g. via `javascript_tool`: monkeypatch `window.fetch` so a URL containing `/votes` resolves to `[{id:"v1",resolution:"Roll No. 438",description:"On the Smith amendment",result:"Yea 236, Nay 193",voteType:"recorded",timestamp:120}]`, then reload.
3. Confirm the **Votes** section renders the row with its result, and clicking it moves the player (`adapter.getCurrentTime()` / the video jumps to ~120 s). Screenshot for the PR.

- [ ] **Step 3: Note follow-ons in the PR description (do not implement here)**
  - Live end-to-end (real votes → click → seek) awaits a meeting published with `meetings.votes` rows (deferred prod write, or a Supabase dev branch).
  - Show the real pass/fail outcome once the pipeline captures it (currently the `result` tally string).
  - Optional: link each vote to member positions / politician profiles (cross-link to `essentials.legislative_votes`).

---

## Self-Review

**Spec coverage:** `Vote` type + `fetchVotes` (Task 1) ✓; Votes panel that click-seeks via the existing `seekToTime` (Task 2) ✓; wired through `MeetingDetailClient` like segments (Task 2) ✓; verification incl. stubbed browser check (Task 3) ✓; real outcome, per-member positions, profile cross-link deferred ✓.

**Placeholder scan:** none — exact API shape, real files/anchors, complete code, runnable commands.

**Type consistency:** `Vote` fields (`resolution`, `description`, `result`, `voteType`, `timestamp`) match the ev-accounts API interface and are used consistently in `fetchVotes` (Task 1) and the MeetingView JSX (Task 2); `timestamp: number | null` drives the clickable-vs-static branch; `seekToTime(seconds)` and `formatTime` are the existing MeetingView helpers the outline already uses. Unmatched votes (null timestamp) render non-seekable, mirroring how the API sorts them last.
