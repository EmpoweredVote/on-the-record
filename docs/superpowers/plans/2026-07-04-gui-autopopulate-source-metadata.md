# GUI Autopopulate Source Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When the operator pastes a yt-dlp-supported URL into the new-meeting form, autofill the empty Date, Title, and Event org(s) fields from the video's own metadata.

**Architecture:** Extract the yt-dlp `extract_info` block already inline in `ingest.normalize_audio` into a shared `fetch_source_metadata(url)` helper (single source of truth for pipeline + form). Expose it via a thin `GET /api/source-meta` GUI endpoint. Wire the form's URL field to call it on blur and fill only-empty fields client-side.

**Tech Stack:** Python, yt-dlp, FastAPI (sync routes → threadpool), Jinja2 templates, vanilla JS, pytest + FastAPI `TestClient`.

**Spec:** [docs/superpowers/specs/2026-07-04-gui-autopopulate-source-metadata-design.md](../specs/2026-07-04-gui-autopopulate-source-metadata-design.md)

---

## File Structure

- `src/ingest.py` — add `fetch_source_metadata(url) -> dict`; refactor `normalize_audio` to call it. (`normalize_chapters`, the `_is_url`/download imports already live here.)
- `gui/app.py` — add `GET /api/source-meta` route (mirrors the existing `/api/politicians/search` JSONResponse pattern).
- `gui/templates/new_meeting.html` — add an empty `<small>` note element under the source-URL label.
- `gui/static/new_meeting.js` — debounced blur/change fetch + empty-only autofill + note updates.
- `tests/test_source_meta.py` — unit tests for `fetch_source_metadata`.
- `tests/test_gui_launch.py` — add endpoint tests (existing GUI form test file).

---

## Task 1: `fetch_source_metadata` helper

**Files:**
- Modify: `src/ingest.py` (add function near `normalize_chapters`, ~line 92)
- Test: `tests/test_source_meta.py`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_meta.py`:

```python
from __future__ import annotations

from src import ingest


class _FakeYDL:
    """Context-manager stand-in for yt_dlp.YoutubeDL."""

    def __init__(self, info):
        self._info = info

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def extract_info(self, url, download=False):
        if isinstance(self._info, Exception):
            raise self._info
        return self._info


def _patch_ydl(monkeypatch, info):
    import yt_dlp

    monkeypatch.setattr(yt_dlp, "YoutubeDL", lambda opts: _FakeYDL(info))


def test_fetch_source_metadata_maps_fields(monkeypatch):
    _patch_ydl(monkeypatch, {
        "title": "City Council Feb 10",
        "uploader": "CBS Evening News",
        "upload_date": "20260210",
        "duration": 3600,
        "chapters": [],
    })
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta["title"] == "City Council Feb 10"
    assert meta["channel"] == "CBS Evening News"
    assert meta["upload_date"] == "2026-02-10"
    assert meta["duration"] == 3600
    assert meta["chapters"] == []


def test_fetch_source_metadata_channel_fallback(monkeypatch):
    # No uploader → fall back to channel.
    _patch_ydl(monkeypatch, {"title": "t", "channel": "WFYI", "upload_date": ""})
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta["channel"] == "WFYI"


def test_fetch_source_metadata_missing_and_malformed(monkeypatch):
    _patch_ydl(monkeypatch, {"upload_date": "2026"})  # too short → None
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta["title"] is None
    assert meta["channel"] is None
    assert meta["upload_date"] is None
    assert meta["duration"] is None
    assert meta["chapters"] == []


