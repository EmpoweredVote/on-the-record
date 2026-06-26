# YouTube-style Meeting List Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Redesign the homepage meeting list into YouTube-style horizontal rows — a video thumbnail on the left, a one-line summary plus metadata on the right — so the list is easy to skim.

**Architecture:** A pure helper (`buildThumbnailModel`) maps a `Meeting` to a render-ready descriptor covering three states (YouTube frame, info tile + play, info tile + "Transcript only"). Two presentational components consume it: `MeetingThumbnail` (the tile/overlays) and `MeetingCard` (one row). `MeetingListClient` keeps its tab/filter logic and renders `MeetingCard`s. Pure logic is unit-tested with Vitest; components are verified in the browser.

**Tech Stack:** Next.js 16 (static export), React 19, TypeScript, plain CSS with theme variables in `web/app/globals.css`, Vitest for unit tests.

**Spec:** `docs/superpowers/specs/2026-06-26-youtube-style-meeting-list-design.md`

> All paths below are relative to `web/`. All commands run from `web/`.

---

## File structure

- **Create** `web/vitest.config.ts` — Vitest config (node env, `@` alias).
- **Modify** `web/package.json` — add `vitest` dev dependency + `test` script.
- **Modify** `web/lib/format.ts` — add `formatDuration` (moved from `MeetingListClient`).
- **Create** `web/lib/format.test.ts` — unit tests for `formatDuration`.
- **Create** `web/lib/thumbnail.ts` — `ThumbnailModel` type, `youtubeThumbnailUrl`, `buildThumbnailModel`.
- **Create** `web/lib/thumbnail.test.ts` — unit tests for the helper.
- **Create** `web/components/MeetingThumbnail.tsx` — renders the thumbnail/info-tile + overlays.
- **Create** `web/components/MeetingCard.tsx` — one row (thumbnail + text column).
- **Modify** `web/app/MeetingListClient.tsx` — render `MeetingCard`s; drop inline row markup + local `formatDuration`.
- **Modify** `web/app/globals.css` — replace `.meetingList` block with row/thumbnail/tile/overlay styles.

---

## Task 1: Vitest setup

**Files:**
- Modify: `web/package.json`
- Create: `web/vitest.config.ts`
- Create: `web/lib/sanity.test.ts` (temporary)

- [ ] **Step 1: Install Vitest**

Run (from `web/`):
```bash
npm install -D vitest
```

- [ ] **Step 2: Add the `test` script**

In `web/package.json`, add a `test` entry to `"scripts"` so the block reads:
```json
  "scripts": {
    "dev": "next dev",
    "build": "next build",
    "start": "next start",
    "lint": "eslint",
    "test": "vitest run"
  },
```

- [ ] **Step 3: Create the Vitest config**

Create `web/vitest.config.ts`:
```ts
import { defineConfig } from "vitest/config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  resolve: {
    alias: {
      "@": fileURLToPath(new URL(".", import.meta.url)),
    },
  },
  test: {
    environment: "node",
    include: ["lib/**/*.test.ts"],
  },
});
```

- [ ] **Step 4: Add a temporary sanity test**

Create `web/lib/sanity.test.ts`:
```ts
import { describe, it, expect } from "vitest";

describe("vitest setup", () => {
  it("runs", () => {
    expect(1 + 1).toBe(2);
  });
});
```

- [ ] **Step 5: Run the test to confirm the runner works**

Run: `npm test`
Expected: PASS — `1 passed`.

- [ ] **Step 6: Delete the sanity test**

Run: `rm lib/sanity.test.ts`

- [ ] **Step 7: Commit**

```bash
git add package.json package-lock.json vitest.config.ts
git commit -m "chore(web): add Vitest test runner"
```

---

## Task 2: `formatDuration` helper (TDD)

Move the duration formatter out of `MeetingListClient` into `lib/format.ts` so it can be reused by the thumbnail helper and tested. (The copy in `MeetingListClient.tsx` is removed in Task 6.)

**Files:**
- Modify: `web/lib/format.ts`
- Test: `web/lib/format.test.ts`

- [ ] **Step 1: Write the failing test**

