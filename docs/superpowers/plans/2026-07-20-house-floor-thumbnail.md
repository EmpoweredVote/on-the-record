# House-floor Thumbnail Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give House-floor (HLS-sourced) meetings a thumbnail by extracting a frame from the HLS stream we already store, and backfill the existing `2026-07-16-house-floor` meeting.

**Architecture:** `attach_thumbnail` currently extracts a frame from a local video file or falls back to downloaded artwork. Add a middle path: when there's no local video, use the HLS manifest URL (`processing_metadata.source_audio_url`) directly as the ffmpeg input — ffmpeg reads `.m3u8` natively. Extend the backfill selector to include HLS-source meetings and persist the resulting `thumbnail_url`.

**Tech Stack:** Python 3 (ffmpeg via subprocess), pytest, Supabase Storage upload.

**Reference spec:** `docs/superpowers/specs/2026-07-20-house-floor-thumbnail-design.md`

---

## File Structure

- `src/thumbnail.py` — add `streamable_video_url(meeting)` helper; add the HLS branch to `attach_thumbnail` (between the local-video and artwork branches).
- `backfill_thumbnails.py` — include HLS-source meetings in `meetings_needing_thumbnail`; persist `thumbnail_url` to `transcript_named.json` after attaching.
- `tests/test_thumbnail.py` — unit coverage for the helper + HLS branch + precedence.
- `tests/test_backfill_thumbnails.py` — unit coverage for the extended selector.

---

## Task 1: HLS thumbnail source in `attach_thumbnail`

**Files:**
- Modify: `src/thumbnail.py` (add `streamable_video_url`; edit `attach_thumbnail`, ~lines 110-138)
- Test: `tests/test_thumbnail.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_thumbnail.py`:

```python
# --- HLS (House Clerk) thumbnail source ---
from src.thumbnail import streamable_video_url


class _HlsMeeting:
    def __init__(self, m3u8, meeting_dir):
        self.audio_source = "https://live.house.gov/?date=2026-07-16"
        self.clip_start_seconds = None
        self.duration_seconds = 12240.0
        self.meeting_id = "2026-07-16-house-floor"
        self.thumbnail_url = None
        class _PM:
            source_audio_url = m3u8
            source_image_url = None
        self.processing_metadata = _PM()


def test_streamable_video_url_returns_m3u8():
    m = _HlsMeeting("https://cdn/east/x/manifest.m3u8", ".")
    assert streamable_video_url(m) == "https://cdn/east/x/manifest.m3u8"


def test_streamable_video_url_handles_query_string():
    m = _HlsMeeting("https://cdn/x/manifest.m3u8?token=abc", ".")
    assert streamable_video_url(m) == "https://cdn/x/manifest.m3u8?token=abc"


def test_streamable_video_url_none_for_non_hls():
    m = _HlsMeeting("https://cdn/x/audio.mp3", ".")
    assert streamable_video_url(m) is None


def test_streamable_video_url_none_when_no_processing_metadata():
    class _Bare:
        pass
    assert streamable_video_url(_Bare()) is None


def test_attach_thumbnail_uses_hls_when_no_local_video(tmp_path, monkeypatch):
    import src.thumbnail as th
    captured = {}
    monkeypatch.setattr(th, "find_video_file", lambda d, s: None)
    monkeypatch.setattr(th, "extract_thumbnail",
                        lambda vp, cs, cd, out: captured.setdefault("vp", vp) or out)
    monkeypatch.setattr("src.storage.upload_thumbnail",
                        lambda jpg, mid: "https://bucket/thumb.jpg")
    m = _HlsMeeting("https://cdn/east/x/manifest.m3u8", tmp_path)
    th.attach_thumbnail(m, tmp_path)
    assert captured["vp"] == "https://cdn/east/x/manifest.m3u8"
    assert m.thumbnail_url == "https://bucket/thumb.jpg"


def test_attach_thumbnail_prefers_local_video_over_hls(tmp_path, monkeypatch):
    import src.thumbnail as th
    (tmp_path / "source.webm").write_bytes(b"x")
    captured = {}
    monkeypatch.setattr(th, "extract_thumbnail",
                        lambda vp, cs, cd, out: captured.setdefault("vp", vp) or out)
    monkeypatch.setattr("src.storage.upload_thumbnail",
                        lambda jpg, mid: "https://bucket/thumb.jpg")
    m = _HlsMeeting("https://cdn/east/x/manifest.m3u8", tmp_path)
    th.attach_thumbnail(m, tmp_path)
    assert captured["vp"] == str(tmp_path / "source.webm")   # local wins over HLS
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `.venv/bin/python -m pytest tests/test_thumbnail.py -k "streamable or hls or prefers_local" -v`
Expected: FAIL — `ImportError: cannot import name 'streamable_video_url'` (helper not defined yet).

- [ ] **Step 3: Add the `streamable_video_url` helper**

In `src/thumbnail.py`, add this function immediately after `find_video_file` (after line 107):

```python
def streamable_video_url(meeting) -> Optional[str]:
    """A remote video URL ffmpeg can pull a frame from when no local video file
    exists — currently the House Clerk HLS manifest stored as source_audio_url.
    Returns the URL for an .m3u8 source, else None.
    """
    pm = getattr(meeting, "processing_metadata", None)
    url = getattr(pm, "source_audio_url", None) if pm else None
    if url and url.split("?", 1)[0].lower().endswith(".m3u8"):
        return url
    return None
