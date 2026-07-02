# Processing GUI — Slice 4a: Metadata Editing (local + Supabase) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

The "I typed the wrong title/date — fix it without reprocessing" capability. Edit a meeting's **display metadata** (title, city, date, meeting_type, event_kind) and push it live:
- always write the **local** files (`transcript_named.json` + `pipeline_state.json`),
- and if the meeting is **already published**, `UPDATE` its row in Supabase (`meetings.meetings`) — which the site's detail page reads live, so the change shows immediately.

**Honors ADR-0002 (freeze rule):** never renames the meeting directory and never rewrites the `slug` / `meeting_id`. Only display columns change; the UUID-based URL is unaffected.

**Deferred:** 4b — first-time publish of an *unpublished* meeting + the (now-optional) deploy hook. 4c — redo-stage buttons. NOTE: per `publish_meeting`'s current docstring, the site reads live from the API and deploys happen via git push, so no per-edit redeploy is needed here; listing/search pages (baked at build) may lag until the next site rebuild — out of scope for 4a.

**Goal:** From the GUI, correct a published meeting's title/date/etc. and have the live detail page reflect it, with zero reprocessing and no slug/URL change.

**Architecture:** `gui/publish_api.py` holds the DB integration: `_db_url()` (reads `DATABASE_URL`, None if unset), `meeting_published_id()` (SELECT id WHERE slug), `update_supabase_metadata()` (UPDATE the 5 display columns WHERE slug), and `apply_metadata_edit()` (writes local via the existing `_load_meeting_ctx`/`_atomic_write_text`, updates `PipelineState` display fields, then best-effort Supabase UPDATE). A GET/POST `/meetings/{id}/edit` form. `.env.local` is loaded in `gui/__main__` (real server only, never in tests) so `DATABASE_URL` is present. Reuses `src/publish.py`'s connection pattern (`psycopg2`, keyed on slug).

**Tech Stack:** `psycopg2` (mocked in tests — no real DB), FastAPI, Jinja2. Reuses `gui.review_api._load_meeting_ctx` / `_atomic_write_text` / `is_safe_meeting_id`, `src.checkpoint.PipelineState`.

---

### Task 1: Load `.env.local` in the server entry (not in tests)

**Files:**
- Create: `gui/env.py`
- Modify: `gui/__main__.py`
- Test: `tests/test_gui_env.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_gui_env.py`:

```python
from __future__ import annotations

import os


def test_load_env_local_setdefaults(tmp_path, monkeypatch):
    from gui.env import load_env_local
    envfile = tmp_path / ".env.local"
    envfile.write_text("DATABASE_URL=postgres://x\n# comment\nRENDER_DEPLOY_HOOK_URL=https://h\nBLANK=\n")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    load_env_local(envfile)
    assert os.environ["DATABASE_URL"] == "postgres://x"
    assert os.environ["RENDER_DEPLOY_HOOK_URL"] == "https://h"


def test_load_env_local_does_not_override_existing(tmp_path, monkeypatch):
    from gui.env import load_env_local
    envfile = tmp_path / ".env.local"
    envfile.write_text("DATABASE_URL=fromfile\n")
    monkeypatch.setenv("DATABASE_URL", "preset")
    load_env_local(envfile)
    assert os.environ["DATABASE_URL"] == "preset"  # setdefault semantics


def test_load_env_local_missing_file_is_noop(tmp_path):
    from gui.env import load_env_local
    load_env_local(tmp_path / "nope.env")  # must not raise
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_env.py -v`
Expected: FAIL — `No module named 'gui.env'`.

- [ ] **Step 3: Implement `gui/env.py`**

```python
"""Load .env.local into os.environ (mirrors run_local's loader). Called from the
server entrypoint only — NOT from create_app — so tests never pick up real
secrets like DATABASE_URL."""
from __future__ import annotations

import os
from pathlib import Path

_REPO_DIR = Path(__file__).resolve().parent.parent


def load_env_local(path: Path | None = None) -> None:
    """setdefault each KEY=VALUE from .env.local. Missing file is a no-op."""
    env_file = path if path is not None else _REPO_DIR / ".env.local"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        os.environ.setdefault(key.strip(), val.strip())
```

- [ ] **Step 4: Call it in `gui/__main__.py`** (before uvicorn starts)

```python
"""Run the GUI: `python -m gui` → http://127.0.0.1:8000"""
from __future__ import annotations

import uvicorn

from gui.app import create_app
from gui.env import load_env_local


def main() -> None:
    load_env_local()  # DATABASE_URL, RENDER_DEPLOY_HOOK_URL, etc. — server only
    uvicorn.run(create_app(), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_env.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/env.py gui/__main__.py tests/test_gui_env.py
git commit -m "feat(gui): load .env.local in the server entry (DATABASE_URL etc.)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `gui/publish_api.py` — Supabase metadata helpers

**Files:**
- Create: `gui/publish_api.py`
- Test: `tests/test_gui_publish.py` (create)

- [ ] **Step 1: Write the failing tests** (psycopg2 fully mocked — no real DB)

Create `tests/test_gui_publish.py`:

```python
from __future__ import annotations

