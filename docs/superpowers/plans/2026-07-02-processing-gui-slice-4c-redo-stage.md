# Processing GUI — Slice 4c: Redo-Stage Buttons Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

The last roadmap item: buttons on a meeting's run page to **re-run a pipeline stage** (`diarize` / `transcribe` / `identify` / `summary`) when a stage needs redoing (bad diarization, a transcript bug fixed, etc.). Runs `run_local.py --resume <meeting_id> --redo <stage>` as a background subprocess — reusing the exact launch/monitor machinery from 3a (log capture, unbuffered, stepper, pid liveness). No `--input` needed; the audio is already on disk.

**Goal:** From a meeting's run page, click "Re-run: Diarize/Transcribe/Identify/Summarize" and watch it re-process via the existing progress view.

**Architecture:** Refactor `launch_run`'s spawn block into a shared `_spawn(meeting_id, meeting_dir, cmd, popen)` (unbuffered env, log capture, `_RUNS` registry, sidecar). Add `build_redo_command` + `launch_redo(meeting_id, stage, ...)` (validates id + stage + existing meeting, then `_spawn`s `--resume <id> --redo <stage>`). A `POST /meetings/{id}/redo` route → redirect to the run page. Redo buttons on `run.html`.

**Tech Stack:** reuses `gui/runner.py` spawn machinery, FastAPI. Tests use a fake Popen (no real process, no `run_local` execution). NO server, NO port 8000.

---

### Task 1: Refactor `_spawn`, add `build_redo_command` + `launch_redo`

**Files:**
- Modify: `gui/runner.py`
- Test: `tests/test_gui_runner.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_runner.py`:

```python
def test_build_redo_command():
    from gui.runner import build_redo_command
    cmd = build_redo_command("py", "run_local.py", "2026-02-04-council", "diarize")
    assert cmd == ["py", "run_local.py", "--resume", "2026-02-04-council", "--redo", "diarize"]


def test_launch_redo_spawns_resume_redo(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env_unbuffered"] = kw.get("env", {}).get("PYTHONUNBUFFERED")
        return _FakePopen(cmd, **kw)

    mid = runner.launch_redo("2026-02-04-council", "transcribe",
                             python_exe="py", script="run_local.py", popen=fake_popen)
    assert mid == "2026-02-04-council"
    assert captured["cmd"] == ["py", "run_local.py", "--resume", "2026-02-04-council",
                               "--redo", "transcribe"]
    assert captured["env_unbuffered"] == "1"           # reuses the unbuffered launch
    assert mid in runner._RUNS
    mdir = tmp_meetings_dir / mid
    assert (mdir / "gui_run.log").exists() and (mdir / "gui_run.json").exists()


def test_launch_redo_guards(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    assert runner.launch_redo("2026-02-04-council", "bogus", python_exe="p", script="s") is None  # bad stage
    assert runner.launch_redo("ghost", "diarize", python_exe="p", script="s") is None            # no meeting
    assert runner.launch_redo("../x", "diarize", python_exe="p", script="s") is None              # unsafe id


def test_existing_launch_run_still_works(tmp_meetings_dir):
    # the _spawn refactor must not change launch_run behavior
    from gui import runner
    runner._RUNS.clear()
    p = runner.RunParams(input="x", date="2026-02-10", meeting_type="Regular", event_kind="council")
    mid = runner.launch_run(p, python_exe="py", script="s", popen=_FakePopen)
    assert mid == "2026-02-10-regular" and mid in runner._RUNS
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "redo or launch_run_still" -v`
Expected: FAIL — `build_redo_command`/`launch_redo` not defined.

- [ ] **Step 3: Refactor + add to `gui/runner.py`**

Extract the spawn block into `_spawn` and rewrite `launch_run` to use it; add the redo pieces:

```python
REDO_STAGES = ("diarize", "transcribe", "identify", "summary")


def _spawn(meeting_id: str, meeting_dir: Path, cmd: list[str], popen) -> str:
    """Spawn cmd as the background pipeline process for meeting_id: capture
    stdout+stderr to gui_run.log, run unbuffered + non-interactive, register the
    handle, write the sidecar. Shared by launch_run and launch_redo."""
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}
    with open(meeting_dir / _LOG_NAME, "wb") as log_f:
        proc = popen(
            cmd,
            stdout=log_f,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=None,
            env=env,
        )
    _RUNS[meeting_id] = proc
    (meeting_dir / _SIDE_NAME).write_text(
        json.dumps({"pid": getattr(proc, "pid", None), "cmd": cmd, "status": "running"}),
        encoding="utf-8",
    )
    return meeting_id


def build_redo_command(python_exe: str, script: str, meeting_id: str, stage: str) -> list[str]:
    """`run_local.py --resume <id> --redo <stage>` — re-run a stage on an existing
    meeting (audio already on disk, so no --input needed)."""
    return [python_exe, script, "--resume", meeting_id, "--redo", stage]


def launch_redo(meeting_id: str, stage: str, *, python_exe: str, script: str,
                popen=subprocess.Popen) -> Optional[str]:
    """Re-run a stage for an existing meeting. Returns the meeting_id, or None on
    unsafe id / invalid stage / unknown meeting."""
    if not is_safe_meeting_id(meeting_id) or stage not in REDO_STAGES:
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    cmd = build_redo_command(python_exe, script, meeting_id, stage)
    return _spawn(meeting_id, meeting_dir, cmd, popen)
```

Rewrite `launch_run`'s tail to delegate to `_spawn` (replace the inline `with open(...) / _RUNS[...] / sidecar write` block):

