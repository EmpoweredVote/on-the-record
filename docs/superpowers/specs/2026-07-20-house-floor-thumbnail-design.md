# Design: Thumbnails for House-floor (HLS) meetings

**Date:** 2026-07-20
**Status:** Approved (pending spec review)
**Scope:** `src/thumbnail.py` + `backfill_thumbnails.py` (+ tests). No web/ev-accounts/schema changes.

## Problem

House-floor meetings (`run_local --house-floor DATE`) get no thumbnail, so they
show no image in the library/meeting list. `thumbnail_url` is `None` for
`2026-07-16-house-floor`.

`attach_thumbnail` (`src/thumbnail.py`) produces a thumbnail one of two ways:

1. **Local video file** — `find_video_file` locates `source.{mp4,m4v,…}` on disk
   (or a local input path) and `extract_thumbnail` grabs a frame via ffmpeg.
2. **Artwork** — for audio-only sources, download `processing_metadata.source_image_url`
   (the podcast case).

House-floor meetings have **neither**: the source is an HLS `.m3u8` stream (only
`source.wav` is kept locally, no video file), and the House Clerk feed carries no
image (`image`/`thumbnailUrl` are absent in its BroadcastEvent JSON-LD). So
`attach_thumbnail` returns without setting a thumbnail.

## Key facts established

- ffmpeg reads the HLS manifest URL directly: `ffmpeg -ss 30 -i <manifest.m3u8>
  -vf "thumbnail=…" -frames:v 1` produced a valid ~63 KB JPEG in ~5.5s.
- The stored HLS URL is `meeting.processing_metadata.source_audio_url`
  (set by `run_local` for `--house-floor`; `.m3u8`).
- A frame near the start (~10s, the current seek point) is the House's clean
  **pre-session title slate** ("2nd Session of the 119th Congress … adjourned
  until 9:00 a.m. Thursday July 16, 2026"). This is the chosen thumbnail content —
  clean, always present, and self-labeling. No attempt to seek past gavel-in.

## Design

### 1. Core: HLS thumbnail source (`src/thumbnail.py`)

Add a helper:

```python
def streamable_video_url(meeting) -> Optional[str]:
    """A remote video URL ffmpeg can pull a frame from when no local video file
    exists — currently the House Clerk HLS manifest stored as source_audio_url.
    Returns the URL for an .m3u8 source, else None."""
    pm = getattr(meeting, "processing_metadata", None)
    url = getattr(pm, "source_audio_url", None) if pm else None
    if url and url.split("?", 1)[0].lower().endswith(".m3u8"):
        return url
    return None
```

In `attach_thumbnail`, change the source-selection precedence to:

1. **Local video file** (`find_video_file`) — unchanged, preferred when present.
2. **HLS stream URL** (`streamable_video_url`) — NEW. When there is no local
   video but a streamable `.m3u8` exists, treat that URL as the ffmpeg input:
   `extract_thumbnail(hls_url, meeting.clip_start_seconds, meeting.duration_seconds, out)`.
3. **Artwork** (`source_image_url`) — unchanged fallback for audio-only sources.

`extract_thumbnail` already takes the input path/URL as a string and passes it to
ffmpeg `-i`, so no change is needed there. The seek point comes from the existing
`thumbnail_seek_start(clip_start, duration)`; for House floor `clip_start` is
`None` → ~10s → the title slate.

This automatically covers **all future House-floor meetings** because both
`attach_thumbnail` callers (`run_local` processing/publish and
`gui/publish_api.apply_publish`) already invoke it.

### 2. Backfill (`backfill_thumbnails.py`)

The current selector `meetings_needing_thumbnail` gates on
`find_video_file(mdir, "")`, which is `None` for HLS meetings — so House-floor
meetings would be skipped even after the core fix. Extend it to also include a
meeting when it has no local video and no `thumbnail.jpg` but DOES have a
streamable HLS source. Because detecting the HLS source requires the meeting's
`source_audio_url`, load the meeting (via the existing `_meeting_for` helper /
`_load_meeting_ctx`) to check `streamable_video_url`; keep the cheap dir-only
check as the fast path for local-video meetings.

Persisting `thumbnail_url`: `attach_thumbnail` sets `meeting.thumbnail_url` on the
in-memory object. The backfill must persist it to `transcript_named.json` (write
the meeting back) so the GUI library and any later publish see it. (The current
script does not persist; this is required for the HLS case to be useful.)

### 3. Apply to `2026-07-16-house-floor` now

- Run the extended backfill for this meeting: extract → upload
  (`SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY` required) → set + persist
  `thumbnail_url`.
- Determine publish state in the plan: if the meeting is published, re-publish
  (GUI publish path or `publish_meeting`, which writes `thumbnail_url` to the DB)
  so the live site shows it; if not published, persisting locally is enough and a
  future publish will include it.

## Error handling

Unchanged philosophy — entirely best-effort/non-fatal:

- No local video + no HLS + no artwork → `thumbnail_url` stays `None` (today's
  behavior). Non-House meetings are unaffected.
- ffmpeg missing / extraction fails / HLS expired → `extract_thumbnail` returns
  `None`, logged, no thumbnail. Never raises (already wrapped in try/except).
- Missing storage creds → `upload_thumbnail` logs and skips; local
  `thumbnail.jpg` still written.

## Testing

- **Unit (`tests/`, pytest)** — monkeypatch `find_video_file`, `extract_thumbnail`,
  `upload_thumbnail`:
  - HLS source (no local video, `source_audio_url` ends `.m3u8`) → `extract_thumbnail`
    is called with the manifest URL and `meeting.thumbnail_url` is set to the
    uploaded URL.
  - Local video present → `extract_thumbnail` is called with the local path (HLS
    not used); precedence preserved.
  - No local video, no HLS, no artwork → `thumbnail_url` stays `None`.
  - `streamable_video_url`: returns the URL for `.m3u8` (incl. with query string),
    `None` for non-HLS / missing / audio-file sources.
- **Manual/live** — run the backfill for `2026-07-16-house-floor`; confirm
  `thumbnail.jpg` is created, uploaded, `thumbnail_url` persisted, and the image
  renders (library card and, if published, the live meeting list).

## Files touched

- `src/thumbnail.py` — `streamable_video_url` helper + HLS branch in `attach_thumbnail`.
- `backfill_thumbnails.py` — include HLS-source meetings in the selector; persist
  `thumbnail_url` after attaching.
- `tests/test_thumbnail*.py` — unit coverage.

No changes to `src/publish.py` (it already writes `meeting.thumbnail_url`), the web
app, ev-accounts, or the DB schema.
