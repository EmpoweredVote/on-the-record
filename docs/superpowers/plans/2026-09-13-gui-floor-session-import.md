# GUI Floor-Session Import (Slice 4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator pull a cloud-processed House-CDN floor session onto the Mac (as a small JSON-only GitHub Actions artifact) so it appears in the local GUI and can be reviewed, voice-enrolled, and re-published — closing the floor voice-profile gap without moving the pipeline to the cloud.

**Architecture:** The weekly floor workflow stages each processed session's JSON files (no raw audio) and uploads them as one artifact per run. On the Mac, a new `gui/floor_import.py` lists draft floor sessions from the DB and imports a chosen one by shelling out to `gh run download`, extracting straight into `MEETINGS_DIR/<slug>`. The existing library scan then shows it, the review page plays clips from the House CDN HLS URL, and enrollment reads the imported `embeddings.json`. Publish is an idempotent upsert by the deterministic `<date>-house-floor` slug, so re-publishing overwrites the same draft row.

**Tech Stack:** FastAPI + Jinja2 + vanilla JS (no build step); GitHub Actions (`actions/upload-artifact`); the `gh` CLI on the Mac; psycopg2 for the DB read (existing pattern).

## Global Constraints

- Stack is fixed: FastAPI + Jinja2 + vanilla JS, **no build step**. The only new external dependency is the **`gh` CLI on the operator's Mac** (already authenticated as chrisandrewsedu; the repo is public). No new Python runtime dependency.
- App factory `gui.app.create_app()`; tests use `.venv/bin/python -m pytest tests/test_gui_*.py`, `TestClient(create_app())`, fixtures `tmp_meetings_dir`/`tagged_meeting_dir` (`tests/conftest.py`).
- **Subagents MUST NOT start a server or touch port 8000**, and MUST NOT run real `gh`/`git` network calls or touch the prod DB. Mock `subprocess` and the DB query in tests. The workflow change is live-verified by the operator via a manual `workflow_dispatch`.
- **Import writes directly to `MEETINGS_DIR/<slug>`** — never through `gui/runner.launch_run` (its `_unique_meeting_id` would fork to `<slug>-2`). Verified: the `-2` risk exists only on the launch path (`gui/runner.py:207-231`).
- **JSON-only artifact requires the CDN HLS URL to be present** in each session's `transcript_named.json` / `processing_metadata.source_audio_url`, so `playback_for_meeting` resolves to `hls` and clips stream from the CDN (`gui/review_api.py:599-600`, `src/publish.py:96-111`). If it is absent the review page shows "No media found" — Task 1 guards this.
- Floor slug is deterministic: `f"{date}-house-floor"` (`run_local.py:846-888`, `src/floor_dispatch.py:112-122`). Publish upserts by slug (`src/publish.py:233-355`). Do not change either.
- Do not rely on `reenroll_profiles.py` (needs `audio.wav`) or the local `/meetings/{id}/media` route (needs a local file) — neither is on the GUI enroll or HLS-clip path.

---

### Task 1: Weekly workflow — stage JSON-only artifacts and upload

Stage each processed floor session's JSON files (excluding audio/video) and upload them as one artifact per run, guarding that each has a CDN HLS URL.

**Files:**
- Create: `bench/stage_floor_artifacts.py` (staging script — pure, testable).
- Modify: `.github/workflows/house-floor-weekly.yml` (add a stage step + `actions/upload-artifact`).
- Test: `tests/test_stage_floor_artifacts.py` (new).

**Interfaces:**
- Produces: `stage_floor_artifacts.stage(meetings_dir: Path, out_dir: Path) -> list[dict]` — copies, for every `*-house-floor` folder under `meetings_dir`, these files into `out_dir/<slug>/`: `pipeline_state.json`, `transcript_named.json`, `embeddings.json`, and (if present) `diarization.json`, `thumbnail.jpg`. **Never** copies `audio.wav`/`audio.*`/video. Writes `out_dir/manifest.json` = a list of `{slug, date, gate_verdict, gate_coverage, has_hls}`. Returns that list. A session with no resolvable HLS URL is still staged but flagged `has_hls: false` and logged as a warning.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_stage_floor_artifacts.py
import json
from pathlib import Path