def test_fetch_source_metadata_swallows_extractor_error(monkeypatch):
    _patch_ydl(monkeypatch, RuntimeError("private video"))
    meta = ingest.fetch_source_metadata("https://youtube.com/watch?v=x")
    assert meta == {
        "title": None, "channel": None, "upload_date": None,
        "duration": None, "chapters": [],
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_source_meta.py -v`
Expected: FAIL — `AttributeError: module 'src.ingest' has no attribute 'fetch_source_metadata'`

- [ ] **Step 3: Implement the helper**

Add to `src/ingest.py` (after `normalize_chapters`, before `_is_url`):

```python
def _normalize_upload_date(raw: str | None) -> str | None:
    """yt-dlp's 'YYYYMMDD' → 'YYYY-MM-DD'; None for missing/malformed."""
    if not raw or len(raw) != 8 or not raw.isdigit():
        return None
    return f"{raw[0:4]}-{raw[4:6]}-{raw[6:8]}"


def fetch_source_metadata(url: str) -> dict:
    """Fetch a video's metadata via yt-dlp without downloading it.

    Single source of truth for what we pull off a source video, reused by both
    the processing pipeline (normalize_audio) and the GUI new-meeting form.
    Best-effort: any extractor error yields an all-empty dict.

    Returns {title, channel, upload_date ('YYYY-MM-DD'), duration (s), chapters}.
    """
    empty = {"title": None, "channel": None, "upload_date": None,
             "duration": None, "chapters": []}
    try:
        import yt_dlp

        with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception:
        return empty

    return {
        "title": info.get("title") or None,
        "channel": info.get("uploader") or info.get("channel") or None,
        "upload_date": _normalize_upload_date(info.get("upload_date")),
        "duration": info.get("duration") or None,
        "chapters": normalize_chapters(info),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_source_meta.py -v`
Expected: PASS (4 tests)

- [ ] **Step 5: Commit**

```bash
git add src/ingest.py tests/test_source_meta.py
git commit -m "feat: add fetch_source_metadata yt-dlp helper"
```

---

## Task 2: Refactor `normalize_audio` to use the helper

**Files:**
- Modify: `src/ingest.py:176-185` (the inline yt-dlp block inside `normalize_audio`)

Keeps the pipeline and form on identical extraction logic. No behavior change → covered by existing `ingest` tests as a regression check.

- [ ] **Step 1: Replace the inline extract block**

In `normalize_audio`, replace the current block:

```python
        if is_ytdlp_url(source_str):
            try:
                import yt_dlp
                with yt_dlp.YoutubeDL({"quiet": True, "no_warnings": True, "skip_download": True}) as ydl:
                    info = ydl.extract_info(source_str, download=False)
                    source_title = info.get("title") or None
                    source_channel = info.get("uploader") or info.get("channel") or None
                    source_chapters = normalize_chapters(info)
            except Exception:
                pass
```

with:

```python
        if is_ytdlp_url(source_str):
            meta = fetch_source_metadata(source_str)
            source_title = meta["title"]
            source_channel = meta["channel"]
            source_chapters = meta["chapters"]
```

- [ ] **Step 2: Run the ingest test suite to verify no regression**

Run: `.venv/bin/python -m pytest tests/ -k "ingest or chapter or normalize" -v`
Expected: PASS (existing tests unchanged)

- [ ] **Step 3: Commit**

```bash
git add src/ingest.py
git commit -m "refactor: normalize_audio uses fetch_source_metadata helper"
```

---

## Task 3: `GET /api/source-meta` endpoint

**Files:**
- Modify: `gui/app.py` (add route near `/api/politicians/search`, ~line 84; add import at top)
- Test: `tests/test_gui_launch.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_launch.py`:

```python
def test_source_meta_non_ytdlp_returns_empty(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.get("/api/source-meta", params={"url": "/path/to/video.mp4"})
    assert resp.status_code == 200
    assert resp.json() == {"date": None, "title": None, "event_org": None}


def test_source_meta_ytdlp_returns_mapped_json(tmp_meetings_dir, monkeypatch):
    from src import ingest

    monkeypatch.setattr(ingest, "fetch_source_metadata", lambda url: {
        "title": "City Council Feb 10", "channel": "WFYI",
        "upload_date": "2026-02-10", "duration": 3600, "chapters": [],
    })
    client = TestClient(create_app())
    resp = client.get("/api/source-meta",
                      params={"url": "https://youtube.com/watch?v=x"})
    assert resp.status_code == 200
    assert resp.json() == {
        "date": "2026-02-10", "title": "City Council Feb 10", "event_org": "WFYI",
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -k source_meta -v`
Expected: FAIL — 404 (route not defined) so `resp.json()` mismatches.

- [ ] **Step 3: Add the route**

In `gui/app.py`, add the import near the other `src` import (line 15):

```python
from src import config
from src import ingest
from src.download import is_ytdlp_url
```

Add the route immediately after `politician_search` (~line 86):

```python
    @app.get("/api/source-meta")
    def source_meta(url: str = "") -> JSONResponse:
        # Only video URLs carry fetchable metadata; local paths / other URLs
        # return an empty payload the client treats as "nothing to fill".
        if not is_ytdlp_url(url):
            return JSONResponse({"date": None, "title": None, "event_org": None})
        # Look up ingest.fetch_source_metadata at call time so tests can
        # monkeypatch it on the module.
        meta = ingest.fetch_source_metadata(url)
        return JSONResponse({
            "date": meta["upload_date"],
            "title": meta["title"],
            "event_org": meta["channel"],
        })
```

Note: this is a plain `def` (not `async def`) so FastAPI runs the blocking
yt-dlp call in its threadpool.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -k source_meta -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_launch.py
git commit -m "feat: add GET /api/source-meta endpoint"
```

---

## Task 4: Note element in the form template

**Files:**
- Modify: `gui/templates/new_meeting.html:10-12` (the source-URL label)

- [ ] **Step 1: Add the note element**

Replace the source-URL label block:

```html
      <label>Source URL or file path
        <input type="text" name="input" id="f-input" required placeholder="https://… or /path/to/video.mp4">
      </label>
```

with:

```html
      <label>Source URL or file path
        <input type="text" name="input" id="f-input" required placeholder="https://… or /path/to/video.mp4">
        <small class="help" id="source-meta-note" aria-live="polite"></small>
      </label>
```

- [ ] **Step 2: Verify the form still renders**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py::test_new_form_renders -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add gui/templates/new_meeting.html
git commit -m "feat: add source-meta note element to new-meeting form"
```

---

## Task 5: Client-side fetch + empty-only autofill

**Files:**
- Modify: `gui/static/new_meeting.js`

No unit test (vanilla DOM glue); verified manually via the preview workflow in
Task 6. Logic is kept minimal and defensive.

- [ ] **Step 1: Add the autofill block**

In `gui/static/new_meeting.js`, add references and the fetch logic. Inside the
IIFE, extend the `input` object and add the block below before the event-wiring
at the bottom (before `main.querySelectorAll(...)`):

```javascript
  const sourceInput = $("f-input");
  const note = $("source-meta-note");
  let lastFetched = null;

  const looksLikeUrl = (s) => /^https?:\/\//i.test(s.trim());

  function fillIfEmpty(el, value) {
    if (el && value && el.value.trim() === "") el.value = value;
  }

  async function fetchSourceMeta() {
    const url = sourceInput.value.trim();
    if (!looksLikeUrl(url) || url === lastFetched) return;
    lastFetched = url;
    note.textContent = "Fetching video details…";
    try {
      const resp = await fetch("/api/source-meta?url=" + encodeURIComponent(url));
      if (!resp.ok) throw new Error("bad status");
      const data = await resp.json();
      if (!data.date && !data.title && !data.event_org) {
        note.textContent = "";  // non-video URL or nothing to fill
        return;
      }
      fillIfEmpty(input.date, data.date);
      fillIfEmpty(input.title, data.title);
      fillIfEmpty($("f-orgs"), data.event_org);
      note.textContent = "";
      refresh();
    } catch (e) {
      note.textContent = "Couldn't fetch details — fill in manually.";
    }
  }

  sourceInput.addEventListener("blur", fetchSourceMeta);
  sourceInput.addEventListener("change", fetchSourceMeta);
```

- [ ] **Step 2: Verify the form still renders and JS loads**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py::test_new_form_renders -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add gui/static/new_meeting.js
git commit -m "feat: autofill new-meeting form from source metadata"
```

---

## Task 6: Manual browser verification

**Files:** none (verification only)

- [ ] **Step 1: Start the GUI dev server**

Start the GUI via the preview tooling (create `.claude/launch.json` if absent,
pointing at the GUI's uvicorn/`python -m gui` entrypoint on its configured port).

- [ ] **Step 2: Exercise the autofill on the /new page**

Navigate to `/new`. In the source field, paste a public YouTube URL, then blur
(Tab out). Verify:
- The "Fetching video details…" note appears, then clears.
- Empty Date / Title / Event org(s) fields populate; the preview card updates.
- Re-blurring the same URL does not re-fetch (no note flash).
- Typing a value into a field, then blurring the URL again, does NOT overwrite
  the typed value.
- Pasting a local path (e.g. `/tmp/x.mp4`) shows no note and fills nothing.

- [ ] **Step 3: Confirm no console errors**

Check preview console logs are clean during the above.

---

## Self-Review Notes

- **Spec coverage:** shared helper (Task 1) + `upload_date` normalization (Task 1) + `normalize_audio` refactor (Task 2) + endpoint with `200`/empty contract (Task 3) + note element (Task 4) + debounced blur / empty-only / fail-quiet / preview-refresh client wiring (Task 5) + no-live-network tests (Tasks 1, 3) + manual verify (Task 6). Duration is captured in the helper per spec.
- **Type consistency:** helper returns keys `title/channel/upload_date/duration/chapters`; endpoint maps `upload_date→date`, `channel→event_org`; `fetch_source_metadata` referenced identically in Tasks 1-3.
- **Out of scope confirmed:** no event_kind guessing, no duration display, no channel→org mapping.
