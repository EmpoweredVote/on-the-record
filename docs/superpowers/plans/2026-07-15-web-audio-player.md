# Web Audio Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a working audio player (artwork + native controls, click-to-seek) on the meeting page for `playback_kind === "audio"` podcast/radio episodes.

**Architecture:** Follow the existing one-component-per-provider pattern behind the `PlayerAdapter` interface. Add a dedicated `AudioPlayer` component and one `"audio"` branch in `MeetingView`. The seek/adapter logic is extracted into a node-testable factory (`web/lib/audioAdapter.ts`) so it fits the repo's node-only, `lib/`-only vitest harness without new test infra; the component itself stays a thin wrapper verified by build/typecheck.

**Tech Stack:** Next.js (static export) + React + TypeScript, vitest (node env), plain CSS in `app/globals.css`. No new dependencies.

**Design reference:** `docs/superpowers/specs/2026-07-15-web-audio-player-design.md`

**Deviations from the spec (both discovered while grounding the plan, both improvements):**
1. **Testing:** the spec assumed a jsdom component test, but the repo's vitest is `environment: "node"` with `include: ["lib/**/*.test.ts"]` and no `@testing-library`. Adding jsdom would be new infra/scope. Instead, extract the adapter logic into `web/lib/audioAdapter.ts` and unit-test *that* in node (the only logic worth testing); the component is a thin wrapper.
2. **Styling:** the spec said "reuse `.playerBox`," but `.playerBox` forces `aspect-ratio: 16/9` + black background (video-shaped). Use a dedicated `.audioBox` (natural height, square artwork + full-width control bar) instead.

**All work is in `web/`. Run commands from `web/` (`cd web`).**

---

## File Structure

**New:**
- `web/lib/audioAdapter.ts` — `createAudioAdapter(el)` factory: wires a media element to the `PlayerAdapter` interface (seek/current-time/playing). Pure, node-testable.
- `web/lib/audioAdapter.test.ts` — unit tests for the factory (fits `include: ["lib/**/*.test.ts"]`).
- `web/app/meetings/[meetingId]/players/AudioPlayer.tsx` — the audio player component (artwork `<img>` + native `<audio controls>`), uses `createAudioAdapter`.

**Modified:**
- `web/app/meetings/[meetingId]/MeetingView.tsx` — import `AudioPlayer`; add the `"audio"` branch to the player-switch ternary.
- `web/app/globals.css` — add `.audioBox` / `.audioArt` rules near `.playerBox`.

---

## Task 1: Node-testable audio adapter factory

**Files:**
- Create: `web/lib/audioAdapter.ts`
- Test: `web/lib/audioAdapter.test.ts`

- [ ] **Step 1: Write the failing test**

Create `web/lib/audioAdapter.test.ts`:

