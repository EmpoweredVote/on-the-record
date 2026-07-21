# GUI Batch Processing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the operator configure meetings via the existing `/new` form and start them into a concurrency-capped parallel pool, with a background scheduler draining overflow, and fold the "what's in flight" view into the library (sorted by processing recency, live-updating, with counts + a pending strip).

**Architecture:** A new `gui/batch.py` owns a small persisted state (pending queue + cap) and a scheduler. All new-meeting launches route through `batch.launch_or_enqueue`, which reuses `runner.launch_run` verbatim (local-only — no `--publish`). A daemon scheduler thread promotes overflow into free slots. The library becomes the dashboard: sorts on `pipeline_state.json` mtime, adds a Processed column, and polls `/batch/status` to live-update in-flight rows + counts + pending.

**Tech Stack:** FastAPI, Jinja2, Python stdlib `threading`, vanilla JS, pytest + `fastapi.testclient.TestClient`.

Spec: `docs/superpowers/specs/2026-07-21-gui-batch-processing-design.md`. Branch: `feat/gui-batch-processing` (off the merged `main`). Run Python via `.venv/bin/python`.

## Grounding facts (from the current code)

- `gui/runner.py`: `launch_run(p, *, python_exe, script, popen=subprocess.Popen) -> str` spawns `run_local.py` and returns the (collision-bumped) meeting_id. `run_status(meeting_id) -> dict | None` returns `{"meeting_id","completed_stage","stage_label","running","exit_code","log_tail"}` (recovers liveness from the pid sidecar after a restart). `RunParams` is a dataclass with required `input,date,meeting_type,event_kind` and defaulted `city,title,compute,diarizer,meeting_id,clip_start,clip_end,num_speakers,event_orgs(list),body_slug,crec_chamber,guest,race_id,race_slug`.
- `gui/app.py`: `new_meeting_launch` (POST `/new`) currently ends with `runner.launch_run(p, python_exe=sys.executable, script=_RUN_LOCAL)` then `RedirectResponse("/meetings/{id}?tab=progress", 303)`. The `library` route (GET `/`) ends by rendering `library.html` with `{"meetings", "event_kinds"}`.
- `gui/asgi.py` is the real server entrypoint (`app = create_app()`); tests call `gui.app.create_app` directly and never import `asgi`. **The scheduler is started in `asgi.py`, not `create_app`, so tests never spawn a background thread.**
- `gui/library.py::scan_meetings` currently sorts by `(date or meeting_id)` desc. `gui/models.py::MeetingSummary` is a dataclass; it already has `event_orgs/body_slug/race_id/race_label/guest` + `context_line`/`status_key`.
- Test fixtures (`tests/conftest.py`): `tmp_meetings_dir` (monkeypatches `src.config.MEETINGS_DIR` to a temp dir), `tagged_meeting_dir(slug, meeting_id=..., *, completed_stage=...)`.

---

## Task 1: `gui/batch.py` — state, counting, `launch_or_enqueue`

**Files:**
- Create: `gui/batch.py`
- Test: `tests/test_gui_batch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_batch.py
from __future__ import annotations

import gui.batch as batch
from gui.runner import RunParams


def _params(**kw):
    base = dict(input="https://x/v", date="2026-05-01",
                meeting_type="Interview", event_kind="news_clip")
    base.update(kw)
    return RunParams(**base)


def _running(mid):   # a run_status dict for a live run
    return {"meeting_id": mid, "completed_stage": 2, "stage_label": "Speakers separated",
            "running": True, "exit_code": None, "log_tail": ""}


def _finished(mid):
    return {"meeting_id": mid, "completed_stage": 7, "stage_label": "Exported",
            "running": False, "exit_code": 0, "log_tail": ""}


def test_launch_or_enqueue_starts_when_under_cap(tmp_meetings_dir, monkeypatch):
    launched = []
    monkeypatch.setattr(batch.runner, "launch_run",
                        lambda p, **kw: launched.append(p) or "2026-05-01-x-interview")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    outcome, mid = batch.launch_or_enqueue(_params())
    assert outcome == "started" and mid == "2026-05-01-x-interview"
    assert len(launched) == 1


def test_launch_or_enqueue_pends_when_at_cap(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    assert batch.launch_or_enqueue(_params())[0] == "started"       # fills the 1 slot
    outcome, mid = batch.launch_or_enqueue(_params(input="https://x/v2"))
    assert outcome == "pending" and mid is None
    st = batch.status()
    assert st["counts"]["pending"] == 1
    assert st["pending"][0]["pending_id"] >= 1                      # stable id assigned


def test_running_count_prunes_finished(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(2)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "mA")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    batch.launch_or_enqueue(_params())                              # active=[mA] running
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _finished(mid))  # mA done
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "mB")
    outcome, mid = batch.launch_or_enqueue(_params(input="https://x/v2"))
    assert outcome == "started" and mid == "mB"                     # mA pruned, slot free
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_batch.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.batch'`.

