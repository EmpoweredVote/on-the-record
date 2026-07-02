# Processing GUI — Slice 3b: New-Meeting Form Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Make the new-meeting form *de-confusing* (pain A's first half). GUI-only — **no pipeline change**:
- **Dropdowns with plain-English help** for event_kind (8 kinds), compute, diarizer.
- **Live "how it'll look on ontherecord.com" preview** card that updates as you type.
- **Auto-derived meeting_id shown read-only** (under "Advanced") — you never type it, killing the mistyped-id duplicate vector.
- **Conditional validation:** council / school_board require a city (both client-side and server-side), so the exact `run_local` guard ("Refusing to guess … missing --city") can't bite mid-run.

**Deferred:** 3c — source-key duplicate detection (needs `run_local` + `PipelineState` to record a `source_key` at ingest, plus a pre-launch scan). 3d — error always-tier catalog. Full essentials Chamber/Race pickers are out of scope here (the form still launches correctly without them; entity linking already happens per-speaker in review).

**Goal:** A form that teaches the concepts and can't submit a council/school_board meeting without a city.

**Architecture:** A `gui/formmeta.py` holds the help/label metadata (single source of truth for the template + validation). The `/new` template renders selects with descriptions + a preview card + a read-only derived-id line. `gui/static/new_meeting.js` updates the preview + derived id + toggles the city-required state as fields change. `POST /new` gains one guard: `event_kind in {council, school_board}` requires a non-empty city → 400. Builds on 3a (the launch path is unchanged).

**Tech Stack:** FastAPI, Jinja2, vanilla JS. Tests: `pytest` + `TestClient` (form render + the new 400 guard; launch still mocked).

---

### Task 1: `gui/formmeta.py` — help/label metadata

**Files:**
- Create: `gui/formmeta.py`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_launch.py`:

```python
def test_formmeta_covers_all_event_kinds():
    from gui.formmeta import EVENT_KIND_HELP, CITY_REQUIRED_KINDS
    from src.event_kinds import EVENT_KINDS
    # every controlled event kind has help text
    assert set(EVENT_KIND_HELP) == set(EVENT_KINDS)
    assert all(v.strip() for v in EVENT_KIND_HELP.values())
    # deliberative kinds require a city
    assert CITY_REQUIRED_KINDS == {"council", "school_board"}


def test_formmeta_compute_and_diarizer_help():
    from gui.formmeta import COMPUTE_HELP, DIARIZER_HELP
    assert set(COMPUTE_HELP) == {"local", "modal"}
    assert set(DIARIZER_HELP) == {"oss", "api", "vibevoice"}
    assert all(v.strip() for v in {**COMPUTE_HELP, **DIARIZER_HELP}.values())
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "formmeta" -v`
Expected: FAIL — `No module named 'gui.formmeta'`.

- [ ] **Step 3: Implement `gui/formmeta.py`**

```python
"""Human-facing labels/help for the new-meeting form. Single source of truth for
the template and the server-side conditional-city guard. Keys must stay in sync
with src.event_kinds.EVENT_KINDS (a test enforces this)."""
from __future__ import annotations

EVENT_KIND_HELP = {
    "council": "City council / board meeting — deliberative, links to a Chamber. Needs a city.",
    "school_board": "School board meeting — deliberative, links to a Chamber. Needs a city.",
    "debate": "Candidates debating within a race — electoral.",
    "forum": "Candidate forum or townhall — electoral.",
    "community_meeting": "A civic or community meeting.",
    "news_clip": "A journalist interviewing a subject.",
    "press_conference": "A subject making a statement and taking questions.",
    "other": "Anything else.",
}

# event kinds that cannot publish without a city (mirrors run_local's guard).
CITY_REQUIRED_KINDS = {"council", "school_board"}

COMPUTE_HELP = {
    "local": "Process on this Mac — no cost, slower for long meetings.",
    "modal": "Process on Modal cloud GPU — free tier, much faster for long meetings.",
}

