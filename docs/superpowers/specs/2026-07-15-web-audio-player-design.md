# Web Audio Player — Design

**Date:** 2026-07-15
**Status:** Approved design, pending implementation plan
**Scope:** `on-the-record/web/` (front-end only — no ev-accounts change)

## Problem

Podcast/radio episodes now ingest and publish with `playback_kind = "audio"` and
the enclosure MP3 as `playback_url` (shipped in the podcast-ingestion feature).
But the meeting detail page's player switch (`web/app/meetings/[meetingId]/MeetingView.tsx`)
only handles `"youtube"`, `"file"`, and `"hls"` — an `"audio"` meeting falls
through to `player = null`. Result: a published podcast renders with **no player
at all**, and because no player wires up the `PlayerAdapter`, its transcript
timestamps are **not click-to-seek**.

This adds an audio player so podcasts are playable on the site and their
transcripts are seekable, matching the existing YouTube/file/HLS experience.

## Goals

- Render a working audio player for `playback_kind === "audio"` meetings.
- Show the episode/show **artwork** (`meeting.thumbnail_url`) as album art above a
  native audio control bar; fall back to controls-only when a meeting has no
  thumbnail.
- Wire the standard `PlayerAdapter` so **click-to-seek** on transcript segments,
  section topics, and votes jumps the audio (same as the other players).
- Front-end only, following the established one-component-per-provider pattern.

## Non-Goals

- **No custom transport UI** (custom play/scrubber). Use the native
  `<audio controls>` bar. A bespoke UI is deliberately out of scope for v1.
- **No ev-accounts / API changes.** The meeting payload already carries
  everything needed (see Data Flow).
- **No autoplay.** Playback starts on user interaction (or on `seekTo`, matching
  `FilePlayer`).

## Architecture

The meeting page already uses a provider-per-component pattern behind a common
`PlayerAdapter` interface (`web/app/meetings/[meetingId]/players/adapter.ts`):

```ts
export interface PlayerAdapter {
  seekTo(seconds: number): void;
  getCurrentTime(): number;
  isPlaying(): boolean;
}
```

`MeetingView` builds a `player` element by switching on `meeting.playback_kind`
and passes each provider an `onAdapter` callback; the transcript/vote click
handlers call `seekTo` through the returned adapter. Adding a provider is "one
new component here and zero changes to the transcript logic" (per the adapter's
own doc comment). We follow that: a new `AudioPlayer` component + one new branch
in `MeetingView`.

### Chosen approach: dedicated `AudioPlayer` component

A new `AudioPlayer.tsx` beside `YouTubePlayer.tsx` / `FilePlayer.tsx`, rather
than extending `FilePlayer` to a third `kind`. Rationale: `FilePlayer` is a
focused `<video>` player; audio is a different element (`<audio>`) with a
different visual (artwork, no video frame). Extending it would add an
artwork-only prop and an element switch that muddy its single responsibility.
The adapter comment explicitly endorses a new component per provider.

## Components

### New: `web/app/meetings/[meetingId]/players/AudioPlayer.tsx`

```
Props: {
  src: string;                       // the enclosure MP3 URL (playback_url)
  thumbnailUrl?: string | null;      // meeting.thumbnail_url (episode/show art)
  onAdapter: (adapter: PlayerAdapter) => void;
}
```

