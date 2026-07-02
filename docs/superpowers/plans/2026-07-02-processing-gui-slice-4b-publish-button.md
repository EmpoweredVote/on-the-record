# Processing GUI — Slice 4b: "Publish this meeting" Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Publish a reviewed meeting to the live site from the GUI: a **confirm-then-publish** flow that runs `src.publish.publish_meeting` (full idempotent upsert into `meetings.*`), **gated on the confidence gate** (`review_status == "pass"`) unless the operator overrides. No redeploy step — the site reads live from the API and code deploys are via git push (`publish_meeting` doesn't rebuild).

Publishing is **outward-facing** (writes prod Supabase, immediately visible), so it's a two-step GET-confirm → POST-publish, never a one-click.

**Deferred:** 4c — redo-stage buttons.

**Goal:** From a meeting, see its gate verdict + published state, and publish (or override-publish) to the live site.

**Architecture:** `gui/publish_api.py` gains `apply_publish(meeting_id, *, force)`: loads the Meeting, reads `review_status`/`body_slug` from `PipelineState`, enforces the gate (`force or review_status == "pass"`), then calls `publish_meeting(meeting, body_slug)` — best-effort, returning a structured result (never raises to the route). A GET `/meetings/{id}/publish` confirm page (shows gate + already-published state) and a POST that publishes and shows the result. All DB mocked in tests.

**Tech Stack:** `src.publish.publish_meeting` (mocked in tests), `src.checkpoint.PipelineState`, FastAPI, Jinja2. Reuses `gui.review_api._load_meeting_ctx`, `gui.publish_api.meeting_published_id` / `_db_url`.

---

### Task 1: `apply_publish` (gated, best-effort)

**Files:**
- Modify: `gui/publish_api.py`
- Test: `tests/test_gui_publish.py` (append)

- [ ] **Step 1: Write the failing tests** (publish_meeting + DB fully mocked)

Append to `tests/test_gui_publish.py`:

```python
def _publish_meeting_ctx(tagged_meeting_dir, review_status):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    from src.checkpoint import PipelineState
    st = PipelineState(mdir); st.review_status = review_status; st.save()
    return mdir


def test_apply_publish_blocked_by_gate(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="review")
    monkeypatch.setattr(pub, "_db_url", lambda: "postgres://fake")
    called = {"n": 0}
    import src.publish as sp
    monkeypatch.setattr(sp, "publish_meeting", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    res = pub.apply_publish("2026-02-04-council", force=False)
    assert res["ok"] is False and res["reason"] == "gate"
    assert res["review_status"] == "review"
    assert called["n"] == 0                     # gate blocked -> never published


def test_apply_publish_force_overrides_gate(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="review")
    monkeypatch.setattr(pub, "_db_url", lambda: "postgres://fake")
    import src.publish as sp
    from src.publish import PublishResult
    monkeypatch.setattr(sp, "publish_meeting",
                        lambda meeting, body_slug=None: PublishResult(meeting.meeting_id, 12, 3))
    res = pub.apply_publish("2026-02-04-council", force=True)
    assert res["ok"] is True and res["segments"] == 12 and res["speakers"] == 3


def test_apply_publish_passes_gate(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="pass")
    monkeypatch.setattr(pub, "_db_url", lambda: "postgres://fake")
    import src.publish as sp
    from src.publish import PublishResult
    seen = {}
    def fake_pub(meeting, body_slug=None):
        seen["body_slug"] = body_slug
        return PublishResult(meeting.meeting_id, 5, 2)
    monkeypatch.setattr(sp, "publish_meeting", fake_pub)
    res = pub.apply_publish("2026-02-04-council", force=False)
    assert res["ok"] is True
    assert "body_slug" in seen                  # body_slug forwarded from state


def test_apply_publish_no_db(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="pass")
    monkeypatch.setattr(pub, "_db_url", lambda: None)
    res = pub.apply_publish("2026-02-04-council", force=False)
    assert res["ok"] is False and res["reason"] == "no_db"


def test_apply_publish_error_is_caught(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="pass")
    monkeypatch.setattr(pub, "_db_url", lambda: "postgres://fake")
    import src.publish as sp
    def boom(*a, **k):
        raise RuntimeError("db exploded")
    monkeypatch.setattr(sp, "publish_meeting", boom)
    res = pub.apply_publish("2026-02-04-council", force=False)
    assert res["ok"] is False and res["reason"] == "error" and "db exploded" in res["error"]


def test_apply_publish_unknown_meeting(tmp_meetings_dir):
    assert pub.apply_publish("ghost", force=False)["reason"] == "unknown"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_publish.py -k "apply_publish" -v`