Create `web/lib/format.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { formatDuration } from "./format";

describe("formatDuration", () => {
  it("returns empty string for null", () => {
    expect(formatDuration(null)).toBe("");
  });

  it("returns empty string for zero", () => {
    expect(formatDuration(0)).toBe("");
  });

  it("formats sub-hour durations as minutes", () => {
    expect(formatDuration(2880)).toBe("48m");
  });

  it("formats hour-plus durations as hours and minutes", () => {
    expect(formatDuration(8040)).toBe("2h 14m");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — `formatDuration` is not exported from `./format`.

- [ ] **Step 3: Implement `formatDuration`**

In `web/lib/format.ts`, add this export (place it after `formatTime`):
```ts
// Human-friendly meeting length, e.g. "2h 14m" or "48m". Empty string when unknown.
export function formatDuration(seconds: number | null): string {
  if (!seconds) return "";
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${m}m` : `${m}m`;
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — all `formatDuration` tests green.

- [ ] **Step 5: Commit**

```bash
git add lib/format.ts lib/format.test.ts
git commit -m "feat(web): add reusable formatDuration helper"
```

---

## Task 3: Thumbnail model helper (TDD)

**Files:**
- Create: `web/lib/thumbnail.ts`
- Test: `web/lib/thumbnail.test.ts`

- [ ] **Step 1: Write the failing test**

Create `web/lib/thumbnail.test.ts`:
```ts
import { describe, it, expect } from "vitest";
import { buildThumbnailModel, youtubeThumbnailUrl } from "./thumbnail";
import type { Meeting } from "./types";

const base: Meeting = {
  meeting_id: "m1",
  slug: null,
  title: null,
  event_kind: "council",
  city: "Asheville",
  chamber_id: null,
  race_id: null,
  meeting_type: "Regular Meeting",
  meeting_date: "2026-02-25",
  source_url: null,
  playback_kind: "youtube",
  playback_url: "abc123",
  duration_seconds: 8040,
  summary_preview: "A summary.",
  speakers: [],
  event_orgs: [],
  source_title: null,
};

describe("youtubeThumbnailUrl", () => {
  it("builds the public hqdefault URL", () => {
    expect(youtubeThumbnailUrl("abc123")).toBe(
      "https://img.youtube.com/vi/abc123/hqdefault.jpg"
    );
  });
});

describe("buildThumbnailModel", () => {
  it("uses the YouTube frame for youtube meetings", () => {
    const m = buildThumbnailModel(base);
    expect(m.imageSrc).toBe("https://img.youtube.com/vi/abc123/hqdefault.jpg");
    expect(m.showPlay).toBe(true);
    expect(m.transcriptOnly).toBe(false);
    expect(m.duration).toBe("2h 14m");
  });

  it("renders the info tile (no frame) for file videos but keeps play + duration", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: "file",
      playback_url: "https://cdn.example.com/v.mp4",
    });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(true);
    expect(m.transcriptOnly).toBe(false);
    expect(m.duration).toBe("2h 14m");
    expect(m.location).toBe("Asheville");
    expect(m.date).toBe("Feb 25, 2026");
  });

  it("treats hls the same as file", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: "hls",
      playback_url: "https://cdn.example.com/v.m3u8",
    });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(true);
  });

  it("marks no-video meetings transcript-only with no play or duration", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: null,
      playback_url: null,
    });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(false);
    expect(m.duration).toBeNull();
    expect(m.transcriptOnly).toBe(true);
  });

  it("falls back to the info tile when a youtube meeting has no video id", () => {
    const m = buildThumbnailModel({ ...base, playback_url: null });
    expect(m.imageSrc).toBeNull();
    expect(m.showPlay).toBe(true);
  });

  it("falls back to the meeting title for location when city is null", () => {
    const m = buildThumbnailModel({
      ...base,
      city: null,
      title: "Special Joint Session",
    });
    expect(m.location).toBe("Special Joint Session");
  });
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `npm test`
Expected: FAIL — `./thumbnail` cannot be resolved.

- [ ] **Step 3: Implement the helper**

Create `web/lib/thumbnail.ts`:
```ts
import type { Meeting } from "./types";
import { formatDuration, formatMeetingDate, meetingTitle } from "./format";

export interface ThumbnailModel {
  /** Real video frame to show; null => render the info tile instead. */
  imageSrc: string | null;
  /** Whether a playable video exists (controls the centered play overlay). */
  showPlay: boolean;
  /** Formatted duration for the badge, or null to hide it. */
  duration: string | null;
  /** True when the meeting has no video at all. */
  transcriptOnly: boolean;
  /** Info-tile location line (city, falling back to the meeting title). */
  location: string;
  /** Info-tile date line. */
  date: string;
}

/** Public YouTube thumbnail URL for a video id. */
export function youtubeThumbnailUrl(videoId: string): string {
  return `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;
}