- [ ] **Step 3: Create `gui/batch.py`**

```python
"""Parallel batch processing: a concurrency-capped pool + a persisted pending
queue + a background scheduler. All new-meeting launches route through
launch_or_enqueue, so the cap governs a single add and a burst identically.
Local-only — reuses runner.launch_run, which never passes --publish."""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Optional

from src import config
from gui import runner
from gui.runner import RunParams

_PYTHON_EXE = sys.executable
_RUN_LOCAL = str(Path(__file__).resolve().parent.parent / "run_local.py")

_STATE_NAME = "_batch.json"
_DEFAULT_MAX = 8
_MAX_CAP = 10
_lock = threading.RLock()

# RunParams fields serialized into a pending item (meeting_id/num_speakers are
# never queued — the id is minted at real launch time).
_PARAM_FIELDS = (
    "input", "date", "meeting_type", "event_kind", "city", "title",
    "compute", "diarizer", "clip_start", "clip_end", "event_orgs",
    "body_slug", "crec_chamber", "guest", "race_id", "race_slug",
)


def _state_path() -> Path:
    return config.MEETINGS_DIR / _STATE_NAME


def _load() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError
    except (ValueError, OSError):
        data = {}
    data.setdefault("max_concurrent", _DEFAULT_MAX)
    data.setdefault("seq", 0)
    data.setdefault("pending", [])
    data.setdefault("active", [])
    return data


def _save(data: dict) -> None:
    tmp = _state_path().with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _state_path())


def _params_to_dict(p: RunParams) -> dict:
    return {f: getattr(p, f) for f in _PARAM_FIELDS}


def _params_from_dict(d: dict) -> RunParams:
    kw = {f: d.get(f) for f in _PARAM_FIELDS}
    kw["event_orgs"] = kw.get("event_orgs") or []
    return RunParams(**kw)


def _prune_active(data: dict) -> None:
    """Drop meeting_ids whose run has finished/vanished from the active set."""
    alive = []
    for mid in data.get("active", []):
        st = runner.run_status(mid)
        if st is not None and st.get("running"):
            alive.append(mid)
    data["active"] = alive


def _running_count(data: dict) -> int:
    _prune_active(data)
    return len(data["active"])


def launch_or_enqueue(p: RunParams):
    """Launch p now if a pool slot is free, else enqueue it. Returns
    ("started", meeting_id) or ("pending", None)."""
    with _lock:
        data = _load()
        if _running_count(data) < data["max_concurrent"]:
            meeting_id = runner.launch_run(p, python_exe=_PYTHON_EXE, script=_RUN_LOCAL)
            data["active"].append(meeting_id)
            _save(data)
            return ("started", meeting_id)
        data["seq"] += 1
        data["pending"].append({"pending_id": data["seq"], "params": _params_to_dict(p)})
        _save(data)
        return ("pending", None)
```

- [ ] **Step 4: Run to verify (Task 1 tests reference `status`/`set_max_concurrent` from Task 2 — run only the three Task-1 tests, which need them)**

`set_max_concurrent` and `status` are used by the Task-1 tests, so add their minimal forms now (fuller versions land in Task 2 but these are complete and final):

```python
def set_max_concurrent(n: int) -> None:
    with _lock:
        data = _load()
        data["max_concurrent"] = max(1, min(_MAX_CAP, int(n)))
        _save(data)


def status() -> dict:
    from gui.runner import derive_meeting_id
    with _lock:
        data = _load()
        _prune_active(data)
        running = []
        for mid in data["active"]:
            st = runner.run_status(mid)
            if st is None:
                continue
            running.append({"meeting_id": mid, "stage": st["completed_stage"],
                            "stage_label": st["stage_label"], "running": st["running"],
                            "exit_code": st.get("exit_code")})
        pending = []
        for item in data["pending"]:
            prm = item["params"]
            try:
                did = derive_meeting_id(_params_from_dict(prm))
            except Exception:
                did = ""
            label = (prm.get("title") or "").strip() or did or prm.get("input", "")
            pending.append({"pending_id": item["pending_id"], "label": label,
                            "event_kind": prm.get("event_kind"), "derived_id": did})
        _save(data)
        return {"counts": {"running": len(running), "pending": len(pending),
                           "max": data["max_concurrent"]},
                "running": running, "pending": pending}
```

