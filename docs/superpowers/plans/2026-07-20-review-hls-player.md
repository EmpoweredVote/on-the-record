# Review HLS Player Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Play the House Clerk HLS (`.m3u8`) video already stored on House-floor meetings in the local review GUI, with exact click-to-seek, so operators can see faces while identifying speakers.

**Architecture:** The review-page builder (`gui/review_api.load_review_page`) already computes `youtube_id`, `media_kind`, and per-card `clip_seeks`. We add an `hls_url` derived from the existing `src.publish.playback_for_meeting` (which prefers `processing_metadata.source_audio_url` and returns `("hls", url)` for `.m3u8`), fold HLS into the full-source seek flag, add a new template branch that renders a `<video id="player" data-hls=...>`, and teach `review.js` to attach the stream (native HLS on Safari, vendored hls.js elsewhere). GUI-only; no DB / ev-accounts / web changes.

**Tech Stack:** Python 3 (FastAPI + Jinja2 templates), pytest, vanilla JS, hls.js (vendored from `web/node_modules`).

**Reference spec:** `docs/superpowers/specs/2026-07-20-review-hls-player-design.md`

---

## File Structure

- `gui/models.py` — add `hls_url` field to `ReviewPageData` dataclass.
- `gui/review_api.py` — in `load_review_page`, derive `hls_url` via `playback_for_meeting`, add it to the `is_full_source` flag, pass it into `ReviewPageData(...)`.
- `gui/templates/review.html` — add the `hls_url` media branch (between `youtube_id` and local-`video`); broaden the cleanup-confirm copy.
- `gui/static/review.js` — on load, attach the HLS stream to `#player` when it carries `data-hls`.
- `gui/static/hls.min.js` — **new**, vendored copy of `web/node_modules/hls.js/dist/hls.min.js` so the GUI works offline.
- `tests/test_review_hls.py` — **new**, unit coverage for the `hls_url` derivation, seek offset, and precedence.

---

## Task 1: `hls_url` field on `ReviewPageData`

**Files:**
- Modify: `gui/models.py` (the `ReviewPageData` dataclass, ~line 192-200)

- [ ] **Step 1: Add the field**

In `gui/models.py`, in the `ReviewPageData` dataclass, add `hls_url` directly under the existing `youtube_id` line:

```python
@dataclass
class ReviewPageData:
    meeting_id: str
    display_name: str
    media_kind: Optional[str]  # "video" | "audio" | None
    youtube_id: Optional[str] = None  # set when the source is a YouTube URL: review streams the embed
    hls_url: Optional[str] = None  # set when the source is an HLS .m3u8 (e.g. House Clerk CDN): review streams it via hls.js
    needs_attention: list[SpeakerCard] = field(default_factory=list)
    confirmed: list[SpeakerCard] = field(default_factory=list)
```

- [ ] **Step 2: Verify it imports**

Run: `.venv/bin/python -c "from gui.models import ReviewPageData; print(ReviewPageData(meeting_id='m', display_name='d', media_kind=None).hls_url)"`
Expected: prints `None`

- [ ] **Step 3: Commit**

```bash
git add gui/models.py
git commit -m "feat(gui): add hls_url field to ReviewPageData"
```

---

## Task 2: Derive `hls_url` in `load_review_page` (TDD)

**Files:**
- Modify: `gui/review_api.py` (`load_review_page`, ~lines 356-364 and the `ReviewPageData(...)` return ~lines 413-420)
- Test: `tests/test_review_hls.py` (new)

- [ ] **Step 1: Write the failing test**

Create `tests/test_review_hls.py`:

```python
"""load_review_page exposes an hls_url for HLS (.m3u8) sources (e.g. House Clerk
CDN), treats it as a full-source stream for seek math, and leaves non-HLS
meetings unchanged."""
from __future__ import annotations

import json

from gui.review_api import load_review_page
from src.models import Meeting, Segment, SpeakerMapping


def _seg(label, start, end, text="x"):
    return Segment(segment_id=0, start_time=start, end_time=end,
                   speaker_label=label, text=text)


def _write_meeting(mdir, mid, *, source_audio_url=None, audio_source="",
                   clip_start=None):
    """Write the on-disk files load_review_page reads. Returns nothing."""
    m0 = SpeakerMapping(speaker_label="SPEAKER_00")
    m0.speaker_name = "Rep. Smith"; m0.confidence = 1.0
    meeting = Meeting(meeting_id=mid, city=None, date="2026-07-16",
                      meeting_type="House Floor", audio_source=audio_source)
    meeting.event_kind = "floor"
    meeting.segments = [_seg("SPEAKER_00", 120.0, 150.0, "hello")]
    meeting.speakers = {"SPEAKER_00": m0}
    if source_audio_url is not None:
        meeting.processing_metadata.source_audio_url = source_audio_url
    if clip_start is not None:
        meeting.clip_start_seconds = clip_start
    (mdir / "transcript_named.json").write_text(
        json.dumps(meeting.to_dict()), encoding="utf-8")
    (mdir / "diarization.json").write_text(
        json.dumps([s.to_dict() for s in meeting.segments]), encoding="utf-8")
    (mdir / "embeddings.json").write_text(
        json.dumps({"SPEAKER_00": [1.0, 0.0]}), encoding="utf-8")
    (mdir / "audio.wav").write_bytes(b"")


def test_hls_source_sets_hls_url(tmp_meetings_dir):
    mid = "2026-07-16-house-floor"
    mdir = tmp_meetings_dir / mid; mdir.mkdir(parents=True)
    url = "https://houseliveprod.example.net/east/2026-07-16/manifest.m3u8"
    _write_meeting(mdir, mid, source_audio_url=url,
                   audio_source="https://live.house.gov/?date=2026-07-16")

    page = load_review_page(mid)

    assert page is not None
    assert page.hls_url == url
    assert page.youtube_id is None


def test_hls_seeks_carry_clip_offset(tmp_meetings_dir):
    # HLS is a full-source stream, so a clip-local candidate must add the offset.
    mid = "clipped-hls"
    mdir = tmp_meetings_dir / mid; mdir.mkdir(parents=True)
    _write_meeting(mdir, mid,
                   source_audio_url="https://x.example/manifest.m3u8",
                   clip_start=1000.0)

    page = load_review_page(mid)

    # segment start 120.0 -> base 117.0 (3s lead-in) -> +1000.0 offset = 1117.0
    card = (page.needs_attention + page.confirmed)[0]
    assert card.clip_seeks[0] == 1117.0


def test_no_hls_source_leaves_hls_url_none(tmp_meetings_dir):
    mid = "podcast-meeting"
    mdir = tmp_meetings_dir / mid; mdir.mkdir(parents=True)
    _write_meeting(mdir, mid, source_audio_url=None,
                   audio_source="https://example.com/episode-page")

    page = load_review_page(mid)

    assert page.hls_url is None
    assert page.media_kind == "audio"  # falls through to the local audio.wav
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_review_hls.py -v`
Expected: FAIL — `test_hls_source_sets_hls_url` and `test_hls_seeks_carry_clip_offset` fail with `AttributeError`/`TypeError` on `hls_url` not being populated (it defaults to `None`); the seek test fails because HLS is not yet treated as full-source (seek would be `117.0`, not `1117.0`). `test_no_hls_source_leaves_hls_url_none` may already pass.

- [ ] **Step 3: Derive `hls_url` and fold it into the full-source flag**

In `gui/review_api.py`, find this block inside `load_review_page` (~lines 356-364):

```python
    from src.publish import extract_youtube_id

    youtube_id = extract_youtube_id(meeting.audio_source or "")
    media = find_meeting_media(meeting_dir)
    media_kind = media[0] if media else None
    # Full-source playback (add clip_offset to seeks): a YouTube stream, or a local
    # full-source video. Local audio (opus/wav) is clip-local, so no offset.
    is_full_source = bool(youtube_id) or (media_kind == "video")
    clip_offset = meeting.clip_start_seconds or 0.0
```

Replace it with:

```python
    from src.publish import extract_youtube_id, playback_for_meeting

    youtube_id = extract_youtube_id(meeting.audio_source or "")
    # HLS video source (e.g. House Clerk CDN, stored as source_audio_url). Reuse
    # the site's playback resolver so review and the live site agree on "the video".
    kind, url = playback_for_meeting(meeting)
    hls_url = url if kind == "hls" else None
    media = find_meeting_media(meeting_dir)
    media_kind = media[0] if media else None
    # Full-source playback (add clip_offset to seeks): a YouTube stream, an HLS
    # stream, or a local full-source video. Local audio (opus/wav) is clip-local.
    is_full_source = bool(youtube_id) or bool(hls_url) or (media_kind == "video")
    clip_offset = meeting.clip_start_seconds or 0.0
```