from bench.stage_floor_artifacts import stage


def _floor_meeting(root: Path, slug: str, *, hls: bool = True) -> Path:
    d = root / slug
    d.mkdir(parents=True)
    (d / "pipeline_state.json").write_text(json.dumps({
        "completed_stage": 4, "event_kind": "floor", "date": slug[:10],
        "review_status": "review", "trusted_coverage": 0.63,
        "processing_metadata": {"source_audio_url":
            "https://cdn.example/house.m3u8" if hls else ""},
    }))
    (d / "transcript_named.json").write_text(json.dumps({"title": "House Floor"}))
    (d / "embeddings.json").write_text(json.dumps({"SPEAKER_00": [0.1, 0.2]}))
    (d / "diarization.json").write_text(json.dumps([{"speaker_label": "SPEAKER_00"}]))
    (d / "audio.wav").write_bytes(b"RIFFxxxx")  # must NOT be staged
    return d


def test_stage_copies_json_only_and_excludes_audio(tmp_path):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    _floor_meeting(meetings, "2026-09-03-house-floor")
    (meetings / "2026-09-03-council").mkdir()  # non-floor: ignored
    (meetings / "2026-09-03-council" / "pipeline_state.json").write_text("{}")

    out = tmp_path / "staging"
    result = stage(meetings, out)

    staged = out / "2026-09-03-house-floor"
    assert (staged / "pipeline_state.json").exists()
    assert (staged / "transcript_named.json").exists()
    assert (staged / "embeddings.json").exists()
    assert not (staged / "audio.wav").exists()          # audio excluded
    assert not (out / "2026-09-03-council").exists()     # non-floor excluded
    manifest = json.loads((out / "manifest.json").read_text())
    assert manifest == result
    assert result[0]["slug"] == "2026-09-03-house-floor"
    assert result[0]["has_hls"] is True