Run: `.venv/bin/python -m pytest tests/test_gui_batch.py -q`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add gui/batch.py tests/test_gui_batch.py
git commit -m "feat(gui): batch.py — capped pool state + launch_or_enqueue"
```

---

## Task 2: scheduler (`_tick`, `remove_pending`, `start_scheduler`)

**Files:**
- Modify: `gui/batch.py`
- Test: `tests/test_gui_batch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_batch.py  (append; reuses _params/_running/_finished)
def test_tick_promotes_pending_when_slot_frees(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    batch.launch_or_enqueue(_params())                       # m1 running (slot full)
    batch.launch_or_enqueue(_params(input="https://x/v2"))   # pending
    batch._tick()                                            # slot still full -> no launch
    assert batch.status()["counts"]["pending"] == 1
    # m1 finishes -> tick promotes the pending item
    monkeypatch.setattr(batch.runner, "run_status",
                        lambda mid: _finished(mid) if mid == "m1" else _running(mid))
    launched = []
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: launched.append(p) or "m2")
    batch._tick()
    assert launched and batch.status()["counts"]["pending"] == 0


def test_tick_skip_and_continue_on_launch_error(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    batch.launch_or_enqueue(_params())                       # m1 running (cap full)
    batch.launch_or_enqueue(_params(input="bad"))            # pending #1
    batch.launch_or_enqueue(_params(input="good"))           # pending #2
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _finished(mid))  # all free
    calls = []
    def flaky(p, **kw):
        calls.append(p.input)
        if p.input == "bad":
            raise RuntimeError("bad source")
        return "mgood"
    monkeypatch.setattr(batch.runner, "launch_run", flaky)
    batch._tick()
    assert "bad" in calls and "good" in calls                # both tried (skip-and-continue)
    assert batch.status()["counts"]["pending"] == 0          # both drained


def test_remove_pending_and_clamp(tmp_meetings_dir, monkeypatch):
    batch.set_max_concurrent(1)
    monkeypatch.setattr(batch.runner, "launch_run", lambda p, **kw: "m1")
    monkeypatch.setattr(batch.runner, "run_status", lambda mid: _running(mid))
    batch.launch_or_enqueue(_params())
    batch.launch_or_enqueue(_params(input="https://x/v2"))   # pending
    pid = batch.status()["pending"][0]["pending_id"]
    assert batch.remove_pending(pid) is True
    assert batch.status()["counts"]["pending"] == 0
    assert batch.remove_pending(pid) is False                # already gone
    batch.set_max_concurrent(99)
    assert batch._load()["max_concurrent"] == 10             # clamped to _MAX_CAP


def test_start_scheduler_idempotent(tmp_meetings_dir):
    import threading
    batch.start_scheduler(interval=999)
    batch.start_scheduler(interval=999)                      # second call is a no-op
    assert [t.name for t in threading.enumerate()].count("batch-scheduler") == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_batch.py -k "tick or remove_pending or scheduler" -q`
Expected: FAIL — `_tick`/`remove_pending`/`start_scheduler` not defined.

- [ ] **Step 3: Implement**

Append to `gui/batch.py`:

```python
def remove_pending(pending_id) -> bool:
    """Drop a pending item by its stable id. True if something was removed."""
    with _lock:
        data = _load()
        before = len(data["pending"])
        data["pending"] = [i for i in data["pending"]
                           if i.get("pending_id") != int(pending_id)]
        _save(data)
        return len(data["pending"]) < before


def _tick() -> None:
    """Promote pending items into free pool slots. A launch that raises drops
    that item (skip-and-continue) so one bad source never blocks the pool."""
    with _lock:
        data = _load()
        while _running_count(data) < data["max_concurrent"] and data["pending"]:
            item = data["pending"].pop(0)
            try:
                mid = runner.launch_run(_params_from_dict(item["params"]),
                                        python_exe=_PYTHON_EXE, script=_RUN_LOCAL)
                data["active"].append(mid)
            except Exception:
                logging.getLogger(__name__).warning(
                    "batch: dropping unlaunchable pending item %s",
                    item.get("pending_id"), exc_info=True)
            _save(data)


_scheduler_started = False


def start_scheduler(interval: float = 4.0) -> None:
    """Start the daemon scheduler thread once. Sleeps first, then ticks, so a
    freshly-built app (and tests with a long interval) don't tick synchronously."""
    global _scheduler_started
    with _lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    def _loop():
        while True:
            time.sleep(interval)
            try:
                _tick()
            except Exception:
                logging.getLogger(__name__).warning("batch scheduler tick failed", exc_info=True)

    threading.Thread(target=_loop, name="batch-scheduler", daemon=True).start()