- [ ] **Step 4: Pass `hls_url` into the returned `ReviewPageData`**

In the same function, the return (~lines 413-420) currently reads:

```python
    return ReviewPageData(
        meeting_id=meeting_id,
        display_name=display_name,
        media_kind=media_kind,
        youtube_id=youtube_id,
        needs_attention=needs,
        confirmed=confirmed,
    )
```

Add the `hls_url` line:

```python
    return ReviewPageData(
        meeting_id=meeting_id,
        display_name=display_name,
        media_kind=media_kind,
        youtube_id=youtube_id,
        hls_url=hls_url,
        needs_attention=needs,
        confirmed=confirmed,
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_review_hls.py -v`
Expected: PASS (all 3)

- [ ] **Step 6: Run the broader review suite for regressions**

Run: `.venv/bin/python -m pytest tests/test_review_seek.py tests/test_review_overview_render.py tests/test_gui_review.py -q`
Expected: PASS (no regressions)

- [ ] **Step 7: Commit**

```bash
git add gui/review_api.py tests/test_review_hls.py
git commit -m "feat(gui): derive hls_url for review page from playback resolver"
```

---

## Task 3: Vendor hls.js into the GUI static dir

**Files:**
- Create: `gui/static/hls.min.js` (copied binary/text asset)

- [ ] **Step 1: Copy the built library**

Run:

```bash
cp web/node_modules/hls.js/dist/hls.min.js gui/static/hls.min.js
```

- [ ] **Step 2: Record the version**

Run: `node -p "require('./web/node_modules/hls.js/package.json').version"` (or `grep '"version"' web/node_modules/hls.js/package.json`) and note it — you'll reference it in the commit message. Expected: a version like `1.6.x`.

- [ ] **Step 3: Verify it's served by the GUI static mount**

Run: `ls -la gui/static/hls.min.js`
Expected: file exists, ~500 KB. (The GUI mounts `gui/static` at `/static` via `_NoCacheStaticFiles` in `gui/app.py:49`, so it will be reachable at `/static/hls.min.js`.)

- [ ] **Step 4: Commit**

```bash
git add gui/static/hls.min.js
git commit -m "chore(gui): vendor hls.js <version> for offline review playback"
```

(Replace `<version>` with the value from Step 2.)

---

## Task 4: Render the HLS branch in the review template

**Files:**
- Modify: `gui/templates/review.html` (media panel ~lines 23-35; cleanup-confirm copy line 16)

- [ ] **Step 1: Add the HLS media branch**

In `gui/templates/review.html`, the media panel currently reads:

```html
    {% if page.youtube_id %}
      <iframe id="yt-player" class="player"
              src="https://www.youtube.com/embed/{{ page.youtube_id }}"
              title="source video" frameborder="0"
              allow="accelerometer; autoplay; encrypted-media; picture-in-picture"
              allowfullscreen></iframe>
    {% elif page.media_kind == "video" %}
      <video id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></video>
    {% elif page.media_kind == "audio" %}
      <audio id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></audio>
    {% else %}
      <p class="empty">No media found for clip playback.</p>
    {% endif %}
```

Insert the HLS branch immediately after the `youtube_id` branch (so HLS wins over a local file):

```html
    {% if page.youtube_id %}
      <iframe id="yt-player" class="player"
              src="https://www.youtube.com/embed/{{ page.youtube_id }}"
              title="source video" frameborder="0"
              allow="accelerometer; autoplay; encrypted-media; picture-in-picture"
              allowfullscreen></iframe>
    {% elif page.hls_url %}
      <video id="player" class="player" data-hls="{{ page.hls_url }}"
             controls preload="metadata"></video>
    {% elif page.media_kind == "video" %}
      <video id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></video>
    {% elif page.media_kind == "audio" %}
      <audio id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></audio>
    {% else %}
      <p class="empty">No media found for clip playback.</p>
    {% endif %}
```

- [ ] **Step 2: Broaden the cleanup-confirm copy**

On line 16, change the confirm text so it is accurate for HLS as well as YouTube. Current:

```html
          onsubmit="return confirm('Delete the local video and WAV for this meeting? A compressed audio copy is kept, and (for YouTube sources) the video streams from the source.');">
```

Change to:

