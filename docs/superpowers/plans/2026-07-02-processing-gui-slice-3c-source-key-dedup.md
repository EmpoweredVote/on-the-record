# Processing GUI — Slice 3c: Source-Key Duplicate Detection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Stop the "same video processed twice" duplicate. Introduces the **source key** ([CONTEXT.md](../../CONTEXT.md)): a normalized identity of a source recording. The GUI checks a new input's source key against existing meetings *before* launching and, on a match, warns instead of silently creating a second meeting.

**This is the project's first pipeline touch — deliberately isolated + additive:**
- new `src/source_key.py` — pure normalizer (no network).
- `PipelineState` gains a `source_key` field (`src/checkpoint.py`), persisted.
- `run_local.py` records it at Stage 1 (2-line additive write; no existing behavior changes).
- GUI: `find_meeting_by_source` scan + a pre-launch confirm flow.

Existing meetings (no `source_key` in state yet) are covered by a **fallback**: normalize their stored `transcript_named.json` `audio_source`. So dedup works on day one, before any meeting has the new field.

**Deferred:** 3d — error always-tier catalog.

**Goal:** Re-submitting a URL you've already processed shows "already grabbed as `<id>`" with Open / Process-anyway, instead of making a duplicate.

**Architecture:** `source_key(raw)` → `youtube:<id>` (all YouTube URL shapes converge), else `url:<host><path><non-tracking-query>`, else `file:<abspath>`. `run_local` writes `state.source_key` in the existing metadata-persist block. `gui/runner.find_meeting_by_source(input)` scans `MEETINGS_DIR`, comparing `source_key(input)` to each meeting's `state.source_key` (or the normalized `audio_source` fallback). `POST /new` (no `confirm`) → if a match, render a confirm page; with `confirm=1`, skip the check and launch. Builds on 3a/3b.

**Tech Stack:** stdlib `urllib.parse`, `src.checkpoint`, `src.source_key`. Tests: `pytest` + `TestClient`.

---

### Task 1: `src/source_key.py` — pure normalizer

**Files:**
- Create: `src/source_key.py`
- Test: `tests/test_source_key.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_source_key.py`:

```python
from __future__ import annotations

from src.source_key import source_key


def test_youtube_shapes_converge():
    k = "youtube:dQw4w9WgXcQ"
    assert source_key("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == k
    assert source_key("https://youtu.be/dQw4w9WgXcQ") == k
    assert source_key("https://m.youtube.com/watch?v=dQw4w9WgXcQ&t=90s") == k
    assert source_key("https://youtube.com/watch?v=dQw4w9WgXcQ&feature=share") == k
    assert source_key("https://www.youtube.com/shorts/dQw4w9WgXcQ") == k


def test_different_youtube_ids_differ():
    assert source_key("https://youtu.be/aaaaaaaaaaa") != source_key("https://youtu.be/bbbbbbbbbbb")


def test_generic_url_normalized():
    # host lowercased, trailing slash + fragment dropped, tracking params removed
    a = source_key("https://CATSTV.blob.core.windows.net/videoarchive/2026/foo.mp4")
    assert a == "url:catstv.blob.core.windows.net/videoarchive/2026/foo.mp4"
    assert source_key("https://ex.com/v/?utm_source=x#frag") == "url:ex.com/v"


def test_local_file_absolute():
    assert source_key("/tmp/meeting.mp4") == "file:/tmp/meeting.mp4"
    assert source_key("file:///tmp/meeting.mp4") == "file:/tmp/meeting.mp4"


def test_empty_is_empty():
    assert source_key("") == ""
    assert source_key("   ") == ""
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_source_key.py -v`
Expected: FAIL — `No module named 'src.source_key'`.

- [ ] **Step 3: Implement `src/source_key.py`**