Behavior (mirrors `FilePlayer`'s non-HLS branch):
- Holds an `<audio>` ref. In a `useEffect` keyed on `[src, onAdapter]`, set
  `audio.src = src` and call `onAdapter({ seekTo, getCurrentTime, isPlaying })`:
  - `seekTo(s)` → `audio.currentTime = s; audio.play().catch(() => {})`
  - `getCurrentTime()` → `audio.currentTime`
  - `isPlaying()` → `!audio.paused && !audio.ended`
- Render a `.playerBox` containing:
  - the artwork `<img src={thumbnailUrl} alt="" />` **only when `thumbnailUrl` is
    truthy** (controls-only otherwise), and
  - `<audio ref={audioRef} controls preload="metadata" />`.
- No `hls.js`, no autoplay, no custom controls.

### Modified: `web/app/meetings/[meetingId]/MeetingView.tsx`

Add an `"audio"` branch to the existing player-switch ternary, before the final
`: null`:

```tsx
) : meeting.playback_kind === "audio" && meeting.playback_url ? (
  <AudioPlayer
    src={meeting.playback_url}
    thumbnailUrl={meeting.thumbnail_url}
    onAdapter={onAdapter}
  />
) : null;
```

Import `AudioPlayer` alongside the existing player imports. No other logic
changes — `onAdapter`, `seekToTime`, and the transcript/vote click handlers are
provider-agnostic and already in place.

### Styling

Reuse the existing `.playerBox` class. Add one small CSS rule for the artwork
image (constrain `max-width` / height so it reads as album art, centered,
sensible rounding) in whatever stylesheet currently defines `.playerBox` /
player styles. No new dependencies, no CSS framework changes.

## Data Flow (already in place — no changes needed)

`web/lib/queries.ts` `mapMeeting` (shared by both `fetchMeetings` and
`fetchMeeting`) already maps every field the player needs:

| Front-end field | API field | Notes |
|---|---|---|
| `playback_kind` | `playbackKind` | `"audio"` passes through |
| `playback_url` | `videoUrl` | the enclosure MP3 for an audio meeting |
| `thumbnail_url` | `thumbnailUrl` | the resolver-captured artwork |

So the detail-page `meeting` object carries `playback_kind`, `playback_url`, and
`thumbnail_url` today. No type change, no query change, no ev-accounts change.

## Error / Edge Handling

- **No thumbnail:** render controls-only (no `<img>`). Never render a broken
  image.
- **Unplayable / missing MP3:** the native `<audio>` element shows its own error
  state; we do not add custom error UI (parity with `FilePlayer`).
- **`seekTo` before playback:** setting `currentTime` then `play()` is safe on a
  loaded `<audio>`; `play()` rejection is swallowed (matches `FilePlayer`).
- **Non-audio meetings:** untouched — the new branch is gated on
  `playback_kind === "audio" && playback_url`.

## Testing

Add `web/app/meetings/[meetingId]/players/AudioPlayer.test.tsx` (vitest + jsdom;
this is the first player test in the repo):

1. Renders an `<audio>` with the given `src` for an audio meeting.
2. Renders the artwork `<img>` when `thumbnailUrl` is provided; omits it when
   `null`.
3. `onAdapter` is called, and the returned `seekTo(42)` sets the audio element's
   `currentTime` to 42. (`play()` is stubbed; jsdom does not implement media
   playback, so assert on `currentTime` only.)

If a lightweight render of the `MeetingView` `"audio"` branch is feasible within
the existing test setup, add a case asserting it mounts `AudioPlayer` for an
`audio` meeting; otherwise the component-level tests above are sufficient.

## Verification (post-implementation, not a build step)

- `web/` build + `vitest` pass; lint clean.
- Confirm the ev-accounts `/api/meetings/:id` payload carries
  `playback_kind: "audio"` and the MP3 as `videoUrl` for a published podcast
  (same columns/serialization as `file`/`hls`, so expected to pass through — but
  verify against a real published audio meeting or the ev-accounts serializer
  before declaring the site end-to-end done).
- Visually: an audio meeting shows artwork + a working control bar, and clicking
  a transcript timestamp seeks the audio.

## Files Touched

| File | Change |
|---|---|
| `web/app/meetings/[meetingId]/players/AudioPlayer.tsx` | **new** — audio player component |
| `web/app/meetings/[meetingId]/MeetingView.tsx` | add the `"audio"` player branch + import |
| `web/app/meetings/[meetingId]/players/AudioPlayer.test.tsx` | **new** — component tests |
| player/`.playerBox` stylesheet | one artwork CSS rule |

## Open Items for the Plan

- Locate the stylesheet that defines `.playerBox` and confirm where the artwork
  rule belongs (CSS module vs global).
- Confirm the exact vitest/jsdom render harness used by existing `web/lib/*.test.ts`
  so the component test matches repo conventions (React Testing Library vs. raw
  render).
