# Meeting thumbnail extraction — design

**Date:** 2026-06-26
**Status:** Approved for planning
**Scope:** Python pipeline + Supabase Storage + DB + `web/`; ev-accounts API (separate repo) as a contract.

## Goal

Generate a real thumbnail for each meeting from the **kept section** of its source
video (the clip window we actually transcribed), host it, and show it on the
homepage list — superseding YouTube's auto-picked frame. This is follow-up #1.

## Why

The homepage redesign currently uses `img.youtube.com/.../hqdefault.jpg` for
YouTube meetings and a generated info tile for everything else. A frame pulled
from the section we kept is more representative (and consistent across all video
sources) than YouTube's auto-pick, and it gives non-YouTube videos a real
thumbnail instead of the info-tile fallback.

## Decisions (settled)

- **Hosting:** a public Supabase Storage bucket (`meeting-thumbnails`); the public
  URL is stored in a new `meetings.meetings.thumbnail_url` column.
- **Coverage:** every meeting with a source video, **including YouTube** — the
  extracted frame supersedes the YouTube frame. No-video meetings keep the info
  tile.
- **Frame selection:** seek a short way into the kept section, then use ffmpeg's
  built-in `thumbnail` filter to auto-pick a representative frame (skips
  black/fade/flat frames). No custom scoring logic.
- **Backfill:** out of scope — new/re-run meetings get thumbnails; existing ones
  get them on re-publish (`republish_all.sh`).

## Relevant existing code

- `src/checkpoint.py` — `PipelineState` holds `meeting_dir`, `clip_start_seconds`,
  `clip_end_seconds` (persisted to `pipeline_state.json`).
- `src/ingest.py:92` — the downloaded source video is written to
  `meeting_dir/source.mp4` (run_local also recognizes `.m4v/.mkv/.webm/.avi/.mov`).
- `duration_seconds` (from ingest) is the **clipped** audio length, i.e. the
  length of the kept section — used directly for the seek math.
- `src/publish.py` — upserts `meetings.meetings` via psycopg2 + `DATABASE_URL`.
- Migrations through `0004_clip_window.sql` (added the clip columns); thumbnails
  are `0005`.
- `requests>=2.28` is available (used in `src/download.py`).
- `web/lib/thumbnail.ts` already has the seam comment for a future `thumbnail_url`.

## Architecture

### 1. Frame extraction — `src/thumbnail.py` (new)

- **`thumbnail_seek_start(clip_start, clip_duration) -> float`** (pure, unit-tested):
  `(clip_start or 0) + min(10.0, 0.10 * (clip_duration or 0))` — a short skip into
  the kept section (past the very intro), capped at 10s. For a meeting with no clip
  window, `clip_start` is 0 so it seeks ~10% in (≤10s).
- **`extract_thumbnail(video_path, clip_start, clip_duration, out_path) -> Path | None`:**
  runs, roughly,
  `ffmpeg -y -ss <seek_start> -i <video> -vf "thumbnail=n=300,scale=640:-2" -frames:v 1 -q:v 3 <out_path>`
  The `thumbnail=n=300` filter analyzes ~300 frames from the seek point and emits
  the most representative one; `scale=640:-2` keeps aspect (the card displays at
  ~200px, 640 covers retina). Returns `out_path` on success, or `None` (logging a
  warning) when there is no video stream, ffmpeg is missing, or the command fails.
  **Never raises into the pipeline.**

### 2. Pipeline wiring — `run_local.py`

Just before `publish_meeting(...)`, where `meeting_dir`, the source video
(`find_video_file`, run_local.py ~410–424), and the clip state are all available:
extract `meeting_dir/thumbnail.jpg`, and if it was produced, upload it
(`upload_thumbnail`) and set `meeting.thumbnail_url` to the returned URL. Then
publish. (Upload lives here rather than in `publish.py` because `publish_meeting`
takes only a `Meeting`, not `meeting_dir`, and keeping the DB writer network-free
is cleaner.) The whole step is best-effort: no source video (transcript-only),
extraction `None`, or a failed upload → log and continue with `thumbnail_url`
null; never fail the run over a thumbnail.

### 3. Upload — `src/storage.py` (new)

- **`public_url(supabase_url, bucket, object_path) -> str`** (pure, unit-tested):
  `f"{supabase_url}/storage/v1/object/public/{bucket}/{object_path}"`.
