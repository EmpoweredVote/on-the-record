# Processing GUI — Slice 4g: Body / roster picker (Chamber link + roster-guided ID)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Let the new-meeting form pick a **Body** (a cached council/board roster) so deliberative meetings get (a) **roster-guided speaker identification** (correct councilmember names + aliases in Stage 4) and (b) a **Chamber link** at publish (`chamber_id` resolved from `body_slug`). `run_local` already accepts `--body <slug>` and fail-fasts if the roster isn't cached — so this is **GUI-only**: list the locally cached rosters (`~/CouncilScribe/config/rosters/*.json`), offer them in a dropdown, pass the choice as `--body`.

**Goal:** Select "Bloomington Common Council" when processing a council meeting → its roster guides identification and the meeting links to that Chamber.

**Architecture:** `gui/rosters.py::list_cached_rosters()` reads `CONFIG_DIR/rosters/*.json` → `[(slug, label)]` (mirrors `run_local._list_cached_rosters`). `RunParams.body_slug` → `build_run_command` emits `--body <slug>` when set. The `/new` route passes the roster list to the template; a `<select name="body_slug">` (default "— none —") is added; the POST handler threads it into `RunParams`. No pipeline change (`--body` exists; the dropdown only offers cached slugs, so run_local's cached-roster fail-fast can't trigger).

**Tech Stack:** `gui/rosters.py`, `gui/runner.py`, `gui/app.py`, `new_meeting.html`. Tests use the `tmp_config_dir` fixture (from conftest) + fake Popen. No server.

---

### Task 1: `list_cached_rosters()` + `RunParams.body_slug` + `--body` in the command

**Files:**
- Create: `gui/rosters.py`
- Modify: `gui/runner.py`
- Test: `tests/test_gui_rosters.py` (create), `tests/test_gui_runner.py` (append)

- [ ] **Step 1: Write the failing tests**

Create `tests/test_gui_rosters.py`:

```python
from __future__ import annotations

import json


def test_list_cached_rosters_reads_dir(tmp_config_dir):
    from gui.rosters import list_cached_rosters
    rosters = tmp_config_dir / "rosters"
    rosters.mkdir(exist_ok=True)
    (rosters / "bloomington-common-council.json").write_text(json.dumps(
        {"body_key": "Bloomington Common Council", "politicians": [{}, {}, {}]}))
    out = list_cached_rosters()
    assert ("bloomington-common-council", "Bloomington Common Council (3 members)") in out


def test_list_cached_rosters_empty_when_no_dir(tmp_config_dir):
    from gui.rosters import list_cached_rosters
    assert list_cached_rosters() == []


def test_list_cached_rosters_tolerates_bad_json(tmp_config_dir):
    from gui.rosters import list_cached_rosters
    rosters = tmp_config_dir / "rosters"; rosters.mkdir(exist_ok=True)
    (rosters / "broken.json").write_text("{ not json")
    # falls back to the slug as the label; doesn't raise
    assert ("broken", "broken") in list_cached_rosters()
```

Append to `tests/test_gui_runner.py`:

```python
def test_build_run_command_includes_body():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-02-04", meeting_type="Regular Session",
                  event_kind="council", body_slug="bloomington-common-council")
    cmd = build_run_command("py", "s", p, "2026-02-04-regular-session")
    assert cmd[cmd.index("--body") + 1] == "bloomington-common-council"


def test_build_run_command_omits_body_when_absent():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-02-04", meeting_type="Regular", event_kind="council")
    assert "--body" not in build_run_command("py", "s", p, "m")
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_gui_rosters.py tests/test_gui_runner.py -k "cached_rosters or includes_body or omits_body" -v`
Expected: FAIL — `gui.rosters` missing; `RunParams` has no `body_slug`.

- [ ] **Step 3: Create `gui/rosters.py`**

```python
"""List locally cached body rosters for the new-meeting Body picker. Mirrors
run_local._list_cached_rosters without importing the heavy CLI module."""
from __future__ import annotations

import json

from src import config


def list_cached_rosters() -> list[tuple[str, str]]:
    """[(slug, label), ...] for each cached roster in CONFIG_DIR/rosters/*.json,
    sorted by filename. label is 'Body Name (N members)', falling back to the slug."""
    rosters_dir = config.CONFIG_DIR / "rosters"
    out: list[tuple[str, str]] = []
    if not rosters_dir.exists():
        return out
    for path in sorted(rosters_dir.glob("*.json")):
        slug = path.stem
        label = slug
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            body_key = data.get("body_key") or slug
            count = len(data.get("politicians", []))
            label = f"{body_key} ({count} members)"
        except (ValueError, OSError, TypeError, AttributeError):
            pass
        out.append((slug, label))
    return out
```

- [ ] **Step 4: Add `body_slug` to `RunParams` + `build_run_command` (`gui/runner.py`)**

