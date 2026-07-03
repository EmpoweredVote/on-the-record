# Processing GUI — Slice 4d: "Continue processing" (resume) Button

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Fill the "pick up where I left off" gap. The redo buttons (4c) re-run *completed* stages backward; nothing runs the *remaining* stages forward. Add a **"Continue processing"** button that runs `run_local.py --resume <meeting_id>` (picks up from the last completed stage — e.g. runs summary after a gate-fail-then-review), plus a **"Continue anyway (override the review gate)"** variant that adds `--publish-anyway` (the only non-interactive way past a failing gate). Neither publishes — publishing stays the separate 4b action.

**Context:** the pipeline gates summary/enroll/publish on `review_status == "pass"` for non-interactive runs (the GUI is non-interactive). So a plain "Continue" finishes a meeting whose gate passes (after review); "Continue anyway" forces past a still-failing gate.

**Goal:** From the run page, continue an interrupted/queued-for-review meeting to completion without dropping to the CLI.

**Architecture:** Reuses the 4c `_spawn` machinery. `build_resume_command` (`--resume <id>` [+ `--publish-anyway`]) + `launch_resume(meeting_id, *, override_gate, ...)`. A `POST /meetings/{id}/continue` route → redirect to the run page. Buttons on `run.html`.

**Tech Stack:** reuses `gui/runner.py` (`_spawn`, `is_safe_meeting_id`), FastAPI. Tests use fake Popen — NO server, NO real subprocess, NO port 8000.

---

### Task 1: `build_resume_command` + `launch_resume`

**Files:**
- Modify: `gui/runner.py`
- Test: `tests/test_gui_runner.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_runner.py`:

```python
def test_build_resume_command():
    from gui.runner import build_resume_command
    assert build_resume_command("py", "s", "m") == ["py", "s", "--resume", "m"]
    assert build_resume_command("py", "s", "m", override_gate=True) == \
        ["py", "s", "--resume", "m", "--publish-anyway"]


def test_launch_resume_spawns(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        return _FakePopen(cmd, **kw)

    mid = runner.launch_resume("2026-02-04-council", python_exe="py", script="s", popen=fake_popen)
    assert mid == "2026-02-04-council"
    assert captured["cmd"] == ["py", "s", "--resume", "2026-02-04-council"]
    assert mid in runner._RUNS


def test_launch_resume_override_adds_flag(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    captured = {}
    runner.launch_resume("2026-02-04-council", override_gate=True,
                         python_exe="py", script="s",
                         popen=lambda cmd, **kw: captured.setdefault("cmd", cmd) or _FakePopen(cmd, **kw))
    assert "--publish-anyway" in captured["cmd"]


def test_launch_resume_guards(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    assert runner.launch_resume("ghost", python_exe="p", script="s") is None       # no meeting
    assert runner.launch_resume("../x", python_exe="p", script="s") is None         # unsafe id
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "resume" -v`
Expected: FAIL — `build_resume_command`/`launch_resume` not defined.

- [ ] **Step 3: Add to `gui/runner.py`** (near `build_redo_command`/`launch_redo`)