- **`upload_thumbnail(jpg_path, meeting_id) -> str | None`:** reads
  `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY` from the environment; uploads the
  file to `meeting-thumbnails/{meeting_id}.jpg` via the Storage REST API
  (`POST {SUPABASE_URL}/storage/v1/object/{bucket}/{path}` with
  `Authorization: Bearer <service key>`, `Content-Type: image/jpeg`,
  `x-upsert: true`), and returns the public URL. Returns `None` + warns when env
  is missing or the request fails. **Never raises into publish.** Re-publish
  overwrites the same object (`x-upsert: true`) — idempotent by `meeting_id`.

### 4. DB — `supabase/migrations/0005_meeting_thumbnail.sql`

```sql
ALTER TABLE meetings.meetings
  ADD COLUMN IF NOT EXISTS thumbnail_url text;
COMMENT ON COLUMN meetings.meetings.thumbnail_url IS
  'Public URL of the extracted frame thumbnail (Supabase Storage); null if none.';
```

### 5. Model + publish — `src/models.py`, `src/publish.py`

- `Meeting` gains `thumbnail_url: str | None` (and round-trips through
  `to_dict`/`from_dict`).
- `publish.py` persists it: add `thumbnail_url` to the `_upsert_meeting` INSERT and
  UPDATE column lists, passing `meeting.thumbnail_url`. The upload itself happens in
  run_local (section 2), so publish stays a pure DB writer.

### 6. web/ (this repo)

- `web/lib/types.ts`: add `thumbnail_url: string | null` to `Meeting`.
- `web/lib/queries.ts` `mapMeeting`: `thumbnail_url: m.thumbnailUrl ?? null`.
- `web/lib/thumbnail.ts` `buildThumbnailModel`: resolve `imageSrc` with precedence
  **`meeting.thumbnail_url` → YouTube-derived URL → null (info tile)`**. This is
  what makes the extracted frame supersede YouTube. Update the Vitest tests to
  cover: explicit `thumbnail_url` wins over a YouTube id; YouTube id still used
  when `thumbnail_url` is null; file/HLS with `thumbnail_url` shows the image (not
  the info tile).

### 7. ev-accounts (separate repo — contract)

Return `thumbnailUrl` (from `meetings.meetings.thumbnail_url`) in the
`GET /api/meetings` list response (and the detail response if convenient). No
other field changes.

## Fallback chain (web)

`thumbnail_url` (extracted) → YouTube-derived frame → info tile. A meeting with no
extracted thumbnail (old, audio-only, or a failed extraction/upload) still shows
the YouTube frame or info tile — nothing regresses.

## Operator prerequisites

- Create a **public** Supabase Storage bucket named `meeting-thumbnails`.
- Set `SUPABASE_URL` (e.g. `https://<project>.supabase.co`) and
  `SUPABASE_SERVICE_ROLE_KEY` in the pipeline env (`.env.local`), documented in
  `.env.local.example`. Without them, `upload_thumbnail` no-ops with a warning and
  publishing proceeds (meetings simply fall back to the YouTube frame / info tile).

## Error handling

Extraction and upload are both **best-effort and non-fatal**. A missing source
video, audio-only stream, missing ffmpeg, missing Supabase env, or upload error
must never block transcription, summarization, or publishing.

## Out of scope

- Backfilling thumbnails for already-published meetings (handled by re-publish).
- Speaker count (follow-up #2).
- Any change to the clip/ingest behavior itself.

## Testing

- **Python (this repo):** unit-test the pure helpers — `thumbnail_seek_start`
  (no clip → ≤10s; with clip → clip_start + capped offset; zero/short durations)
  and `public_url` (correct path joining). The ffmpeg extraction and the Storage
  upload are verified by a manual end-to-end run on one meeting (frame written,
  object uploaded, `thumbnail_url` populated). An optional automated extraction
  test can synthesize a tiny clip via `ffmpeg lavfi` if cheap.
- **web/ (this repo):** Vitest covers the `imageSrc` precedence in
  `buildThumbnailModel`; browser-verify that an extracted thumbnail renders and
  that meetings without one fall back correctly, in light/dark and at 375px.
- **ev-accounts:** unit-test that `thumbnailUrl` is included and passes through.
