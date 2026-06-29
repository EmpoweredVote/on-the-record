# Meeting Thumbnail Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Generate a thumbnail from each meeting's kept video section, host it on Supabase Storage, and surface it on the homepage list — superseding YouTube's frame.

**Architecture:** A new `src/thumbnail.py` extracts a representative frame with ffmpeg's `thumbnail` filter; `src/storage.py` uploads it to a public Supabase Storage bucket; `run_local.py` orchestrates extract→upload→set `meeting.thumbnail_url` before publish; `publish.py` persists the column (migration `0005`); the `web/` thumbnail helper prefers it over the YouTube frame.

**Tech Stack:** Python 3 (ffmpeg via subprocess, `requests`), psycopg2/Supabase Postgres, Supabase Storage REST, Next.js 16 `web/` + Vitest.

**Spec:** `docs/superpowers/specs/2026-06-26-meeting-thumbnail-extraction-design.md`

> Python commands use the project venv: `.venv/bin/python` and `.venv/bin/pytest`
> (system python3 lacks deps). Tests live in `tests/` (`pytest.ini` → `testpaths = tests`).
> `web/` commands run from `web/`.

---

## File structure

- **Create** `src/thumbnail.py` — `thumbnail_seek_start` (pure) + `extract_thumbnail` (ffmpeg).
- **Create** `src/storage.py` — `public_url` (pure) + `upload_thumbnail` (Supabase REST).
- **Create** `tests/test_thumbnail.py`, `tests/test_storage.py`.
- **Create** `supabase/migrations/0005_meeting_thumbnail.sql`.
- **Modify** `src/models.py` — `Meeting.thumbnail_url` + dict round-trip.
- **Modify** `src/publish.py` — `thumbnail_url` in the `_upsert_meeting` INSERT/UPDATE.
- **Modify** `run_local.py` — `_attach_thumbnail` helper + call before each `publish_meeting`.
- **Modify** `.env.local.example` — document `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY`.
- **Modify** `web/lib/types.ts`, `web/lib/queries.ts`, `web/lib/thumbnail.ts` (+ `web/lib/thumbnail.test.ts`).
- **(Separate repo)** ev-accounts — return `thumbnailUrl` (contract, Task 8).

---

## Task 1: Frame extraction — `src/thumbnail.py`

**Files:**
- Create: `src/thumbnail.py`
- Test: `tests/test_thumbnail.py`

- [ ] **Step 1: Write the failing test for the seek helper**

Create `tests/test_thumbnail.py`:
```python
import shutil
import subprocess
from pathlib import Path

import pytest

from src.thumbnail import thumbnail_seek_start, extract_thumbnail


def test_seek_no_clip_seeks_up_to_ten_percent():
    # No clip window: clip_start is 0, so seek ~10% in, capped at 10s.
    assert thumbnail_seek_start(None, 60.0) == pytest.approx(6.0)
    assert thumbnail_seek_start(0.0, 300.0) == pytest.approx(10.0)  # capped


def test_seek_with_clip_offsets_from_clip_start():
    # 40s kept section starting 120s into the source: 120 + min(10, 4) = 124.
    assert thumbnail_seek_start(120.0, 40.0) == pytest.approx(124.0)


def test_seek_zero_duration_is_clip_start():
    assert thumbnail_seek_start(90.0, 0.0) == pytest.approx(90.0)
    assert thumbnail_seek_start(None, None) == pytest.approx(0.0)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/test_thumbnail.py -q`