```

- [ ] **Step 4: Run to verify**

Run: `.venv/bin/python -m pytest tests/test_gui_batch.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add gui/batch.py tests/test_gui_batch.py
git commit -m "feat(gui): batch scheduler _tick + remove_pending + start_scheduler"
```

---

## Task 3: Route `/new` through the pool + flash banner

**Files:**
- Modify: `gui/app.py` (`new_meeting_launch` tail; `new_meeting_form` signature + context)
- Modify: `gui/templates/new_meeting.html` (banner + button label)
- Test: `tests/test_gui_launch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_launch.py  (append)
def test_post_new_routes_through_batch_started(tmp_meetings_dir, monkeypatch):
    import gui.batch as batch
    seen = {}
    monkeypatch.setattr(batch, "launch_or_enqueue",
                        lambda p: seen.setdefault("p", p) or ("started", "2026-05-01-x"))
    from fastapi.testclient import TestClient
    from gui.app import create_app
    resp = TestClient(create_app()).post("/new", data={
        "input": "https://x/v", "date": "2026-05-01", "meeting_type": "Interview",
        "event_kind": "news_clip", "compute": "modal", "diarizer": "oss",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/new?flash=started")
    assert seen["p"].input == "https://x/v"


def test_post_new_routes_through_batch_pending(tmp_meetings_dir, monkeypatch):
    import gui.batch as batch
    monkeypatch.setattr(batch, "launch_or_enqueue", lambda p: ("pending", None))
    from fastapi.testclient import TestClient
    from gui.app import create_app
    resp = TestClient(create_app()).post("/new", data={
        "input": "https://x/v", "date": "2026-05-01", "meeting_type": "Interview",
        "event_kind": "news_clip",
    }, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"].startswith("/new?flash=pending")


def test_new_form_renders_flash_banner(tmp_meetings_dir, monkeypatch):
    import gui.batch as batch
    monkeypatch.setattr(batch, "status",
                        lambda: {"counts": {"running": 3, "pending": 1, "max": 8},
                                 "running": [], "pending": []})
    from fastapi.testclient import TestClient
    from gui.app import create_app
    body = TestClient(create_app()).get("/new?flash=started&label=Becerra").text
    assert "Becerra" in body and "3 running" in body and "1 pending" in body


def test_new_form_add_and_start_button(tmp_meetings_dir):
    from fastapi.testclient import TestClient
    from gui.app import create_app
    body = TestClient(create_app()).get("/new").text
    assert "Add &amp; start" in body or "Add & start" in body
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_launch.py -k "routes_through_batch or flash_banner or add_and_start" -q`
Expected: FAIL — route still calls `runner.launch_run`; no banner/button.

- [ ] **Step 3: Change `new_meeting_launch`'s tail in `gui/app.py`**

Add `from urllib.parse import quote` to the imports at the top of `gui/app.py` (with the other stdlib imports). Then replace the launch tail (currently lines ~365–369):

```python
        try:
            meeting_id = runner.launch_run(p, python_exe=sys.executable, script=_RUN_LOCAL)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=progress", status_code=303)
```

with:

```python
        from gui import batch
        try:
            outcome, meeting_id = batch.launch_or_enqueue(p)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        label = (p.title or p.input or "").strip()
        return RedirectResponse(
            url=f"/new?flash={outcome}&label={quote(label)}", status_code=303)
```

- [ ] **Step 4: Add flash + counts to `new_meeting_form`**

Change the `new_meeting_form` signature to accept the flash query params, and add flash/counts to the context:

```python
    @app.get("/new", response_class=HTMLResponse)
    def new_meeting_form(request: Request, flash: str = "", label: str = "") -> HTMLResponse:
        from src.event_kinds import EVENT_KINDS
        from gui.formmeta import (EVENT_KIND_HELP, COMPUTE_HELP, DIARIZER_HELP,
                                   CITY_REQUIRED_KINDS, MEETING_TYPE_DEFAULTS,
                                   FIELDS_BY_KIND, DEFAULT_COMPUTE, DEFAULT_DIARIZER)
        from gui.rosters import list_cached_rosters
        from gui import batch
        return _templates.TemplateResponse(
            request, "new_meeting.html",
            {
                "event_kinds": list(EVENT_KINDS),
                "event_kind_help": EVENT_KIND_HELP,
                "compute_help": COMPUTE_HELP,
                "diarizer_help": DIARIZER_HELP,
                "city_required_kinds": sorted(CITY_REQUIRED_KINDS),
                "meeting_type_defaults": MEETING_TYPE_DEFAULTS,
                "cached_rosters": list_cached_rosters(),
                "fields_by_kind": FIELDS_BY_KIND,
                "default_compute": DEFAULT_COMPUTE,
                "default_diarizer": DEFAULT_DIARIZER,
                "flash": flash,
                "flash_label": label,
                "batch_counts": batch.status()["counts"],
            },
        )
```

- [ ] **Step 5: Add the banner + button label in `gui/templates/new_meeting.html`**

Right after the `<header>...</header>` line (line 6), insert the banner:

```html
  {% if flash %}
  <div class="flash flash-{{ flash }}">
    {% if flash == "started" %}✓ Started: {{ flash_label }}{% else %}⏳ Queued (pool full): {{ flash_label }}{% endif %}
    · {{ batch_counts.running }} running · {{ batch_counts.pending }} pending ·
    <a href="/">View library →</a>
  </div>
  {% endif %}
```

Change the submit button (line ~101) from:
```html
      <button type="submit" class="enroll">Start processing</button>
```
to:
```html
      <button type="submit" class="enroll">Add &amp; start</button>
```

Append to `gui/static/style.css`:
```css
.flash { margin: 0.75rem 0; padding: 0.5rem 0.75rem; border-radius: 0.5rem; font-size: 0.9rem; border: 1px solid #cfe3cf; background: #eef7ee; color: #1b5e20; }
.flash-pending { border-color: #e6d59a; background: #fbf3dd; color: #7a5b00; }
```

- [ ] **Step 6: Run to verify + regression**

Run:
```bash
.venv/bin/python -m pytest tests/test_gui_launch.py -q
```
Expected: PASS. The pre-existing `/new` POST tests (`test_post_new_launches_and_redirects`, `test_post_new_council_with_city_launches`, `test_post_new_gates_fields_not_allowed_for_kind`, `test_post_new_passes_guest_and_race`) monkeypatch `runner.launch_run`; they now need to monkeypatch `batch.launch_or_enqueue` instead. Update each: replace `monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "<id>")` with `monkeypatch.setattr(batch, "launch_or_enqueue", lambda p: ("started", "<id>"))` (import `gui.batch as batch`), and change any `assert resp.headers["location"] == "/meetings/<id>?tab=progress"` to `assert resp.headers["location"].startswith("/new?flash=started")`. Tests that inspect the captured `RunParams` keep working (capture `p` inside the lambda). Re-run until green.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/new_meeting.html gui/static/style.css tests/test_gui_launch.py
git commit -m "feat(gui): /new adds into the batch pool + flash banner"
```

---

## Task 4: Batch routes + start the scheduler on the real server

**Files:**
- Modify: `gui/app.py` (three routes)
- Modify: `gui/asgi.py` (start scheduler)
- Test: `tests/test_gui_batch.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_batch.py  (append)
from fastapi.testclient import TestClient
from gui.app import create_app


def test_batch_status_route(tmp_meetings_dir, monkeypatch):
    monkeypatch.setattr(batch, "status",
                        lambda: {"counts": {"running": 2, "pending": 1, "max": 8},
                                 "running": [{"meeting_id": "m1", "stage": 3,
                                              "stage_label": "Transcribed", "running": True,
                                              "exit_code": None}],
                                 "pending": [{"pending_id": 9, "label": "X",
                                              "event_kind": "news_clip", "derived_id": "d"}]})
    r = TestClient(create_app()).get("/batch/status")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"]["running"] == 2
    assert body["running"][0]["meeting_id"] == "m1"


def test_batch_max_route(tmp_meetings_dir):
    client = TestClient(create_app())
    r = client.post("/batch/max", data={"n": "5"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert batch._load()["max_concurrent"] == 5


def test_batch_remove_pending_route(tmp_meetings_dir, monkeypatch):
    removed = {}
    monkeypatch.setattr(batch, "remove_pending", lambda pid: removed.setdefault("pid", pid) or True)
    r = TestClient(create_app()).post("/batch/pending/7/remove", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    assert removed["pid"] == 7
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_batch.py -k "batch_status_route or batch_max_route or remove_pending_route" -q`
Expected: FAIL — routes 404.

- [ ] **Step 3: Add the routes to `gui/app.py`**

Near the existing `/api/politicians/search` route, add:

```python
    @app.get("/batch/status")
    def batch_status() -> JSONResponse:
        from gui import batch
        return JSONResponse(batch.status())

    @app.post("/batch/max")
    def batch_set_max(n: str = Form("")):
        from gui import batch
        try:
            batch.set_max_concurrent(int(n))
        except (TypeError, ValueError):
            pass
        return RedirectResponse(url="/", status_code=303)

    @app.post("/batch/pending/{pending_id}/remove")
    def batch_remove_pending(pending_id: int):
        from gui import batch
        batch.remove_pending(pending_id)
        return RedirectResponse(url="/", status_code=303)
```

- [ ] **Step 4: Start the scheduler in `gui/asgi.py`**

Change `gui/asgi.py` to start the scheduler after building the app (real server only — tests use `create_app` directly and never import this module, so they stay thread-free):

```python
from gui.app import create_app
from gui.env import load_env_local

load_env_local()  # server-only; must run in the reload worker, not in create_app
app = create_app()

from gui import batch
batch.start_scheduler()  # daemon pool scheduler; drains overflow into free slots
```

- [ ] **Step 5: Run to verify + regression**

Run: `.venv/bin/python -m pytest tests/test_gui_batch.py -q`
Expected: PASS (all).

- [ ] **Step 6: Commit**

```bash
git add gui/app.py gui/asgi.py tests/test_gui_batch.py
git commit -m "feat(gui): /batch routes + start the pool scheduler on the server"
```

---

## Task 5: Library sorts by processing recency + a Processed column

**Files:**
- Modify: `gui/models.py` (`MeetingSummary.processed_at` + `processed_label`)
- Modify: `gui/library.py` (`_summarize` sets it; `scan_meetings` sorts by it)
- Modify: `gui/templates/library.html` (Processed column)
- Test: `tests/test_gui_library.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_library.py  (append)
def test_processed_label_relative():
    import time
    from gui.models import MeetingSummary
    def s(pa):
        return MeetingSummary(meeting_id="m", title=None, city=None, meeting_type=None,
                              date=None, event_kind=None, completed_stage=0, processed_at=pa)
    now = time.time()
    assert s(now - 90).processed_label.endswith("m ago")     # "1m ago"
    assert s(now - 7200).processed_label.endswith("h ago")   # "2h ago"
    assert s(None).processed_label == "—"


def test_scan_meetings_sorts_by_processed_recency(tagged_meeting_dir, tmp_meetings_dir):
    import os, time
    old = tagged_meeting_dir("x", meeting_id="2026-01-01-old", completed_stage=4)
    new = tagged_meeting_dir("x", meeting_id="2026-01-02-new", completed_stage=4)
    # Make "old" the more-recently-processed one (older clip date, newer mtime).
    now = time.time()
    os.utime(new / "pipeline_state.json", (now - 1000, now - 1000))
    os.utime(old / "pipeline_state.json", (now, now))
    ids = [s.meeting_id for s in scan_meetings(tmp_meetings_dir)]
    assert ids == ["2026-01-01-old", "2026-01-02-new"]       # by mtime desc, not clip date


def test_library_renders_processed_column(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/").text
    assert "<th>Processed</th>" in body
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -k "processed" -q`
Expected: FAIL — `processed_at`/`processed_label` and the column don't exist.

- [ ] **Step 3: Add `processed_at` + `processed_label` to `MeetingSummary` (`gui/models.py`)**

Add the field (after `guest`):
```python
    processed_at: Optional[float] = None   # pipeline_state.json mtime (epoch); library sort key
```

Add the property (near `stage_label`):
```python
    @property
    def processed_label(self) -> str:
        """Relative 'when last processed', from processed_at: 'just now' / '5m ago'
        / '3h ago' / a date for older. '—' when unknown."""
        if not self.processed_at:
            return "—"
        import time
        delta = time.time() - self.processed_at
        if delta < 60:
            return "just now"
        if delta < 3600:
            return f"{int(delta // 60)}m ago"
        if delta < 86400:
            return f"{int(delta // 3600)}h ago"
        import datetime
        return datetime.date.fromtimestamp(self.processed_at).isoformat()
```

- [ ] **Step 4: Set + sort by `processed_at` in `gui/library.py`**

In `_summarize`, compute the mtime and pass it. Add before the `return MeetingSummary(...)`:
```python
    try:
        processed_at = (meeting_dir / "pipeline_state.json").stat().st_mtime
    except OSError:
        processed_at = None
```
and add `processed_at=processed_at,` to the `MeetingSummary(...)` kwargs.

Replace the sort line in `scan_meetings`:
```python
    summaries.sort(key=lambda s: (s.date or s.meeting_id, s.meeting_id), reverse=True)
```
with:
```python
    # Sort by most-recent processing activity (state-file mtime) so running and
    # just-finished meetings float to the top; fall back to clip date / id.
    summaries.sort(key=lambda s: (s.processed_at or 0.0, s.date or "", s.meeting_id),
                   reverse=True)
```

- [ ] **Step 5: Add the Processed column in `gui/templates/library.html`**

In the `<thead>` row, add `<th>Processed</th>` right after `<th>Date</th>`:
```html
        <tr><th>Meeting</th><th>Date</th><th>Processed</th><th>Kind</th><th>Speakers</th><th>Length</th><th>Review</th><th>Status</th><th>Live</th></tr>
```
In the body row, add the cell right after the Date `<td>`:
```html
          <td>{{ m.date or "—" }}</td>
          <td>{{ m.processed_label }}</td>
```

- [ ] **Step 6: Run to verify + regression**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -q`
Expected: PASS. The pre-existing `test_scan_meetings_reads_state_and_sorts_by_date_desc` asserts clip-date ordering; it now conflicts with the mtime sort. Update it: keep its two meetings but assert the order reflects mtime (set both state files' mtimes with `os.utime` so the intended one is first), or narrow it to assert the fields it reads (`city`, `completed_stage`) without asserting order. Re-run until green.

- [ ] **Step 7: Commit**

```bash
git add gui/models.py gui/library.py gui/templates/library.html tests/test_gui_library.py
git commit -m "feat(gui): library sorts by processing recency + Processed column"
```

---

## Task 6: Library batch header + pending strip + live poll

**Files:**
- Modify: `gui/app.py` (library route passes batch data)
- Modify: `gui/templates/library.html` (batch header, pending strip, row poll hooks)
- Modify: `gui/static/library.js` (poll `/batch/status`)
- Test: `tests/test_gui_library.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_library.py  (append)
def test_library_renders_batch_header_and_pending(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.batch as batch
    monkeypatch.setattr(batch, "status",
                        lambda: {"counts": {"running": 2, "pending": 1, "max": 8},
                                 "running": [],
                                 "pending": [{"pending_id": 9, "label": "Ken Paxton",
                                              "event_kind": "news_clip", "derived_id": "d"}]})
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/").text
    assert 'id="batch-running-count"' in body
    assert "Ken Paxton" in body                                  # pending chip
    assert 'action="/batch/pending/9/remove"' in body            # remove form
    assert 'action="/batch/max"' in body                         # max-concurrent control


def test_library_rows_have_meeting_id_hook(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/").text
    assert 'data-meeting-id="2026-02-04-council"' in body
    assert 'class="status-cell"' in body


def test_library_js_polls_batch_status(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/library.js").read_text()
    assert "/batch/status" in js and "status-cell" in js
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -k "batch_header or meeting_id_hook or js_polls" -q`
Expected: FAIL.

- [ ] **Step 3: Pass batch data from the library route (`gui/app.py`)**

Replace the library route's return (currently lines ~68–71):
```python
        from src.event_kinds import EVENT_KINDS
        return _templates.TemplateResponse(
            request, "library.html", {"meetings": meetings, "event_kinds": list(EVENT_KINDS)},
        )
```
with:
```python
        from src.event_kinds import EVENT_KINDS
        from gui import batch
        bs = batch.status()
        return _templates.TemplateResponse(
            request, "library.html",
            {"meetings": meetings, "event_kinds": list(EVENT_KINDS),
             "batch_counts": bs["counts"], "batch_pending": bs["pending"]},
        )
```

- [ ] **Step 4: Add the batch header + pending strip + row hooks in `gui/templates/library.html`**

Immediately after `<main>` (before `<div class="lib-toolbar">`), add:
```html
    <div class="batch-header" id="batch-header">
      <span class="batch-counts">
        <i class="batch-dot"></i>
        <b id="batch-running-count">{{ batch_counts.running }}</b> running ·
        <b id="batch-pending-count">{{ batch_counts.pending }}</b> pending
      </span>
      <form method="post" action="/batch/max" class="batch-max">
        <label>Max concurrent
          <select name="n" onchange="this.form.submit()">
            {% for v in range(1, 11) %}<option value="{{ v }}"{% if v == batch_counts.max %} selected{% endif %}>{{ v }}</option>{% endfor %}
          </select>
        </label>
      </form>
    </div>
    <div class="pending-strip" id="pending-strip">
      {% for pitem in batch_pending %}
      <span class="pending-chip">
        {{ pitem.label }}
        <form method="post" action="/batch/pending/{{ pitem.pending_id }}/remove">
          <button type="submit" title="Remove from queue">✕</button>
        </form>
      </span>
      {% endfor %}
    </div>
```
On the `<tr>` (line ~39), add a `data-meeting-id`:
```html
        <tr data-meeting-id="{{ m.meeting_id }}" data-kind="{{ m.event_kind or '' }}" data-status="{{ m.status_key }}"
            data-search="{{ [m.display_name, m.meeting_id, m.context_line, m.event_kind]|select|join(' ')|lower }}">
```
And give the Status cell a class so the poller can target it — change:
```html
          <td><span class="stage stage-{{ m.completed_stage }}">{{ m.stage_label }}</span></td>
```
to:
```html
          <td class="status-cell"><span class="stage stage-{{ m.completed_stage }}">{{ m.stage_label }}</span></td>
```

Append to `gui/static/style.css`:
```css
.batch-header { display: flex; align-items: center; gap: 1rem; margin-bottom: 0.75rem; font-size: 0.9rem; }
.batch-counts { color: #444; }
.batch-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #2a7de1; margin-right: 4px; vertical-align: middle; }
.batch-max form, .batch-max { margin: 0; }
.pending-strip { display: flex; gap: 0.4rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.pending-chip { display: inline-flex; align-items: center; gap: 0.3rem; background: #f1f1f4; border-radius: 0.5rem; padding: 0.15rem 0.5rem; font-size: 0.82rem; }
.pending-chip form { margin: 0; }
.pending-chip button { border: none; background: none; cursor: pointer; color: #999; font-size: 0.8rem; padding: 0; }
```

- [ ] **Step 5: Add batch polling to `gui/static/library.js`**

Append (a second IIFE, leaving the existing filter IIFE intact):
```javascript
// Live batch view: poll /batch/status while anything is in flight, updating the
// counts, the pending strip, and the status cell of each running row in place.
(function () {
  const header = document.getElementById("batch-header");
  if (!header) return;
  const runCount = document.getElementById("batch-running-count");
  const pendCount = document.getElementById("batch-pending-count");
  const strip = document.getElementById("pending-strip");

  function renderPending(pending) {
    strip.innerHTML = (pending || []).map((p) => {
      const label = String(p.label == null ? "" : p.label)
        .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/"/g, "&quot;");
      return '<span class="pending-chip">' + label +
        '<form method="post" action="/batch/pending/' + p.pending_id + '/remove">' +
        '<button type="submit" title="Remove from queue">✕</button></form></span>';
    }).join("");
  }

  async function poll() {
    let st;
    try { st = await (await fetch("/batch/status")).json(); }
    catch (_) { return setTimeout(poll, 4000); }
    if (runCount) runCount.textContent = st.counts.running;
    if (pendCount) pendCount.textContent = st.counts.pending;
    renderPending(st.pending);
    (st.running || []).forEach((r) => {
      const row = document.querySelector('tr[data-meeting-id="' + r.meeting_id + '"]');
      const cell = row && row.querySelector(".status-cell .stage");
      if (cell) cell.textContent = r.stage_label;
    });
    if (st.counts.running > 0 || st.counts.pending > 0) setTimeout(poll, 4000);
  }
  poll();
})();
```

- [ ] **Step 6: Run to verify + regression**

Run:
```bash
.venv/bin/python -m pytest tests/test_gui_library.py tests/test_gui_launch.py tests/test_gui_batch.py -q
node --check gui/static/library.js
```
Expected: PASS; JS syntax OK. Also confirm the empty-library case still renders (the `batch-header` is outside the `{% if meetings %}` block, so it shows even with no meetings — good; `library.js`'s filter IIFE already returns early when `#lib-table` is absent).

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/library.html gui/static/library.js gui/static/style.css tests/test_gui_library.py
git commit -m "feat(gui): library batch header, pending strip, and live status poll"
```

---

## Self-Review (completed during planning)

- **Spec coverage:** capped parallel pool + persisted pending + scheduler (Tasks 1–2); race-safe `pending_id` removal (Task 2); skip-and-continue (Task 2 `_tick`); local-only via `runner.launch_run` (Task 1); restart-safe (persisted `active`/`pending` + pid-sidecar recovery in `run_status`); Add-and-start + flash banner (Task 3); `/batch/status|max|pending/{id}/remove` routes + scheduler-on-server (Task 4); library sort-by-mtime + Processed column (Task 5); batch header + pending strip + live poll (Task 6). Cap default 8, clamp 1–10 (Task 1/2).
- **Deviation from spec (intentional):** the scheduler starts in `gui/asgi.py` (the real entrypoint), not `create_app`, so `TestClient(create_app())` never spawns a background thread. The scheduler loop sleeps-then-ticks so a long test interval never ticks synchronously.
- **Type consistency:** `launch_or_enqueue -> (str, str|None)` used identically in Task 3. `status()` shape (`counts{running,pending,max}`, `running[{meeting_id,stage,stage_label,running,exit_code}]`, `pending[{pending_id,label,event_kind,derived_id}]`) is produced in Task 1 and consumed by the routes (Task 4), the library route + template (Task 6), and `library.js` (Task 6 uses `counts.running/pending`, `pending[].pending_id/label`, `running[].meeting_id/stage_label`). `processed_at`/`processed_label` defined in Task 5 and rendered there. `remove_pending(pending_id)` / `set_max_concurrent(n)` signatures match their routes.
- **Placeholder scan:** none — every step has complete code.
- **Known limitations (per spec, not bugs):** manual Continue/Re-run stay immediate (not pooled); the poll updates the status cell live but doesn't re-sort finished rows until a manual refresh (they were already near the top by recency).
