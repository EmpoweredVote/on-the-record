# Processing GUI — Slice 1: Meeting Library Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A local FastAPI app that scans the meetings directory and serves a browser page listing every processed meeting with its friendly stage/status.

**Architecture:** New top-level `gui/` package. A pure `library.scan_meetings()` reads each meeting's `pipeline_state.json` (via the existing `PipelineState`) into `MeetingSummary` dataclasses; a FastAPI route renders them with a Jinja2 template. No subprocess, no `review.py`, no Supabase yet — this slice only *reads local state*, proving the core plumbing the later slices build on. See `2026-07-02-processing-gui-overview.md` for the full design.

**Tech Stack:** Python 3, FastAPI, Jinja2, uvicorn. Tests use `pytest` + `fastapi.testclient.TestClient` (via `httpx`), reusing `tmp_meetings_dir` / `tagged_meeting_dir` fixtures in `tests/conftest.py`.

---

### Task 1: Add web dependencies

**Files:**
- Modify: `requirements.txt`

- [ ] **Step 1: Append the GUI runtime + test deps**

Add these lines to the end of `requirements.txt`:

```
fastapi>=0.110
uvicorn>=0.29
jinja2>=3.1
python-multipart>=0.0.9
httpx>=0.27
```

(`python-multipart` and `httpx` are needed by later slices' form handling and by `TestClient`; adding them now avoids a second dependency commit.)

- [ ] **Step 2: Install into the project venv**

Run: `.venv/bin/pip install fastapi uvicorn jinja2 python-multipart httpx`
Expected: installs succeed; `.venv/bin/python -c "import fastapi, jinja2, httpx; print('ok')"` prints `ok`.

- [ ] **Step 3: Commit**

```bash
git add requirements.txt
git commit -m "chore: add FastAPI/Jinja2 deps for processing GUI"
```

---

### Task 2: `MeetingSummary` model + friendly stage labels

**Files:**
- Create: `gui/__init__.py`
- Create: `gui/models.py`
- Test: `tests/test_gui_library.py`

- [ ] **Step 1: Create the empty package marker**

Create `gui/__init__.py`:

```python
"""Local single-user processing GUI for the CouncilScribe pipeline."""
```

- [ ] **Step 2: Write the failing test for stage labels + the model**

Create `tests/test_gui_library.py`:

```python
from __future__ import annotations

from gui.models import MeetingSummary, stage_label


def test_stage_label_maps_each_stage_to_friendly_text():
    assert stage_label(0) == "Not started"
    assert stage_label(1) == "Audio ingested"
    assert stage_label(2) == "Speakers separated"
    assert stage_label(3) == "Transcribed"
    assert stage_label(4) == "Identified — ready to review"
    assert stage_label(5) == "Summarized"
    assert stage_label(6) == "Voices enrolled"
    assert stage_label(7) == "Published"


def test_stage_label_tolerates_unknown_stage():
    assert stage_label(99) == "Unknown (99)"


def test_meeting_summary_display_name_prefers_title():
    s = MeetingSummary(
        meeting_id="2026-02-04-regular-session",
        title="Budget Hearing",
        city="Bloomington",
        meeting_type="Regular Session",
        date="2026-02-04",
        event_kind="council",
        completed_stage=4,
    )
    assert s.display_name == "Budget Hearing"
    assert s.stage_label == "Identified — ready to review"


def test_meeting_summary_display_name_falls_back_to_city_and_type():
    s = MeetingSummary(
        meeting_id="2026-02-04-regular-session",
        title=None,
        city="Bloomington",
        meeting_type="Regular Session",
        date="2026-02-04",
        event_kind="council",
        completed_stage=2,
    )
    assert s.display_name == "Bloomington Regular Session"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gui.models'`.

- [ ] **Step 4: Implement `gui/models.py`**

Create `gui/models.py`:

```python
"""GUI-facing view models. No HTTP, no I/O — pure data + display helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Friendly labels for src.checkpoint.PipelineStage values (0..7). Kept here (not
# imported from checkpoint) so the label wording is a GUI concern the pipeline
# can't accidentally change.
_STAGE_LABELS = {
    0: "Not started",
    1: "Audio ingested",
    2: "Speakers separated",
    3: "Transcribed",
    4: "Identified — ready to review",
    5: "Summarized",
    6: "Voices enrolled",
    7: "Published",
}


def stage_label(completed_stage: int) -> str:
    """Human label for a PipelineStage integer value."""
    return _STAGE_LABELS.get(completed_stage, f"Unknown ({completed_stage})")


@dataclass
class MeetingSummary:
    """One row in the meeting library. Built from pipeline_state.json (+ title
    from transcript_named.json when present)."""

    meeting_id: str
    title: Optional[str]
    city: Optional[str]
    meeting_type: Optional[str]
    date: Optional[str]
    event_kind: Optional[str]
    completed_stage: int

    @property
    def stage_label(self) -> str:
        return stage_label(self.completed_stage)

    @property
    def display_name(self) -> str:
        """Title if set, else 'City MeetingType', else the meeting_id."""
        if self.title and self.title.strip():
            return self.title.strip()
        parts = [p for p in (self.city, self.meeting_type) if p and p.strip()]
        return " ".join(parts) if parts else self.meeting_id
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: PASS (4 passed).

- [ ] **Step 6: Commit**

```bash
git add gui/__init__.py gui/models.py tests/test_gui_library.py
git commit -m "feat(gui): MeetingSummary view model + friendly stage labels"
```

---

### Task 3: `scan_meetings()` — read the meetings dir into summaries

**Files:**
- Create: `gui/library.py`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_library.py`:

```python
import json

from gui.library import scan_meetings


def test_scan_meetings_reads_state_and_sorts_by_date_desc(tagged_meeting_dir, tmp_meetings_dir):
    # tagged_meeting_dir writes pipeline_state.json with completed_stage + body_slug.
    older = tagged_meeting_dir(
        "bloomington-common-council",
        meeting_id="2026-01-10-regular-session",
        completed_stage=4,
    )
    newer = tagged_meeting_dir(
        "bloomington-common-council",
        meeting_id="2026-03-02-special-session",
        completed_stage=2,
    )
    # Enrich one state file with the newer metadata keys the GUI displays.
    state_path = older / "pipeline_state.json"
    data = json.loads(state_path.read_text())
    data.update({"city": "Bloomington", "meeting_type": "Regular Session",
                 "date": "2026-01-10", "event_kind": "council"})
    state_path.write_text(json.dumps(data))

    summaries = scan_meetings(tmp_meetings_dir)

    assert [s.meeting_id for s in summaries] == [
        "2026-03-02-special-session",  # newer date first
        "2026-01-10-regular-session",
    ]
    older_summary = summaries[1]
    assert older_summary.city == "Bloomington"
    assert older_summary.completed_stage == 4
    assert older_summary.stage_label == "Identified — ready to review"


def test_scan_meetings_reads_title_from_named_transcript(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    # transcript_named.json holds the Meeting dict; title lives there, not in state.
    (mdir / "transcript_named.json").write_text(json.dumps({"title": "Budget Hearing"}))

    summaries = scan_meetings(tmp_meetings_dir)

    assert summaries[0].title == "Budget Hearing"
    assert summaries[0].display_name == "Budget Hearing"


def test_scan_meetings_missing_dir_returns_empty(tmp_path):
    assert scan_meetings(tmp_path / "does-not-exist") == []


def test_scan_meetings_skips_dirs_without_state(tmp_meetings_dir):
    (tmp_meetings_dir / "stray-dir").mkdir()
    assert scan_meetings(tmp_meetings_dir) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gui.library'`.

- [ ] **Step 3: Implement `gui/library.py`**

Create `gui/library.py`:

```python
"""Read the meetings directory into a sorted list of MeetingSummary rows.

Pure filesystem reads — no HTTP. Reuses src.checkpoint.PipelineState so the
GUI and the pipeline agree on how pipeline_state.json is parsed (and tolerate
older state files missing the newer metadata keys)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from src.checkpoint import PipelineState

from gui.models import MeetingSummary


def _title_from_named_transcript(meeting_dir: Path) -> Optional[str]:
    """Title is stored on the Meeting (transcript_named.json), not in state."""
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        data = json.loads(named.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return None
    title = data.get("title")
    return title if isinstance(title, str) and title.strip() else None


def _summarize(meeting_dir: Path) -> Optional[MeetingSummary]:
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    state = PipelineState(meeting_dir)
    return MeetingSummary(
        meeting_id=meeting_dir.name,
        title=_title_from_named_transcript(meeting_dir),
        city=state.city,
        meeting_type=state.meeting_type,
        date=state.date,
        event_kind=state.event_kind,
        completed_stage=int(state.completed_stage),
    )


def scan_meetings(meetings_dir: Path) -> list[MeetingSummary]:
    """All meetings under meetings_dir, newest date first (missing dates last)."""
    if not meetings_dir.exists():
        return []
    summaries: list[MeetingSummary] = []
    for child in sorted(meetings_dir.iterdir()):
        if not child.is_dir():
            continue
        summary = _summarize(child)
        if summary is not None:
            summaries.append(summary)
    # Sort by date descending; None/empty dates sort last. Secondary key on
    # meeting_id keeps the order stable/deterministic for equal dates.
    summaries.sort(key=lambda s: (s.date or "", s.meeting_id), reverse=True)
    return summaries
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: PASS (all tests in the file).

- [ ] **Step 5: Commit**

```bash
git add gui/library.py tests/test_gui_library.py
git commit -m "feat(gui): scan_meetings reads pipeline state into summaries"
```

---

### Task 4: FastAPI app + library route + template

**Files:**
- Create: `gui/app.py`
- Create: `gui/templates/library.html`
- Create: `gui/static/style.css`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing HTTP test**

Append to `tests/test_gui_library.py`:

```python
from fastapi.testclient import TestClient

from gui.app import create_app


def test_library_route_renders_meetings(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-regular-session", completed_stage=4)
    client = TestClient(create_app())

    resp = client.get("/")

    assert resp.status_code == 200
    body = resp.text
    assert "2026-02-04-regular-session" in body
    assert "Identified — ready to review" in body


def test_library_route_empty_state(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.get("/")
    assert resp.status_code == 200
    assert "No meetings processed yet" in resp.text
```

Note: `tmp_meetings_dir` monkeypatches `src.config.MEETINGS_DIR`; the route reads it via `src.config` at request time so the patch takes effect.

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py::test_library_route_renders_meetings -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gui.app'`.

- [ ] **Step 3: Create the Jinja template**

Create `gui/templates/library.html`:

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CouncilScribe — Meeting Library</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header><h1>Meeting Library</h1></header>
  <main>
    {% if meetings %}
    <table class="library">
      <thead>
        <tr><th>Meeting</th><th>Date</th><th>Kind</th><th>Status</th><th>ID</th></tr>
      </thead>
      <tbody>
        {% for m in meetings %}
        <tr>
          <td>{{ m.display_name }}</td>
          <td>{{ m.date or "—" }}</td>
          <td>{{ m.event_kind or "—" }}</td>
          <td><span class="stage stage-{{ m.completed_stage }}">{{ m.stage_label }}</span></td>
          <td class="mid">{{ m.meeting_id }}</td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}
    <p class="empty">No meetings processed yet.</p>
    {% endif %}
  </main>
</body>
</html>
```

- [ ] **Step 4: Create a minimal stylesheet**

Create `gui/static/style.css`:

```css
:root { font-family: -apple-system, system-ui, sans-serif; color: #1a1a1a; }
body { margin: 0; }
header { padding: 1rem 1.5rem; border-bottom: 1px solid #e2e2e2; }
h1 { margin: 0; font-size: 1.25rem; }
main { padding: 1.5rem; }
table.library { width: 100%; border-collapse: collapse; }
table.library th, table.library td { text-align: left; padding: 0.5rem 0.75rem; border-bottom: 1px solid #eee; }
td.mid { font-family: ui-monospace, monospace; font-size: 0.8rem; color: #666; }
.stage { font-size: 0.85rem; padding: 0.1rem 0.5rem; border-radius: 0.5rem; background: #eef; }
.empty { color: #888; }
```

- [ ] **Step 5: Implement `gui/app.py`**

Create `gui/app.py`:

```python
"""FastAPI app factory for the processing GUI.

Slice 1: a single library route. Later slices mount review/launch/publish
routers onto the same app."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src import config

from gui.library import scan_meetings

_GUI_DIR = Path(__file__).resolve().parent
_templates = Jinja2Templates(directory=str(_GUI_DIR / "templates"))


def create_app() -> FastAPI:
    app = FastAPI(title="CouncilScribe GUI")
    app.mount("/static", StaticFiles(directory=str(_GUI_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def library(request: Request) -> HTMLResponse:
        # Read MEETINGS_DIR via the module at request time so tests that
        # monkeypatch src.config.MEETINGS_DIR are honored.
        meetings = scan_meetings(config.MEETINGS_DIR)
        return _templates.TemplateResponse(
            request, "library.html", {"meetings": meetings}
        )

    return app
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py -v`
Expected: PASS (all tests).

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/library.html gui/static/style.css tests/test_gui_library.py
git commit -m "feat(gui): FastAPI library route rendering processed meetings"
```

---

### Task 5: `python -m gui` entrypoint

**Files:**
- Create: `gui/__main__.py`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_library.py`:

```python
def test_main_module_exposes_app_factory():
    import gui.__main__ as entry
    assert hasattr(entry, "main")
    # create_app is importable and returns a FastAPI instance
    from gui.app import create_app
    from fastapi import FastAPI
    assert isinstance(create_app(), FastAPI)
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py::test_main_module_exposes_app_factory -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'gui.__main__'`.

- [ ] **Step 3: Implement `gui/__main__.py`**

Create `gui/__main__.py`:

```python
"""Run the GUI: `python -m gui` → http://127.0.0.1:8000"""
from __future__ import annotations

import uvicorn

from gui.app import create_app


def main() -> None:
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py::test_main_module_exposes_app_factory -v`
Expected: PASS.

- [ ] **Step 5: Manual smoke test**

Run: `.venv/bin/python -m gui`
Expected: uvicorn logs `Uvicorn running on http://127.0.0.1:8000`. Open that URL — the library page renders your real processed meetings (or "No meetings processed yet"). Ctrl-C to stop.

- [ ] **Step 6: Commit**

```bash
git add gui/__main__.py tests/test_gui_library.py
git commit -m "feat(gui): python -m gui entrypoint (uvicorn on localhost:8000)"
```

---

### Task 6: Full-suite regression check

- [ ] **Step 1: Run the whole test suite**

Run: `.venv/bin/pytest -q`
Expected: all tests pass, including the new `tests/test_gui_library.py`. No existing test regresses (the `gui/` package is additive and imports `src` read-only).

- [ ] **Step 2: If green, Slice 1 is complete.** Proceed to writing `slice-2-review.md`.

---

## Self-Review

**Spec coverage (against the overview's Slice 1 scope):**
- "Read `MEETINGS_DIR` + each `pipeline_state.json`" → Task 3 (`scan_meetings` via `PipelineState`). ✅
- "List processed meetings with stage/status" → Task 4 (route + template) + Task 2 (`stage_label`). ✅
- "Proves the plumbing / immediately useful" → Task 5 (`python -m gui` smoke test). ✅
- Title source nuance (state has no title; it's in `transcript_named.json`) → Task 3 `_title_from_named_transcript`. ✅
- Tolerates older/minimal state files → `PipelineState` defaults + `test_scan_meetings_skips_dirs_without_state`. ✅

**Placeholder scan:** No TBDs; every code step shows complete code; every run step states the exact command and expected result. ✅

**Type consistency:** `MeetingSummary` fields (`meeting_id`, `title`, `city`, `meeting_type`, `date`, `event_kind`, `completed_stage`) are used identically in `models.py`, `library.py`, the template, and tests. `stage_label` is both a module function (Task 2) and a `@property` delegating to it — consistent. `create_app()` factory name used identically in `app.py`, `__main__.py`, and tests. ✅
