# Web: Upcoming Meetings + Agenda Item Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an `/upcoming` meetings page and `/items/[itemId]` agenda-item permalink pages (rendering both upcoming and happened states) to the static-export Next.js site.

**Architecture:** Follows the site's documented pattern exactly: client-side runtime fetching from the ev-accounts API (`NEXT_PUBLIC_EV_ACCOUNTS_URL`), the sentinel-param + Render-rewrite trick for the dynamic route, types in `lib/types.ts` + mappers in `lib/queries.ts`, logic pushed into pure tested `lib/` functions, plain-CSS classes in `globals.css`. The item page is a **top-level** `/items/[itemId]` route (nesting under `/meetings/*` would be swallowed by that rewrite).

**Tech Stack:** Next.js 16 App Router (`output: "export"`), React 19, Vitest (lib only). Directory: `/Users/chrisandrews/Documents/GitHub/on-the-record/web`. Spec: `docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md`.

**Prerequisite:** ev-accounts endpoints from companion plan `2026-07-27-agenda-items-schema-api.md` (deployed, or stub locally while developing).

**Conventions (recon 2026-07-27):**
- Four-state ladder on pages: loading / error / empty / data (see `app/page.tsx:11-34`).
- New dynamic route = three coordinated edits: `generateStaticParams` sentinel + `render.yaml` rewrite + `usePathParam(n)`. Missing the rewrite 404s in prod only.
- Types snake_case? No — `lib/types.ts` interfaces use snake_case field names for pipeline-shaped data but the ev-accounts API returns camelCase; the `mapX()` functions in `lib/queries.ts` are the boundary. New agenda types mirror the API camelCase → site types the way `Vote` does.
- Only `lib/**/*.test.ts` is tested; keep components thin.
- Branch: `feat/web-upcoming-item-pages` off main.

---

### Task 1: Types + fetchers + mappers

**Files:**
- Modify: `web/lib/types.ts` (add `AgendaItem`, `AgendaItemDetail`; add `status` + `starts_at` to `Meeting`)
- Modify: `web/lib/queries.ts` (add `fetchUpcomingMeetings`, `fetchAgendaItems`, `fetchAgendaItem` + mappers; extend `mapMeeting`)
- Test: `web/lib/queries.test.ts` (extend)

- [ ] **Step 1: Write the failing tests**