Expected: FAIL — `apply_publish` not defined.

- [ ] **Step 3: Implement in `gui/publish_api.py`**

```python
def apply_publish(meeting_id: str, *, force: bool = False) -> dict:
    """Publish a meeting to the live site via src.publish.publish_meeting.

    Gated on the confidence gate: only publishes when review_status == "pass",
    unless force=True (human override). Best-effort — returns a structured result,
    never raises. reasons: "unknown" | "gate" | "no_db" | "error"."""
    from gui.review_api import _load_meeting_ctx
    from src.checkpoint import PipelineState

    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return {"ok": False, "reason": "unknown"}
    meeting, meeting_dir, _roster = ctx
    state = PipelineState(meeting_dir)
    review_status = state.review_status

    if not force and review_status != "pass":
        return {"ok": False, "reason": "gate", "review_status": review_status}
    if not _db_url():
        return {"ok": False, "reason": "no_db"}
    try:
        from src.publish import publish_meeting
        result = publish_meeting(meeting, state.body_slug)
        return {"ok": True, "meeting_id": result.meeting_id,
                "segments": result.segments, "speakers": result.speakers}
    except Exception as exc:  # DB / validation failure — surface, don't crash
        return {"ok": False, "reason": "error", "error": str(exc)}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_publish.py -k "apply_publish" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/publish_api.py tests/test_gui_publish.py
git commit -m "feat(gui): apply_publish — gated, best-effort publish via publish_meeting

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Publish confirm (GET) + publish (POST) routes + templates

**Files:**
- Modify: `gui/app.py`
- Create: `gui/templates/publish_confirm.html`
- Create: `gui/templates/publish_result.html`
- Test: `tests/test_gui_publish.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_publish.py`:

```python
def test_publish_confirm_shows_gate(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="pass")
    monkeypatch.setattr(pub, "meeting_published_id", lambda mid: None)  # not yet published
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/publish").text
    assert "pass" in body.lower()
    assert 'action="/meetings/2026-02-04-council/publish"' in body


def test_publish_confirm_unknown_404(tmp_meetings_dir):
    assert TestClient(create_app()).get("/meetings/ghost/publish").status_code == 404


def test_post_publish_success(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="pass")
    monkeypatch.setattr(pub, "apply_publish",
                        lambda mid, force=False: {"ok": True, "meeting_id": mid, "segments": 5, "speakers": 2})
    resp = TestClient(create_app()).post("/meetings/2026-02-04-council/publish", data={})
    assert resp.status_code == 200
    assert "publish" in resp.text.lower()
    assert "5" in resp.text                       # segment count shown