def test_stage_flags_sessions_without_hls(tmp_path):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    _floor_meeting(meetings, "2026-09-04-house-floor", hls=False)
    result = stage(meetings, tmp_path / "staging")
    assert result[0]["has_hls"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_stage_floor_artifacts.py -v`
Expected: FAIL (`bench/stage_floor_artifacts` does not exist).

- [ ] **Step 3: Implement the staging script**

```python
# bench/stage_floor_artifacts.py
"""Stage JSON-only copies of processed House-floor meeting folders for upload.

Excludes raw audio/video: the local GUI enroll path reads embeddings.json, and
review clips stream from the CDN HLS URL, so no audio is needed on the Mac.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

_JSON_FILES = ("pipeline_state.json", "transcript_named.json",
               "embeddings.json", "diarization.json")
_EXTRA = ("thumbnail.jpg",)


def _hls_url(state: dict) -> str:
    meta = state.get("processing_metadata") or {}
    return (meta.get("source_audio_url") or state.get("audio_source") or "")


def stage(meetings_dir: Path, out_dir: Path) -> list[dict]:
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest: list[dict] = []
    for d in sorted(meetings_dir.glob("*-house-floor")):
        state_path = d / "pipeline_state.json"
        if not state_path.exists():
            continue
        try:
            state = json.loads(state_path.read_text())
        except json.JSONDecodeError:
            continue
        dest = out_dir / d.name
        dest.mkdir(parents=True, exist_ok=True)
        for name in (*_JSON_FILES, *_EXTRA):
            src = d / name
            if src.exists():
                shutil.copy2(src, dest / name)
        has_hls = bool(_hls_url(state))
        if not has_hls:
            print(f"WARNING: {d.name} has no CDN HLS url; review playback will be unavailable")
        manifest.append({
            "slug": d.name,
            "date": state.get("date"),
            "gate_verdict": state.get("review_status"),
            "gate_coverage": state.get("trusted_coverage"),
            "has_hls": has_hls,
        })
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


if __name__ == "__main__":  # pragma: no cover
    import os
    meetings = Path(os.environ.get("MEETINGS_DIR", "")) or Path.cwd()
    stage(meetings, Path("artifact_staging"))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_stage_floor_artifacts.py -v`
Expected: PASS.

- [ ] **Step 5: Add the workflow steps**

In `.github/workflows/house-floor-weekly.yml`, after the `python -m src.floor_dispatch …` step, add:

```yaml
      - name: Stage floor session artifacts (JSON only)
        if: always()
        run: .venv/bin/python bench/stage_floor_artifacts.py
        env:
          MEETINGS_DIR: ${{ env.MEETINGS_DIR }}   # same dir the dispatch step wrote to

      - name: Upload floor sessions
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: house-floor-sessions
          path: artifact_staging
          retention-days: 30
          if-no-files-found: warn
```

Match `MEETINGS_DIR` to whatever the dispatch step uses (confirm the env var name the runner reads for the meetings dir; if the dispatch step derives it from `config.DRIVE_ROOT`, export the same value here). `if: always()` uploads whatever completed even if a later session failed.

- [ ] **Step 6: Validate + commit**

Run: `.venv/bin/python -c "import yaml; yaml.safe_load(open('.github/workflows/house-floor-weekly.yml'))"`
Expected: no error.

```bash
git add bench/stage_floor_artifacts.py tests/test_stage_floor_artifacts.py .github/workflows/house-floor-weekly.yml
git commit -m "feat(floor): upload JSON-only session artifacts from the weekly workflow"
```

- [ ] **Step 7: Operator live verification (not a subagent)**

Operator triggers a manual `workflow_dispatch`, confirms the `house-floor-sessions` artifact appears on the run with per-slug JSON folders + `manifest.json` and **no** `audio.wav`, and that `has_hls` is `true` for a substantive session. (This also confirms floor runs persist the CDN URL; if `has_hls` is false, floor runs are not storing `source_audio_url` and that must be fixed in the pipeline before import is useful — surface to the user.)

---

### Task 2: List available floor sessions (DB discovery)

Add a DB-backed listing of draft floor sessions, marking which are already present locally.

**Files:**
- Create: `gui/floor_import.py`.
- Test: `tests/test_gui_floor_import.py` (new).

**Interfaces:**
- Consumes: the `DATABASE_URL` psycopg2 pattern from `gui/publish_api.py`.
- Produces:
  - `FloorSession` dataclass: `slug: str`, `date: str | None`, `gate_verdict: str | None`, `gate_coverage: float | None`, `is_local: bool`.
  - `list_floor_sessions(meetings_dir: Path) -> list[FloorSession]` — queries `meetings.meetings` for `status='draft'` rows whose slug ends in `-house-floor`, newest first, and sets `is_local` by checking `meetings_dir/<slug>` exists. Returns `[]` if the DB is not configured (mirrors `publish_api` returning `None`/empty when `DATABASE_URL` is unset).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_floor_import.py
from pathlib import Path

import gui.floor_import as fi


def test_list_floor_sessions_marks_local(monkeypatch, tmp_path):
    rows = [
        {"slug": "2026-09-03-house-floor", "date": "2026-09-03",
         "gate_verdict": "review", "gate_coverage": 0.63},
        {"slug": "2026-09-04-house-floor", "date": "2026-09-04",
         "gate_verdict": "review", "gate_coverage": 0.0},
    ]
    monkeypatch.setattr(fi, "_query_draft_floor_rows", lambda: rows)
    (tmp_path / "2026-09-03-house-floor").mkdir()  # already pulled

    out = fi.list_floor_sessions(tmp_path)

    assert [s.slug for s in out] == ["2026-09-03-house-floor", "2026-09-04-house-floor"]
    assert out[0].is_local is True and out[1].is_local is False
    assert out[0].gate_coverage == 0.63


def test_list_floor_sessions_empty_without_db(monkeypatch, tmp_path):
    monkeypatch.setattr(fi, "_query_draft_floor_rows", lambda: [])
    assert fi.list_floor_sessions(tmp_path) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_floor_import.py -v`
Expected: FAIL (`gui.floor_import` does not exist).

- [ ] **Step 3: Implement discovery**

```python
# gui/floor_import.py
"""Import cloud-processed House-floor sessions onto the Mac for local review."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class FloorSession:
    slug: str
    date: str | None
    gate_verdict: str | None
    gate_coverage: float | None
    is_local: bool


def _query_draft_floor_rows() -> list[dict]:
    """Draft floor meetings from the DB, newest first. [] if DB not configured."""
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return []
    import psycopg2  # local import: keeps import-time hermetic for tests
    from psycopg2.extras import RealDictCursor
    sql = (
        "SELECT slug, date::text AS date, "
        "  processing_metadata->>'gate_verdict' AS gate_verdict, "
        "  (processing_metadata->>'gate_coverage')::float AS gate_coverage "
        "FROM meetings.meetings "
        "WHERE status = 'draft' AND slug LIKE %s "
        "ORDER BY date DESC"
    )
    with psycopg2.connect(dsn) as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:
            cur.execute(sql, ("%-house-floor",))
            return [dict(r) for r in cur.fetchall()]


def list_floor_sessions(meetings_dir: Path) -> list[FloorSession]:
    out: list[FloorSession] = []
    for r in _query_draft_floor_rows():
        slug = r["slug"]
        out.append(FloorSession(
            slug=slug, date=r.get("date"),
            gate_verdict=r.get("gate_verdict"),
            gate_coverage=r.get("gate_coverage"),
            is_local=(meetings_dir / slug).is_dir(),
        ))
    return out
```

Confirm the gate field names against a real draft row: the house-floor automation stores gate data in `processing_metadata` (`gate_verdict`/`gate_coverage` per the `house-floor-weekly-automation` note). If the keys differ, adjust the two `->>` extractions to match.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_floor_import.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/floor_import.py tests/test_gui_floor_import.py
git commit -m "feat(gui): list draft House-floor sessions from the DB"
```

---

### Task 3: Import one session via `gh` into `MEETINGS_DIR/<slug>`

Download the artifact containing a chosen slug and extract that folder locally, without clobbering local edits and without the `-2` fork.

**Files:**
- Modify: `gui/floor_import.py` (add `import_session`).
- Test: `tests/test_gui_floor_import.py` (extend).

**Interfaces:**
- Consumes: `gh` on PATH; the meetings dir.
- Produces: `import_session(slug: str, meetings_dir: Path, *, runner=subprocess.run, max_runs: int = 6, force: bool = False) -> Path` — searches the most recent `max_runs` `house-floor-weekly.yml` runs, downloads their `house-floor-sessions` artifact, and when one contains `<slug>/pipeline_state.json`, copies that folder to `meetings_dir/<slug>` and returns it. Raises `FloorImportError` if not found in the searched runs. If `meetings_dir/<slug>` already exists and `force` is False, raises `FloorAlreadyLocalError` (protects local review edits). Writes directly to `meetings_dir/<slug>` — never via `launch_run`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_floor_import.py
import json
import gui.floor_import as fi


def _fake_gh(tmp_download_root):
    """A subprocess.run stand-in for the gh calls import_session makes."""
    def run(cmd, *a, **k):
        import types
        if cmd[:2] == ["gh", "run"] and "list" in cmd:
            return types.SimpleNamespace(
                returncode=0,
                stdout=json.dumps([{"databaseId": 111}, {"databaseId": 222}]))
        if cmd[:2] == ["gh", "run"] and "download" in cmd:
            # -D <dir> is where gh would place the artifact contents
            out = tmp_download_root / cmd[cmd.index("-D") + 1]
            slug = "2026-09-03-house-floor"
            # Only run 222's artifact contains the slug.
            if "222" in cmd:
                d = out / slug
                d.mkdir(parents=True, exist_ok=True)
                (d / "pipeline_state.json").write_text(json.dumps(
                    {"completed_stage": 4, "event_kind": "floor"}))
                (d / "transcript_named.json").write_text(json.dumps({"title": "Floor"}))
                (d / "embeddings.json").write_text("{}")
            else:
                out.mkdir(parents=True, exist_ok=True)
            return types.SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(f"unexpected gh call: {cmd}")
    return run


def test_import_session_extracts_slug_folder(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    monkeypatch.setattr(fi, "_download_root", lambda: tmp_path / "dl")
    dest = fi.import_session("2026-09-03-house-floor", meetings,
                             runner=_fake_gh(tmp_path / "dl"))
    assert dest == meetings / "2026-09-03-house-floor"
    assert (dest / "pipeline_state.json").exists()
    assert (dest / "embeddings.json").exists()


def test_import_session_refuses_to_clobber_local(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"; (meetings / "2026-09-03-house-floor").mkdir(parents=True)
    monkeypatch.setattr(fi, "_download_root", lambda: tmp_path / "dl")
    import pytest
    with pytest.raises(fi.FloorAlreadyLocalError):
        fi.import_session("2026-09-03-house-floor", meetings, runner=_fake_gh(tmp_path / "dl"))


def test_import_session_not_found_raises(tmp_path, monkeypatch):
    meetings = tmp_path / "meetings"; meetings.mkdir()
    monkeypatch.setattr(fi, "_download_root", lambda: tmp_path / "dl")
    import pytest
    with pytest.raises(fi.FloorImportError):
        fi.import_session("2099-01-01-house-floor", meetings, runner=_fake_gh(tmp_path / "dl"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_floor_import.py -k import_session -v`
Expected: FAIL (`import_session` / error classes not defined).

- [ ] **Step 3: Implement the importer**

```python
# add to gui/floor_import.py
import json
import shutil
import subprocess
import tempfile


class FloorImportError(RuntimeError):
    pass


class FloorAlreadyLocalError(FloorImportError):
    pass


_WORKFLOW = "house-floor-weekly.yml"
_ARTIFACT = "house-floor-sessions"


def _download_root() -> Path:
    return Path(tempfile.mkdtemp(prefix="floor-import-"))


def import_session(slug: str, meetings_dir: Path, *, runner=subprocess.run,
                   max_runs: int = 6, force: bool = False) -> Path:
    dest = meetings_dir / slug
    if dest.exists() and not force:
        raise FloorAlreadyLocalError(f"{slug} already exists locally; delete it or pass force")

    listed = runner(["gh", "run", "list", "--workflow", _WORKFLOW,
                     "--json", "databaseId", "-L", str(max_runs)],
                    capture_output=True, text=True)
    if listed.returncode != 0:
        raise FloorImportError(f"gh run list failed: {getattr(listed, 'stderr', '')}")
    run_ids = [r["databaseId"] for r in json.loads(listed.stdout or "[]")]

    root = _download_root()
    for rid in run_ids:
        sub = root / str(rid)
        got = runner(["gh", "run", "download", str(rid), "-n", _ARTIFACT, "-D", str(rid)],
                     capture_output=True, text=True, cwd=str(root))
        candidate = sub / slug
        if got.returncode == 0 and (candidate / "pipeline_state.json").exists():
            meetings_dir.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(candidate, dest)
            return dest
    raise FloorImportError(f"{slug} not found in the last {max_runs} {_WORKFLOW} runs")
```

(The `-D str(rid)` with `cwd=root` places each run's artifact under `root/<rid>`, matching the test's `_fake_gh`. Keep the download path and the `candidate` path in sync if you refactor.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_floor_import.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/floor_import.py tests/test_gui_floor_import.py
git commit -m "feat(gui): import a floor session artifact via gh into MEETINGS_DIR/<slug>"
```

---

### Task 4: "House floor sessions" GUI view + pull action

Surface the sessions and wire the pull button; after import the session flows through the existing library → review → enroll → publish path.

**Files:**
- Create: `gui/templates/floor_import.html` (extends `base.html` from Slice 1).
- Modify: `gui/app.py` (add `GET /floor` and `POST /floor/import`), `gui/templates/library.html` (add a "House floor" link next to Discovery).
- Test: `tests/test_gui_floor_import.py` (extend with route tests).

**Interfaces:**
- Consumes: `floor_import.list_floor_sessions`, `floor_import.import_session`; `config.MEETINGS_DIR`.
- Produces: `GET /floor` renders the session list; `POST /floor/import` (form field `slug`) imports then 303-redirects to `/meetings/<slug>` (lands the operator on the review workspace). On `FloorAlreadyLocalError` it redirects to the existing local meeting; on `FloorImportError` it re-renders `/floor` with an error banner.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_floor_import.py
from fastapi.testclient import TestClient
from gui.app import create_app
import gui.app as gui_app


def test_floor_route_lists_sessions(monkeypatch, tmp_meetings_dir):
    monkeypatch.setattr(fi, "list_floor_sessions", lambda md: [
        fi.FloorSession("2026-09-03-house-floor", "2026-09-03", "review", 0.63, False),
        fi.FloorSession("2026-09-02-house-floor", "2026-09-02", "review", 0.63, True),
    ])
    body = TestClient(create_app()).get("/floor").text
    assert "2026-09-03-house-floor" in body
    assert "Pull to local" in body            # pullable (not local)
    assert "Open" in body                      # already-local one links to the workspace


def test_floor_import_redirects_to_workspace(monkeypatch, tmp_meetings_dir):
    calls = {}
    monkeypatch.setattr(fi, "import_session",
                        lambda slug, md, **k: calls.setdefault("slug", slug))
    client = TestClient(create_app())
    resp = client.post("/floor/import", data={"slug": "2026-09-03-house-floor"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/meetings/2026-09-03-house-floor"
    assert calls["slug"] == "2026-09-03-house-floor"


def test_floor_import_surfaces_error(monkeypatch, tmp_meetings_dir):
    def _boom(slug, md, **k):
        raise fi.FloorImportError("nope")
    monkeypatch.setattr(fi, "import_session", _boom)
    monkeypatch.setattr(fi, "list_floor_sessions", lambda md: [])
    client = TestClient(create_app())
    resp = client.post("/floor/import", data={"slug": "x-house-floor"}, follow_redirects=False)
    # Re-renders the list with an error rather than 500ing.
    assert resp.status_code == 200
    assert "nope" in resp.text or "could not" in resp.text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_floor_import.py -k floor -v`
Expected: FAIL (no `/floor` route/template).

- [ ] **Step 3: Add the template**

```html
<!-- gui/templates/floor_import.html -->
{% extends "base.html" %}
{% from "_ui.html" import badge %}
{% block title %}House Floor Sessions — CouncilScribe{% endblock %}
{% block content %}
  <header><a class="back" href="/">← Library</a><h1>House Floor Sessions</h1></header>
  <main>
    {% if error %}<div class="error-banner">{{ error }}</div>{% endif %}
    {% if sessions %}
    <table class="library">
      <thead><tr><th>Session</th><th>Date</th><th>Gate</th><th></th></tr></thead>
      <tbody>
        {% for s in sessions %}
        <tr>
          <td class="mid">{{ s.slug }}</td>
          <td>{{ s.date or "—" }}</td>
          <td>{{ badge("gate gate-" ~ (s.gate_verdict or "none"), s.gate_verdict or "—") }}</td>
          <td>
            {% if s.is_local %}
              <a class="mlink" href="/meetings/{{ s.slug }}">Open</a>
            {% else %}
              <form method="post" action="/floor/import">
                <input type="hidden" name="slug" value="{{ s.slug }}">
                <button type="submit" class="accept">Pull to local</button>
              </form>
            {% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    {% else %}<p class="empty">No draft floor sessions found.</p>{% endif %}
  </main>
{% endblock %}
```

- [ ] **Step 4: Add the routes**

In `gui/app.py`, add (using the existing `_templates`, `config.MEETINGS_DIR`, and `RedirectResponse` imports):

```python
from gui import floor_import as _floor

@app.get("/floor", response_class=HTMLResponse)
def floor_sessions(request: Request, error: str | None = None):
    sessions = _floor.list_floor_sessions(config.MEETINGS_DIR)
    return _templates.TemplateResponse(
        "floor_import.html", {"request": request, "sessions": sessions, "error": error})

@app.post("/floor/import")
def floor_import_action(slug: str = Form(...)):
    try:
        _floor.import_session(slug, config.MEETINGS_DIR)
    except _floor.FloorAlreadyLocalError:
        return RedirectResponse(f"/meetings/{slug}", status_code=303)
    except _floor.FloorImportError as e:
        sessions = _floor.list_floor_sessions(config.MEETINGS_DIR)
        return _templates.TemplateResponse(
            "floor_import.html",
            {"request": Request, "sessions": sessions, "error": str(e)}, status_code=200)
    return RedirectResponse(f"/meetings/{slug}", status_code=303)
```

Match the exact import style already in `gui/app.py` (it uses `Form`, `Request`, `HTMLResponse`, `RedirectResponse`, `config`). In the error branch pass the real `request` object the handler receives — add `request: Request` to the signature so the template renders (the snippet's `Request` placeholder must become the injected `request`). Add a link in `library.html`'s toolbar next to Discovery: `<a class="newlink" href="/floor">House floor</a>`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_floor_import.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/app.py gui/templates/floor_import.html gui/templates/library.html tests/test_gui_floor_import.py
git commit -m "feat(gui): House floor sessions view + pull-to-local action"
```

- [ ] **Step 7: Operator end-to-end live verification (not a subagent)**

With Task 1 deployed and at least one draft in the DB, the operator: opens `/floor`, pulls a session, lands on its review workspace, confirms clips play from the CDN, enrolls a voice, and publishes (draft → published) — then verifies the same slug row flipped live (no duplicate row, no `-2` dir).

---

## Self-Review

- **Spec coverage (Slice 4 = floor-session import):** JSON-only artifact upload from the weekly workflow (Task 1) ✓; storage optimization — no raw audio, CDN playback (Task 1 guard + Global Constraints) ✓; GUI list of available sessions (Task 2) ✓; pull-to-local into `MEETINGS_DIR/<slug>` via `gh`, no `launch_run`/`-2` fork (Task 3) ✓; review + enroll + publish through the existing flow, land on the workspace (Task 4) ✓; republish overwrite by deterministic slug (Global Constraints — no code change needed; Task 4 step 7 verifies) ✓.
- **Placeholder scan:** every step has concrete code or a concrete YAML/command. The two operator live-verification steps are deliberate (subagents cannot run `gh`/prod/port-8000) and are not code placeholders. The one thing left to confirm against real data — the `processing_metadata` gate/HLS key names — is called out inline in Tasks 1–2 with the fallback action.
- **Type/name consistency:** `FloorSession` fields, `list_floor_sessions(meetings_dir)`, `import_session(slug, meetings_dir, *, runner, max_runs, force)`, `FloorImportError`/`FloorAlreadyLocalError`, the artifact name `house-floor-sessions`, and the `<date>-house-floor` slug are used consistently across tasks and match the verified code facts.

## Dependencies / ordering

- Task 4 uses the `badge` macro and `base.html` from **Slice 1** (visual foundation). Run Slice 1 first, or inline a plain badge if importing before Slice 1 lands.
- Tasks 2–4 are Mac-side and testable without Task 1 deployed (all `gh`/DB calls are mocked). Task 1's real value needs the operator's `workflow_dispatch` de-risk run.