```python
def launch_run(p: RunParams, *, python_exe: str, script: str, popen=subprocess.Popen) -> str:
    """Spawn run_local.py for a NEW meeting in the background. Returns the meeting_id."""
    meeting_id = derive_meeting_id(p)
    meeting_dir = ensure_drive_structure(meeting_id)
    cmd = build_run_command(python_exe, script, p, meeting_id)
    return _spawn(meeting_id, meeting_dir, cmd, popen)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_runner.py -v`
Expected: PASS (redo tests + all existing launch/run_status tests — the `_spawn` refactor is behavior-preserving).

- [ ] **Step 5: Commit**

```bash
git add gui/runner.py tests/test_gui_runner.py
git commit -m "feat(gui): launch_redo (--resume --redo) via shared _spawn

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Redo route + buttons on the run page

**Files:**
- Modify: `gui/app.py`
- Modify: `gui/templates/run.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_launch.py`:

```python
def test_post_redo_launches_and_redirects(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    called = {}
    monkeypatch.setattr(runner, "launch_redo",
                        lambda mid, stage, **kw: called.setdefault("v", (mid, stage)) or mid)
    client = TestClient(create_app())
    resp = client.post("/meetings/2026-02-04-council/redo", data={"stage": "diarize"},
                       follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/meetings/2026-02-04-council/run"
    assert called["v"] == ("2026-02-04-council", "diarize")


def test_post_redo_invalid_stage_400(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    resp = TestClient(create_app()).post("/meetings/2026-02-04-council/redo",
                                         data={"stage": "bogus"}, follow_redirects=False)
    assert resp.status_code == 400


def test_post_redo_unknown_meeting_404(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_redo", lambda mid, stage, **kw: None)  # unknown -> None
    resp = TestClient(create_app()).post("/meetings/ghost/redo",
                                         data={"stage": "diarize"}, follow_redirects=False)
    assert resp.status_code == 404


def test_run_page_has_redo_buttons(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/run").text
    assert 'action="/meetings/2026-02-04-council/redo"' in body
    assert 'value="diarize"' in body and 'value="transcribe"' in body
    assert 'value="identify"' in body and 'value="summary"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "redo" -v`
Expected: FAIL — route + buttons missing.

- [ ] **Step 3: Add the route to `gui/app.py`** (after the run routes)

```python
    @app.post("/meetings/{meeting_id}/redo")
    def redo_route(meeting_id: str, stage: str = Form("")):
        stage = stage.strip()
        if stage not in runner.REDO_STAGES:
            raise HTTPException(status_code=400, detail="invalid redo stage")
        if runner.launch_redo(meeting_id, stage, python_exe=sys.executable, script=_RUN_LOCAL) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/run", status_code=303)
```

Pass the stage list to the run page — update `run_page`'s context:

```python
    @app.get("/meetings/{meeting_id}/run", response_class=HTMLResponse)
    def run_page(request: Request, meeting_id: str) -> HTMLResponse:
        from src.checkpoint import PipelineStage
        stages = [(s.value, stage_label_for(s.value)) for s in PipelineStage if s.value >= 1]
        return _templates.TemplateResponse(
            request, "run.html",
            {"meeting_id": meeting_id, "stages": stages, "redo_stages": list(runner.REDO_STAGES)},
        )
```

- [ ] **Step 4: Add the redo section to `gui/templates/run.html`** (after the log `<pre>`, before the review link)

```html
    <details class="redo"><summary>Re-run a stage</summary>
      <p class="mid">Re-processes from that stage onward (uses compute). Watch progress above.</p>
      <div class="redo-buttons">
        {% for stage in redo_stages %}
        <form method="post" action="/meetings/{{ meeting_id }}/redo">
          <input type="hidden" name="stage" value="{{ stage }}">
          <button type="submit" class="mark">Re-run {{ stage }}</button>
        </form>
        {% endfor %}
      </div>
    </details>
```

- [ ] **Step 5: Append styles to `gui/static/style.css`**

```css
details.redo { margin-top: 1rem; }
.redo-buttons { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-top: 0.4rem; }
.redo-buttons form { margin: 0; }
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "redo" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/run.html gui/static/style.css tests/test_gui_launch.py
git commit -m "feat(gui): redo-stage buttons on the run page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass — especially the existing `launch_run` / `run_status` tests (the `_spawn` refactor is behavior-preserving). NO server, NO real subprocess launched (fake Popen in tests).

---

## Self-Review

**Spec coverage:** redo diarize/transcribe/identify/summary (Task 1 `REDO_STAGES` + `launch_redo`) ✅ · `--resume <id> --redo <stage>` command, no --input (Task 1 `build_redo_command`) ✅ · reuses launch machinery via `_spawn` (unbuffered/log/registry/sidecar) (Task 1) ✅ · POST route → run page, 400 invalid stage / 404 unknown (Task 2) ✅ · buttons on run page (Task 2) ✅ · `launch_run` behavior preserved (Task 1 test) ✅ · no server / real subprocess in tests ✅.

**Placeholder scan:** none.

**Type consistency:** `_spawn(meeting_id, meeting_dir, cmd, popen) -> str` used by both `launch_run` and `launch_redo`. `launch_redo(meeting_id, stage, *, python_exe, script, popen) -> str|None`; route pre-checks `stage in runner.REDO_STAGES` (400) then maps None→404. `REDO_STAGES` is the single source used by `launch_redo`, the route guard, and the run.html buttons (via `redo_stages` context). Redirect target `/meetings/{id}/run` matches the 3a run page. Command shape matches run_local's `--redo requires --resume/--input` (uses `--resume`).