def test_post_publish_gate_blocked_shown(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    _publish_meeting_ctx(tagged_meeting_dir, review_status="review")
    monkeypatch.setattr(pub, "apply_publish",
                        lambda mid, force=False: {"ok": False, "reason": "gate", "review_status": "review"})
    resp = TestClient(create_app()).post("/meetings/2026-02-04-council/publish", data={})
    assert resp.status_code == 200
    assert "gate" in resp.text.lower() or "review" in resp.text.lower()
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_publish.py -k "publish_confirm or post_publish" -v`
Expected: FAIL — routes/templates missing.

- [ ] **Step 3: Add routes to `gui/app.py`** (inside `create_app`, after the edit routes)

```python
    @app.get("/meetings/{meeting_id}/publish", response_class=HTMLResponse)
    def publish_confirm(request: Request, meeting_id: str) -> HTMLResponse:
        from gui.review_api import _load_meeting_ctx
        from src.checkpoint import PipelineState
        ctx = _load_meeting_ctx(meeting_id)
        if ctx is None:
            raise HTTPException(status_code=404)
        _meeting, meeting_dir, _roster = ctx
        state = PipelineState(meeting_dir)
        return _templates.TemplateResponse(
            request, "publish_confirm.html",
            {
                "meeting_id": meeting_id,
                "review_status": state.review_status,
                "gate_pass": state.review_status == "pass",
                "already_published": publish_api.meeting_published_id(meeting_id) is not None,
            },
        )

    @app.post("/meetings/{meeting_id}/publish", response_class=HTMLResponse)
    def publish_apply(request: Request, meeting_id: str, force: str = Form("")):
        result = publish_api.apply_publish(meeting_id, force=bool(force.strip()))
        if result.get("reason") == "unknown":
            raise HTTPException(status_code=404)
        return _templates.TemplateResponse(
            request, "publish_result.html",
            {"meeting_id": meeting_id, "result": result},
        )
```

- [ ] **Step 4: Create `gui/templates/publish_confirm.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish — {{ meeting_id }}</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/meetings/{{ meeting_id }}/review">← Review</a>
    <h1>Publish to the live site: <span class="mid">{{ meeting_id }}</span></h1></header>
  <main class="review">
    <p>Review gate:
      <span class="gate gate-{{ 'pass' if gate_pass else 'review' }}">{{ review_status or 'not scored' }}</span>
      {% if already_published %}· <strong>already published</strong> (this will update it){% endif %}
    </p>
    {% if gate_pass %}
    <form method="post" action="/meetings/{{ meeting_id }}/publish">
      <button type="submit" class="enroll">Publish to site</button>
    </form>
    {% else %}
    <div class="error-banner" style="background:#fdf3e0;color:#9a6a00;border-color:#e0c07a;">
      The confidence gate did not pass ({{ review_status or 'not scored' }}). Review the speakers first,
      or override if you're sure.
    </div>
    <form method="post" action="/meetings/{{ meeting_id }}/publish">
      <input type="hidden" name="force" value="1">
      <button type="submit" class="mark">Publish anyway (override gate)</button>
    </form>
    {% endif %}
  </main>
</body></html>
```

- [ ] **Step 5: Create `gui/templates/publish_result.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Publish result — {{ meeting_id }}</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/meetings/{{ meeting_id }}/review">← Review</a>
    <h1>Publish: <span class="mid">{{ meeting_id }}</span></h1></header>
  <main class="review">
    {% if result.ok %}
    <div class="linked">✓ Published — {{ result.segments }} segments, {{ result.speakers }} speakers.
      It's live on the site now.</div>
    {% elif result.reason == "gate" %}
    <div class="error-banner">Not published — the review gate is "{{ result.review_status or 'not scored' }}",
      not "pass". <a href="/meetings/{{ meeting_id }}/publish">Override?</a></div>
    {% elif result.reason == "no_db" %}
    <div class="error-banner">Not published — DATABASE_URL isn't configured (add it to .env.local).</div>
    {% else %}
    <div class="error-banner">Publish failed: {{ result.error }}</div>
    {% endif %}
    <p><a class="back" href="/meetings/{{ meeting_id }}/review">← Back to review</a></p>
  </main>
</body></html>
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_publish.py -k "publish_confirm or post_publish" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/publish_confirm.html gui/templates/publish_result.html tests/test_gui_publish.py
git commit -m "feat(gui): publish confirm + publish routes (gated, override, result page)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: "Publish" link from the review page

**Files:**
- Modify: `gui/templates/review.html`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_review.py`:

```python
def test_review_page_links_to_publish(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    assert 'href="/meetings/2026-02-04-council/publish"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "links_to_publish" -v`
Expected: FAIL.

- [ ] **Step 3: Add the link in `gui/templates/review.html`** (header, next to the edit/run links)

```html
    <a class="back runlink" href="/meetings/{{ page.meeting_id }}/publish">Publish →</a>
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "links_to_publish" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/templates/review.html tests/test_gui_review.py
git commit -m "feat(gui): Publish link on the review page

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass. NO real DB (publish_meeting + _db_url mocked everywhere); NO server started.

---

## Self-Review

**Spec coverage:** gated publish (Task 1 `apply_publish` — `force or review_status=="pass"`) ✅ · override via force (Task 1 + confirm page) ✅ · runs `publish_meeting(meeting, body_slug)` with body_slug from state (Task 1) ✅ · confirm-then-publish (GET confirm + POST, outward-facing) (Task 2) ✅ · result page for ok/gate/no_db/error (Task 2) ✅ · already-published shown (Task 2 `meeting_published_id`) ✅ · Publish link on review (Task 3) ✅ · best-effort, never raises to the route; no redeploy (publish_meeting doesn't) ✅ · all DB mocked in tests ✅.

**Placeholder scan:** none.

**Type consistency:** `apply_publish(meeting_id, *, force) -> dict` with `reason` in {unknown, gate, no_db, error} + ok/segments/speakers on success; route maps reason=="unknown"→404, else renders result. `publish_meeting(meeting, body_slug)` signature matches `src/publish.py`. Reuses `_load_meeting_ctx`, `meeting_published_id`, `_db_url`. Confirm/result templates read the exact keys `apply_publish` returns. Publishing writes prod only in the real server (tests monkeypatch `src.publish.publish_meeting` + `pub._db_url`); the autouse conftest fixture also clears `DATABASE_URL` so an un-mocked path can't connect.