```html
          onsubmit="return confirm('Delete the local video and WAV for this meeting? A compressed audio copy is kept, and (for streamed sources) the video streams from the source.');">
```

- [ ] **Step 3: Commit**

```bash
git add gui/templates/review.html
git commit -m "feat(gui): render HLS source in review media panel"
```

---

## Task 5: Attach the HLS stream in `review.js`

**Files:**
- Modify: `gui/static/review.js` (append an init block; the existing seek handler is unchanged)

- [ ] **Step 1: Add the HLS attach block**

The clip-click handler already does `player.currentTime = seek; player.play()` for the `#player` element — that works for an HLS `<video>` once media is attached, so it needs no change. Add a new self-invoking init block at the **end** of `gui/static/review.js`:

```javascript
// HLS attach: a House Clerk (or other .m3u8) source renders as
// <video id="player" data-hls="..."> with no src. Safari plays HLS natively;
// elsewhere we lazy-load the vendored hls.js and attach the stream. The clip
// seek handler above is unchanged — it seeks #player once media is attached.
(function () {
  const video = document.getElementById("player");
  if (!video) return;
  const src = video.getAttribute("data-hls");
  if (!src) return;

  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = src; // Safari: native HLS
    return;
  }

  const script = document.createElement("script");
  script.src = "/static/hls.min.js";
  script.onload = function () {
    if (window.Hls && window.Hls.isSupported()) {
      const hls = new window.Hls();
      hls.loadSource(src);
      hls.attachMedia(video);
    }
    // No MSE + non-Safari: leave the empty <video>; transcript/review still work.
  };
  document.head.appendChild(script);
})();
```

- [ ] **Step 2: Sanity-check the JS parses**

Run: `node --check gui/static/review.js`
Expected: no output (exit 0). If `node` is unavailable, skip — Step 3's browser check covers it.

- [ ] **Step 3: Commit**

```bash
git add gui/static/review.js
git commit -m "feat(gui): attach HLS stream to review player via hls.js"
```

---

## Task 6: End-to-end verification in the browser

**Files:** none (manual verification via the preview tools).

- [ ] **Step 1: Start the GUI dev server**

Ensure `.claude/launch.json` has an entry that runs the GUI (FastAPI/uvicorn on port 8000). If present, start it with the preview tool by name; otherwise add one that launches `gui` (e.g. `uvicorn gui.asgi:app --port 8000`) and start it. Confirm it serves on `http://localhost:8000`.

- [ ] **Step 2: Open the House-floor review page**

Navigate to `http://localhost:8000/meetings/2026-07-16-house-floor/review`.

- [ ] **Step 3: Confirm the video renders and plays**

Read the page and confirm a `<video id="player">` is present (not the audio-only element). Check the console for hls.js fatal errors (`read_console_messages`). Confirm the House Clerk video loads and shows picture. (Note: the House CDN manifest was confirmed live at plan time; if it has since expired, the `<video>` will be empty — that is the documented degraded state, and the fix is still correct.)

- [ ] **Step 4: Confirm click-to-seek alignment**

Click a speaker card's clip button and confirm the video seeks to that moment and the on-screen speaker matches the transcript text — timestamps should align exactly (this meeting has no clip offset).

- [ ] **Step 5: Confirm non-House meetings are unaffected**

Open a YouTube-sourced meeting's review page and confirm the YouTube iframe still renders; open an audio-only (podcast) meeting and confirm the `<audio>` element still renders.

- [ ] **Step 6: Capture proof**

Take a screenshot of the House-floor review page with the video visible, to share with the user.

- [ ] **Step 7: Final full-suite run**

Run: `.venv/bin/python -m pytest tests/ -q`
Expected: PASS (no regressions across the suite).

---

## Notes for the implementer

- **Python interpreter:** always use `.venv/bin/python`, not system `python3` (project deps live in the venv).
- **Reuse over reinvention:** `hls_url` is derived from `src.publish.playback_for_meeting`, the same resolver the live site uses — do not re-parse `source_audio_url` by hand.
- **Precedence is intentional:** HLS is placed above the local-`video` branch so the richest, disk-free source wins whenever present.
- **Degraded, never broken:** if the CDN stream is gone or the browser lacks MSE, the `<video>` is simply empty; the transcript, cards, and seek buttons still render. The durable YouTube+offset fallback is a deliberately deferred follow-up (see the spec).