In `RunParams` (after `event_orgs`):

```python
    body_slug: Optional[str] = None
```

In `build_run_command`, before `return cmd`:

```python
    if p.body_slug:
        cmd += ["--body", p.body_slug]
```

- [ ] **Step 5: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_gui_rosters.py tests/test_gui_runner.py -k "cached_rosters or includes_body or omits_body" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/rosters.py gui/runner.py tests/test_gui_rosters.py tests/test_gui_runner.py
git commit -m "feat(gui): list cached rosters + --body in the run command

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Body dropdown on the form + POST threading

**Files:**
- Modify: `gui/app.py`
- Modify: `gui/templates/new_meeting.html`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_launch.py`:

```python
def test_new_form_has_body_picker(tmp_config_dir, tmp_meetings_dir):
    import json
    rosters = tmp_config_dir / "rosters"; rosters.mkdir(exist_ok=True)
    (rosters / "bloomington-common-council.json").write_text(json.dumps(
        {"body_key": "Bloomington Common Council", "politicians": [{}]}))
    body = TestClient(create_app()).get("/new").text
    assert 'name="body_slug"' in body
    assert 'value="bloomington-common-council"' in body
    assert "Bloomington Common Council" in body


def test_post_new_threads_body_slug(tmp_config_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    seen = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: seen.setdefault("body", p.body_slug) or "2026-02-04-regular")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-04", "meeting_type": "Regular Session",
        "event_kind": "council", "city": "Bloomington",
        "body_slug": "bloomington-common-council",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert seen["body"] == "bloomington-common-council"


def test_post_new_blank_body_is_none(tmp_config_dir, tmp_meetings_dir, monkeypatch):
    from gui import runner
    seen = {}
    monkeypatch.setattr(runner, "launch_run",
                        lambda p, **kw: seen.setdefault("body", p.body_slug) or "2026-02-04-clip")
    TestClient(create_app()).post("/new", data={
        "input": "https://x/v", "date": "2026-02-04", "meeting_type": "Clip",
        "event_kind": "news_clip", "body_slug": ""}, follow_redirects=False)
    assert seen["body"] is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "body_picker or threads_body or blank_body" -v`
Expected: FAIL — no body field / RunParams not threaded.

- [ ] **Step 3: Pass rosters to the template — `gui/app.py` `new_meeting_form`**

Add to the imports/context in `new_meeting_form`:

```python
        from gui.rosters import list_cached_rosters
        ...
        "cached_rosters": list_cached_rosters(),   # add to the TemplateResponse context dict
```

- [ ] **Step 4: Add `body_slug` to the POST handler — `gui/app.py` `new_meeting_launch`**

Add `body_slug: str = Form("")` to the signature, and in the `RunParams(...)` build:

```python
            body_slug=body_slug.strip() or None,
```

- [ ] **Step 5: Add the dropdown to `gui/templates/new_meeting.html`** (after the City field)

```html
      <label>Body / roster (for council &amp; school-board meetings)
        <select name="body_slug" id="f-body">
          <option value="">— none —</option>
          {% for slug, label in cached_rosters %}<option value="{{ slug }}">{{ label }}</option>{% endfor %}
        </select>
        <small class="help">Guides speaker naming and links the meeting to its Chamber. Only cached rosters appear (refresh with refresh_roster.py).</small>
      </label>
```

- [ ] **Step 6: Run to verify they pass**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "body_picker or threads_body or blank_body" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/new_meeting.html tests/test_gui_launch.py
git commit -m "feat(gui): Body/roster dropdown on the new-meeting form (--body)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. NO server, NO real subprocess. `--body` is only ever a slug that already has a cached roster (it's sourced from the dropdown), so run_local's cached-roster fail-fast can't trigger.

---

## Self-Review

**Spec coverage:** list cached rosters (Task 1 `list_cached_rosters`) ✅ · Body dropdown on the form, options from cache, default none (Task 2) ✅ · selection → `--body <slug>` → roster-guided ID + `chamber_id` at publish (Task 1 command + existing run_local/publish) ✅ · blank → None → no `--body` (Task 1/2) ✅ · GUI-only, no pipeline change (`--body` already exists) ✅ · only cached slugs offered, so no fail-fast ✅.

**Placeholder scan:** none.

**Type consistency:** `list_cached_rosters() -> list[tuple[str,str]]` (slug, label) — consumed by the template loop and the `new_meeting_form` context. `RunParams.body_slug: Optional[str]`; `build_run_command` emits `--body <slug>` iff set; POST handler parses `body_slug` form field → `strip() or None`. Mirrors `run_local._list_cached_rosters`'s label format (body_key + member count). Reuses the `tmp_config_dir` conftest fixture (monkeypatches `config.CONFIG_DIR`) so tests read an isolated rosters dir.
