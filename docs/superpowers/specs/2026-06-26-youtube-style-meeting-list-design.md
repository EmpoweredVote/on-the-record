# YouTube-style meeting list — design

**Date:** 2026-06-26
**Status:** Approved for planning
**Scope:** `web/` (homepage meeting list only)

## Goal

Make the homepage meeting list easier to skim. Replace the current text-only
rows with YouTube-style horizontal rows: a video thumbnail on the left and a
one-line summary plus metadata on the right.

## Current state

`web/app/page.tsx` renders a header, tagline, site nav, and `MeetingListClient`.
`web/app/MeetingListClient.tsx` derives event-kind filter tabs and renders a
flat `<ul class="meetingList">` where each `<li><a>` stacks: title, event-kind,
date, optional duration, an optional "▶ video" badge, and the one-line
`summary_preview`. Styling lives in `web/app/globals.css` (`.meetingList`,
`.meetingTitle`, `.meetingPreview`, etc.).

Data comes from `fetchMeetings()` (`web/lib/queries.ts`) hitting
`GET {EV_ACCOUNTS_URL}/api/meetings` and mapping each row through `mapMeeting`.
Relevant `Meeting` fields (`web/lib/types.ts`): `meeting_id`, `title`, `city`,
`event_kind`, `meeting_date`, `playback_kind` (`"youtube" | "file" | "hls" |
null`), `playback_url` (for YouTube this **is** the video ID — confirmed by
`MeetingView.tsx:172` passing it straight to `YouTubePlayer` as `videoId`),
`duration_seconds`, `summary_preview`, and `speakers[]`.

## What changes

The meeting list becomes a list of horizontal **rows**. The page header,
tagline, site nav, and event-kind filter tabs are unchanged.

### Row anatomy

Each row is a single clickable link to `/meetings/{meeting_id}`, laid out as
`[ thumbnail | text column ]`.

**Thumbnail** — 16:9, ~200px wide on desktop, fixed-size, `border-radius: 8px`,
`overflow: hidden`. It has three states driven by the meeting's video data:

1. **YouTube** (`playback_kind === "youtube"`): background image
   `https://img.youtube.com/vi/{playback_url}/hqdefault.jpg`, a centered
   circular play button overlay, and a duration badge bottom-right.
2. **Non-YouTube video** (`playback_kind === "file" | "hls"`): the **info tile**
   (see below), a centered play button overlay, and a duration badge
   bottom-right.
3. **No video** (`playback_kind === null`): the **info tile**, **no** play
   button, **no** duration badge, and a "Transcript only" tag bottom-right
   (small icon + label).

**Info tile** (states 2 and 3): a light gray placeholder (`#f1f5f9`-equivalent
via theme tokens) with a **top band** spanning the tile:
- **Location** (left): `meeting.city` when present; otherwise fall back to the
  meeting title via `meetingTitle(m)`. Clamped to 2 lines with ellipsis.
- **Date** (right): formatted via `formatMeetingDate`, `white-space: nowrap`.

The top band keeps text out of the way of the centered play button and the
bottom-right corner, so overlays never cover the location/date. Verified in the
mockup that a long location wrapping to two lines still clears the play button.

**Thumbnail source precedence** (forward-compatible): if a meeting ever carries
an explicit thumbnail URL, prefer it; else derive the YouTube frame; else render
the info tile. This lets a future frame-extraction pipeline light up real
thumbnails for non-YouTube videos with no front-end change. No `thumbnail_url`
field exists today, so the helper just leaves a clearly-marked seam for it.

**Text column:**
- Event-kind **badge** (pill), via `eventKindLabel(m.event_kind)`.
- **Title**, via `meetingTitle(m)` (bold).
- **Meta line** (muted): `formatMeetingDate(m.meeting_date)` followed by
  `· N speakers` **only when** speaker data is available (see caveat 1).
- **Summary**: `m.summary_preview`, clamped to ~2 lines.

### Component structure

- Extract a **`MeetingCard`** component that renders one row given a `Meeting`.
  It owns the text column and delegates the thumbnail.
- Extract a **thumbnail helper** (a `MeetingThumbnail` component or a
  `thumbnailFor(meeting)` function in `web/lib/`) that encodes the
  precedence/state logic above and returns the right rendering. Keeping this in
  one place makes the YouTube-ID → URL derivation and the three-state branching
  independently testable.
- `MeetingListClient` keeps its current responsibility: derive tabs, filter by
  active kind, and map the shown meetings to `MeetingCard`s.

### Styling

Update `web/app/globals.css`. Replace the stacked `.meetingList li a` flex rules
with a row layout (`display: flex; gap`), add thumbnail/info-tile/overlay/badge
classes, and use existing theme variables (`--surface`, `--border`, `--muted`,
`--accent`, `--accent-soft`, `--shadow`) so light/dark themes keep working.
Remove the now-unused `.hasVideo` text badge. Rows must collapse gracefully on
narrow screens (thumbnail above text, or a smaller thumbnail) — responsive
behavior to be confirmed during implementation against the existing breakpoints.

## Out of scope

- **Non-YouTube thumbnail extraction.** Generating real frames for file/HLS
  videos (ffmpeg during processing + a storage bucket + a `thumbnail_url` column
  on `meetings.meetings` + publish/API/web-type plumbing) is a separate backend
  follow-up. This design only leaves the front-end seam for it.
- Search results, people, and topics pages keep their current presentation.

## Caveats / verification during implementation

1. **Speaker count** depends on `GET /api/meetings` including the `speakers`
   array. `mapMeeting` already maps `m.speakers ?? []`, but the list endpoint
   may omit it (it may be a detail-only field). Verify against the live API:
   if speakers aren't present in the list payload, show the count only when
   `m.speakers.length > 0` and otherwise omit the `· N speakers` segment
   (graceful degradation — never render "0 speakers").
2. **YouTube thumbnail availability.** `hqdefault.jpg` exists for effectively all
   public videos; if a frame 404s the browser shows the gray tile background
   underneath, which is acceptable. (No build-time fetch — these are
   `<img>`/background loads in a static export.)
3. **Static export.** `web/` is a static-export Next.js app; thumbnails load as
   plain images/backgrounds (no `next/image` optimization server). Use lazy
   loading where appropriate.

## Testing

- Unit-test the thumbnail helper: YouTube ID → correct `img.youtube.com` URL;
  `file`/`hls` → info tile + play + duration; `null` → info tile + transcript-only,
  no play/duration; explicit thumbnail URL (future) takes precedence.
- Verify the rendered list in the browser via the preview workflow across the
  three states and a narrow viewport.
