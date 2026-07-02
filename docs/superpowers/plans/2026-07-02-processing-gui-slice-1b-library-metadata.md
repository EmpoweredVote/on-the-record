# Processing GUI — Slice 1b: Richer Library Metadata Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Enrich the Meeting Library rows with **review-gate badge**, **speaker count**, **duration**, and a **thumbnail** — all read from files already on disk, degrading to "—" when a field isn't available yet.

**Architecture:** Extend `gui/models.py` (`MeetingSummary` gets new fields + display helpers) and `gui/library.py` (read `transcript_named.json` for duration + identified-speaker count, fall back to `diarization.json` for raw speaker count and `soundfile` for duration; gate fields come from the already-loaded `PipelineState`; thumbnail presence is a file check). Add a **thumbnail image route** to `gui/app.py`, guarded by the same "simple path component" rule the pipeline uses (`run_local._is_simple_meeting_id`), reimplemented locally to avoid importing the heavy CLI module. Update `gui/templates/library.html` + `gui/static/style.css`. Builds directly on Slice 1 (`2026-07-02-processing-gui-slice-1-library.md`).

**Tech Stack:** Python 3, FastAPI (`FileResponse`), Jinja2, `soundfile` (already a dependency). Tests: `pytest` + `fastapi.testclient.TestClient`, reusing `tagged_meeting_dir` / `tmp_meetings_dir`.

---

### Task 1: Extend `MeetingSummary` with new fields + display helpers

**Files:**
- Modify: `gui/models.py`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_library.py`:

```python
from gui.models import gate_badge


def test_gate_badge_pass_with_coverage():
    level, text = gate_badge("pass", 0.972)
    assert level == "pass"
    assert text == "97% trusted"


def test_gate_badge_pass_without_coverage():
    assert gate_badge("pass", None) == ("pass", "passed")


def test_gate_badge_review_and_failed():
    assert gate_badge("review", None) == ("review", "needs review")
    assert gate_badge("failed", 0.4) == ("failed", "failed")


def test_gate_badge_none():
    assert gate_badge(None, None) == ("none", "—")


def test_duration_label_formats_hours_and_minutes():
    from gui.models import duration_label
    assert duration_label(10325.26) == "2h 52m"
    assert duration_label(2820) == "47m"
    assert duration_label(None) == "—"
    assert duration_label(0) == "—"


def test_meeting_summary_exposes_new_display_helpers():
    s = MeetingSummary(
        meeting_id="m", title="T", city=None, meeting_type=None, date=None,
        event_kind="council", completed_stage=5,
        speaker_count=12, duration_seconds=10325.26,
        review_status="pass", trusted_coverage=0.972, has_thumbnail=True,
    )
    assert s.speakers_label == "12"
    assert s.duration_label == "2h 52m"
    assert s.gate_badge == ("pass", "97% trusted")


def test_meeting_summary_new_fields_default_to_absent():
    s = MeetingSummary(
        meeting_id="m", title=None, city=None, meeting_type=None, date=None,
        event_kind=None, completed_stage=0,
    )
    assert s.speakers_label == "—"
    assert s.duration_label == "—"
    assert s.gate_badge == ("none", "—")
    assert s.has_thumbnail is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py -k "gate_badge or duration_label or new_display or new_fields" -v`
Expected: FAIL — `cannot import name 'gate_badge'` / `MeetingSummary` missing kwargs.

- [ ] **Step 3: Implement the additions in `gui/models.py`**

Add these two module-level functions (near `stage_label`):

```python
def gate_badge(review_status: Optional[str], trusted_coverage: Optional[float]) -> tuple[str, str]:
    """(level, text) for the confidence-gate badge. level is a CSS class token."""
    if review_status == "pass":
        if trusted_coverage is not None:
            return "pass", f"{round(trusted_coverage * 100)}% trusted"
        return "pass", "passed"
    if review_status == "review":
        return "review", "needs review"
    if review_status == "failed":
        return "failed", "failed"
    return "none", "—"