```

- [ ] **Step 4: Add the HLS branch to `attach_thumbnail`**

In `attach_thumbnail`, the current body (lines 115-138) reads:

```python
    try:
        from src.storage import upload_thumbnail

        video_path = find_video_file(meeting_dir, meeting.audio_source)
        out = Path(meeting_dir) / "thumbnail.jpg"
        if not video_path:
            # Audio-only source: use the resolver-provided artwork, if any.
            processing_metadata = getattr(meeting, "processing_metadata", None)
            image_url = getattr(processing_metadata, "source_image_url", None)
            if not image_url:
                return
            if download_image(image_url, out):
                url = upload_thumbnail(out, meeting.meeting_id)
                if url:
                    meeting.thumbnail_url = url
                    logger.info("Thumbnail (artwork): %s", url)
            return
        if extract_thumbnail(
            video_path, meeting.clip_start_seconds, meeting.duration_seconds, out
        ):
            url = upload_thumbnail(out, meeting.meeting_id)
            if url:
                meeting.thumbnail_url = url
                logger.info("Thumbnail: %s", url)
    except Exception as exc:  # absolutely non-fatal
        logger.warning("thumbnail step failed — %s", exc)
```

Insert the HLS fallback between the `find_video_file` line and the `if not video_path:` artwork block, so it becomes:

```python
    try:
        from src.storage import upload_thumbnail

        video_path = find_video_file(meeting_dir, meeting.audio_source)
        out = Path(meeting_dir) / "thumbnail.jpg"
        if not video_path:
            # No local file, but a streamable HLS video (House Clerk): ffmpeg can
            # pull a frame straight from the .m3u8 manifest.
            video_path = streamable_video_url(meeting)
        if not video_path:
            # Audio-only source: use the resolver-provided artwork, if any.
            processing_metadata = getattr(meeting, "processing_metadata", None)
            image_url = getattr(processing_metadata, "source_image_url", None)
            if not image_url:
                return
            if download_image(image_url, out):
                url = upload_thumbnail(out, meeting.meeting_id)
                if url:
                    meeting.thumbnail_url = url
                    logger.info("Thumbnail (artwork): %s", url)
            return
        if extract_thumbnail(
            video_path, meeting.clip_start_seconds, meeting.duration_seconds, out
        ):
            url = upload_thumbnail(out, meeting.meeting_id)
            if url:
                meeting.thumbnail_url = url
                logger.info("Thumbnail: %s", url)
    except Exception as exc:  # absolutely non-fatal
        logger.warning("thumbnail step failed — %s", exc)
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `.venv/bin/python -m pytest tests/test_thumbnail.py -v`
Expected: PASS (all — new HLS tests plus the pre-existing ones, including `test_attach_thumbnail_no_video_is_noop`, which stays a no-op because `_FakeMeeting` has no `processing_metadata`).

- [ ] **Step 6: Commit**

