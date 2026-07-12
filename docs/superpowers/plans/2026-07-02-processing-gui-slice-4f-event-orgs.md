# Processing GUI — Slice 4f: Event org(s) — "Produced by …"

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Collect the producing/hosting **organization(s)** for a meeting (e.g. CBS, NBC) so they surface publicly as **"Produced by CBS, NBC"** (and feed the interview/media headline). The field is real end-to-end already — `Meeting.event_orgs`, `publish._upsert_event_orgs` (writes `meetings.event_orgs`), and `web/lib/format.ts` all use it — but **nothing collects it** (no `run_local` flag, no GUI field), so it's always empty. This adds the collection.

**Goal:** Enter org(s) on the new-meeting form → they publish as "Produced by …".

**Architecture:** Additive `run_local.py --event-org` flag (repeatable) → set `meeting.event_orgs` when the Meeting is built. GUI: `RunParams.event_orgs` (list) → `build_run_command` emits one `--event-org` per org; a comma-separated form field parsed into the list.

**Tech Stack:** `run_local.py` argparse (1 additive flag), `gui/runner.py`, `gui/app.py`, `new_meeting.html`. Tests use fake Popen; run_local change verified by import/ast (no pipeline run).

---

### Task 1: `run_local.py --event-org` flag (additive)

**Files:**
- Modify: `run_local.py`

- [ ] **Step 1: Add the argparse flag** (next to `--title`, ~line 3517)

```python
    parser.add_argument(
        "--event-org",
        action="append",
        default=None,
        metavar="ORG",
        help="Producing/hosting organization; repeatable (e.g. --event-org CBS "
             "--event-org NBC). Published as 'Produced by ...'.",
    )
```

- [ ] **Step 2: Set it on the Meeting** — in `run_pipeline`'s `Meeting(...)` construction (~line 833), add:

```python
        event_orgs=getattr(args, "event_org", None) or [],
```

(argparse turns `--event-org` into `args.event_org`; `action="append"` yields a list or None.)

- [ ] **Step 3: Verify it parses + imports (no pipeline run)**

Run: `.venv/bin/python -c "import ast; ast.parse(open('run_local.py').read()); import run_local; print('ok')"`
Expected: `ok`.

Run: `.venv/bin/python run_local.py --help 2>&1 | grep -A1 event-org`
Expected: the `--event-org` help line appears.

- [ ] **Step 4: Commit**

```bash
git add run_local.py
git commit -m "feat: --event-org flag sets meeting.event_orgs (Produced by ...)

Additive: repeatable --event-org populates the existing (publish-ready)
Meeting.event_orgs field, which was never collected before. No existing
behavior changes.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Thread orgs through the GUI launch

**Files:**
- Modify: `gui/runner.py` (RunParams + build_run_command)
- Modify: `gui/app.py` (parse the form field)
- Test: `tests/test_gui_runner.py` + `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_runner.py`:

```python
def test_build_run_command_includes_event_orgs():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-05-15", meeting_type="Interview", event_kind="news_clip",
                  event_orgs=["CBS", "NBC"])
    cmd = build_run_command("py", "s", p, "2026-05-15-interview")
    # one --event-org per org, in order
    assert cmd.count("--event-org") == 2
    i = cmd.index("--event-org")
    assert cmd[i + 1] == "CBS"
    assert "NBC" in cmd


def test_build_run_command_omits_event_orgs_when_empty():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-05-15", meeting_type="Interview", event_kind="news_clip")
    assert "--event-org" not in build_run_command("py", "s", p, "m")
```

Append to `tests/test_gui_launch.py`:

```python
def test_post_new_parses_event_orgs(tmp_meetings_dir, monkeypatch):
    from gui import runner
    seen = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: seen.setdefault("orgs", p.event_orgs) or "2026-05-15-interview")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-05-15", "meeting_type": "Interview",
        "event_kind": "news_clip", "event_orgs": "CBS, NBC ,, Telemundo",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert seen["orgs"] == ["CBS", "NBC", "Telemundo"]   # split, trimmed, blanks dropped


def test_new_form_has_event_orgs_field(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    assert 'name="event_orgs"' in body
    assert "Produced by" in body
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "event_orgs" tests/test_gui_launch.py -k "event_orgs" -v`
Expected: FAIL — `RunParams` has no `event_orgs`; form field absent.

- [ ] **Step 3: Add `event_orgs` to `RunParams` + `build_run_command` (`gui/runner.py`)**

In `RunParams` (after `num_speakers`):

```python
    event_orgs: list = field(default_factory=list)
```

Add `from dataclasses import dataclass, field` at the top if `field` isn't already imported.

In `build_run_command`, before `return cmd`:

```python
    for org in (p.event_orgs or []):
        if org:
            cmd += ["--event-org", org]
```

- [ ] **Step 4: Parse the form field in `gui/app.py` `new_meeting_launch`**

Add `event_orgs: str = Form("")` to the handler signature, and when building `RunParams`, add:

```python
            event_orgs=[o.strip() for o in event_orgs.split(",") if o.strip()],
```

- [ ] **Step 5: Add the field to `gui/templates/new_meeting.html`** (after the Title field)

```html
      <label>Event org(s) — "Produced by …" on the site (comma-separated)
        <input type="text" name="event_orgs" id="f-orgs" placeholder="e.g. CBS, NBC">
      </label>
```

- [ ] **Step 6: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "event_orgs" tests/test_gui_launch.py -k "event_orgs" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/runner.py gui/app.py gui/templates/new_meeting.html tests/test_gui_runner.py tests/test_gui_launch.py
git commit -m "feat(gui): Event org(s) field -> --event-org -> 'Produced by ...'

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (existing pipeline tests unaffected — `--event-org` is additive). NO server, NO real subprocess/pipeline run.

---

## Self-Review

**Spec coverage:** collect org(s) at launch (Task 2 form field + parse) ✅ · additive `run_local --event-org` → `meeting.event_orgs` (Task 1) ✅ · publish + web already render "Produced by" (no change needed) ✅ · comma-split, trimmed, blanks dropped (Task 2 parse + test) ✅ · omitted when empty (Task 2 test) ✅ · no existing pipeline behavior changed (additive flag) ✅.

**Placeholder scan:** none.

**Type consistency:** `RunParams.event_orgs: list`; `build_run_command` emits one `--event-org <org>` per non-empty entry; `run_local` `action="append"` reads them back into `args.event_org` → `meeting.event_orgs`. Form field `name="event_orgs"` (comma string) → parsed to list in the POST handler → `RunParams.event_orgs`. `field` import confirmed. Reuses `_spawn`/launch path unchanged (only the command gains flags). Publish (`_upsert_event_orgs`) and `format.ts` already consume `event_orgs` — untouched.