```python
"""Normalize a source recording reference (URL or path) to a stable 'source key'.

One source key identifies one recording regardless of how its URL was typed, so
the GUI can detect 'already processed this' before launching a duplicate. Pure
and network-free — see CONTEXT.md 'Source key'."""
from __future__ import annotations

import os
from urllib.parse import parse_qs, parse_qsl, urlencode, urlparse

_YT_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com",
             "music.youtube.com", "youtu.be"}
_TRACKING = {"t", "feature", "utm_source", "utm_medium", "utm_campaign", "si", "pp"}


def _youtube_id(parsed) -> str | None:
    host = parsed.netloc.lower()
    if host == "youtu.be":
        vid = parsed.path.lstrip("/").split("/")[0]
        return vid or None
    if host in _YT_HOSTS:
        qs = parse_qs(parsed.query)
        if qs.get("v"):
            return qs["v"][0]
        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in ("shorts", "embed", "live", "v"):
            return parts[1]
    return None


def source_key(raw: str) -> str:
    """Stable identity for a source. '' for blank input."""
    s = (raw or "").strip()
    if not s:
        return ""
    parsed = urlparse(s)
    if parsed.scheme in ("http", "https"):
        yid = _youtube_id(parsed)
        if yid:
            return f"youtube:{yid}"
        host = parsed.netloc.lower()
        path = parsed.path.rstrip("/")
        q = sorted((k, v) for k, v in parse_qsl(parsed.query) if k.lower() not in _TRACKING)
        qstr = ("?" + urlencode(q)) if q else ""
        return f"url:{host}{path}{qstr}"
    if parsed.scheme == "file":
        return f"file:{os.path.abspath(parsed.path)}"
    return f"file:{os.path.abspath(s)}"
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_source_key.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/source_key.py tests/test_source_key.py
git commit -m "feat: source_key normalizer for duplicate detection

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `PipelineState.source_key` field

**Files:**
- Modify: `src/checkpoint.py`
- Test: `tests/test_checkpoint_source_key.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_checkpoint_source_key.py`:

```python
from __future__ import annotations

from src.checkpoint import PipelineState


def test_source_key_persists_and_reloads(tmp_path):
    st = PipelineState(tmp_path)
    assert st.source_key is None            # default
    st.source_key = "youtube:abc123"
    st.save()

    st2 = PipelineState(tmp_path)            # fresh load from disk
    assert st2.source_key == "youtube:abc123"


def test_source_key_absent_in_old_state_defaults_none(tmp_path):
    import json
    (tmp_path / "pipeline_state.json").write_text(json.dumps({"completed_stage": 4}))
    assert PipelineState(tmp_path).source_key is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_checkpoint_source_key.py -v`
Expected: FAIL — `PipelineState` has no `source_key`.

- [ ] **Step 3: Add the field in `src/checkpoint.py`**

In `__init__` (near the other optional fields, e.g. after `self.meeting_type = None`):

```python
        self.source_key: Optional[str] = None
```

In `_load` (near the other `data.get(...)` reads):

```python
            self.source_key = data.get("source_key")
```

In `save`'s `data` dict (add a key):

```python
            "source_key": self.source_key,
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_checkpoint_source_key.py -v`
Expected: PASS.

- [ ] **Step 5: Run the existing checkpoint suite (guard against regressions in the shared class)**

Run: `.venv/bin/pytest tests/ -k checkpoint -v`
Expected: PASS (all existing checkpoint tests still green — the field is additive).

- [ ] **Step 6: Commit**

```bash
git add src/checkpoint.py tests/test_checkpoint_source_key.py
git commit -m "feat: persist source_key on PipelineState (additive field)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `run_local.py` records source_key at Stage 1 (2-line additive)

**Files:**
- Modify: `run_local.py`
- Test: manual/inspection (run_local's `run_pipeline` has no unit harness; the change is additive and the GUI's audio_source fallback covers dedup regardless)

- [ ] **Step 1: Locate the existing metadata-persist block**

In `run_pipeline`, find the block that sets `_state_dirty` for `event_kind`/`city`/`date`/`meeting_type` and then `state.save()` (around the "Persist pipeline metadata so --resume can recover" comment). This runs early, after `state` is created and `audio_path` is known.

- [ ] **Step 2: Add source_key recording into that block**

Immediately before the `if _state_dirty: state.save()` line, add:

```python
    # Record the normalized source key so the GUI can detect duplicate grabs.
    if audio_path and not state.source_key:
        from src.source_key import source_key as _source_key
        _sk = _source_key(str(audio_path))
        if _sk and state.source_key != _sk:
            state.source_key = _sk
            _state_dirty = True
```

- [ ] **Step 3: Byte-compile check (no unit harness for run_pipeline)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('run_local.py').read()); print('run_local.py parses OK')"`
Expected: `run_local.py parses OK`.

Run: `.venv/bin/python -c "import run_local; print('imports OK')"`
Expected: `imports OK` (module imports without executing the pipeline).

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "feat: run_local records source_key on the meeting state at ingest