```bash
git add src/thumbnail.py tests/test_thumbnail.py
git commit -m "feat(thumbnail): extract House-floor thumbnails from the HLS stream"
```

---

## Task 2: Backfill includes HLS meetings + persists thumbnail_url

**Files:**
- Modify: `backfill_thumbnails.py` (`meetings_needing_thumbnail`; `backfill`)
- Test: `tests/test_backfill_thumbnails.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_backfill_thumbnails.py`:

```python
import json


def _mk_hls(mdir: Path, *, m3u8=True, thumb=False):
    mdir.mkdir(parents=True)
    if thumb:
        (mdir / "thumbnail.jpg").write_bytes(b"x")
    src = "https://cdn/east/x/manifest.m3u8" if m3u8 else "https://cdn/x/audio.mp3"
    (mdir / "transcript_named.json").write_text(
        json.dumps({"processing_metadata": {"source_audio_url": src}}),
        encoding="utf-8",
    )


def test_includes_hls_source_meetings(tmp_path: Path):
    _mk_hls(tmp_path / "house")                    # HLS, no video, no thumb -> included
    out = meetings_needing_thumbnail(tmp_path)
    assert out == [tmp_path / "house"]


def test_skips_hls_meeting_that_has_thumbnail(tmp_path: Path):
    _mk_hls(tmp_path / "house", thumb=True)        # already has thumb -> skipped
    assert meetings_needing_thumbnail(tmp_path) == []


def test_skips_non_hls_audio_only_meeting(tmp_path: Path):
    _mk_hls(tmp_path / "podcast", m3u8=False)      # audio.mp3 source, no video -> skipped
    assert meetings_needing_thumbnail(tmp_path) == []
```

- [ ] **Step 2: Run the tests, verify they fail**

Run: `.venv/bin/python -m pytest tests/test_backfill_thumbnails.py -k "hls or non_hls" -v`
Expected: FAIL — `test_includes_hls_source_meetings` returns `[]` (selector ignores HLS meetings).

- [ ] **Step 3: Add the HLS detection helper + extend the selector**

In `backfill_thumbnails.py`, add `import json` at the top (after `import argparse`), and add this helper above `meetings_needing_thumbnail`:

```python
def _has_streamable_hls(mdir: Path) -> bool:
    """True when the meeting's source is an HLS .m3u8 (House Clerk) — ffmpeg can
    extract a frame from it even though there's no local video file on disk."""
    named = mdir / "transcript_named.json"
    if not named.exists():
        return False
    try:
        d = json.loads(named.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return False
    url = (d.get("processing_metadata") or {}).get("source_audio_url") or ""
    return url.split("?", 1)[0].lower().endswith(".m3u8")
```

Then change the selection condition inside `meetings_needing_thumbnail`. Current:

```python
        if (mdir / "thumbnail.jpg").exists():
            continue
        if find_video_file(mdir, ""):
            out.append(mdir)
```

Change to:

```python
        if (mdir / "thumbnail.jpg").exists():
            continue
        if find_video_file(mdir, "") or _has_streamable_hls(mdir):
            out.append(mdir)
```

- [ ] **Step 4: Persist `thumbnail_url` after attaching**

In `backfill`, the current loop body runs:

```python
        attach_thumbnail(_meeting_for(meeting_id), mdir)
        if (mdir / "thumbnail.jpg").exists():
            made += 1
            print(f"  OK   {meeting_id}")
        else:
            print(f"  FAIL {meeting_id} — extraction produced no thumbnail")
```

Change it to capture the meeting and persist `thumbnail_url` so the GUI library and any later publish see it:

```python
        meeting = _meeting_for(meeting_id)
        attach_thumbnail(meeting, mdir)
        if getattr(meeting, "thumbnail_url", None) and hasattr(meeting, "to_dict"):
            from gui.review_api import _atomic_write_text
            _atomic_write_text(
                mdir / "transcript_named.json",
                json.dumps(meeting.to_dict(), indent=2),
            )
        if (mdir / "thumbnail.jpg").exists():
            made += 1
            print(f"  OK   {meeting_id}")
        else:
            print(f"  FAIL {meeting_id} — extraction produced no thumbnail")
```