Expected: FAIL — `cannot import name 'thumbnail_seek_start' from 'src.thumbnail'` (module doesn't exist).

- [ ] **Step 3: Implement the module**

Create `src/thumbnail.py`:
```python
"""Extract a representative thumbnail frame from a meeting's source video.

Best-effort: every function returns None / no-ops on failure so the pipeline
never breaks over a missing thumbnail.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def thumbnail_seek_start(
    clip_start: Optional[float], clip_duration: Optional[float]
) -> float:
    """Seconds into the FULL source video to start scanning for a frame.

    Skips a short way into the kept section (past the intro), capped at 10s.
    ``clip_duration`` is the kept-section length (the clipped audio duration).
    """
    base = clip_start or 0.0
    dur = clip_duration or 0.0
    return base + min(10.0, 0.10 * dur)


def extract_thumbnail(
    video_path: str,
    clip_start: Optional[float],
    clip_duration: Optional[float],
    out_path: Path,
) -> Optional[Path]:
    """Write a JPEG thumbnail to ``out_path``; return it, or None on failure.

    Uses ffmpeg's ``thumbnail`` filter to auto-pick a representative frame
    (skips black/fade/flat frames) from a batch starting at the seek point.
    """
    if not shutil.which("ffmpeg"):
        logger.warning("ffmpeg not on PATH; skipping thumbnail extraction")
        return None

    seek = thumbnail_seek_start(clip_start, clip_duration)
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(seek),
        "-i", str(video_path),
        "-vf", "thumbnail=n=300,scale=640:-2",
        "-frames:v", "1",
        "-q:v", "3",
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as exc:
        stderr = exc.stderr.decode("utf-8", "ignore")[:500] if exc.stderr else ""
        logger.warning("thumbnail extraction failed: %s", stderr)
        return None

    if out_path.exists() and out_path.stat().st_size > 0:
        return out_path
    return None
```

- [ ] **Step 4: Run the seek tests to verify they pass**

Run: `.venv/bin/pytest tests/test_thumbnail.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Add an ffmpeg-gated extraction test**

Append to `tests/test_thumbnail.py`:
```python
ffmpeg_missing = shutil.which("ffmpeg") is None


@pytest.mark.skipif(ffmpeg_missing, reason="ffmpeg not installed")
def test_extract_thumbnail_writes_a_jpeg(tmp_path: Path):
    # Synthesize a 3s test video so the test is self-contained.
    src = tmp_path / "source.mp4"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi",
         "-i", "testsrc=duration=3:size=320x240:rate=10",
         str(src)],
        check=True, capture_output=True,
    )
    out = tmp_path / "thumbnail.jpg"
    result = extract_thumbnail(str(src), None, 3.0, out)
    assert result == out
    assert out.exists() and out.stat().st_size > 0


def test_extract_thumbnail_missing_video_returns_none(tmp_path: Path):
    out = tmp_path / "thumbnail.jpg"
    result = extract_thumbnail(str(tmp_path / "nope.mp4"), None, 10.0, out)
    assert result is None
    assert not out.exists()
```

- [ ] **Step 6: Run the full file**

Run: `.venv/bin/pytest tests/test_thumbnail.py -q`
Expected: PASS (extraction test passes, or is skipped if ffmpeg is absent; the missing-video test passes).

- [ ] **Step 7: Commit**

```bash
git add src/thumbnail.py tests/test_thumbnail.py
git commit -m "feat: extract representative thumbnail frame from source video"
```

---

## Task 2: Upload — `src/storage.py`

**Files:**
- Create: `src/storage.py`
- Test: `tests/test_storage.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_storage.py`:
```python
from pathlib import Path

from src.storage import public_url, upload_thumbnail, THUMBNAIL_BUCKET


def test_public_url_joins_path():
    assert public_url("https://x.supabase.co", "meeting-thumbnails", "abc.jpg") == (
        "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/abc.jpg"
    )


def test_public_url_strips_trailing_slash():
    assert public_url("https://x.supabase.co/", THUMBNAIL_BUCKET, "a.jpg").startswith(
        "https://x.supabase.co/storage/v1/object/public/"
    )