```python
def build_resume_command(python_exe: str, script: str, meeting_id: str, *,
                         override_gate: bool = False) -> list[str]:
    """`run_local.py --resume <id>` — pick up the pipeline from the last completed
    stage. override_gate adds --publish-anyway, the only non-interactive way past a
    failing confidence gate (it does NOT publish — that needs a separate --publish)."""
    cmd = [python_exe, script, "--resume", meeting_id]
    if override_gate:
        cmd.append("--publish-anyway")
    return cmd


def launch_resume(meeting_id: str, *, override_gate: bool = False,
                  python_exe: str, script: str, popen=subprocess.Popen) -> Optional[str]:
    """Resume an existing meeting forward to completion. Returns meeting_id, or None
    on unsafe id / unknown meeting."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    cmd = build_resume_command(python_exe, script, meeting_id, override_gate=override_gate)
    return _spawn(meeting_id, meeting_dir, cmd, popen)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "resume" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/runner.py tests/test_gui_runner.py
git commit -m "feat(gui): launch_resume (--resume [+ --publish-anyway]) via _spawn

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Continue route + buttons on the run page

**Files:**
- Modify: `gui/app.py`
- Modify: `gui/templates/run.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_launch.py`:

```python
def test_post_continue_launches_and_redirects(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    seen = {}
    monkeypatch.setattr(runner, "launch_resume",
                        lambda mid, override_gate=False, **kw: seen.setdefault("v", (mid, override_gate)) or mid)
    client = TestClient(create_app())
    r = client.post("/meetings/2026-02-04-council/continue", data={}, follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/meetings/2026-02-04-council/run"
    assert seen["v"] == ("2026-02-04-council", False)


def test_post_continue_override(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    seen = {}
    monkeypatch.setattr(runner, "launch_resume",
                        lambda mid, override_gate=False, **kw: seen.setdefault("og", override_gate) or mid)
    TestClient(create_app()).post("/meetings/2026-02-04-council/continue",
                                  data={"override": "1"}, follow_redirects=False)
    assert seen["og"] is True


def test_post_continue_unknown_404(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_resume", lambda mid, override_gate=False, **kw: None)
    assert TestClient(create_app()).post("/meetings/ghost/continue", data={},
                                         follow_redirects=False).status_code == 404


def test_run_page_has_continue_button(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/run").text
    assert 'action="/meetings/2026-02-04-council/continue"' in body
    assert "Continue processing" in body
    assert "override" in body.lower()  # the gate-override variant present
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "continue" -v`
Expected: FAIL — route/buttons missing.

- [ ] **Step 3: Add the route to `gui/app.py`** (near the redo route)

```python
    @app.post("/meetings/{meeting_id}/continue")
    def continue_route(meeting_id: str, override: str = Form("")):
        if runner.launch_resume(meeting_id, override_gate=bool(override.strip()),
                                python_exe=sys.executable, script=_RUN_LOCAL) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/run", status_code=303)
```

- [ ] **Step 4: Add the Continue section to `gui/templates/run.html`** (before the "Re-run a stage" `<details>`)

```html
    <section class="continue">
      <h2>Continue processing</h2>
      <p class="mid">Run the remaining stages (e.g. summary) from where this meeting stopped.
        A meeting queued for review continues once its gate passes — review the speakers first.</p>
      <div class="continue-buttons">
        <form method="post" action="/meetings/{{ meeting_id }}/continue">
          <button type="submit" class="enroll">Continue processing</button>
        </form>
        <form method="post" action="/meetings/{{ meeting_id }}/continue">
          <input type="hidden" name="override" value="1">
          <button type="submit" class="mark">Continue anyway (override the review gate)</button>
        </form>
      </div>
    </section>
```

- [ ] **Step 5: Append styles to `gui/static/style.css`**

```css
section.continue { margin-top: 1rem; }
.continue-buttons { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-top: 0.4rem; }
.continue-buttons form { margin: 0; }
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "continue" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/run.html gui/static/style.css tests/test_gui_launch.py
git commit -m "feat(gui): 'Continue processing' button (resume + gate override) on run page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. NO server, NO real subprocess (fake Popen in tests).

---

## Self-Review

**Spec coverage:** Continue = `--resume <id>` picks up remaining stages (Task 1) ✅ · "Continue anyway" adds `--publish-anyway` to override the gate, no publish (Task 1 build_resume_command) ✅ · POST /continue → run page, 404 unknown (Task 2) ✅ · buttons on run page (Task 2) ✅ · reuses `_spawn` (Task 1) ✅ · no server / real subprocess in tests ✅.

**Placeholder scan:** none.

**Type consistency:** `build_resume_command(python_exe, script, meeting_id, *, override_gate) -> list`; `launch_resume(meeting_id, *, override_gate, python_exe, script, popen) -> str|None`; route maps None→404, else 303 to `/meetings/{id}/run`. Reuses `_spawn`/`is_safe_meeting_id` from the runner. `--publish-anyway` matches run_local's argparse flag and its gate-override semantics (does not itself publish). Redirect target matches the 3a run page. Distinct from `launch_redo` (backward re-run of a completed stage) — `launch_resume` runs forward from the checkpoint.