DIARIZER_HELP = {
    "oss": "pyannote OSS 3.1 — the local default.",
    "api": "pyannote.ai Precision-2 — needs PYANNOTE_AI_KEY; higher accuracy.",
    "vibevoice": "VibeVoice — requires Compute = modal.",
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "formmeta" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/formmeta.py tests/test_gui_launch.py
git commit -m "feat(gui): form metadata (event-kind/compute/diarizer help + city rule)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Server-side conditional-city guard

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_launch.py`:

```python
def test_post_new_council_requires_city(tmp_meetings_dir):
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "",  # no city
    }, follow_redirects=False)
    assert resp.status_code == 400
    assert "city" in resp.text.lower()


def test_post_new_council_with_city_launches(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-02-10-regular")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Regular",
        "event_kind": "council", "city": "Bloomington",
    }, follow_redirects=False)
    assert resp.status_code == 303


def test_post_new_other_kind_needs_no_city(tmp_meetings_dir, monkeypatch):
    from gui import runner
    monkeypatch.setattr(runner, "launch_run", lambda p, **kw: "2026-02-10-clip")
    client = TestClient(create_app())
    resp = client.post("/new", data={
        "input": "https://x/v", "date": "2026-02-10", "meeting_type": "Clip",
        "event_kind": "news_clip", "city": "",
    }, follow_redirects=False)
    assert resp.status_code == 303  # news_clip doesn't require a city
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "requires_city or with_city_launches or needs_no_city" -v`
Expected: FAIL — council-without-city currently reaches `launch_run` (not 400).

- [ ] **Step 3: Add the guard in `gui/app.py` `new_meeting_launch`**

After the existing `input/date/meeting_type` required-check, before building `RunParams`:

```python
        from gui.formmeta import CITY_REQUIRED_KINDS
        if event_kind in CITY_REQUIRED_KINDS and not city.strip():
            raise HTTPException(
                status_code=400,
                detail=f"A city is required for event kind '{event_kind}'.",
            )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "requires_city or with_city_launches or needs_no_city" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_launch.py
git commit -m "feat(gui): require a city for council/school_board before launch

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Rich form template (dropdowns + help + preview + advanced id)

**Files:**
- Modify: `gui/app.py` (pass formmeta to the template)
- Modify: `gui/templates/new_meeting.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_launch.py`:

```python
def test_new_form_shows_help_and_preview(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    # event-kind help text is rendered (from formmeta)
    assert "deliberative, links to a Chamber" in body
    # compute + diarizer help present
    assert "Modal cloud GPU" in body
    assert "pyannote.ai Precision-2" in body
    # live preview + derived-id scaffolding present, wired via new_meeting.js
    assert 'id="preview"' in body
    assert 'id="derived-id"' in body
    assert "new_meeting.js" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py::test_new_form_shows_help_and_preview -v`