def test_upload_noops_without_env(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)
    jpg = tmp_path / "thumbnail.jpg"
    jpg.write_bytes(b"\xff\xd8\xff")  # minimal JPEG-ish bytes
    assert upload_thumbnail(jpg, "some-slug") is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_storage.py -q`
Expected: FAIL — `src.storage` does not exist.

- [ ] **Step 3: Implement the module**

Create `src/storage.py`:
```python
"""Upload meeting thumbnails to a public Supabase Storage bucket.

Best-effort: returns None (with a warning) when env is missing or the upload
fails, so publishing never breaks over a thumbnail.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)

THUMBNAIL_BUCKET = "meeting-thumbnails"


def public_url(supabase_url: str, bucket: str, object_path: str) -> str:
    """Public read URL for an object in a public Storage bucket."""
    base = supabase_url.rstrip("/")
    return f"{base}/storage/v1/object/public/{bucket}/{object_path}"


def upload_thumbnail(jpg_path: Path, meeting_id: str) -> Optional[str]:
    """Upload ``jpg_path`` as ``{meeting_id}.jpg``; return its public URL or None."""
    base = os.environ.get("SUPABASE_URL", "").strip().rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    if not base or not key:
        logger.warning(
            "SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set; skipping thumbnail upload"
        )
        return None

    object_path = f"{meeting_id}.jpg"
    url = f"{base}/storage/v1/object/{THUMBNAIL_BUCKET}/{object_path}"
    try:
        with open(jpg_path, "rb") as fh:
            resp = requests.post(
                url,
                data=fh,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "image/jpeg",
                    "x-upsert": "true",
                },
                timeout=30,
            )
        resp.raise_for_status()
    except Exception as exc:  # network, auth, file IO — all non-fatal
        logger.warning("thumbnail upload failed: %s", exc)
        return None

    return public_url(base, THUMBNAIL_BUCKET, object_path)
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/pytest tests/test_storage.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add src/storage.py tests/test_storage.py
git commit -m "feat: upload meeting thumbnails to Supabase Storage"
```

---

## Task 3: DB migration

**Files:**
- Create: `supabase/migrations/0005_meeting_thumbnail.sql`

- [ ] **Step 1: Create the migration**

Create `supabase/migrations/0005_meeting_thumbnail.sql`:
```sql
-- 0005_meeting_thumbnail.sql
-- Public URL of the extracted-frame thumbnail (Supabase Storage), or null.
ALTER TABLE meetings.meetings
  ADD COLUMN IF NOT EXISTS thumbnail_url text;

COMMENT ON COLUMN meetings.meetings.thumbnail_url IS
  'Public URL of the extracted frame thumbnail (Supabase Storage); null if none.';
```

- [ ] **Step 2: Apply it to the database**

Apply via the project's normal migration path (the same way `0004_clip_window.sql`
was applied — e.g. run the SQL against `DATABASE_URL` in the Supabase SQL editor or
`psql "$DATABASE_URL" -f supabase/migrations/0005_meeting_thumbnail.sql`).
Expected: `ALTER TABLE` succeeds; re-running is a no-op (`IF NOT EXISTS`).

- [ ] **Step 3: Commit**

```bash
git add supabase/migrations/0005_meeting_thumbnail.sql
git commit -m "feat(db): add meetings.meetings.thumbnail_url column"
```

---

## Task 4: Model field + publish persistence

**Files:**
- Modify: `src/models.py` (the `Meeting` dataclass + `to_dict`/`from_dict`)
- Modify: `src/publish.py` (`_upsert_meeting` INSERT + UPDATE)
- Test: `tests/test_thumbnail_model.py`

- [ ] **Step 1: Write the failing round-trip test**

Create `tests/test_thumbnail_model.py`:
```python
from src.models import Meeting


def test_thumbnail_url_round_trips():
    m = Meeting(meeting_id="2026-02-18-regular", city="Asheville", date="2026-02-18")
    m.thumbnail_url = "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/2026-02-18-regular.jpg"
    restored = Meeting.from_dict(m.to_dict())
    assert restored.thumbnail_url == m.thumbnail_url


def test_thumbnail_url_defaults_none():
    m = Meeting(meeting_id="x", city=None, date="2026-01-01")
    assert m.thumbnail_url is None
    assert Meeting.from_dict(m.to_dict()).thumbnail_url is None
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/pytest tests/test_thumbnail_model.py -q`
Expected: FAIL — `Meeting` has no attribute `thumbnail_url`.

- [ ] **Step 3: Add the field to the `Meeting` dataclass**

In `src/models.py`, in the `Meeting` dataclass, add the field next to the clip
fields (after `clip_end_seconds: Optional[float] = None`):
```python
    thumbnail_url: Optional[str] = None
```

- [ ] **Step 4: Add it to `to_dict`**

In `Meeting.to_dict`, alongside the optional clip fields (which use
`if ... is not None`), add:
```python
        if self.thumbnail_url is not None:
            d["thumbnail_url"] = self.thumbnail_url
```

- [ ] **Step 5: Add it to `from_dict`**

In `Meeting.from_dict`, alongside `clip_start_seconds=d.get("clip_start_seconds")`, add:
```python
            thumbnail_url=d.get("thumbnail_url"),
```

- [ ] **Step 6: Run the model test to verify it passes**

Run: `.venv/bin/pytest tests/test_thumbnail_model.py -q`
Expected: PASS (2 passed).

- [ ] **Step 7: Persist in the publish UPDATE**

In `src/publish.py` `_upsert_meeting`, in the `UPDATE meetings.meetings SET`
statement, add `thumbnail_url = %s,` immediately after the
`clip_end_seconds = %s,` line:
```sql
              clip_end_seconds = %s,
              thumbnail_url = %s,
              summary = %s,
```
and add `meeting.thumbnail_url,` to the params tuple immediately after
`meeting.clip_end_seconds,`:
```python
                meeting.clip_start_seconds,
                meeting.clip_end_seconds,
                meeting.thumbnail_url,
                psycopg2.extras.Json(summary),
```

- [ ] **Step 8: Persist in the publish INSERT**

In the same function's INSERT statement, add `thumbnail_url` to the column list
(after `clip_end_seconds`, before `slug`):
```sql
               chamber_id, source_url, playback_kind, clip_start_seconds, clip_end_seconds, thumbnail_url, slug,
```
add one more `%s` to the matching VALUES row (the row currently reading
`%s, %s, %s, %s, %s, %s,` for `chamber_id..slug` gains one placeholder):
```sql
               %s, %s, %s, %s, %s, %s, %s,
```
and insert `meeting.thumbnail_url,` into the params tuple immediately after
`meeting.clip_end_seconds,` (before `meeting.meeting_id,`):
```python
                meeting.clip_start_seconds,
                meeting.clip_end_seconds,
                meeting.thumbnail_url,
                meeting.meeting_id,
```

- [ ] **Step 9: Sanity-check publish still imports/parses**

Run: `.venv/bin/python -c "import src.publish"`
Expected: no error (the module imports cleanly; SQL string edits are valid Python).

- [ ] **Step 10: Commit**

```bash
git add src/models.py src/publish.py tests/test_thumbnail_model.py
git commit -m "feat: persist meeting thumbnail_url through model and publish"
```

---

## Task 5: Pipeline wiring — `run_local.py`

Add a best-effort orchestrator and call it before publishing, at both the main
pipeline publish and the standalone/re-publish publish, so fresh runs and
re-publishes (when a source video is present) both attach a thumbnail.

**Files:**
- Modify: `run_local.py`

- [ ] **Step 1: Add the `_attach_thumbnail` helper**

In `run_local.py`, just after the existing `find_video_file` function
(around line 410–430), add:
```python
def _attach_thumbnail(meeting, meeting_dir) -> None:
    """Best-effort: extract a frame from the kept section, upload it, and set
    meeting.thumbnail_url. Never raises — a thumbnail must not break publishing."""
    try:
        from src.thumbnail import extract_thumbnail
        from src.storage import upload_thumbnail

        video_path = find_video_file(meeting_dir, meeting.audio_source)
        if not video_path:
            return
        out = meeting_dir / "thumbnail.jpg"
        if extract_thumbnail(
            video_path, meeting.clip_start_seconds, meeting.duration_seconds, out
        ):
            url = upload_thumbnail(out, meeting.meeting_id)
            if url:
                meeting.thumbnail_url = url
                print(f"  Thumbnail: {url}")
    except Exception as exc:  # absolutely non-fatal
        print(f"  WARNING: thumbnail step failed — {exc}")
```

- [ ] **Step 2: Wire it into the main pipeline publish**

Find the main publish call (around line 1691–1693):
```python
                from src.publish import publish_meeting

                result = publish_meeting(meeting, state.body_slug)
```
Insert the attach call immediately before `publish_meeting`:
```python
                from src.publish import publish_meeting

                _attach_thumbnail(meeting, meeting_dir)
                result = publish_meeting(meeting, state.body_slug)
```

- [ ] **Step 3: Wire it into the standalone/re-publish path**

Find `_publish_meeting_standalone` (around line 1955). It loads a meeting by id and
calls `publish_meeting(meeting, body_slug)` (around line 1979). Immediately before
that `publish_meeting` call, derive the meeting directory and attach the thumbnail:
```python
    import src.config as config
    _attach_thumbnail(meeting, config.MEETINGS_DIR / meeting_id)
    result = publish_meeting(meeting, body_slug)
```
(If `config` / `MEETINGS_DIR` is already imported at the top of the file, reuse it
instead of the local import. The existing line numbers may have shifted by Step 2 —
locate by the `publish_meeting(meeting, body_slug)` call inside
`_publish_meeting_standalone`.)

- [ ] **Step 4: Verify the module imports and the helper is reachable**

Run: `.venv/bin/python -c "import run_local; print(hasattr(run_local, '_attach_thumbnail'))"`
Expected: prints `True` with no import error.

- [ ] **Step 5: Manual end-to-end check (operator)**

With `SUPABASE_URL` + `SUPABASE_SERVICE_ROLE_KEY` set and the `meeting-thumbnails`
bucket created (Task 6), run the pipeline on one meeting that has a source video
(or re-publish one whose `meeting_dir` still has `source.*`). Confirm:
  - `meeting_dir/thumbnail.jpg` is written,
  - the console prints `Thumbnail: https://.../meeting-thumbnails/<slug>.jpg`,
  - `SELECT thumbnail_url FROM meetings.meetings WHERE slug = '<slug>';` returns the URL,
  - opening the URL shows the frame.
If `SUPABASE_*` env is unset, confirm the run still completes and publishes with a
warning and `thumbnail_url` null (best-effort behavior).

- [ ] **Step 6: Commit**

```bash
git add run_local.py
git commit -m "feat: attach extracted thumbnail to meetings before publish"
```

---

## Task 6: Document the new env vars

**Files:**
- Modify: `.env.local.example`

- [ ] **Step 1: Append the Supabase Storage settings**

Add to `.env.local.example` (below the existing `DATABASE_URL` block):
```bash

# Thumbnail upload: public Supabase Storage bucket "meeting-thumbnails".
# Create the bucket (public) in the Supabase dashboard, then set:
#   SUPABASE_URL: the project URL, e.g. https://<project>.supabase.co
#   SUPABASE_SERVICE_ROLE_KEY: Project Settings -> API -> service_role key
# If unset, thumbnail upload is skipped (meetings fall back to the YouTube frame
# or the info tile).
SUPABASE_URL=
SUPABASE_SERVICE_ROLE_KEY=
```

- [ ] **Step 2: Commit**

```bash
git add .env.local.example
git commit -m "docs: document SUPABASE_URL/SERVICE_ROLE_KEY for thumbnail upload"
```

---

## Task 7: Web — prefer the extracted thumbnail (web/)

**Files:**
- Modify: `web/lib/types.ts`, `web/lib/queries.ts`, `web/lib/thumbnail.ts`
- Test: `web/lib/thumbnail.test.ts`

- [ ] **Step 1: Add the field to the type and mapper**

In `web/lib/types.ts`, add to the `Meeting` interface (after `source_title`):
```ts
  thumbnail_url: string | null;  // extracted-frame thumbnail (Supabase Storage)
```
In `web/lib/queries.ts` `mapMeeting`, add (next to the other mapped fields):
```ts
    thumbnail_url: m.thumbnailUrl ?? null,
```

- [ ] **Step 2: Write the failing precedence tests**

In `web/lib/thumbnail.test.ts`, add to the existing `base` object literal the new
required field so it type-checks:
```ts
  thumbnail_url: null,
```
Then add this block after the existing `buildThumbnailModel` tests:
```ts
describe("buildThumbnailModel — thumbnail_url precedence", () => {
  it("prefers an explicit thumbnail_url over the YouTube frame", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: "youtube",
      playback_url: "abc123",
      thumbnail_url: "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/m.jpg",
    });
    expect(m.imageSrc).toBe(
      "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/m.jpg"
    );
    expect(m.showPlay).toBe(true);
  });

  it("shows an extracted thumbnail for a file video (not the info tile)", () => {
    const m = buildThumbnailModel({
      ...base,
      playback_kind: "file",
      playback_url: "https://cdn.example.com/v.mp4",
      thumbnail_url: "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/m.jpg",
    });
    expect(m.imageSrc).toBe(
      "https://x.supabase.co/storage/v1/object/public/meeting-thumbnails/m.jpg"
    );
    expect(m.showPlay).toBe(true);
  });

  it("falls back to the YouTube frame when thumbnail_url is null", () => {
    const m = buildThumbnailModel({ ...base, thumbnail_url: null });
    expect(m.imageSrc).toBe("https://img.youtube.com/vi/abc123/hqdefault.jpg");
  });
});
```

- [ ] **Step 3: Run to verify failure**

Run (from `web/`): `npm test`
Expected: FAIL — the precedence tests fail (thumbnail_url is ignored) and/or a type
error until `thumbnail_url` is read in `buildThumbnailModel`.

- [ ] **Step 4: Implement the precedence in `buildThumbnailModel`**

In `web/lib/thumbnail.ts`, replace the `imageSrc` derivation block (the comment
SEAM + the YouTube `if`) with:
```ts
  // Source precedence: explicit extracted thumbnail > YouTube-derived frame > none.
  let imageSrc: string | null = null;
  if (meeting.thumbnail_url) {
    imageSrc = meeting.thumbnail_url;
  } else if (meeting.playback_kind === "youtube" && meeting.playback_url) {
    imageSrc = youtubeThumbnailUrl(meeting.playback_url);
  }
```

- [ ] **Step 5: Run to verify pass**

Run (from `web/`): `npm test`
Expected: PASS — all thumbnail tests green (existing + 3 new).

- [ ] **Step 6: Lint and build**

Run (from `web/`): `npm run lint && npm run build`
Expected: lint shows only the pre-existing `SiteHeader.tsx` error; build succeeds.
(If build fails only on a DB/network error, `npx tsc --noEmit` is an acceptable
substitute — note it.)

- [ ] **Step 7: Commit**

```bash
git add web/lib/types.ts web/lib/queries.ts web/lib/thumbnail.ts web/lib/thumbnail.test.ts
git commit -m "feat(web): prefer extracted thumbnail_url over the YouTube frame"
```

---

## Task 8: ev-accounts (separate repo — contract)

> Implemented in the ev-accounts repository, not here. Captured as a contract.

**Where:** the `GET /api/meetings` list serializer (and the meeting detail
serializer if convenient).

**Behavior:** include a `thumbnailUrl` field sourced from
`meetings.meetings.thumbnail_url` (string or null). No other fields change. The web
maps `m.thumbnailUrl` → `thumbnail_url` (Task 7).

- [ ] **Step 1:** In the ev-accounts list serializer, add `thumbnailUrl` from the
  `thumbnail_url` column.
- [ ] **Step 2:** Add/extend a serializer test asserting `thumbnailUrl` is present
  (URL when set, null when the column is null).
- [ ] **Step 3:** Commit in the ev-accounts repo.

After this ships, a re-published meeting with an extracted frame shows it on the
homepage; meetings without one fall back to the YouTube frame, then the info tile.

---

## Self-review notes

- **Spec coverage:** extraction module + seek math (Task 1); Supabase upload (Task 2);
  `0005` migration (Task 3); model field + publish persistence (Task 4); run_local
  orchestration at both publish sites, best-effort (Task 5); env docs + operator
  prereq (Task 6); web precedence `thumbnail_url > YouTube > info tile` (Task 7);
  ev-accounts contract (Task 8). Backfill is out of scope per the spec; re-publish
  wiring (Task 5 Step 3) covers it opportunistically when a source video is present.
- **Placeholder scan:** none — every code step shows full code; SQL edits are
  anchored to exact existing lines; tests have concrete assertions.
- **Consistency:** `thumbnail_url` (Python/DB/web) ↔ `thumbnailUrl` (API/JSON) used
  consistently; `extract_thumbnail`/`thumbnail_seek_start`/`upload_thumbnail`/
  `public_url`/`_attach_thumbnail`/`THUMBNAIL_BUCKET` names match across tasks; the
  seek helper takes `(clip_start, clip_duration)` everywhere; `duration_seconds`
  (clipped length) is passed as `clip_duration`.
- **Best-effort invariant:** Task 1/2 return None on failure, Task 5 wraps in
  try/except — no path can fail publishing over a thumbnail.