export function buildThumbnailModel(meeting: Meeting): ThumbnailModel {
  const hasVideo = meeting.playback_kind !== null;

  // Source precedence: explicit thumbnail (future) > YouTube-derived frame > none.
  // SEAM: when the API later exposes an extracted-frame URL for file/HLS videos,
  // prefer it here, e.g. `if (meeting.thumbnail_url) imageSrc = meeting.thumbnail_url;`
  let imageSrc: string | null = null;
  if (meeting.playback_kind === "youtube" && meeting.playback_url) {
    imageSrc = youtubeThumbnailUrl(meeting.playback_url);
  }

  const duration =
    hasVideo && meeting.duration_seconds
      ? formatDuration(meeting.duration_seconds)
      : null;

  return {
    imageSrc,
    showPlay: hasVideo,
    duration,
    transcriptOnly: !hasVideo,
    location: meeting.city?.trim() || meetingTitle(meeting),
    date: formatMeetingDate(meeting.meeting_date),
  };
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `npm test`
Expected: PASS — all `thumbnail` and `youtubeThumbnailUrl` tests green.

- [ ] **Step 5: Commit**

```bash
git add lib/thumbnail.ts lib/thumbnail.test.ts
git commit -m "feat(web): add buildThumbnailModel helper for meeting thumbnails"
```

---

## Task 4: `MeetingThumbnail` component

**Files:**
- Create: `web/components/MeetingThumbnail.tsx`

- [ ] **Step 1: Create the component**

Create `web/components/MeetingThumbnail.tsx`:
```tsx
import { buildThumbnailModel } from "@/lib/thumbnail";
import type { Meeting } from "@/lib/types";

export default function MeetingThumbnail({ meeting }: { meeting: Meeting }) {
  const t = buildThumbnailModel(meeting);

  return (
    <div className="meetingThumb">
      {t.imageSrc ? (
        // Static export has no image optimizer; an intentional lazy <img> is correct here.
        // eslint-disable-next-line @next/next/no-img-element
        <img className="meetingThumbImg" src={t.imageSrc} alt="" loading="lazy" />
      ) : (
        <div className="meetingThumbBand">
          <span className="meetingThumbLoc">{t.location}</span>
          <span className="meetingThumbDate">{t.date}</span>
        </div>
      )}
      {t.showPlay && <span className="meetingThumbPlay" aria-hidden="true" />}
      {t.duration && <span className="meetingThumbDuration">{t.duration}</span>}
      {t.transcriptOnly && (
        <span className="meetingThumbTranscript">
          <svg
            width="11"
            height="11"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2.5"
            aria-hidden="true"
          >
            <path d="M3 3l18 18M10 7h7a2 2 0 012 2v6m-2 2H6a2 2 0 01-2-2V8" />
          </svg>
          Transcript only
        </span>
      )}
    </div>
  );
}
```

- [ ] **Step 2: Type-check via build**

Run: `npm run build`
Expected: build succeeds (component compiles; it isn't rendered anywhere yet, so no visual change). If the build is slow or hits the DB, a `npx tsc --noEmit` is an acceptable substitute for this step.

- [ ] **Step 3: Commit**

```bash
git add components/MeetingThumbnail.tsx
git commit -m "feat(web): add MeetingThumbnail component"
```

---

## Task 5: `MeetingCard` component

**Files:**
- Create: `web/components/MeetingCard.tsx`

- [ ] **Step 1: Create the component**

Create `web/components/MeetingCard.tsx`:
```tsx
import Link from "next/link";
import type { Meeting } from "@/lib/types";
import { eventKindLabel, formatMeetingDate, meetingTitle } from "@/lib/format";
import MeetingThumbnail from "./MeetingThumbnail";

export default function MeetingCard({ meeting }: { meeting: Meeting }) {
  const speakerCount = meeting.speakers?.length ?? 0;
  const date = formatMeetingDate(meeting.meeting_date);
  // Speaker count only renders when the list API actually returns speakers
  // (see plan caveat) — never show "0 speakers".
  const meta =
    speakerCount > 0
      ? `${date} · ${speakerCount} ${speakerCount === 1 ? "speaker" : "speakers"}`
      : date;

  return (
    <li>
      <Link href={`/meetings/${meeting.meeting_id}`} className="meetingCard">
        <MeetingThumbnail meeting={meeting} />
        <div className="meetingBody">
          <span className="eventKind">{eventKindLabel(meeting.event_kind)}</span>
          <span className="meetingTitle">{meetingTitle(meeting)}</span>
          <span className="meetingMeta">{meta}</span>
          {meeting.summary_preview && (
            <span className="meetingPreview">{meeting.summary_preview}</span>
          )}
        </div>
      </Link>
    </li>
  );
}
```

- [ ] **Step 2: Type-check via build**

Run: `npm run build` (or `npx tsc --noEmit`)
Expected: compiles cleanly. (Still not wired into the page, so no visual change.)

- [ ] **Step 3: Commit**

```bash
git add components/MeetingCard.tsx
git commit -m "feat(web): add MeetingCard row component"
```

---

## Task 6: Wire `MeetingListClient` to `MeetingCard`

Replace the inline `<li>` markup with `MeetingCard`, and remove the now-duplicate local `formatDuration`.

**Files:**
- Modify: `web/app/MeetingListClient.tsx`

- [ ] **Step 1: Replace the file contents**

Overwrite `web/app/MeetingListClient.tsx` with:
```tsx
'use client';

import { useState, useMemo } from 'react';
import type { Meeting, EventKind } from '@/lib/types';
import { eventKindLabel } from '@/lib/format';
import MeetingCard from '@/components/MeetingCard';

export default function MeetingListClient({ meetings }: { meetings: Meeting[] }) {
  // Derive tabs from actual event_kinds present — no hardcoding per CONTEXT specifics
  const kinds = useMemo(() => {
    const seen = new Set<EventKind>();
    meetings.forEach(m => seen.add(m.event_kind));
    return [...seen].sort();
  }, [meetings]);

  const [active, setActive] = useState<EventKind | 'all'>('all');

  const shown = active === 'all'
    ? meetings
    : meetings.filter(m => m.event_kind === active);

  return (
    <>
      {kinds.length > 1 && (
        <nav className="kindTabs" aria-label="Filter by event type">
          <button
            className={active === 'all' ? 'active' : ''}
            onClick={() => setActive('all')}
          >
            All
          </button>
          {kinds.map(k => (
            <button
              key={k}
              className={active === k ? 'active' : ''}
              onClick={() => setActive(k)}
            >
              {eventKindLabel(k)}
            </button>
          ))}
        </nav>
      )}
      <ul className="meetingList">
        {shown.map((m) => (
          <MeetingCard key={m.meeting_id} meeting={m} />
        ))}
      </ul>
    </>
  );
}
```

- [ ] **Step 2: Lint to confirm no unused imports remain**

Run: `npm run lint`
Expected: no errors (the old `Link`, `formatMeetingDate`, `meetingTitle`, and local `formatDuration` are gone; only `eventKindLabel` is still imported here).

- [ ] **Step 3: Commit**

```bash
git add app/MeetingListClient.tsx
git commit -m "feat(web): render meetings as MeetingCard rows"
```

---

## Task 7: Styles

Replace the old list styles with the row layout, thumbnail/info-tile, overlays, and responsive rules. The shared `.eventKind` badge (defined elsewhere in this file) is reused as-is.

**Files:**
- Modify: `web/app/globals.css`

- [ ] **Step 1: Replace the `.meetingList` block**

In `web/app/globals.css`, replace the entire block from `.meetingList {` (currently line ~208) through the end of the `.meetingPreview { ... }` rule (currently line ~265) with:
```css
.meetingList {
  list-style: none;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

.meetingCard {
  display: flex;
  gap: 1rem;
  align-items: flex-start;
  padding: 0.75rem;
  background: var(--surface);
  border: 1px solid var(--border);
  border-radius: 10px;
  box-shadow: var(--shadow);
  transition: border-color 150ms ease, background 150ms ease, box-shadow 150ms ease;
  cursor: pointer;
}

.meetingCard:hover {
  border-color: var(--accent);
  background: var(--accent-soft);
  box-shadow: 0 2px 8px rgba(3, 105, 161, 0.12);
}

/* Thumbnail / info tile */
.meetingThumb {
  position: relative;
  flex: 0 0 auto;
  width: 200px;
  aspect-ratio: 16 / 9;
  border-radius: 8px;
  overflow: hidden;
  background: var(--border);
}

.meetingThumbImg {
  position: absolute;
  inset: 0;
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}

/* Info-tile top band: location (left) + date (right) — kept clear of overlays */
.meetingThumbBand {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 0.5rem;
  padding: 0.5rem 0.55rem;
}

.meetingThumbLoc {
  font-size: 0.75rem;
  font-weight: 700;
  color: var(--foreground);
  line-height: 1.2;
  overflow: hidden;
  text-overflow: ellipsis;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
}

.meetingThumbDate {
  font-size: 0.7rem;
  font-weight: 600;
  color: var(--muted);
  white-space: nowrap;
}

/* Centered play overlay */
.meetingThumbPlay {
  position: absolute;
  top: 50%;
  left: 50%;
  transform: translate(-50%, -50%);
  width: 42px;
  height: 42px;
  border-radius: 999px;
  background: rgba(255, 255, 255, 0.92);
  display: flex;
  align-items: center;
  justify-content: center;
}

.meetingThumbPlay::after {
  content: "";
  border-left: 13px solid #0f172a;
  border-top: 8px solid transparent;
  border-bottom: 8px solid transparent;
  margin-left: 3px;
}

/* Duration badge (bottom-right) */
.meetingThumbDuration {
  position: absolute;
  bottom: 0.35rem;
  right: 0.35rem;
  background: rgba(0, 0, 0, 0.8);
  color: #fff;
  font-size: 0.7rem;
  font-weight: 600;
  padding: 0.05rem 0.3rem;
  border-radius: 4px;
}

/* "Transcript only" tag (bottom-right; no-video rows) */
.meetingThumbTranscript {
  position: absolute;
  bottom: 0.35rem;
  right: 0.35rem;
  display: flex;
  align-items: center;
  gap: 0.25rem;
  font-size: 0.65rem;
  font-weight: 700;
  color: var(--muted);
  background: var(--surface);
  padding: 0.1rem 0.35rem;
  border-radius: 4px;
}

/* Text column */
.meetingBody {
  display: flex;
  flex-direction: column;
  gap: 0.2rem;
  min-width: 0;
  flex: 1 1 auto;
}

.meetingTitle {
  font-weight: 600;
  font-size: 1rem;
  color: var(--foreground);
}

.meetingMeta {
  color: var(--muted);
  font-size: 0.8rem;
}

.meetingPreview {
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}

/* Narrow screens: shrink the thumbnail so the summary keeps room */
@media (max-width: 640px) {
  .meetingThumb {
    width: 132px;
  }
}
```

- [ ] **Step 2: Confirm the removed classes are unused**

Run: `grep -rn "hasVideo\|meetingDate\|meetingDuration" app components lib`
Expected: no matches in `.tsx`/`.ts` files (these classes were only used by the old inline list markup, now removed). If any remain, they are stale references to fix.

- [ ] **Step 3: Commit**

```bash
git add app/globals.css
git commit -m "style(web): YouTube-style row layout for the meeting list"
```

---

## Task 8: Verify in the browser

No new code — confirm the three states render correctly and nothing overlaps.

- [ ] **Step 1: Build and lint**

Run: `npm run build && npm run lint && npm test`
Expected: build succeeds, lint clean, all unit tests pass.

- [ ] **Step 2: Start the dev server and load the homepage**

Use the preview tooling: start the server, open `/`, and capture a snapshot. Confirm:
  - YouTube meetings show a real thumbnail, centered play button, and duration badge.
  - File/HLS meetings (if any are published) show the info tile + play + duration.
  - No-video meetings show the info tile with location + date and a "Transcript only" tag, **no** play button or duration.
  - The location/date text in info tiles is never covered by the play button or the corner tag.
  - The event-kind filter tabs still filter the list.

- [ ] **Step 3: Confirm the speaker-count caveat**

In the rendered rows, check whether `· N speakers` appears in the meta line. If it never appears, the `/api/meetings` list payload omits `speakers`; that is acceptable graceful degradation (the design intends count-when-present). Note the outcome in the PR/commit description. If speaker count is desired and absent, that is a follow-up against the ev-accounts API — out of scope for this plan.

- [ ] **Step 4: Check a narrow viewport**

Resize the preview to ~375px wide and snapshot. Confirm the thumbnail shrinks (132px) and the summary text stays readable without horizontal overflow.

- [ ] **Step 5: Capture proof**

Take a screenshot of the redesigned list (desktop) and one at the narrow viewport to share with the user.

---

## Self-review notes

- **Spec coverage:** rows (Tasks 5–7), three thumbnail states (Task 3 logic + Task 4 render + Task 7 styles), info-tile top band clear of overlays (Task 4 + Task 7), speaker count in meta line with graceful degradation (Task 5 + Task 8 Step 3), `thumbnail_url` forward-compat seam (Task 3), component decomposition (Tasks 3–6), reused `.eventKind` badge + theme variables (Tasks 5, 7). Out-of-scope items (frame extraction pipeline, other pages) intentionally have no tasks.
- **Testing approach:** pure logic (`formatDuration`, `buildThumbnailModel`) is unit-tested; components are verified via build + browser, matching the chosen Vitest-for-logic approach.
- **Type consistency:** `ThumbnailModel` fields (`imageSrc`, `showPlay`, `duration`, `transcriptOnly`, `location`, `date`) are defined in Task 3 and consumed unchanged in Task 4. `buildThumbnailModel` / `youtubeThumbnailUrl` names match across helper, tests, and component. `formatDuration` signature matches the original it replaces.