Extend `web/lib/queries.test.ts` (follow the file's env-stub + `vi.resetModules()` + dynamic-import pattern at lines 12-16):

```ts
describe('agenda item queries', () => {
  it('mapAgendaItem maps camelCase API JSON to site type', async () => {
    const { mapAgendaItem } = await importQueries();
    const api = {
      id: 'i1',
      meetingId: 'm1',
      position: 6,
      itemNumber: '6A',
      titleRaw: 'Ordinance 2026-16 – To Amend Salaries',
      kind: 'ordinance',
      legislationRef: 'Ordinance 2026-16',
      summaryPlain: 'Raises pay 4 percent.',
      decisionPlain: 'First of two votes.',
      stage: 'First reading',
      publicComment: false,
      publicCommentNote: null,
      status: 'upcoming',
      outcome: null,
      segmentStartSeconds: null,
      segmentEndSeconds: null,
      continuedFromItemId: null,
      sourceUrl: 'https://bloomington.in.gov/onboard/meetingFiles/17202/download',
    };
    expect(mapAgendaItem(api)).toEqual({
      id: 'i1',
      meeting_id: 'm1',
      position: 6,
      item_number: '6A',
      title_raw: 'Ordinance 2026-16 – To Amend Salaries',
      kind: 'ordinance',
      legislation_ref: 'Ordinance 2026-16',
      summary_plain: 'Raises pay 4 percent.',
      decision_plain: 'First of two votes.',
      stage: 'First reading',
      public_comment: false,
      public_comment_note: null,
      status: 'upcoming',
      outcome: null,
      segment_start_seconds: null,
      segment_end_seconds: null,
      continued_from_item_id: null,
      source_url: 'https://bloomington.in.gov/onboard/meetingFiles/17202/download',
    });
  });

  it('mapAgendaItem defaults missing nullables', async () => {
    const { mapAgendaItem } = await importQueries();
    const item = mapAgendaItem({
      id: 'i1', meetingId: 'm1', position: 1, itemNumber: '1',
      titleRaw: 'ROLL CALL', kind: 'procedural', publicComment: false,
      status: 'upcoming', sourceUrl: 'https://x.gov/a.pdf',
    });
    expect(item.legislation_ref).toBeNull();
    expect(item.outcome).toBeNull();
  });

  it('fetchUpcomingMeetings returns [] when API base unset', async () => {
    const { fetchUpcomingMeetings } = await importQueries({ base: '' });
    expect(await fetchUpcomingMeetings()).toEqual([]);
  });

  it('mapMeeting carries status and starts_at through', async () => {
    const { mapMeeting } = await importQueries();
    const m = mapMeeting({
      id: 'm1', date: '2026-07-29', status: 'scheduled',
      startsAt: '2026-07-29T18:30:00-04:00',
    });
    expect(m.status).toBe('scheduled');
    expect(m.starts_at).toBe('2026-07-29T18:30:00-04:00');
  });
});
```

(`importQueries` — reuse/extend the existing test helper in this file that stubs `NEXT_PUBLIC_EV_ACCOUNTS_URL` and dynamic-imports the module; if `mapMeeting`/`mapAgendaItem` aren't exported, export them — `firstSentence`-style export-for-testing is house precedent in ev-accounts, and this file already tests mappers.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `npm test --prefix web` (or `cd web && npx vitest run lib/queries.test.ts`)
Expected: FAIL — `mapAgendaItem`/`fetchUpcomingMeetings` don't exist.

- [ ] **Step 3: Implement**

In `web/lib/types.ts`, add to `Meeting` (lines 12-34): `status: string;` and `starts_at: string | null;`. Then append:

```ts
export interface AgendaItem {
  id: string;
  meeting_id: string;
  position: number;
  item_number: string;
  title_raw: string;
  kind: string;
  legislation_ref: string | null;
  summary_plain: string | null;
  decision_plain: string | null;
  stage: string | null;
  public_comment: boolean;
  public_comment_note: string | null;
  status: "upcoming" | "happened";
  outcome: string | null;
  segment_start_seconds: number | null;
  segment_end_seconds: number | null;
  continued_from_item_id: string | null;
  source_url: string;
}

export interface AgendaItemDetail extends AgendaItem {
  meeting: {
    id: string;
    title: string | null;
    date: string;
    city: string | null;
    status: string;
    starts_at: string | null;
  };
}
```

In `web/lib/queries.ts`:

1. Extend `mapMeeting` (lines 24-58) with `status: json.status ?? "published",` and `starts_at: json.startsAt ?? null,`.
2. Add (mirroring the existing fetcher/mapper style, with the unset-base guard):

```ts
export function mapAgendaItem(json: Record<string, unknown>): AgendaItem {
  return {
    id: json.id as string,
    meeting_id: json.meetingId as string,
    position: (json.position as number) ?? 0,
    item_number: (json.itemNumber as string) ?? "",
    title_raw: (json.titleRaw as string) ?? "",
    kind: (json.kind as string) ?? "other",
    legislation_ref: (json.legislationRef as string) ?? null,
    summary_plain: (json.summaryPlain as string) ?? null,
    decision_plain: (json.decisionPlain as string) ?? null,
    stage: (json.stage as string) ?? null,
    public_comment: (json.publicComment as boolean) ?? false,
    public_comment_note: (json.publicCommentNote as string) ?? null,
    status: ((json.status as string) === "happened" ? "happened" : "upcoming"),
    outcome: (json.outcome as string) ?? null,
    segment_start_seconds: (json.segmentStartSeconds as number) ?? null,
    segment_end_seconds: (json.segmentEndSeconds as number) ?? null,
    continued_from_item_id: (json.continuedFromItemId as string) ?? null,
    source_url: (json.sourceUrl as string) ?? "",
  };
}

export async function fetchUpcomingMeetings(): Promise<Meeting[]> {
  if (!base()) return [];
  const res = await fetch(`${base()}/api/meetings/upcoming`, FETCH_INIT);
  if (!res.ok) throw new Error(`upcoming meetings: ${res.status}`);
  const json = (await res.json()) as Record<string, unknown>[];
  return json.map(mapMeeting);
}

export async function fetchAgendaItems(meetingId: string): Promise<AgendaItem[]> {
  if (!base()) return [];
  const res = await fetch(
    `${base()}/api/meetings/${encodeURIComponent(meetingId)}/agenda-items`,
    FETCH_INIT
  );
  if (!res.ok) throw new Error(`agenda items: ${res.status}`);
  const json = (await res.json()) as Record<string, unknown>[];
  return json.map(mapAgendaItem);
}

export async function fetchAgendaItem(itemId: string): Promise<AgendaItemDetail | null> {
  if (!base()) return null;
  const res = await fetch(
    `${base()}/api/agenda-items/${encodeURIComponent(itemId)}`,
    FETCH_INIT
  );
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`agenda item: ${res.status}`);
  const json = (await res.json()) as Record<string, unknown>;
  const m = json.meeting as Record<string, unknown>;
  return {
    ...mapAgendaItem(json),
    meeting: {
      id: m.id as string,
      title: (m.title as string) ?? null,
      date: m.date as string,
      city: (m.city as string) ?? null,
      status: (m.status as string) ?? "published",
      starts_at: (m.startsAt as string) ?? null,
    },
  };
}
```

(Adapt exact typing style to the file's existing mappers — if they type `json` more loosely, match them.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd web && npx vitest run lib/queries.test.ts` → PASS (all pre-existing tests too).

- [ ] **Step 5: Commit**

```bash
git add web/lib/types.ts web/lib/queries.ts web/lib/queries.test.ts && git commit -m "feat(web): AgendaItem types + upcoming/items fetchers and mappers"
```

---

### Task 2: /upcoming page

**Files:**
- Create: `web/app/upcoming/page.tsx`
- Create: `web/app/upcoming/UpcomingClient.tsx`
- Modify: `web/components/SiteHeader.tsx` (nav link, next to existing nav items)
- Modify: `web/app/globals.css` (append an `/* ===== Upcoming ===== */` section)
- Create: `web/lib/upcoming.ts` + Test: `web/lib/upcoming.test.ts` (date formatting/grouping logic)

- [ ] **Step 1: Write the failing lib test**

Create `web/lib/upcoming.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { formatMeetingWhen, groupByDate } from "./upcoming";

describe("formatMeetingWhen", () => {
  it("renders starts_at with weekday, date, and local time", () => {
    // 6:30 PM Eastern (Bloomington)
    const label = formatMeetingWhen("2026-07-29", "2026-07-29T18:30:00-04:00");
    expect(label).toContain("Wednesday");
    expect(label).toContain("July 29");
    expect(label).toMatch(/6:30/);
  });

  it("falls back to date-only when starts_at is null", () => {
    const label = formatMeetingWhen("2026-07-29", null);
    expect(label).toContain("July 29");
    expect(label).not.toMatch(/\d:\d\d/);
  });
});

describe("groupByDate", () => {
  it("groups meetings by date preserving order", () => {
    const groups = groupByDate([
      { date: "2026-07-29", id: "a" },
      { date: "2026-07-29", id: "b" },
      { date: "2026-08-05", id: "c" },
    ] as never[]);
    expect(groups.map((g) => g.date)).toEqual(["2026-07-29", "2026-08-05"]);
    expect(groups[0].meetings).toHaveLength(2);
  });
});
```

- [ ] **Step 2: Run to verify failure** → `cd web && npx vitest run lib/upcoming.test.ts` → FAIL.

- [ ] **Step 3: Implement `web/lib/upcoming.ts`**

```ts
// Date/time presentation for scheduled meetings. Times render in the
// MEETING's own zone (starts_at carries the offset) — a Bloomington meeting
// must say 6:30 PM regardless of the viewer's timezone.
import type { Meeting } from "./types";

export function formatMeetingWhen(date: string, startsAt: string | null): string {
  if (startsAt) {
    const d = new Date(startsAt);
    // Extract the offset from the ISO string so we format in meeting-local time.
    const offsetMatch = startsAt.match(/([+-]\d{2}:?\d{2}|Z)$/);
    const fmt = new Intl.DateTimeFormat("en-US", {
      weekday: "long", month: "long", day: "numeric",
      hour: "numeric", minute: "2-digit",
      timeZone: offsetToTimeZone(offsetMatch?.[1]),
    });
    return fmt.format(d);
  }
  const d = new Date(`${date}T12:00:00`);
  return new Intl.DateTimeFormat("en-US", {
    weekday: "long", month: "long", day: "numeric",
  }).format(d);
}

// Fixed-offset zones are expressible as Etc/GMT (sign inverted, whole hours);
// for half-hour offsets fall back to UTC rendering of the original wall time.
function offsetToTimeZone(offset: string | undefined): string {
  if (!offset || offset === "Z") return "UTC";
  const m = offset.match(/([+-])(\d{2}):?(\d{2})/);
  if (!m || m[3] !== "00") return "UTC";
  const hours = Number(m[2]);
  const sign = m[1] === "+" ? "-" : "+";
  return `Etc/GMT${sign}${hours}`;
}

export interface DateGroup {
  date: string;
  meetings: Meeting[];
}

export function groupByDate(meetings: Meeting[]): DateGroup[] {
  const groups: DateGroup[] = [];
  for (const m of meetings) {
    const last = groups[groups.length - 1];
    if (last && last.date === m.date) last.meetings.push(m);
    else groups.push({ date: m.date, meetings: [m] });
  }
  return groups;
}
```

Run: `npx vitest run lib/upcoming.test.ts` → PASS. (If the Etc/GMT trick fails the 6:30 assertion in CI's node, simplify: parse hour/minute digits straight out of the ISO string — the offset already encodes meeting-local wall time as `18:30`. Adjust implementation until the test passes; the test is the contract.)

- [ ] **Step 4: Build the page**

Create `web/app/upcoming/page.tsx`:

```tsx
import type { Metadata } from "next";
import UpcomingClient from "./UpcomingClient";

export const metadata: Metadata = { title: "Upcoming meetings — On the Record" };

export default function UpcomingPage() {
  return <UpcomingClient />;
}
```

Create `web/app/upcoming/UpcomingClient.tsx` (four-state ladder, per `app/page.tsx`):

```tsx
"use client";

import Link from "next/link";
import { useApi } from "@/lib/useApi";
import { fetchUpcomingMeetings, fetchAgendaItems } from "@/lib/queries";
import { formatMeetingWhen, groupByDate } from "@/lib/upcoming";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import EmptyState from "@/components/EmptyState";

export default function UpcomingClient() {
  const { data: meetings, loading, error } = useApi(fetchUpcomingMeetings);

  if (loading) return <Loading label="Loading upcoming meetings…" />;
  if (error) return <ErrorState message="Couldn't load upcoming meetings." />;
  if (!meetings?.length)
    return <EmptyState message="No upcoming meetings published yet." />;

  return (
    <div className="indexPage">
      <h1>Upcoming meetings</h1>
      {groupByDate(meetings).map((group) => (
        <section key={group.date} className="upcomingGroup">
          {group.meetings.map((m) => (
            <article key={m.id} className="upcomingMeeting">
              <h2>{m.title ?? m.meeting_type}</h2>
              <p className="upcomingWhen">{formatMeetingWhen(m.date, m.starts_at)}</p>
              {m.city && <p className="upcomingWhere">{m.city}</p>}
              <UpcomingAgenda meetingId={m.id} />
              {m.source_url && (
                <a className="upcomingSource" href={m.source_url} target="_blank" rel="noreferrer">
                  Official agenda (PDF)
                </a>
              )}
            </article>
          ))}
        </section>
      ))}
    </div>
  );
}

function UpcomingAgenda({ meetingId }: { meetingId: string }) {
  const { data: items, loading } = useApi(
    () => fetchAgendaItems(meetingId).catch(() => []),
    [meetingId]
  );
  if (loading || !items?.length) return null;
  const substantive = items.filter((i) =>
    ["ordinance", "resolution", "appointment", "public-comment"].includes(i.kind)
  );
  if (!substantive.length) return null;
  return (
    <ul className="upcomingItems">
      {substantive.map((item) => (
        <li key={item.id}>
          <Link href={`/items/${item.id}`} className="upcomingItemLink">
            <span className="upcomingItemTitle">
              {item.summary_plain ?? item.title_raw}
            </span>
            {item.stage && <span className="upcomingItemStage">{item.stage}</span>}
            {item.public_comment && (
              <span className="upcomingItemComment">Public comment</span>
            )}
          </Link>
        </li>
      ))}
    </ul>
  );
}
```

(Verify `useApi`'s actual signature/import path from `app/page.tsx` and match it; same for the shared state components' props.)

Add the nav link in `web/components/SiteHeader.tsx` next to the existing nav entries: `Upcoming` → `/upcoming`.

Append to `web/app/globals.css`:

```css
/* ===== Upcoming meetings ===== */
.upcomingGroup { margin: 1.5rem 0; }
.upcomingMeeting {
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 1rem 1.25rem;
  margin-bottom: 1rem;
}
.upcomingMeeting h2 { margin: 0 0 0.25rem; font-size: 1.1rem; }
.upcomingWhen { color: var(--accent); font-weight: 600; margin: 0; }
.upcomingWhere { color: var(--muted); margin: 0.15rem 0 0; }
.upcomingItems { list-style: none; margin: 0.75rem 0 0; padding: 0; }
.upcomingItemLink {
  display: flex; flex-wrap: wrap; gap: 0.5rem; align-items: baseline;
  padding: 0.4rem 0.5rem; border-radius: 8px; text-decoration: none;
  color: var(--foreground);
}
.upcomingItemLink:hover { background: var(--accent-soft); }
.upcomingItemTitle { flex: 1 1 24rem; }
.upcomingItemStage, .upcomingItemComment {
  font-size: 0.8rem; padding: 0.1rem 0.5rem; border-radius: 999px;
  border: 1px solid var(--border); color: var(--muted); white-space: nowrap;
}
.upcomingItemComment { color: var(--inform); border-color: var(--inform-soft); }
.upcomingSource { display: inline-block; margin-top: 0.6rem; font-size: 0.85rem; }
```

- [ ] **Step 5: Verify in the browser**

Start the dev server (use the repo's existing launch config / `npm run dev` in `web/` with `NEXT_PUBLIC_EV_ACCOUNTS_URL` pointed at prod or a local stub). Visit `/upcoming`. With no scheduled meetings in the DB yet, expect the EmptyState. To see the full render before real data exists, temporarily point fetchers at a local stub (e.g. `npx serve` a JSON file) or stub `fetchUpcomingMeetings` in dev — do NOT commit stubs.

- [ ] **Step 6: Commit**

```bash
git add web/app/upcoming web/lib/upcoming.ts web/lib/upcoming.test.ts web/components/SiteHeader.tsx web/app/globals.css && git commit -m "feat(web): /upcoming page — scheduled meetings with interpreted agenda items"
```

---

### Task 3: /items/[itemId] permalink page (both states)

**Files:**
- Create: `web/app/items/[itemId]/page.tsx` (sentinel shell)
- Create: `web/app/items/[itemId]/ItemDetailClient.tsx`
- Modify: `render.yaml` (add the `/items/*` rewrite — **without this the route 404s in prod only**)
- Modify: `web/app/globals.css` (append `/* ===== Agenda item page ===== */` section)
- Create: `web/lib/itemPresentation.ts` + Test: `web/lib/itemPresentation.test.ts`

- [ ] **Step 1: Write the failing lib test**

Create `web/lib/itemPresentation.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { outcomeLabel, itemStateBadge } from "./itemPresentation";

describe("outcomeLabel", () => {
  it("maps outcomes to citizen language", () => {
    expect(outcomeLabel("passed")).toBe("Passed");
    expect(outcomeLabel("failed")).toBe("Failed");
    expect(outcomeLabel("continued")).toBe("Continued to a later meeting");
    expect(outcomeLabel("pulled")).toBe("Pulled from the agenda");
    expect(outcomeLabel("no-action")).toBe("No action taken");
    expect(outcomeLabel(null)).toBeNull();
  });
});

describe("itemStateBadge", () => {
  it("upcoming item on a scheduled meeting reads as upcoming", () => {
    expect(itemStateBadge("upcoming", "2026-07-29")).toEqual({
      label: "Coming up",
      tone: "upcoming",
    });
  });
  it("happened item reads as decided", () => {
    expect(itemStateBadge("happened", "2026-07-29")).toEqual({
      label: "From the meeting on July 29, 2026",
      tone: "happened",
    });
  });
});
```

- [ ] **Step 2: Run to verify failure** → `cd web && npx vitest run lib/itemPresentation.test.ts` → FAIL.

- [ ] **Step 3: Implement `web/lib/itemPresentation.ts`**

```ts
// Citizen-language presentation for agenda items. Neutral voice — never
// editorialize outcomes (spec: provenance-first, rep-citable pages).
export function outcomeLabel(outcome: string | null): string | null {
  switch (outcome) {
    case "passed": return "Passed";
    case "failed": return "Failed";
    case "continued": return "Continued to a later meeting";
    case "pulled": return "Pulled from the agenda";
    case "no-action": return "No action taken";
    default: return null;
  }
}

export interface StateBadge {
  label: string;
  tone: "upcoming" | "happened";
}

export function itemStateBadge(
  status: "upcoming" | "happened",
  meetingDate: string
): StateBadge {
  if (status === "happened") {
    const d = new Date(`${meetingDate}T12:00:00`);
    const formatted = new Intl.DateTimeFormat("en-US", {
      month: "long", day: "numeric", year: "numeric",
    }).format(d);
    return { label: `From the meeting on ${formatted}`, tone: "happened" };
  }
  return { label: "Coming up", tone: "upcoming" };
}
```

Run: `npx vitest run lib/itemPresentation.test.ts` → PASS.

- [ ] **Step 4: Build the route**

Create `web/app/items/[itemId]/page.tsx` (the sentinel pattern, verbatim from the meetings route):

```tsx
// One sentinel so output:"export" emits a single shell file for this route.
// Render rewrites serve this shell for ANY /items/* id; the client reads the
// real id from the URL and fetches it at runtime.
export function generateStaticParams() {
  return [{ itemId: "view" }];
}

export default function ItemPage() {
  return <ItemDetailClient />;
}

import ItemDetailClient from "./ItemDetailClient";
```

(Order imports at the top per lint; shown inline here for compactness.)

Create `web/app/items/[itemId]/ItemDetailClient.tsx`:

```tsx
"use client";

import Link from "next/link";
import { useApi } from "@/lib/useApi";
import { usePathParam } from "@/lib/usePathParam";
import { fetchAgendaItem } from "@/lib/queries";
import { formatMeetingWhen } from "@/lib/upcoming";
import { outcomeLabel, itemStateBadge } from "@/lib/itemPresentation";
import Breadcrumbs from "@/components/Breadcrumbs";
import Loading from "@/components/Loading";
import ErrorState from "@/components/ErrorState";
import NotFound from "@/components/NotFound";

export default function ItemDetailClient() {
  const itemId = usePathParam(1); // /items/<itemId>
  const ready = itemId != null;
  const { data: item, loading, error } = useApi(
    () => (ready ? fetchAgendaItem(itemId) : Promise.resolve(null)),
    [itemId]
  );

  if (!ready || loading) return <Loading label="Loading agenda item…" />;
  if (error) return <ErrorState message="Couldn't load this agenda item." />;
  if (!item) return <NotFound />;

  const badge = itemStateBadge(item.status, item.meeting.date);
  const outcome = outcomeLabel(item.outcome);
  const seekHref =
    item.status === "happened" && item.segment_start_seconds != null
      ? `/meetings/${item.meeting.id}?t=${Math.floor(item.segment_start_seconds)}`
      : null;

  return (
    <div className="itemPage">
      <Breadcrumbs
        crumbs={[
          { href: "/upcoming", label: "Meetings" },
          { label: item.item_number },
        ]}
      />
      <span className={`itemBadge itemBadge-${badge.tone}`}>{badge.label}</span>
      <h1>{item.summary_plain ?? item.title_raw}</h1>
      {item.summary_plain && (
        <p className="itemTitleRaw">
          On the agenda as: <em>{item.title_raw}</em>
        </p>
      )}

      {item.decision_plain && (
        <section className="itemSection">
          <h2>What&apos;s being decided</h2>
          <p>{item.decision_plain}</p>
        </section>
      )}

      {(item.stage || item.public_comment_note) && (
        <section className="itemSection">
          <h2>Where this stands</h2>
          {item.stage && <p className="itemStage">{item.stage}</p>}
          {item.public_comment_note && (
            <p className={item.public_comment ? "itemCommentYes" : "itemCommentNo"}>
              {item.public_comment_note}
            </p>
          )}
        </section>
      )}

      {item.status === "happened" && (
        <section className="itemSection">
          <h2>What happened</h2>
          {outcome && <p className="itemOutcome">{outcome}</p>}
          {seekHref && (
            <Link className="itemSeekLink" href={seekHref}>
              Watch the discussion
            </Link>
          )}
        </section>
      )}

      <section className="itemSection itemMeta">
        <p>
          {item.meeting.title ?? "Meeting"} —{" "}
          {formatMeetingWhen(item.meeting.date, item.meeting.starts_at)}
          {item.meeting.city ? `, ${item.meeting.city}` : ""}
        </p>
        <p>
          {item.legislation_ref && <span>{item.legislation_ref} · </span>}
          <a href={item.source_url} target="_blank" rel="noreferrer">
            Official agenda (PDF)
          </a>
        </p>
      </section>
    </div>
  );
}
```

(Verify `Breadcrumbs`' actual prop shape from an existing page and match it.)

Add the rewrite in `render.yaml` alongside the existing three:

```yaml
      - { type: rewrite, source: /items/*, destination: /items/view/index.html }
```

Append to `web/app/globals.css`:

```css
/* ===== Agenda item page ===== */
.itemPage { max-width: 44rem; margin: 0 auto; }
.itemBadge {
  display: inline-block; font-size: 0.8rem; font-weight: 600;
  padding: 0.15rem 0.6rem; border-radius: 999px; margin-bottom: 0.5rem;
}
.itemBadge-upcoming { background: var(--accent-soft); color: var(--accent); }
.itemBadge-happened { background: var(--inform-soft); color: var(--inform); }
.itemTitleRaw { color: var(--muted); font-size: 0.9rem; }
.itemSection { margin-top: 1.5rem; }
.itemSection h2 { font-size: 1rem; margin-bottom: 0.4rem; }
.itemStage { font-weight: 600; margin: 0 0 0.3rem; }
.itemCommentYes { color: var(--inform); }
.itemCommentNo { color: var(--muted); }
.itemOutcome { font-weight: 600; }
.itemSeekLink { display: inline-block; margin-top: 0.4rem; }
.itemMeta { color: var(--muted); font-size: 0.9rem; border-top: 1px solid var(--border); padding-top: 1rem; }
```

- [ ] **Step 5: Verify in the browser**

`next dev` + visit `/items/<any-uuid>` — with the API returning 404 you should see NotFound, not a crash. If a real item exists (after the adapter's live E2E), load its real permalink and check both the upcoming rendering and (by hand-editing a stub response) the happened rendering with outcome + Watch link.

- [ ] **Step 6: Run the full web test suite + build**

Run: `cd web && npx vitest run && npm run build`
Expected: tests pass; `next build` succeeds and `out/items/view/index.html` exists (proves the sentinel emitted).

- [ ] **Step 7: Commit**

```bash
git add web/app/items render.yaml web/lib/itemPresentation.ts web/lib/itemPresentation.test.ts web/app/globals.css && git commit -m "feat(web): /items/[itemId] permalink page rendering upcoming + happened states"
```

---

### Task 4: PR

- [ ] **Step 1: Lint + full suite one more time**

Run: `cd web && npm run lint && npx vitest run && npm run build` → all pass.

- [ ] **Step 2: Push and open PR**

```bash
git push -u origin feat/web-upcoming-item-pages
gh pr create --title "web: /upcoming meetings + /items/[itemId] agenda-item permalinks" --body "$(cat <<'EOF'
Citizen-facing pages for the item-centric coverage spec (docs/superpowers/specs/2026-07-27-bloomington-item-centric-civic-coverage-design.md).

- /upcoming: scheduled meetings (live-fetched from /api/meetings/upcoming), grouped by date, with interpreted agenda items, stage + public-comment chips, official-PDF links
- /items/[itemId]: stable permalink per agenda item; renders upcoming state (plain-language summary, what's being decided, where it stands, can-I-comment) and happened state (outcome + Watch-the-discussion seek link into the meeting page)
- Sentinel + render.yaml rewrite for the new dynamic route (/items/*)
- All logic in tested lib/ modules (upcoming.ts, itemPresentation.ts); components stay thin

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```