```ts
import { describe, it, expect, vi } from "vitest";
import { createAudioAdapter, type MediaLike } from "./audioAdapter";

function fakeEl(over: Partial<MediaLike> = {}): MediaLike {
  return {
    currentTime: 0,
    paused: true,
    ended: false,
    play: vi.fn(() => Promise.resolve()),
    ...over,
  };
}

describe("createAudioAdapter", () => {
  it("seekTo sets currentTime and starts playback", () => {
    const el = fakeEl();
    createAudioAdapter(el).seekTo(42);
    expect(el.currentTime).toBe(42);
    expect(el.play).toHaveBeenCalledTimes(1);
  });

  it("seekTo swallows a rejected play() and still seeks", () => {
    const el = fakeEl({ play: vi.fn(() => Promise.reject(new Error("autoplay blocked"))) });
    expect(() => createAudioAdapter(el).seekTo(5)).not.toThrow();
    expect(el.currentTime).toBe(5);
  });

  it("getCurrentTime reflects the element", () => {
    expect(createAudioAdapter(fakeEl({ currentTime: 12.5 })).getCurrentTime()).toBe(12.5);
  });

  it("isPlaying is true only when not paused and not ended", () => {
    expect(createAudioAdapter(fakeEl({ paused: false, ended: false })).isPlaying()).toBe(true);
    expect(createAudioAdapter(fakeEl({ paused: true })).isPlaying()).toBe(false);
    expect(createAudioAdapter(fakeEl({ paused: false, ended: true })).isPlaying()).toBe(false);
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run lib/audioAdapter.test.ts`
Expected: FAIL — cannot resolve `./audioAdapter` (module doesn't exist).

- [ ] **Step 3: Write minimal implementation**

Create `web/lib/audioAdapter.ts`:

```ts
import type { PlayerAdapter } from "@/app/meetings/[meetingId]/players/adapter";

// Minimal media-element surface the audio player needs. Declaring it here lets
// the adapter be unit tested in the node env without a DOM (HTMLAudioElement
// structurally satisfies it).
export interface MediaLike {
  currentTime: number;
  paused: boolean;
  ended: boolean;
  play(): Promise<void> | void;
}

// Wire a media element to the shared PlayerAdapter used by MeetingView for
// click-to-seek. seekTo starts playback on seek (matching FilePlayer); a
// rejected play() (e.g. autoplay policy) is swallowed so a seek never throws.
export function createAudioAdapter(el: MediaLike): PlayerAdapter {
  return {
    seekTo: (seconds: number) => {
      el.currentTime = seconds;
      Promise.resolve(el.play()).catch(() => {});
    },
    getCurrentTime: () => el.currentTime,
    isPlaying: () => !el.paused && !el.ended,
  };
}
```

Note: `PlayerAdapter` is a type-only import, erased at compile time — no runtime coupling from `lib/` to `app/`. The `@/*` → `./*` alias is configured in both `tsconfig.json` and `vitest.config.ts`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run lib/audioAdapter.test.ts`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add web/lib/audioAdapter.ts web/lib/audioAdapter.test.ts
git commit -m "feat(web): node-testable audio PlayerAdapter factory"
```

---

## Task 2: AudioPlayer component

**Files:**
- Create: `web/app/meetings/[meetingId]/players/AudioPlayer.tsx`

There is no component-level test harness in this repo (vitest is node-env, `lib/`-only, no `@testing-library`). This task is verified by typecheck/build/lint in Task 5. The tested logic lives in `createAudioAdapter` (Task 1).

- [ ] **Step 1: Create the component**

Create `web/app/meetings/[meetingId]/players/AudioPlayer.tsx`:

```tsx
"use client";

import { useEffect, useRef } from "react";
import type { PlayerAdapter } from "./adapter";
import { createAudioAdapter } from "@/lib/audioAdapter";

// Native <audio> player for podcast/radio episodes (playback_kind "audio").
// Shows the episode/show artwork as album art above the control bar when a
// thumbnail is present; controls-only otherwise. Wires the shared PlayerAdapter
// so transcript/vote click-to-seek jumps the audio.
export default function AudioPlayer({
  src,
  thumbnailUrl,
  onAdapter,
}: {
  src: string;
  thumbnailUrl?: string | null;
  onAdapter: (adapter: PlayerAdapter) => void;
}) {
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.src = src;
    onAdapter(createAudioAdapter(audio));
  }, [src, onAdapter]);

  return (
    <div className="audioBox">
      {thumbnailUrl ? (
        // Static export has no image optimizer; an intentional <img> is correct here.
        // eslint-disable-next-line @next/next/no-img-element
        <img className="audioArt" src={thumbnailUrl} alt="" loading="lazy" />
      ) : null}
      <audio ref={audioRef} controls preload="metadata" />
    </div>
  );
}
```

(The `<img>` + eslint-disable pattern mirrors `web/components/MeetingThumbnail.tsx`, which does the same for the static-export build.)

- [ ] **Step 2: Typecheck the new file compiles**

Run: `cd web && npx tsc --noEmit`
Expected: no errors (exit 0).

- [ ] **Step 3: Commit**

```bash
git add web/app/meetings/\[meetingId\]/players/AudioPlayer.tsx
git commit -m "feat(web): AudioPlayer component (artwork + native controls)"
```

---

## Task 3: Wire the "audio" branch into MeetingView

**Files:**
- Modify: `web/app/meetings/[meetingId]/MeetingView.tsx`

- [ ] **Step 1: Add the import**

In `web/app/meetings/[meetingId]/MeetingView.tsx`, the player imports currently read:

```tsx
import YouTubePlayer from "./players/YouTubePlayer";
import FilePlayer from "./players/FilePlayer";
```

Add below them:

```tsx
import AudioPlayer from "./players/AudioPlayer";
```

- [ ] **Step 2: Add the "audio" branch to the player switch**

The current player-switch ternary ends like this:

```tsx
    ) : (meeting.playback_kind === "file" || meeting.playback_kind === "hls") &&
      meeting.playback_url ? (
      <FilePlayer
        src={meeting.playback_url}
        kind={meeting.playback_kind}
        onAdapter={onAdapter}
      />
    ) : null;
```

Replace the final `) : null;` so the audio branch is inserted before the fallback:

```tsx
    ) : (meeting.playback_kind === "file" || meeting.playback_kind === "hls") &&
      meeting.playback_url ? (
      <FilePlayer
        src={meeting.playback_url}
        kind={meeting.playback_kind}
        onAdapter={onAdapter}
      />
    ) : meeting.playback_kind === "audio" && meeting.playback_url ? (
      <AudioPlayer
        src={meeting.playback_url}
        thumbnailUrl={meeting.thumbnail_url}
        onAdapter={onAdapter}
      />
    ) : null;
```

`meeting.thumbnail_url`, `meeting.playback_url`, and `meeting.playback_kind` are already on the `Meeting` type (`web/lib/types.ts`) and populated by `mapMeeting` (`web/lib/queries.ts`) — no type or query change.

- [ ] **Step 3: Typecheck**

Run: `cd web && npx tsc --noEmit`
Expected: no errors (exit 0).

- [ ] **Step 4: Commit**

```bash
git add web/app/meetings/\[meetingId\]/MeetingView.tsx
git commit -m "feat(web): render AudioPlayer for playback_kind audio"
```

---

## Task 4: Audio player styling

**Files:**
- Modify: `web/app/globals.css`

- [ ] **Step 1: Add the CSS**

In `web/app/globals.css`, immediately AFTER the existing `.playerBox iframe, .playerBox video, .playerBox > div { ... }` rule (ends around line 507, just before `.noPlayer`), insert:

```css
.audioBox {
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 14px;
}

.audioArt {
  width: 100%;
  max-width: 320px;
  aspect-ratio: 1 / 1;
  object-fit: cover;
  border-radius: 10px;
  box-shadow: var(--shadow);
  display: block;
}

.audioBox audio {
  width: 100%;
}
```

Rationale: unlike `.playerBox` (forced `16/9`, black background — video-shaped), `.audioBox` grows to its natural height and centers a square artwork above a full-width native control bar. `--shadow` is the same variable `.playerBox` uses.

- [ ] **Step 2: Verify the stylesheet still builds**

Run: `cd web && npm run build`
Expected: build succeeds (Next compiles global CSS as part of the build).

- [ ] **Step 3: Commit**

```bash
git add web/app/globals.css
git commit -m "style(web): audioBox artwork + control-bar layout"
```

---

## Task 5: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Unit tests**

Run: `cd web && npm test`
Expected: all pass, including the new `lib/audioAdapter.test.ts` (4 tests).

- [ ] **Step 2: Typecheck + lint**

Run: `cd web && npx tsc --noEmit && npm run lint`
Expected: no type errors; lint clean (no `@next/next/no-img-element` error — the disable comment is in place).

- [ ] **Step 3: Production build**

Run: `cd web && npm run build`
Expected: build succeeds.

- [ ] **Step 4: Visual smoke check (manual / browser preview)**

Only if a published `audio` meeting is reachable by the running `web` app (or via a locally stubbed meeting). Load a meeting whose `playback_kind === "audio"` and confirm: artwork renders above a working audio control bar, and clicking a transcript timestamp seeks the audio. If no audio meeting is published yet, this step is deferred to post-publish verification (note it in the completion report rather than marking it done).

- [ ] **Step 5: Commit (only if any verification fix was needed)**

If Steps 1-3 required a fix, commit it:

```bash
git add -A web
git commit -m "fix(web): address audio-player verification findings"
```

Otherwise nothing to commit — the feature is complete.

---

## Self-Review (completed during authoring)

**Spec coverage:**
- Render audio player for `playback_kind === "audio"` → Tasks 2, 3. ✓
- Artwork above native controls, controls-only fallback → Task 2 (`thumbnailUrl ? <img> : null`) + Task 4 CSS. ✓
- Click-to-seek via `PlayerAdapter` → Task 1 (`createAudioAdapter`) + Task 3 (`onAdapter`). ✓
- Front-end only, no ev-accounts change → data flow already in `mapMeeting` (noted in Task 3). ✓
- No custom transport UI / no autoplay → Task 2 uses native `<audio controls>`, `seekTo`'s `play()` only fires on user-driven seek. ✓
- Testing that fits the harness → Task 1 (node-env `lib/` test). Deviation from the spec's jsdom idea documented at top. ✓
- Verify-not-build: ev-accounts emits `audio` kind → Task 5 Step 4 + spec Verification section. ✓

**Placeholder scan:** none. Task 5 Step 4's conditional ("if a published audio meeting is reachable") is a real environmental gate, not a vague instruction — it names exactly what to do in each case.

**Type consistency:** `MediaLike` and `createAudioAdapter` names match across Tasks 1 and 2. `AudioPlayer` prop names (`src`, `thumbnailUrl`, `onAdapter`) match between Task 2 (definition) and Task 3 (usage). `PlayerAdapter` is the existing interface, imported (type-only) in both `lib/audioAdapter.ts` and `AudioPlayer.tsx`.
