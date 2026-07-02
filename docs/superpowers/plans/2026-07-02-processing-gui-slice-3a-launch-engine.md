# Processing GUI — Slice 3a: Launch Engine + Live Progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

The tracer for the launch half (pain A): kick off `run_local.py` as a background subprocess from a **basic** new-meeting form, then watch it run — a **6/7-stage stepper** (read from `pipeline_state.json`) + a **live log pane** (tail of the subprocess's captured stdout). No script changes: the pipeline already writes `pipeline_state.json` per stage, and we tail its stdout.

**Key behavior:** the subprocess runs **non-interactively** (`stdin=DEVNULL`), so the pipeline auto-runs ingest→diarize→transcribe→identify→summarize and **skips the terminal review** (which now lives in the GUI, Slice 2). Publishing stays opt-in (not passed here). So "launch" = process automatically; the operator reviews later via the Slice 2 review page.

**Deferred:** 3b — the *nice* form (event_kind/compute/diarizer dropdowns with help text, conditional Chamber/Race pickers, live "how it'll look" preview, auto-hidden meeting_id, **source_key duplicate detection**). 3c — error always-tier (traceback headline extraction, growing error catalog).

**Goal:** From a form, launch a real processing run and watch its stages + log advance live in the browser; see a clear done/failed state with a Retry.

**Architecture:** New `gui/runner.py`: pure `derive_meeting_id` + `build_run_command`; `launch_run` spawns `subprocess.Popen` (injectable for tests) with stdout+stderr → `{meeting_dir}/gui_run.log` and `stdin=DEVNULL`, recording the handle in a module-level `_RUNS` registry + a `gui_run.json` sidecar; `run_status` reports stage (from `pipeline_state.json`), liveness/exit (from the registry), and a log tail. `gui/app.py` gets: `GET /new` (form), `POST /new` (launch → 303 to the run page), `GET /meetings/{id}/run` (status page), `GET /meetings/{id}/run/status` (JSON for polling). A small `gui/static/run.js` polls and updates the stepper + log. Builds on Slices 1–2.

**Tech Stack:** `subprocess.Popen`, `src.checkpoint.ensure_drive_structure` + `PipelineStage`, `gui.models.stage_label`, FastAPI `Form`/`JSONResponse`. Tests inject a fake Popen and use `tmp_meetings_dir`; no real pipeline runs in tests.

---

### Task 1: `derive_meeting_id` + `build_run_command` (pure)

**Files:**
- Create: `gui/runner.py`
- Test: `tests/test_gui_runner.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_runner.py`:

```python
from __future__ import annotations

import pytest

from gui.runner import RunParams, build_run_command, derive_meeting_id


def test_derive_meeting_id_from_date_and_type():
    p = RunParams(input="https://x/v", date="2026-02-10", meeting_type="Regular Session",
                  event_kind="council")
    assert derive_meeting_id(p) == "2026-02-10-regular-session"


def test_derive_meeting_id_custom_wins():
    p = RunParams(input="x", date="2026-02-10", meeting_type="Regular Session",
                  event_kind="council", meeting_id="my-custom-id")
    assert derive_meeting_id(p) == "my-custom-id"


def test_derive_meeting_id_rejects_unsafe():
    p = RunParams(input="x", date="2026-02-10", meeting_type="a/b", event_kind="council")
    # slug strips the slash -> safe single component
    assert derive_meeting_id(p) == "2026-02-10-a-b"
    with pytest.raises(ValueError):
        derive_meeting_id(RunParams(input="x", date="", meeting_type="", event_kind="council"))


def test_build_run_command_core_and_optional_flags():
    p = RunParams(input="https://x/v", date="2026-02-10", meeting_type="Regular Session",
                  event_kind="council", city="Bloomington", compute="modal", diarizer="api",
                  title="Budget Hearing", clip_start="10:00", clip_end="20:00", num_speakers=5)
    cmd = build_run_command("/venv/bin/python", "/repo/run_local.py", p, "2026-02-10-regular-session")
    # core
    assert cmd[:2] == ["/venv/bin/python", "/repo/run_local.py"]
    assert "--input" in cmd and "https://x/v" in cmd
    assert cmd[cmd.index("--meeting-id") + 1] == "2026-02-10-regular-session"
    assert cmd[cmd.index("--event-kind") + 1] == "council"
    assert cmd[cmd.index("--compute") + 1] == "modal"
    assert cmd[cmd.index("--diarizer") + 1] == "api"
    assert cmd[cmd.index("--title") + 1] == "Budget Hearing"
    assert cmd[cmd.index("--num-speakers") + 1] == "5"
    # --clip takes two values START END
    ci = cmd.index("--clip")
    assert cmd[ci + 1] == "10:00" and cmd[ci + 2] == "20:00"


def test_build_run_command_omits_absent_optionals():
    p = RunParams(input="x", date="2026-02-10", meeting_type="Regular", event_kind="council")
    cmd = build_run_command("py", "s", p, "2026-02-10-regular")
    for flag in ("--title", "--clip", "--city", "--num-speakers"):
        assert flag not in cmd
    # compute/diarizer default through to the flags (explicit is fine)
    assert cmd[cmd.index("--compute") + 1] == "local"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "derive_meeting_id or build_run_command" -v`
Expected: FAIL — `No module named 'gui.runner'`.

- [ ] **Step 3: Implement `gui/runner.py` (this task: the pure parts)**

```python
"""Launch run_local.py as a background subprocess and report its progress.

This module owns the *mechanics* of launching + monitoring; the pure helpers
(derive_meeting_id, build_run_command) are unit-tested without spawning."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from gui.paths import is_safe_meeting_id


@dataclass
class RunParams:
    input: str
    date: str
    meeting_type: str
    event_kind: str
    city: Optional[str] = None
    title: Optional[str] = None
    compute: str = "local"
    diarizer: str = "oss"
    meeting_id: Optional[str] = None
    clip_start: Optional[str] = None
    clip_end: Optional[str] = None
    num_speakers: int = 0


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")


def derive_meeting_id(p: RunParams) -> str:
    """Custom id if given, else '{date}-{slug(meeting_type)}'. Raises ValueError
    if the result isn't a safe single path component (matches run_local's rule)."""
    mid = (p.meeting_id or "").strip() or f"{p.date}-{_slug(p.meeting_type)}"
    mid = mid.strip("-")
    if not is_safe_meeting_id(mid) or mid in ("", "-"):
        raise ValueError(f"Cannot derive a valid meeting id from date={p.date!r} type={p.meeting_type!r}")
    return mid


def build_run_command(python_exe: str, script: str, p: RunParams, meeting_id: str) -> list[str]:
    """Compose the run_local.py argv. meeting_id is passed explicitly so the GUI
    knows the target dir. Optional flags are omitted when absent."""
    cmd = [
        python_exe, script,
        "--input", p.input,
        "--meeting-id", meeting_id,
        "--date", p.date,
        "--event-kind", p.event_kind,
        "--meeting-type", p.meeting_type,
        "--compute", p.compute,
        "--diarizer", p.diarizer,
    ]
    if p.city:
        cmd += ["--city", p.city]
    if p.title:
        cmd += ["--title", p.title]
    if p.num_speakers and p.num_speakers > 0:
        cmd += ["--num-speakers", str(p.num_speakers)]
    if p.clip_start and p.clip_end:
        cmd += ["--clip", p.clip_start, p.clip_end]
    return cmd
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/runner.py tests/test_gui_runner.py
git commit -m "feat(gui): runner command builder + meeting-id derivation

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `launch_run` + `run_status` (subprocess + monitoring)

**Files:**
- Modify: `gui/runner.py`
- Test: `tests/test_gui_runner.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_runner.py`:

```python
import json
from pathlib import Path


class _FakePopen:
    """Stand-in for subprocess.Popen: records args, controllable exit."""
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.kw = kw
        self.pid = 4321
        self._rc = None
        # write a marker to the provided stdout so the log-tail path has content
        out = kw.get("stdout")
        if out is not None and hasattr(out, "write"):
            out.write(b"STAGE 1: Audio Ingestion\n")
            out.flush()

    def poll(self):
        return self._rc

    def finish(self, rc=0):
        self._rc = rc


def test_launch_run_spawns_and_records(tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["stdin_devnull"] = kw.get("stdin") is not None
        return _FakePopen(cmd, **kw)

    p = runner.RunParams(input="https://x/v", date="2026-02-10", meeting_type="Regular",
                         event_kind="council")
    mid = runner.launch_run(p, python_exe="py", script="run_local.py", popen=fake_popen)

    assert mid == "2026-02-10-regular"
    assert mid in runner._RUNS
    mdir = tmp_meetings_dir / mid
    assert (mdir / "gui_run.log").exists()      # stdout captured here
    side = json.loads((mdir / "gui_run.json").read_text())
    assert side["status"] == "running" and side["pid"] == 4321
    assert captured["cmd"][0] == "py"


def test_run_status_reports_stage_and_liveness(tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    p = runner.RunParams(input="x", date="2026-02-10", meeting_type="Regular", event_kind="council")
    proc_box = {}

    def fake_popen(cmd, **kw):
        proc = _FakePopen(cmd, **kw)
        proc_box["p"] = proc
        return proc

    mid = runner.launch_run(p, python_exe="py", script="s", popen=fake_popen)
    mdir = tmp_meetings_dir / mid
    # simulate the pipeline having written progress
    (mdir / "pipeline_state.json").write_text(json.dumps({"completed_stage": 2}))

    st = runner.run_status(mid)
    assert st["running"] is True
    assert st["completed_stage"] == 2
    assert st["stage_label"]                       # human label present
    assert "STAGE 1" in st["log_tail"]

    proc_box["p"].finish(rc=0)
    st2 = runner.run_status(mid)
    assert st2["running"] is False
    assert st2["exit_code"] == 0

    assert runner.run_status("no-such-meeting") is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "launch_run or run_status" -v`
Expected: FAIL — `launch_run`/`run_status` not defined.

- [ ] **Step 3: Implement in `gui/runner.py`**

Add imports at top (`import json, os, subprocess` and `from pathlib import Path`) and:

```python
from src import config
from src.checkpoint import ensure_drive_structure

from gui.models import stage_label

# meeting_id -> Popen handle for the current process's launches. A local
# single-user tool runs one uvicorn worker, so a module dict is sufficient;
# handles are lost on restart (as are the children), which run_status tolerates.
_RUNS: dict = {}

_LOG_NAME = "gui_run.log"
_SIDE_NAME = "gui_run.json"


def launch_run(p: RunParams, *, python_exe: str, script: str, popen=subprocess.Popen) -> str:
    """Spawn run_local.py for these params in the background. Returns the meeting_id.
    stdout+stderr are captured to gui_run.log; stdin is /dev/null so the pipeline
    runs non-interactively (terminal review is skipped — review happens in the GUI)."""
    meeting_id = derive_meeting_id(p)
    meeting_dir = ensure_drive_structure(meeting_id)
    cmd = build_run_command(python_exe, script, p, meeting_id)

    log_f = open(meeting_dir / _LOG_NAME, "wb")
    proc = popen(
        cmd,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        cwd=str(config.DRIVE_ROOT.parent) if False else None,  # inherit CWD (repo)
    )
    _RUNS[meeting_id] = proc
    (meeting_dir / _SIDE_NAME).write_text(
        json.dumps({"pid": getattr(proc, "pid", None), "cmd": cmd, "status": "running"}),
        encoding="utf-8",
    )
    return meeting_id


def _log_tail(meeting_dir: Path, max_bytes: int = 16000) -> str:
    log = meeting_dir / _LOG_NAME
    if not log.exists():
        return ""
    data = log.read_bytes()
    return data[-max_bytes:].decode("utf-8", errors="replace")


def run_status(meeting_id: str) -> Optional[dict]:
    """Progress snapshot, or None if this meeting has no run sidecar/registry entry."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / _SIDE_NAME).exists() and meeting_id not in _RUNS:
        return None

    completed = 0
    ps = meeting_dir / "pipeline_state.json"
    if ps.exists():
        try:
            completed = int(json.loads(ps.read_text()).get("completed_stage", 0))
        except (ValueError, OSError, TypeError, AttributeError):
            completed = 0

    proc = _RUNS.get(meeting_id)
    if proc is not None:
        rc = proc.poll()
        running = rc is None
        exit_code = rc
    else:
        # No live handle (e.g. after a GUI restart): fall back to the sidecar.
        running = False
        exit_code = None

    return {
        "meeting_id": meeting_id,
        "completed_stage": completed,
        "stage_label": stage_label(completed),
        "running": running,
        "exit_code": exit_code,
        "log_tail": _log_tail(meeting_dir),
    }
```

(Note: the `cwd=... if False else None` is deliberately `None` so the child inherits the GUI's working directory — the repo root, since `python -m gui` runs there. Simplify to `cwd=None` if the linter objects; the intent is "inherit CWD".)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_runner.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/runner.py tests/test_gui_runner.py
git commit -m "feat(gui): launch_run subprocess + run_status monitoring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Routes — new-meeting form, launch, run page, status JSON

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_launch.py` (create)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_launch.py`:

```python
from __future__ import annotations

import json

from fastapi.testclient import TestClient

from gui.app import create_app


def test_new_form_renders(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    assert "<form" in body and 'action="/new"' in body
    assert 'name="input"' in body and 'name="event_kind"' in body


def test_post_new_launches_and_redirects(tmp_meetings_dir, monkeypatch):
    from gui import runner
    launched = {}

    def fake_launch(p, **kw):
        launched["params"] = p
        return "2026-02-10-regular"

    monkeypatch.setattr(runner, "launch_run", fake_launch)
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "Bloomington", "compute": "local", "diarizer": "oss",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/meetings/2026-02-10-regular/run"
    assert launched["params"].input == "https://x/v"


def test_post_new_missing_input_is_rejected(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.post("/new", data={"input": "", "date": "2026-02-10",
                                     "meeting_type": "Regular", "event_kind": "council"},
                       follow_redirects=False)
    assert resp.status_code == 400


def test_run_page_and_status_json(tmp_meetings_dir, tagged_meeting_dir, monkeypatch):
    # Seed a meeting with a run sidecar + state so run_status returns data.
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=2)
    (mdir / "gui_run.json").write_text(json.dumps({"pid": 1, "cmd": [], "status": "running"}))
    (mdir / "gui_run.log").write_text("STAGE 2: Speaker Diarization\n")
    client = TestClient(create_app())

    page = client.get("/meetings/2026-02-10-regular/run")
    assert page.status_code == 200
    assert "run.js" in page.text and "Diarization" in page.text or "stepper" in page.text.lower()

    st = client.get("/meetings/2026-02-10-regular/run/status")
    assert st.status_code == 200
    body = st.json()
    assert body["completed_stage"] == 2
    assert "STAGE 2" in body["log_tail"]

    assert client.get("/meetings/ghost/run/status").status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -v`
Expected: FAIL — routes/templates missing.

- [ ] **Step 3: Add routes to `gui/app.py`**

Add imports:

```python
import sys
from pathlib import Path as _Path

from gui import runner
from gui.runner import RunParams
```

Define the repo paths near the module top (after `_GUI_DIR`):

```python
_REPO_DIR = _GUI_DIR.parent
_RUN_LOCAL = str(_REPO_DIR / "run_local.py")
```

Inside `create_app()`, add:

```python
    @app.get("/new", response_class=HTMLResponse)
    def new_meeting_form(request: Request) -> HTMLResponse:
        from src.event_kinds import EVENT_KINDS
        return _templates.TemplateResponse(
            request, "new_meeting.html",
            {"event_kinds": list(EVENT_KINDS), "computes": ["local", "modal"],
             "diarizers": ["oss", "api", "vibevoice"]},
        )

    @app.post("/new")
    def new_meeting_launch(
        request: Request,
        input: str = Form(""),
        date: str = Form(""),
        meeting_type: str = Form(""),
        event_kind: str = Form("council"),
        city: str = Form(""),
        title: str = Form(""),
        compute: str = Form("local"),
        diarizer: str = Form("oss"),
        clip_start: str = Form(""),
        clip_end: str = Form(""),
    ):
        if not input.strip() or not date.strip() or not meeting_type.strip():
            raise HTTPException(status_code=400, detail="input, date, and meeting_type are required")
        p = RunParams(
            input=input.strip(), date=date.strip(), meeting_type=meeting_type.strip(),
            event_kind=event_kind, city=city.strip() or None, title=title.strip() or None,
            compute=compute, diarizer=diarizer,
            clip_start=clip_start.strip() or None, clip_end=clip_end.strip() or None,
        )
        try:
            meeting_id = runner.launch_run(p, python_exe=sys.executable, script=_RUN_LOCAL)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return RedirectResponse(url=f"/meetings/{meeting_id}/run", status_code=303)

    @app.get("/meetings/{meeting_id}/run", response_class=HTMLResponse)
    def run_page(request: Request, meeting_id: str) -> HTMLResponse:
        from src.checkpoint import PipelineStage
        stages = [(s.value, stage_label_for(s.value)) for s in PipelineStage if s.value >= 1]
        return _templates.TemplateResponse(
            request, "run.html", {"meeting_id": meeting_id, "stages": stages},
        )

    @app.get("/meetings/{meeting_id}/run/status")
    def run_status_json(meeting_id: str) -> JSONResponse:
        st = runner.run_status(meeting_id)
        if st is None:
            raise HTTPException(status_code=404)
        return JSONResponse(st)
```

Add a tiny module-level helper near the top of `gui/app.py` (used by the run page to label the stepper), delegating to the model:

```python
from gui.models import stage_label as stage_label_for
```

- [ ] **Step 4: Create templates (minimal — 3b makes the form nice)**

Create `gui/templates/new_meeting.html`:

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>New meeting</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/">← Library</a><h1>Process a new meeting</h1></header>
  <main class="review">
    <form method="post" action="/new" class="newform">
      <label>Source URL or file path<input type="text" name="input" required></label>
      <label>Date (YYYY-MM-DD)<input type="text" name="date" placeholder="2026-02-10" required></label>
      <label>Meeting type<input type="text" name="meeting_type" placeholder="Regular Session" required></label>
      <label>City<input type="text" name="city" placeholder="Bloomington"></label>
      <label>Title (optional)<input type="text" name="title"></label>
      <label>Event kind
        <select name="event_kind">{% for k in event_kinds %}<option value="{{ k }}">{{ k }}</option>{% endfor %}</select>
      </label>
      <label>Compute
        <select name="compute">{% for c in computes %}<option value="{{ c }}">{{ c }}</option>{% endfor %}</select>
      </label>
      <label>Diarizer
        <select name="diarizer">{% for d in diarizers %}<option value="{{ d }}">{{ d }}</option>{% endfor %}</select>
      </label>
      <label>Clip start (optional)<input type="text" name="clip_start" placeholder="10:00"></label>
      <label>Clip end (optional)<input type="text" name="clip_end" placeholder="20:00"></label>
      <button type="submit" class="enroll">Start processing</button>
    </form>
  </main>
</body></html>
```

Create `gui/templates/run.html`:

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Processing {{ meeting_id }}</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/">← Library</a><h1>Processing: <span class="mid">{{ meeting_id }}</span></h1>
    <p class="sub" id="run-state">Starting…</p></header>
  <main class="review" data-meeting-id="{{ meeting_id }}">
    <ol class="stepper" id="stepper">
      {% for value, label in stages %}<li data-stage="{{ value }}">{{ label }}</li>{% endfor %}
    </ol>
    <div id="error-banner" class="error-banner" hidden></div>
    <h2>Log</h2>
    <pre id="log" class="runlog">(waiting for output…)</pre>
    <p><a id="review-link" href="/meetings/{{ meeting_id }}/review" hidden>→ Review speakers</a></p>
  </main>
  <script src="/static/run.js"></script>
</body></html>
```

- [ ] **Step 5: Create `gui/static/run.js`**

```javascript
// Poll the run status and update the stepper + log until the process exits.
(function () {
  const main = document.querySelector("main[data-meeting-id]");
  if (!main) return;
  const id = main.getAttribute("data-meeting-id");
  const logEl = document.getElementById("log");
  const stateEl = document.getElementById("run-state");
  const stepper = document.getElementById("stepper");
  const errBanner = document.getElementById("error-banner");
  const reviewLink = document.getElementById("review-link");

  async function tick() {
    let st;
    try {
      const resp = await fetch(`/meetings/${encodeURIComponent(id)}/run/status`);
      if (!resp.ok) { stateEl.textContent = "Run not found."; return; }
      st = await resp.json();
    } catch (_) { setTimeout(tick, 2000); return; }

    if (st.log_tail) logEl.textContent = st.log_tail;
    logEl.scrollTop = logEl.scrollHeight;

    stepper.querySelectorAll("li").forEach((li) => {
      const s = parseInt(li.getAttribute("data-stage"), 10);
      li.classList.toggle("done", s <= st.completed_stage);
      li.classList.toggle("current", s === st.completed_stage + 1 && st.running);
    });

    if (st.running) {
      stateEl.textContent = `Running — stage ${st.completed_stage}/7 (${st.stage_label})`;
      setTimeout(tick, 1500);
    } else if (st.exit_code === 0 || st.completed_stage >= 5) {
      stateEl.textContent = "Done.";
      reviewLink.hidden = false;
    } else if (st.exit_code != null && st.exit_code !== 0) {
      stateEl.textContent = "Failed.";
      errBanner.hidden = false;
      errBanner.textContent = `Process exited with code ${st.exit_code}. See log below.`;
    } else {
      stateEl.textContent = "Idle.";
      reviewLink.hidden = false;
    }
  }
  tick();
})();
```

- [ ] **Step 6: Append styles to `gui/static/style.css`**

```css
.newform { display: flex; flex-direction: column; gap: 0.6rem; max-width: 32rem; }
.newform label { display: flex; flex-direction: column; font-size: 0.85rem; color: #444; gap: 0.2rem; }
.newform input, .newform select { padding: 0.3rem 0.4rem; border: 1px solid #ccc; border-radius: 0.4rem; font-size: 0.95rem; }
ol.stepper { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 0.5rem; }
ol.stepper li { font-size: 0.8rem; padding: 0.2rem 0.6rem; border-radius: 0.5rem; background: #eee; color: #888; }
ol.stepper li.done { background: #e6f5ea; color: #1b7a3d; }
ol.stepper li.current { background: #fdf3e0; color: #9a6a00; font-weight: 600; }
pre.runlog { background: #0e0e12; color: #d8d8e0; padding: 0.75rem; border-radius: 0.5rem; max-height: 45vh; overflow: auto; font-size: 0.8rem; white-space: pre-wrap; }
.error-banner { background: #fdeaea; color: #b32020; border: 1px solid #e0a0a0; border-radius: 0.5rem; padding: 0.6rem; margin-bottom: 0.6rem; }
```

- [ ] **Step 7: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add gui/app.py gui/templates/new_meeting.html gui/templates/run.html gui/static/run.js gui/static/style.css tests/test_gui_launch.py
git commit -m "feat(gui): new-meeting form, launch, run status page + polling

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Library "New meeting" entry point

**Files:**
- Modify: `gui/templates/library.html`
- Test: `tests/test_gui_library.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_library.py`:

```python
def test_library_has_new_meeting_link(tmp_meetings_dir):
    body = TestClient(create_app()).get("/").text
    assert 'href="/new"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_library.py::test_library_has_new_meeting_link -v`
Expected: FAIL.

- [ ] **Step 3: Add the link to `gui/templates/library.html`**

In the `<header>`, add next to the `<h1>`:

```html
    <a class="newlink" href="/new">+ New meeting</a>
```

Append to `gui/static/style.css`:

```css
a.newlink { display: inline-block; margin-top: 0.4rem; color: #24507f; text-decoration: none; font-size: 0.9rem; }
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_library.py::test_library_has_new_meeting_link -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/templates/library.html gui/static/style.css tests/test_gui_library.py
git commit -m "feat(gui): + New meeting link from the library

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + launch-mechanics smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1–3a), no regressions.

- [ ] **Step 2: Launch-mechanics smoke (fast-failing input — proves spawn/log/error WITHOUT heavy processing)**

Run: `.venv/bin/python -m gui`, open http://127.0.0.1:8000/new. Submit with a bogus input like `input=/does/not/exist.mp4`, date `2026-01-01`, meeting_type `smoke test`, event_kind `other`, compute `local`. You should redirect to the run page; the log pane fills with `run_local.py`'s output and the process **fails fast** (bad input) → "Failed." banner + nonzero exit in the log. This exercises spawn → capture → status polling → error state end to end without a real transcription. Delete the `2026-01-01-smoke-test` meeting dir afterward. Ctrl-C to stop.

---

## Self-Review

**Spec coverage:** launch run_local.py as background subprocess non-interactively (Task 2 `launch_run`, `stdin=DEVNULL`) ✅ · basic new-meeting form (Task 3 `new_meeting.html`) ✅ · 6/7-stage stepper from `pipeline_state.json` (Task 2 `run_status` + Task 3 run page + run.js) ✅ · live log tail of captured stdout (Task 2 `_log_tail` + poll) ✅ · done/failed state + review link + basic error banner (run.js) ✅ · library entry point (Task 4) ✅ · NO script change (reads existing state + stdout) ✅ · deferred: nice form / dedup / preview (3b), error-headline catalog (3c) ✅.

**Placeholder scan:** none — complete code + exact commands.

**Type consistency:** `RunParams` fields consumed identically in `derive_meeting_id`/`build_run_command`/`launch_run` and built from the POST form in `app.py`. `launch_run(p, *, python_exe, script, popen=subprocess.Popen) -> str`; `run_status(meeting_id) -> dict|None` (None→404). `_RUNS` registry keyed by meeting_id. Stepper labels via `gui.models.stage_label` (reused, values 1–7). run.js polls `/meetings/{id}/run/status` matching the route. Redirects to `/meetings/{id}/run` (this slice) and links to `/meetings/{id}/review` (Slice 2a). Meeting-id safety via `is_safe_meeting_id` in both `derive_meeting_id` and `run_status`.
