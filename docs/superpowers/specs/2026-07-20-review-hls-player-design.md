# Design: Play the House Clerk HLS video in the review GUI

**Date:** 2026-07-20
**Status:** Approved (pending spec review)
**Scope:** Local review GUI only. No DB, ev-accounts, or web-app changes.

## Problem

A House floor meeting ingested from the House Clerk CDN
(`run_local --house-floor DATE`) has no video in the local review GUI, so an
operator identifying speakers can't see faces. The gap is *not* a missing
source — it is that the review GUI never learned to play the source we already
store.

For a House floor meeting we persist two distinct URLs (`run_local.py:879-881`):

- `meeting.audio_source` = `https://live.house.gov/?date=YYYY-MM-DD` — the human
  citation page. Not a playable video. The review GUI derives its embed solely
  from this via `extract_youtube_id`, which returns nothing, so no video shows.
- `meeting.processing_metadata.source_audio_url` = the House Clerk **HLS
  `.m3u8`** stream (public domain, Title 17 §105) — the actual video we
  transcribed from.

Because we transcribed *from* the manifest and `clip_start_seconds` is null,
segment timestamps align to the HLS stream **exactly (zero offset)**.

The published web site **already** plays this stream: `resolve_playback` maps
`.m3u8` → `("hls", url)` (`src/publish.py`), and `FilePlayer.tsx` plays `kind:
"hls"` via hls.js (Safari natively). So no web/DB work is needed — this is a
review-GUI-only fix.

## Goal

In the review GUI, when a meeting has an HLS video source, play that stream in
the media panel with working click-to-seek, so the operator sees faces from the
rich original source. This fixes every House-floor meeting automatically, with
no per-meeting manual step.

Out of scope (deliberately deferred): attaching a YouTube URL + alignment
offset as a fallback for when the House CDN expires a stream. Revisit only if
CDN retention becomes a real problem.

## Design

### Data flow

`review_api.build_review_page` already computes `youtube_id`, `media_kind`, and
per-card `clip_seeks`. Add an `hls_url` alongside them:

- Resolve playback with the existing `src.publish.playback_for_meeting(meeting)`,
  which prefers `processing_metadata.source_audio_url` and returns
  `(kind, url)`. When `kind == "hls"`, set `hls_url = url`; otherwise `None`.
  Reusing `playback_for_meeting` keeps the review GUI and the site in lockstep on
  what "the video" is.
- Treat HLS as a full-source stream for seek math:
  `is_full_source = bool(youtube_id) or bool(hls_url) or (media_kind == "video")`.
  (For House floor `clip_start_seconds` is null, so the offset is 0; this line
  keeps the general case correct if a clipped HLS meeting ever appears.)

### Precedence

Media panel selection order in `review.html`:

1. `youtube_id` → existing YouTube iframe (unchanged)
2. **`hls_url` → new HLS `<video id="player">` branch** ← new, before local video
3. `media_kind == "video"` → local `<video>` (unchanged)
4. `media_kind == "audio"` → local `<audio>` (unchanged)
5. else → "No media found"

HLS is placed above the local-file branches: it is the richest source and needs
no local disk, so it should win whenever present. A local video only appears
when there is no HLS stream (e.g. a YouTube meeting whose file was kept, or a
non-CDN source).

### Components

**`gui/models.py` — `ReviewPageData`**
Add `hls_url: Optional[str] = None` with a comment mirroring the `youtube_id`
one.

**`gui/review_api.py` — `build_review_page`**
- Import and call `playback_for_meeting`; derive `hls_url`.
- Fold `hls_url` into `is_full_source`.
- Pass `hls_url=hls_url` into `ReviewPageData(...)`.

**`gui/templates/review.html` — media panel**
- Add the `{% elif page.hls_url %}` branch producing
  `<video id="player" class="player" controls preload="metadata"
   data-hls="{{ page.hls_url }}"></video>` (no `src`; the JS attaches the
  stream). Reusing `id="player"` means the existing seek path in `review.js`
  works with no branching.

**`gui/static/review.js` — HLS attach**
- On load, if a `#player` element has `data-hls`, attach the stream:
  - If `video.canPlayType("application/vnd.apple.mpegurl")` (Safari), set
    `video.src = url` directly.
  - Otherwise load the vendored hls.js, `new Hls()`, `loadSource(url)`,
    `attachMedia(video)`.
- The existing clip-click handler (`player.currentTime = seek; player.play()`)
  is unchanged and works once the media is attached (VOD stream is seekable).

**`gui/static/hls.min.js` — vendored library**
- Copy `web/node_modules/hls.js/dist/hls.min.js` into `gui/static/` so the GUI
  works offline (no CDN dependency). Loaded lazily by `review.js` only when a
  non-Safari browser needs it, via a `<script>` injection or dynamic import of
  the static path. Pin/record the copied version in a short comment.

### Cleanup-media banner copy

The "Clean up media" confirm text mentions YouTube streaming from source. HLS
meetings also stream from source (no local video needed). Broaden the copy to
"(for streamed sources) the video streams from the source" so it is accurate for
HLS too. Cosmetic; no behavior change.

## Error handling

- **No HLS source** (`hls_url is None`): unchanged behavior — falls through to
  local video/audio/none. Non-House meetings are unaffected.
- **HLS manifest expired / network error**: hls.js emits a fatal error; the
  `<video>` simply shows no playable media. The transcript, cards, and seek
  buttons still render — review is degraded to no-video, exactly today's state.
  No crash, no blocking. (Durable YouTube fallback is the deferred follow-up.)
- **Browser without MSE and non-Safari**: `Hls.isSupported()` is false; leave
  the empty `<video>`. Rare on a dev machine.

## Testing

- **Unit (`tests/`, pytest):** extend the review-page builder tests
  (`test_review_seek.py` / `test_review*`) —
  - a meeting whose `source_audio_url` ends in `.m3u8` yields
    `hls_url` set and `is_full_source` true (seeks carry the clip offset);
  - a meeting with no HLS source yields `hls_url is None` and unchanged
    `media_kind` behavior;
  - a YouTube meeting still yields `youtube_id` and `hls_url is None`
    (precedence unchanged).
- **Manual, in-browser (verification workflow):** open
  `http://localhost:8000/meetings/2026-07-16-house-floor/review`; confirm the
  House video renders and plays, click a speaker's clip button and confirm the
  video seeks to that moment aligned with the spoken words, and check the
  console for hls.js errors.

## Files touched

- `gui/models.py` (add field)
- `gui/review_api.py` (derive + pass `hls_url`, seek math)
- `gui/templates/review.html` (HLS branch + banner copy)
- `gui/static/review.js` (attach hls.js)
- `gui/static/hls.min.js` (vendored, new)
- `tests/test_review*.py` (coverage)

No changes to `src/`, the meetings DB schema, ev-accounts, or `web/`.