import gui.publish_api as pub


class _FakeCursor:
    def __init__(self, rows):
        self._rows = rows
        self.executed = []
    def execute(self, sql, params=None):
        self.executed.append((sql, params))
    def fetchone(self):
        return self._rows.pop(0) if self._rows else None
    def __enter__(self): return self
    def __exit__(self, *a): return False


class _FakeConn:
    def __init__(self, rows):
        self.cursor_obj = _FakeCursor(rows)
        self.committed = False
    def cursor(self): return self.cursor_obj
    def commit(self): self.committed = True
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): return False


def _patch_db(monkeypatch, rows):
    conn = _FakeConn(list(rows))
    monkeypatch.setattr(pub, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(pub.psycopg2, "connect", lambda url: conn)
    return conn


def test_meeting_published_id_found(monkeypatch):
    conn = _patch_db(monkeypatch, [("uuid-123",)])
    assert pub.meeting_published_id("2026-02-04-council") == "uuid-123"


def test_meeting_published_id_absent(monkeypatch):
    _patch_db(monkeypatch, [])  # no row
    assert pub.meeting_published_id("ghost") is None


def test_meeting_published_id_no_db_url(monkeypatch):
    monkeypatch.setattr(pub, "_db_url", lambda: None)
    assert pub.meeting_published_id("x") is None  # not configured -> None, no crash


def test_update_supabase_metadata_updates_when_published(monkeypatch):
    conn = _patch_db(monkeypatch, [("uuid-123",)])  # SELECT id finds a row
    ok = pub.update_supabase_metadata("2026-02-04-council", {
        "title": "Fixed Title", "city": "Bloomington", "date": "2026-02-04",
        "meeting_type": "Special Session", "event_kind": "council"})
    assert ok is True
    assert conn.committed is True
    # an UPDATE ... WHERE slug was issued
    sqls = " ".join(sql for sql, _ in conn.cursor_obj.executed).lower()
    assert "update meetings.meetings" in sqls and "where slug" in sqls


def test_update_supabase_metadata_skips_when_unpublished(monkeypatch):
    conn = _patch_db(monkeypatch, [])  # no row
    ok = pub.update_supabase_metadata("ghost", {"title": "x"})
    assert ok is False
    assert conn.committed is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_publish.py -v`
Expected: FAIL — `No module named 'gui.publish_api'`.

- [ ] **Step 3: Implement `gui/publish_api.py`**

```python
"""Push GUI metadata edits to the meetings.* Supabase schema. Reuses src.publish's
connection model (DATABASE_URL + psycopg2, keyed on slug). Best-effort: when the
DB isn't configured or the meeting isn't published, Supabase steps are skipped —
the local write is always authoritative."""
from __future__ import annotations

import os
from typing import Optional

import psycopg2

# Display columns a metadata edit may change. NEVER includes slug/id (ADR-0002).
_EDITABLE = ("title", "city", "date", "meeting_type", "event_kind")


def _db_url() -> Optional[str]:
    url = os.environ.get("DATABASE_URL", "").strip()
    return url or None


def meeting_published_id(meeting_id: str) -> Optional[str]:
    """The Supabase UUID for a published meeting (row where slug = meeting_id),
    or None if unpublished / DB not configured / any error."""
    url = _db_url()
    if not url:
        return None
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM meetings.meetings WHERE slug = %s", (meeting_id,))
                row = cur.fetchone()
                return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def update_supabase_metadata(meeting_id: str, fields: dict) -> bool:
    """UPDATE the editable display columns for a published meeting. Returns True if
    a row was updated, False if unpublished / not configured / error. Never raises."""
    url = _db_url()
    if not url:
        return False
    cols = [c for c in _EDITABLE if c in fields]
    if not cols:
        return False
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT id FROM meetings.meetings WHERE slug = %s", (meeting_id,))
                if cur.fetchone() is None:
                    return False  # unpublished — nothing to update
                set_clause = ", ".join(f"{c} = %s" for c in cols) + ", updated_at = NOW()"
                params = [fields[c] for c in cols] + [meeting_id]
                cur.execute(
                    f"UPDATE meetings.meetings SET {set_clause} WHERE slug = %s", params
                )
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_publish.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/publish_api.py tests/test_gui_publish.py
git commit -m "feat(gui): Supabase metadata update helpers (keyed on slug, best-effort)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `apply_metadata_edit` — local write + Supabase push (freeze rule)

**Files:**
- Modify: `gui/publish_api.py`
- Test: `tests/test_gui_publish.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_publish.py`:

```python
import json
import pytest


def _write_meeting(mdir):
    from src.models import Meeting, Segment, SpeakerMapping
    m = Meeting(meeting_id=mdir.name, city="Bloomington", date="2026-02-04",
                meeting_type="Regular Session", title=None, event_kind="council",
                segments=[Segment(segment_id=0, start_time=0.0, end_time=5.0,
                                  speaker_label="SPEAKER_00", speaker_name="X")],
                speakers={"SPEAKER_00": SpeakerMapping(speaker_label="SPEAKER_00", speaker_name="X")})
    (mdir / "transcript_named.json").write_text(json.dumps(m.to_dict()))


def test_apply_metadata_edit_writes_local_and_freezes_slug(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    # no DB configured -> Supabase skipped, local still written
    monkeypatch.setattr(pub, "_db_url", lambda: None)

    res = pub.apply_metadata_edit("2026-02-04-council",
                                  {"title": "Budget Hearing", "meeting_type": "Special Session"})
    assert res["local"] is True
    assert res["supabase"] is False

    data = json.loads((mdir / "transcript_named.json").read_text())
    assert data["title"] == "Budget Hearing"
    assert data["meeting_type"] == "Special Session"
    assert data["meeting_id"] == "2026-02-04-council"   # FROZEN — slug/id unchanged
    assert mdir.name == "2026-02-04-council"             # dir not renamed
    # pipeline_state display fields updated too
    from src.checkpoint import PipelineState
    assert PipelineState(mdir).meeting_type == "Special Session"


def test_apply_metadata_edit_pushes_to_supabase_when_published(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    calls = {}
    monkeypatch.setattr(pub, "update_supabase_metadata",
                        lambda mid, fields: calls.setdefault("args", (mid, fields)) or True)
    res = pub.apply_metadata_edit("2026-02-04-council", {"title": "New"})
    assert res["supabase"] is True
    assert calls["args"][0] == "2026-02-04-council"
    assert calls["args"][1]["title"] == "New"


def test_apply_metadata_edit_unknown_meeting(tmp_meetings_dir):
    assert pub.apply_metadata_edit("ghost", {"title": "x"}) is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_publish.py -k "apply_metadata_edit" -v`
Expected: FAIL — `apply_metadata_edit` not defined.

- [ ] **Step 3: Implement in `gui/publish_api.py`**

```python
def apply_metadata_edit(meeting_id: str, fields: dict) -> Optional[dict]:
    """Apply display-metadata edits (title/city/date/meeting_type/event_kind).
    Writes the local meeting files, then best-effort pushes to Supabase if the
    meeting is published. NEVER changes the slug / meeting_id / directory
    (ADR-0002). Returns {"local": bool, "supabase": bool} or None if the meeting
    doesn't exist."""
    from gui.review_api import _atomic_write_text, _load_meeting_ctx
    import json as _json

    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return None
    meeting, meeting_dir, _roster = ctx

    edits = {k: v for k, v in fields.items() if k in _EDITABLE}
    for k, v in edits.items():
        setattr(meeting, k, (v.strip() or None) if isinstance(v, str) else v)

    # local: transcript_named.json (the Meeting) — meeting_id/slug untouched.
    _atomic_write_text(meeting_dir / "transcript_named.json",
                       _json.dumps(meeting.to_dict(), indent=2))
    # local: pipeline_state display fields (for --resume parity).
    from src.checkpoint import PipelineState
    state = PipelineState(meeting_dir)
    for k in ("city", "date", "meeting_type", "event_kind"):
        if k in edits:
            setattr(state, k, getattr(meeting, k))
    state.save()

    pushed = update_supabase_metadata(meeting_id, edits) if edits else False
    return {"local": True, "supabase": pushed}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_publish.py -v`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add gui/publish_api.py tests/test_gui_publish.py
git commit -m "feat(gui): apply_metadata_edit writes local + pushes to Supabase (freeze slug)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Edit form route + UI

**Files:**
- Modify: `gui/app.py`
- Create: `gui/templates/edit_meeting.html`
- Modify: `gui/templates/review.html` (link to edit)
- Test: `tests/test_gui_publish.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_publish.py`:

```python
from fastapi.testclient import TestClient
from gui.app import create_app


def test_edit_form_prefills(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/edit").text
    assert 'value="Regular Session"' in body    # meeting_type prefilled
    assert 'value="Bloomington"' in body        # city prefilled
    assert 'action="/meetings/2026-02-04-council/edit"' in body


def test_edit_form_unknown_meeting_404(tmp_meetings_dir):
    assert TestClient(create_app()).get("/meetings/ghost/edit").status_code == 404


def test_post_edit_applies_and_redirects(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.publish_api as pub2
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    monkeypatch.setattr(pub2, "_db_url", lambda: None)  # no real DB in test
    client = TestClient(create_app())
    resp = client.post("/meetings/2026-02-04-council/edit",
                       data={"title": "Budget Hearing", "city": "Bloomington",
                             "date": "2026-02-04", "meeting_type": "Special Session",
                             "event_kind": "council"}, follow_redirects=False)
    assert resp.status_code == 303
    import json as _json
    assert _json.loads((mdir / "transcript_named.json").read_text())["title"] == "Budget Hearing"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_publish.py -k "edit_form or post_edit" -v`
Expected: FAIL — routes/template missing.

- [ ] **Step 3: Add routes to `gui/app.py`**

Add import: `from gui import publish_api`. Inside `create_app()`:

```python
    @app.get("/meetings/{meeting_id}/edit", response_class=HTMLResponse)
    def edit_meeting_form(request: Request, meeting_id: str) -> HTMLResponse:
        from gui.review_api import _load_meeting_ctx
        from src.event_kinds import EVENT_KINDS
        ctx = _load_meeting_ctx(meeting_id)
        if ctx is None:
            raise HTTPException(status_code=404)
        meeting, _dir, _roster = ctx
        return _templates.TemplateResponse(
            request, "edit_meeting.html",
            {"meeting_id": meeting_id, "m": meeting, "event_kinds": list(EVENT_KINDS)},
        )

    @app.post("/meetings/{meeting_id}/edit")
    def edit_meeting_apply(
        meeting_id: str,
        title: str = Form(""), city: str = Form(""), date: str = Form(""),
        meeting_type: str = Form(""), event_kind: str = Form(""),
    ):
        fields = {"title": title, "city": city, "date": date,
                  "meeting_type": meeting_type, "event_kind": event_kind}
        if publish_api.apply_metadata_edit(meeting_id, fields) is None:
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
```

- [ ] **Step 4: Create `gui/templates/edit_meeting.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Edit — {{ meeting_id }}</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/meetings/{{ meeting_id }}/review">← Review</a>
    <h1>Edit metadata: <span class="mid">{{ meeting_id }}</span></h1>
    <p class="sub">Changes save locally and, if this meeting is published, push live to the site. The URL never changes.</p>
  </header>
  <main class="review">
    <form method="post" action="/meetings/{{ meeting_id }}/edit" class="newform">
      <label>Title <input type="text" name="title" value="{{ m.title or '' }}"></label>
      <label>City <input type="text" name="city" value="{{ m.city or '' }}"></label>
      <label>Date <input type="text" name="date" value="{{ m.date or '' }}"></label>
      <label>Event label (meeting_type) <input type="text" name="meeting_type" value="{{ m.meeting_type or '' }}"></label>
      <label>Event kind
        <select name="event_kind">
          {% for k in event_kinds %}<option value="{{ k }}"{% if k == m.event_kind %} selected{% endif %}>{{ k }}</option>{% endfor %}
        </select>
      </label>
      <button type="submit" class="enroll">Save changes</button>
    </form>
  </main>
</body></html>
```

- [ ] **Step 5: Add an "Edit metadata" link to `gui/templates/review.html`** (header, next to the run link)

```html
    <a class="back runlink" href="/meetings/{{ page.meeting_id }}/edit">Edit metadata ✎</a>
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_publish.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/edit_meeting.html gui/templates/review.html tests/test_gui_publish.py
git commit -m "feat(gui): metadata edit form + route (local + Supabase, frozen slug)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. NO real DB touched (psycopg2 mocked in every publish test); NO server started.

---

## Self-Review

**Spec coverage:** edit title/city/date/meeting_type/event_kind (Task 3/4) ✅ · local write always (Task 3) ✅ · Supabase UPDATE when published, keyed on slug (Task 2) ✅ · freeze rule — no slug/id/dir change (Task 3 test asserts) ✅ · `.env.local` loaded for DATABASE_URL, server-only (Task 1) ✅ · best-effort DB (None/skip on unconfigured/unpublished/error, never raises) ✅ · deferred first-publish + redo (4b/4c) ✅.

**Placeholder scan:** none.

**Type consistency:** `apply_metadata_edit(meeting_id, fields) -> dict|None`; route maps None→404, success→303 to `/review`. `_EDITABLE` is the single list of editable columns used by both `update_supabase_metadata` and `apply_metadata_edit`. `meeting_published_id`/`update_supabase_metadata`/`_db_url` all read `DATABASE_URL` and degrade to None/False when unset. Reuses `review_api._load_meeting_ctx`/`_atomic_write_text` (no duplication). psycopg2 is imported at module top so tests monkeypatch `gui.publish_api.psycopg2.connect`. `.env.local` load is in `__main__` only, so `create_app()` in tests never loads real secrets.