- [ ] **Step 5: Run the tests, verify they pass**

Run: `.venv/bin/python -m pytest tests/test_backfill_thumbnails.py -v`
Expected: PASS (all — new HLS selector tests plus the pre-existing ones).

- [ ] **Step 6: Commit**

```bash
git add backfill_thumbnails.py tests/test_backfill_thumbnails.py
git commit -m "feat(backfill): include HLS-source meetings and persist thumbnail_url"
```

---

## Task 3: Apply to `2026-07-16-house-floor` and verify (live)

**Files:** none (runtime application + verification). Requires `.env.local` with `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` (upload) and `DATABASE_URL` (publish state / re-publish).

- [ ] **Step 1: Dry-run the selector to confirm the meeting is now picked up**

Run:
```bash
.venv/bin/python backfill_thumbnails.py --dry-run
```
Expected: the output lists `2026-07-16-house-floor` among the meetings that would be backfilled.

- [ ] **Step 2: Backfill the thumbnail (extract + upload + persist)**

The upload needs Supabase creds, which live in `.env.local`; load them the same way the GUI server does, then run the backfill:
```bash
.venv/bin/python -c "from gui.env import load_env_local; load_env_local(); import backfill_thumbnails as b; b.backfill()"
```
Expected: prints `OK   2026-07-16-house-floor`.

- [ ] **Step 3: Verify the local + persisted results**

Run:
```bash
D=/Users/chrisandrews/CouncilScribe/meetings/2026-07-16-house-floor
ls -la "$D/thumbnail.jpg"
.venv/bin/python -c "import json; print('thumbnail_url:', json.load(open('$D/transcript_named.json')).get('thumbnail_url'))"
```
Expected: `thumbnail.jpg` exists (non-zero size); `thumbnail_url:` prints a `https://…/meeting-thumbnails/…` URL (not `None`).

- [ ] **Step 4: Confirm the uploaded image is publicly reachable**

Run (substitute the URL from Step 3):
```bash
curl -sS -m 15 -o /dev/null -w "thumb: HTTP %{http_code}  ctype=%{content_type}\n" "<thumbnail_url>"
```
Expected: `HTTP 200`, `ctype=image/jpeg`.

- [ ] **Step 5: If the meeting is published live, re-publish to push thumbnail_url to the DB**

Check publish state, then re-publish only if already live:
```bash
.venv/bin/python -c "
from gui.env import load_env_local; load_env_local()
from gui import publish_api
pid = publish_api.meeting_published_id('2026-07-16-house-floor')
print('published_id:', pid)
if pid:
    print(publish_api.apply_publish('2026-07-16-house-floor', force=True))
else:
    print('not published — thumbnail will be included when it is published')
"
```
Expected: prints the published id and a publish result dict if live; otherwise the not-published message. (Re-publish is idempotent for display metadata; `force=True` mirrors an operator override since we're only refreshing the thumbnail.)

- [ ] **Step 6: Visual confirmation in the GUI library**

Ensure a GUI dev server is running (see `.claude/launch.json` "gui", port 8000; start it with the preview tool if not up), open `http://localhost:8000/`, and confirm the `2026-07-16-house-floor` library card now shows the session-slate thumbnail. Capture a screenshot to share with the user. (If the meeting is published, the same `thumbnail_url` now backs the live meeting list.)

- [ ] **Step 7: Full suite regression check**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (no regressions).

---

## Notes for the implementer

- **Python interpreter:** always `.venv/bin/python`, never system `python3`.
- **Reuse, don't reinvent:** the HLS path reuses `extract_thumbnail` + `upload_thumbnail` unchanged — only the *source selection* changes.
- **Precedence is intentional:** local video file → HLS stream → artwork. Local wins when both exist (`test_attach_thumbnail_prefers_local_video_over_hls`).
- **Non-fatal always:** every failure mode (no ffmpeg, HLS expired, no creds) leaves `thumbnail_url` as it was and never raises — this is load-bearing for publishing.
- **Slate, not floor action:** the ~10s seek intentionally lands on the House pre-session title slate (clean, self-labeling). Don't add gavel-in seeking — that was explicitly deferred.