def duration_label(seconds: Optional[float]) -> str:
    """'2h 52m' / '47m' / '—' (— for None or non-positive)."""
    if not seconds or seconds <= 0:
        return "—"
    total_minutes = int(seconds // 60)
    hours, minutes = divmod(total_minutes, 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"
```

Extend the `MeetingSummary` dataclass with new optional fields (after `completed_stage`) and matching properties:

```python
    # Slice 1b: enrichment fields; all optional so older/partial meetings still build.
    speaker_count: Optional[int] = None
    duration_seconds: Optional[float] = None
    review_status: Optional[str] = None
    trusted_coverage: Optional[float] = None
    has_thumbnail: bool = False

    @property
    def speakers_label(self) -> str:
        return str(self.speaker_count) if self.speaker_count is not None else "—"

    @property
    def duration_label(self) -> str:
        return duration_label(self.duration_seconds)

    @property
    def gate_badge(self) -> tuple[str, str]:
        return gate_badge(self.review_status, self.trusted_coverage)
```

Note: `duration_label` the property and `duration_label` the module function share a name; inside the property call the module function via its global (works because the property body resolves `duration_label` to the module global, and the property is accessed as an attribute, not shadowing the global). If this reads confusingly, rename the free function `format_duration` and update the property + tests to match — pick one and keep it consistent.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: PASS (all, including prior Slice 1 tests).

- [ ] **Step 5: Commit**

```bash
git add gui/models.py tests/test_gui_library.py
git commit -m "feat(gui): MeetingSummary gate badge, speaker count, duration helpers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Read the enrichment facts in `gui/library.py`

**Files:**
- Modify: `gui/library.py`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_library.py`:

```python
def test_scan_meetings_reads_named_speaker_count_and_duration(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    # transcript_named.json: identified/merged speakers + duration live here.
    (mdir / "transcript_named.json").write_text(json.dumps({
        "title": "Council",
        "duration_seconds": 10325.26,
        "speakers": [{"speaker_label": "SPEAKER_00"}, {"speaker_label": "SPEAKER_01"},
                     {"speaker_label": "SPEAKER_02"}],
    }))
    # gate fields come from state.
    state = mdir / "pipeline_state.json"
    data = json.loads(state.read_text())
    data.update({"review_status": "pass", "trusted_coverage": 0.972})
    state.write_text(json.dumps(data))

    s = scan_meetings(tmp_meetings_dir)[0]
    assert s.speaker_count == 3
    assert round(s.duration_seconds) == 10325
    assert s.gate_badge == ("pass", "97% trusted")


def test_scan_meetings_speaker_count_falls_back_to_diarization(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-03-01-council", completed_stage=2)
    # No transcript_named yet (pre-identification); count comes from diarization labels.
    (mdir / "diarization.json").write_text(json.dumps([
        {"speaker_label": "SPEAKER_00"}, {"speaker_label": "SPEAKER_00"},
        {"speaker_label": "SPEAKER_01"},
    ]))
    s = scan_meetings(tmp_meetings_dir)[0]
    assert s.speaker_count == 2  # unique labels


def test_scan_meetings_thumbnail_flag(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-05-council", completed_stage=4)
    (mdir / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0jpegbytes")
    s = scan_meetings(tmp_meetings_dir)[0]
    assert s.has_thumbnail is True


def test_scan_meetings_enrichment_absent_is_graceful(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-06-council", completed_stage=1)
    s = scan_meetings(tmp_meetings_dir)[0]
    assert s.speaker_count is None
    assert s.duration_seconds is None
    assert s.has_thumbnail is False
    assert s.gate_badge == ("none", "—")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py -k "named_speaker or falls_back or thumbnail_flag or enrichment_absent" -v`
Expected: FAIL — `scan_meetings` doesn't populate the new fields (they're all `None`/`False`).

- [ ] **Step 3: Implement the readers in `gui/library.py`**

Add these helpers (each fully defensive — never raise):

```python
def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None


def _speaker_count(meeting_dir: Path, named: Optional[dict]) -> Optional[int]:
    """Prefer identified/merged speakers (transcript_named 'speakers'); else unique
    raw diarization labels; else None."""
    if isinstance(named, dict) and isinstance(named.get("speakers"), list):
        return len(named["speakers"])
    diar = _read_json(meeting_dir / "diarization.json")
    if isinstance(diar, list):
        labels = {s.get("speaker_label") for s in diar if isinstance(s, dict)}
        labels.discard(None)
        return len(labels) if labels else None
    return None


def _duration_seconds(meeting_dir: Path, named: Optional[dict]) -> Optional[float]:
    """transcript_named duration_seconds; else read the audio.wav header (cheap)."""
    if isinstance(named, dict) and isinstance(named.get("duration_seconds"), (int, float)):
        return float(named["duration_seconds"])
    wav = meeting_dir / "audio.wav"
    if wav.exists():
        try:
            import soundfile as sf
            return float(sf.info(str(wav)).duration)
        except Exception:
            return None
    return None
```

Update `_summarize` to populate the new fields (read `transcript_named.json` once, reuse for title + speakers + duration):

```python
def _summarize(meeting_dir: Path) -> Optional[MeetingSummary]:
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    try:
        state = PipelineState(meeting_dir)
    except Exception:
        return None  # malformed/incompatible state file — skip, don't 500
    named = _read_json(meeting_dir / "transcript_named.json")
    title = None
    if isinstance(named, dict):
        t = named.get("title")
        title = t if isinstance(t, str) and t.strip() else None
    return MeetingSummary(
        meeting_id=meeting_dir.name,
        title=title,
        city=state.city,
        meeting_type=state.meeting_type,
        date=state.date,
        event_kind=state.event_kind,
        completed_stage=int(state.completed_stage),
        speaker_count=_speaker_count(meeting_dir, named),
        duration_seconds=_duration_seconds(meeting_dir, named),
        review_status=state.review_status,
        trusted_coverage=state.trusted_coverage,
        has_thumbnail=(meeting_dir / "thumbnail.jpg").exists(),
    )
```

Delete the now-unused `_title_from_named_transcript` helper (its logic moved inline so we read `transcript_named.json` only once). Confirm no other caller references it (`grep -rn _title_from_named_transcript gui/`).

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: PASS (all, including the Slice 1 title-precedence tests still pass via the inline logic).

- [ ] **Step 5: Commit**

```bash
git add gui/library.py tests/test_gui_library.py
git commit -m "feat(gui): scan_meetings reads speaker count, duration, gate, thumbnail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Thumbnail image route (path-safe)

**Files:**
- Modify: `gui/app.py`
- Create: `gui/paths.py`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_library.py`:

```python
from gui.paths import is_safe_meeting_id


def test_is_safe_meeting_id_rejects_traversal():
    assert is_safe_meeting_id("2026-02-04-council") is True
    assert is_safe_meeting_id("..") is False
    assert is_safe_meeting_id(".") is False
    assert is_safe_meeting_id("a/b") is False
    assert is_safe_meeting_id("") is False
    assert is_safe_meeting_id("/abs") is False


def test_thumbnail_route_serves_existing_jpg(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    (mdir / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0jpegbytes")
    client = TestClient(create_app())
    resp = client.get("/meetings/2026-02-04-council/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"\xff\xd8\xff\xe0jpegbytes"


def test_thumbnail_route_404_when_missing(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    assert client.get("/meetings/2026-02-04-council/thumbnail").status_code == 404


def test_thumbnail_route_404_on_unsafe_id(tmp_meetings_dir):
    client = TestClient(create_app())
    # A dot-segment id must never resolve outside MEETINGS_DIR.
    assert client.get("/meetings/../thumbnail").status_code in (404, 400)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py -k "safe_meeting_id or thumbnail_route" -v`
Expected: FAIL — `No module named 'gui.paths'`.

- [ ] **Step 3: Implement `gui/paths.py`**

```python
"""Path-safety helpers for the GUI. Mirrors run_local._is_simple_meeting_id
without importing the heavy CLI module."""
from __future__ import annotations

from pathlib import Path


def is_safe_meeting_id(meeting_id: str) -> bool:
    """True iff meeting_id is a single, non-traversing path component."""
    return (
        bool(meeting_id)
        and meeting_id not in {".", ".."}
        and not Path(meeting_id).is_absolute()
        and Path(meeting_id).name == meeting_id
    )
```

- [ ] **Step 4: Add the route to `gui/app.py`**

Add the imports at the top:

```python
from fastapi import HTTPException
from fastapi.responses import FileResponse

from gui.paths import is_safe_meeting_id
```

Inside `create_app()`, after the library route:

```python
    @app.get("/meetings/{meeting_id}/thumbnail")
    def thumbnail(meeting_id: str) -> FileResponse:
        if not is_safe_meeting_id(meeting_id):
            raise HTTPException(status_code=404)
        path = config.MEETINGS_DIR / meeting_id / "thumbnail.jpg"
        if not path.exists():
            raise HTTPException(status_code=404)
        return FileResponse(str(path), media_type="image/jpeg")
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: PASS. (Note: `/meetings/../thumbnail` is normalized by the test client/router; the `is_safe_meeting_id` guard is the defense-in-depth for any id that does reach the handler — the direct unit test in Step 1 covers the guard explicitly.)

- [ ] **Step 6: Commit**

```bash
git add gui/paths.py gui/app.py tests/test_gui_library.py
git commit -m "feat(gui): path-safe thumbnail image route

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Render the new columns in the template

**Files:**
- Modify: `gui/templates/library.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_library.py`:

```python
def test_library_route_renders_enrichment_columns(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    (mdir / "transcript_named.json").write_text(json.dumps({
        "title": "Council", "duration_seconds": 10325.26,
        "speakers": [{"speaker_label": "A"}, {"speaker_label": "B"}, {"speaker_label": "C"}],
    }))
    (mdir / "thumbnail.jpg").write_bytes(b"\xff\xd8\xff\xe0j")
    state = mdir / "pipeline_state.json"
    data = json.loads(state.read_text()); data.update({"review_status": "pass", "trusted_coverage": 0.972})
    state.write_text(json.dumps(data))

    body = TestClient(create_app()).get("/").text
    assert "97% trusted" in body            # gate badge
    assert "2h 52m" in body                 # duration
    assert ">3<" in body or "3 speakers" in body  # speaker count (see template choice below)
    assert "/meetings/2026-02-04-council/thumbnail" in body  # thumbnail img src
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py::test_library_route_renders_enrichment_columns -v`
Expected: FAIL — the strings aren't in the current template.

- [ ] **Step 3: Update `gui/templates/library.html`**

Replace the `<thead>` row and the `<tbody>` loop body to add columns. New header row:

```html
        <tr><th>Meeting</th><th>Date</th><th>Kind</th><th>Speakers</th><th>Length</th><th>Review</th><th>Status</th></tr>
```

New row body (drops the raw ID column into a small sub-line under the name; adds thumbnail, speakers, length, gate badge):

```html
        {% for m in meetings %}
        <tr>
          <td class="name">
            {% if m.has_thumbnail %}
            <img class="thumb" src="/meetings/{{ m.meeting_id }}/thumbnail" alt="" loading="lazy">
            {% endif %}
            <div>
              <div>{{ m.display_name }}</div>
              <div class="mid">{{ m.meeting_id }}</div>
            </div>
          </td>
          <td>{{ m.date or "—" }}</td>
          <td>{{ m.event_kind or "—" }}</td>
          <td>{{ m.speakers_label }}</td>
          <td>{{ m.duration_label }}</td>
          <td>
            {% set level, text = m.gate_badge %}
            <span class="gate gate-{{ level }}">{{ text }}</span>
          </td>
          <td><span class="stage stage-{{ m.completed_stage }}">{{ m.stage_label }}</span></td>
        </tr>
        {% endfor %}
```

(The speaker-count test asserts `">3<"` — the bare `{{ m.speakers_label }}` cell renders `>3<`, so that branch matches.)

- [ ] **Step 4: Add styles in `gui/static/style.css`**

Append:

```css
td.name { display: flex; gap: 0.6rem; align-items: center; }
img.thumb { width: 64px; height: 36px; object-fit: cover; border-radius: 0.25rem; flex: none; }
.gate { font-size: 0.8rem; padding: 0.1rem 0.5rem; border-radius: 0.5rem; }
.gate-pass { background: #e6f5ea; color: #1b7a3d; }
.gate-review { background: #fdf3e0; color: #9a6a00; }
.gate-failed { background: #fdeaea; color: #b32020; }
.gate-none { color: #999; }
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add gui/templates/library.html gui/static/style.css tests/test_gui_library.py
git commit -m "feat(gui): show speakers, length, review gate, thumbnail in library

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slice 1 + 1b), no regressions.

- [ ] **Step 2: Manual smoke**

Run: `.venv/bin/python -m gui`, open http://127.0.0.1:8000 — rows now show a thumbnail, speaker count, length, and a coloured review badge; meetings lacking a field show "—". Ctrl-C to stop.

---

## Self-Review

**Spec coverage:** Review-gate badge (Task 1 `gate_badge` + Task 2 state fields + Task 4 render) ✅ · Speaker count (Task 2 `_speaker_count` named→diarization fallback + Task 4) ✅ · Duration (Task 1 `duration_label` + Task 2 `_duration_seconds` named→soundfile fallback + Task 4) ✅ · Thumbnail (Task 2 `has_thumbnail` + Task 3 path-safe route + Task 4 `<img>`) ✅ · Graceful "—" when absent (Task 1/2 tests) ✅.

**Placeholder scan:** none — every step has complete code and exact commands.

**Type consistency:** `gate_badge`/`duration_label` module functions vs `MeetingSummary` properties share names; the plan flags the shadowing and offers a rename escape hatch. New `MeetingSummary` fields (`speaker_count`, `duration_seconds`, `review_status`, `trusted_coverage`, `has_thumbnail`) used identically across `models.py`, `library.py`, template, and tests. `is_safe_meeting_id` name consistent across `gui/paths.py`, `gui/app.py`, tests. `_read_json` reused by `_speaker_count`/`_duration_seconds`/`_summarize`.