Expected: FAIL — help/preview/scaffolding absent (3a's form is minimal).

- [ ] **Step 3: Pass formmeta into the template — `gui/app.py` `new_meeting_form`**

```python
    @app.get("/new", response_class=HTMLResponse)
    def new_meeting_form(request: Request) -> HTMLResponse:
        from src.event_kinds import EVENT_KINDS
        from gui.formmeta import EVENT_KIND_HELP, COMPUTE_HELP, DIARIZER_HELP, CITY_REQUIRED_KINDS
        return _templates.TemplateResponse(
            request, "new_meeting.html",
            {
                "event_kinds": list(EVENT_KINDS),
                "event_kind_help": EVENT_KIND_HELP,
                "compute_help": COMPUTE_HELP,
                "diarizer_help": DIARIZER_HELP,
                "city_required_kinds": sorted(CITY_REQUIRED_KINDS),
            },
        )
```

- [ ] **Step 4: Rewrite `gui/templates/new_meeting.html`**

```html
<!doctype html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>New meeting</title><link rel="stylesheet" href="/static/style.css"></head>
<body>
  <header><a class="back" href="/">← Library</a><h1>Process a new meeting</h1></header>
  <main class="review newpage"
        data-city-required="{{ city_required_kinds|join(',') }}">
    <form method="post" action="/new" class="newform" id="newform">
      <label>Source URL or file path
        <input type="text" name="input" id="f-input" required placeholder="https://… or /path/to/video.mp4">
      </label>
      <label>Date (YYYY-MM-DD)
        <input type="text" name="date" id="f-date" placeholder="2026-02-10" required>
      </label>
      <label>Meeting type (shown on the site, e.g. "Regular Session")
        <input type="text" name="meeting_type" id="f-mtype" placeholder="Regular Session" required>
      </label>
      <label>Event kind
        <select name="event_kind" id="f-kind">
          {% for k in event_kinds %}<option value="{{ k }}">{{ k }}</option>{% endfor %}
        </select>
        <small class="help" id="kind-help"></small>
      </label>
      <label id="city-label">City <span class="req" id="city-req" hidden>(required)</span>
        <input type="text" name="city" id="f-city" placeholder="Bloomington">
      </label>
      <label>Title (optional — overrides the "city + type" line)
        <input type="text" name="title" id="f-title">
      </label>
      <label>Compute
        <select name="compute" id="f-compute">
          {% for c, h in compute_help.items() %}<option value="{{ c }}">{{ c }}</option>{% endfor %}
        </select>
        <small class="help" id="compute-help"></small>
      </label>
      <label>Diarizer
        <select name="diarizer" id="f-diarizer">
          {% for d, h in diarizer_help.items() %}<option value="{{ d }}">{{ d }}</option>{% endfor %}
        </select>
        <small class="help" id="diarizer-help"></small>
      </label>
      <label>Clip (optional — process only part of the source)
        <span class="cliprow">
          <input type="text" name="clip_start" id="f-clipstart" placeholder="start 10:00">
          <input type="text" name="clip_end" id="f-clipend" placeholder="end 20:00">
        </span>
      </label>
      <details class="advanced"><summary>Advanced</summary>
        <p class="mid">Meeting ID (auto-derived): <span id="derived-id">—</span></p>
      </details>
      <button type="submit" class="enroll">Start processing</button>
    </form>

    <aside class="previewcard">
      <div class="previewlabel">How this will look on ontherecord.com</div>
      <div id="preview" class="preview">
        <div class="pv-title" id="pv-title">—</div>
        <div class="pv-meta"><span id="pv-kind" class="pv-badge"></span> <span id="pv-sub"></span></div>
      </div>
    </aside>
  </main>

  <script>
    window.__EVENT_KIND_HELP = {{ event_kind_help|tojson }};
    window.__COMPUTE_HELP = {{ compute_help|tojson }};
    window.__DIARIZER_HELP = {{ diarizer_help|tojson }};
  </script>
  <script src="/static/new_meeting.js"></script>
</body></html>
```

- [ ] **Step 5: Append styles to `gui/static/style.css`**

```css
main.newpage { display: grid; grid-template-columns: minmax(0, 32rem) 1fr; gap: 1.5rem; align-items: start; }
.newform .help { color: #777; font-size: 0.78rem; }
.newform .req { color: #b32020; font-size: 0.78rem; }
.newform .cliprow { display: flex; gap: 0.4rem; }
.advanced { font-size: 0.85rem; color: #555; }
.previewcard { border: 1px solid #e2e2e2; border-radius: 0.6rem; padding: 1rem; background: #fafafc; position: sticky; top: 0.5rem; }
.previewlabel { font-size: 0.75rem; text-transform: uppercase; letter-spacing: 0.04em; color: #999; margin-bottom: 0.5rem; }
.pv-title { font-weight: 600; font-size: 1.05rem; }
.pv-meta { margin-top: 0.3rem; color: #555; font-size: 0.9rem; }
.pv-badge { background: #eef; color: #445; border-radius: 0.4rem; padding: 0.05rem 0.45rem; font-size: 0.78rem; }
@media (max-width: 720px) { main.newpage { grid-template-columns: 1fr; } }
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py::test_new_form_shows_help_and_preview -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/new_meeting.html gui/static/style.css tests/test_gui_launch.py
git commit -m "feat(gui): rich new-meeting form with dropdown help + preview scaffold

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `new_meeting.js` — live preview, derived id, city-required toggle

**Files:**
- Create: `gui/static/new_meeting.js`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_launch.py`:

```python
def test_new_meeting_js_wires_preview_and_city_rule():
    from pathlib import Path
    js = Path("gui/static/new_meeting.js").read_text()
    # updates the derived id, the preview, and toggles the city-required marker
    assert "derived-id" in js
    assert "preview" in js or "pv-title" in js
    assert "city-req" in js
    # slug derivation mirrors the server ({date}-{slug(meeting_type)})
    assert "toLowerCase" in js
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py::test_new_meeting_js_wires_preview_and_city_rule -v`
Expected: FAIL — file doesn't exist.

- [ ] **Step 3: Create `gui/static/new_meeting.js`**

```javascript
// Live new-meeting form: preview card, auto-derived meeting id, and the
// city-required toggle for deliberative kinds. Display-only — the server is
// authoritative for derivation and validation.
(function () {
  const main = document.querySelector("main.newpage");
  if (!main) return;
  const cityRequired = (main.getAttribute("data-city-required") || "").split(",").filter(Boolean);

  const $ = (id) => document.getElementById(id);
  const input = { kind: $("f-kind"), city: $("f-city"), mtype: $("f-mtype"),
                  date: $("f-date"), title: $("f-title") };

  const slug = (s) => (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");

  function refresh() {
    const kind = input.kind.value;
    const city = input.city.value.trim();
    const mtype = input.mtype.value.trim();
    const date = input.date.value.trim();
    const title = input.title.value.trim();

    // help text
    const kh = (window.__EVENT_KIND_HELP || {})[kind] || "";
    $("kind-help").textContent = kh;
    $("compute-help").textContent = (window.__COMPUTE_HELP || {})[$("f-compute").value] || "";
    $("diarizer-help").textContent = (window.__DIARIZER_HELP || {})[$("f-diarizer").value] || "";

    // derived meeting id: {date}-{slug(meeting_type)}
    const mid = (date && mtype) ? `${date}-${slug(mtype)}` : "—";
    $("derived-id").textContent = mid;

    // preview card
    $("pv-title").textContent = title || [city, mtype].filter(Boolean).join(" ") || "(untitled)";
    $("pv-kind").textContent = kind;
    $("pv-sub").textContent = [date].filter(Boolean).join(" · ");

    // city-required marker + native required attr
    const needCity = cityRequired.includes(kind);
    $("city-req").hidden = !needCity;
    input.city.toggleAttribute("required", needCity);
  }

  main.querySelectorAll("input, select").forEach((el) => {
    el.addEventListener("input", refresh);
    el.addEventListener("change", refresh);
  });
  refresh();
})();
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py::test_new_meeting_js_wires_preview_and_city_rule -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/static/new_meeting.js tests/test_gui_launch.py
git commit -m "feat(gui): live preview, auto meeting-id, and city-required toggle

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1–3b), no regressions.

- [ ] **Step 2: Manual smoke** (form only — do NOT launch a real run)

Run: `.venv/bin/python -m gui`, open http://127.0.0.1:8000/new. As you type: the **preview card** updates (title/kind/date), the **Advanced → Meeting ID** shows the derived id, and picking **event kind = council** shows "(required)" next to City and blocks submit until a city is entered. Switching to **news_clip** drops the requirement. Don't submit a real job. Ctrl-C to stop.

---

## Self-Review

**Spec coverage:** dropdown help for event_kind/compute/diarizer (Task 1 metadata + Task 3 template + Task 4 JS) ✅ · live preview card (Task 3 scaffold + Task 4 JS) ✅ · auto-derived meeting_id shown read-only, not typed (Task 3 Advanced + Task 4 JS; launch still derives server-side) ✅ · conditional city requirement client + server (Task 2 guard + Task 4 toggle) ✅ · GUI-only, no pipeline change ✅ · source-key dedup correctly deferred to 3c ✅.

**Placeholder scan:** none.

**Type consistency:** `EVENT_KIND_HELP` keys == `EVENT_KINDS` (test-enforced); `CITY_REQUIRED_KINDS` used by both the server guard (Task 2) and the JS toggle (via `data-city-required`, Task 3/4). Template ids (`f-kind`, `f-city`, `derived-id`, `pv-title`, `city-req`, …) match `new_meeting.js` selectors. `POST /new` signature and launch path unchanged from 3a (only the city guard added). The JS slug mirrors `runner._slug` ({date}-{slug(meeting_type)}) for display; Python `derive_meeting_id` remains authoritative.