Additive: writes a new PipelineState.source_key field the GUI uses for
duplicate detection. Changes no existing pipeline behavior.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `find_meeting_by_source` scan (with audio_source fallback)

**Files:**
- Modify: `gui/runner.py`
- Test: `tests/test_gui_runner.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_runner.py`:

```python
def test_find_meeting_by_source_matches_state_key(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import find_meeting_by_source
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=4)
    # record a source_key on the meeting's state
    from src.checkpoint import PipelineState
    st = PipelineState(mdir)
    st.source_key = "youtube:abc123"
    st.save()

    assert find_meeting_by_source("https://youtu.be/abc123") == "2026-02-10-regular"
    assert find_meeting_by_source("https://youtu.be/different") is None


def test_find_meeting_by_source_fallback_to_audio_source(tagged_meeting_dir, tmp_meetings_dir):
    import json
    from gui.runner import find_meeting_by_source
    mdir = tagged_meeting_dir("x", meeting_id="2026-03-01-regular", completed_stage=4)
    # NO source_key in state; only a transcript_named.json with audio_source
    (mdir / "transcript_named.json").write_text(json.dumps(
        {"audio_source": "https://www.youtube.com/watch?v=zzz999"}))
    assert find_meeting_by_source("https://youtu.be/zzz999") == "2026-03-01-regular"


def test_find_meeting_by_source_blank_returns_none(tmp_meetings_dir):
    from gui.runner import find_meeting_by_source
    assert find_meeting_by_source("") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "find_meeting_by_source" -v`
Expected: FAIL — function missing.

- [ ] **Step 3: Implement in `gui/runner.py`**

```python
def _meeting_source_key(meeting_dir) -> Optional[str]:
    """A meeting's source key: the recorded state field, else derived from the
    saved audio_source (covers meetings processed before the field existed)."""
    from src.source_key import source_key
    state_file = meeting_dir / "pipeline_state.json"
    if state_file.exists():
        try:
            sk = json.loads(state_file.read_text()).get("source_key")
            if sk:
                return sk
        except (ValueError, OSError, AttributeError):
            pass
    named = meeting_dir / "transcript_named.json"
    if named.exists():
        try:
            audio_source = json.loads(named.read_text()).get("audio_source")
            if audio_source:
                return source_key(audio_source)
        except (ValueError, OSError, AttributeError):
            pass
    return None


def find_meeting_by_source(raw_input: str) -> Optional[str]:
    """meeting_id of an existing meeting sharing this input's source key, or None."""
    from src.source_key import source_key
    key = source_key(raw_input)
    if not key:
        return None
    if not config.MEETINGS_DIR.exists():
        return None
    for child in sorted(config.MEETINGS_DIR.iterdir()):
        if not child.is_dir():
            continue
        if _meeting_source_key(child) == key:
            return child.name
    return None
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "find_meeting_by_source" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/runner.py tests/test_gui_runner.py
git commit -m "feat(gui): find_meeting_by_source scan (state key + audio_source fallback)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Pre-launch dedup confirm flow in `POST /new`

**Files:**
- Modify: `gui/app.py`
- Create: `gui/templates/dedup_confirm.html`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_launch.py`:

```python
def test_post_new_warns_on_duplicate_source(tagged_meeting_dir, tmp_meetings_dir):
    from src.checkpoint import PipelineState
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=4)
    st = PipelineState(mdir); st.source_key = "youtube:dup123"; st.save()

    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://youtu.be/dup123", "date": "2026-05-05", "meeting_type": "Regular",
        "event_kind": "other",
    }, follow_redirects=False)
    # Not launched: a confirm page (200) naming the existing meeting.
    assert resp.status_code == 200
    assert "already" in resp.text.lower()
    assert "2026-02-10-regular" in resp.text


def test_post_new_confirm_bypasses_dedup(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from src.checkpoint import PipelineState
    from gui import runner
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=4)
    st = PipelineState(mdir); st.source_key = "youtube:dup123"; st.save()
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-05-05-regular")

    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://youtu.be/dup123", "date": "2026-05-05", "meeting_type": "Regular",
        "event_kind": "other", "confirm": "1",
    }, follow_redirects=False)
    assert resp.status_code == 303  # confirmed -> launched


def test_post_new_no_duplicate_launches(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-05-05-regular")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://youtu.be/brandnew", "date": "2026-05-05", "meeting_type": "Regular",
        "event_kind": "other",
    }, follow_redirects=False)
    assert resp.status_code == 303
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "duplicate_source or confirm_bypasses or no_duplicate_launches" -v`
Expected: FAIL — no dedup branch yet (duplicate currently launches → 303, not 200).

- [ ] **Step 3: Add the dedup branch to `gui/app.py` `new_meeting_launch`**

Add a `confirm: str = Form("")` parameter to the handler signature. After the required-field + city guards, before building `RunParams` / launching:

```python
        if not confirm.strip():
            existing = runner.find_meeting_by_source(input)
            if existing:
                from src.checkpoint import PipelineState
                st = PipelineState(config.MEETINGS_DIR / existing)
                return _templates.TemplateResponse(
                    request, "dedup_confirm.html",
                    {
                        "existing_id": existing,
                        "completed_stage": int(st.completed_stage),
                        "review_status": st.review_status,
                        # echo the form so "Process anyway" can resubmit with confirm=1
                        "form": {
                            "input": input, "date": date, "meeting_type": meeting_type,
                            "event_kind": event_kind, "city": city, "title": title,
                            "compute": compute, "diarizer": diarizer,
                            "clip_start": clip_start, "clip_end": clip_end,
                        },
                    },
                )
```

(Place this so it runs only when `confirm` is empty; when `confirm=1` the whole block is skipped and the existing launch path runs.)

- [ ] **Step 4: Create `gui/templates/dedup_confirm.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Already processed?</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/new">← Back to form</a><h1>You've already grabbed this source</h1></header>
  <main class="review">
    <div class="error-banner" style="background:#fdf3e0;color:#9a6a00;border-color:#e0c07a;">
      This source is already processed as <strong>{{ existing_id }}</strong>
      (stage {{ completed_stage }}/7{% if review_status %}, review: {{ review_status }}{% endif %}).
      One source recording maps to one meeting — you probably want to open it.
    </div>
    <p>
      <a class="enroll" href="/meetings/{{ existing_id }}/review" style="text-decoration:none;">→ Open existing meeting</a>
    </p>
    <form method="post" action="/new">
      {% for k, v in form.items() %}<input type="hidden" name="{{ k }}" value="{{ v }}">{% endfor %}
      <input type="hidden" name="confirm" value="1">
      <button type="submit" class="mark">Process anyway (re-run this source)</button>
    </form>
  </main>
</body></html>
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "duplicate_source or confirm_bypasses or no_duplicate_launches" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/app.py gui/templates/dedup_confirm.html tests/test_gui_launch.py
git commit -m "feat(gui): pre-launch duplicate-source warning + confirm-to-proceed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full-suite regression + smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1–3c + src tests), no regressions — especially existing `checkpoint`/`identify`/pipeline tests (the `source_key` field is additive).

- [ ] **Step 2: Manual smoke** (dedup only — no real launch)

Run: `.venv/bin/python -m gui`, open `/new`. Enter a URL whose video you've already processed (or temporarily set a known meeting's `pipeline_state.json` `source_key`), fill date/type, submit → you get the **"already grabbed this source"** page naming the existing meeting, with **Open existing** + **Process anyway**. A brand-new URL submits straight to the run page. Ctrl-C to stop.

---

## Self-Review

**Spec coverage:** source_key normalizer, YouTube shapes converge (Task 1) ✅ · additive PipelineState.source_key persisted (Task 2) ✅ · run_local records it at ingest, no behavior change (Task 3) ✅ · GUI scan with audio_source fallback so existing meetings are covered before the field exists (Task 4) ✅ · pre-launch warn + Open/Process-anyway, confirm=1 bypass (Task 5) ✅ · deferred 3d error catalog ✅.

**Placeholder scan:** none.

**Type consistency:** `source_key(raw) -> str` used by run_local, `_meeting_source_key`, and `find_meeting_by_source`. `PipelineState.source_key: Optional[str]` added to `__init__`/`_load`/`save` consistently. `find_meeting_by_source(raw) -> Optional[str]` → meeting_id; POST /new renders `dedup_confirm.html` on truthy result unless `confirm` set. The confirm page re-POSTs the full echoed form + `confirm=1`, matching the handler's Form params. run_local change lands in the existing `_state_dirty` block (guarded by `audio_path and not state.source_key`, idempotent). No existing pipeline behavior altered — the field is write-only-new and read only by the GUI.
