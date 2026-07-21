# GUI Meeting Workspace Shell + Fragment Refactor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the four separate per-meeting pages (`/run`, `/review`, `/edit`, `/publish`) with one tabbed `/meetings/{id}` workspace whose panels swap without full-page reloads, and refactor the per-page templates into shared partials.

**Architecture:** FastAPI + Jinja + vanilla JS (no framework). A shell route renders a persistent header + tab strip + the active panel inline (server-rendered, so it works without JS). New fragment endpoints (`/panel/{name}`) and a JSON `/status` endpoint let `workspace.js` swap panels and poll progress live. The existing speaker/edit/publish/continue/redo action POST endpoints keep their current 303 responses unchanged; `workspace.js` intercepts form submits, POSTs, then re-fetches the affected panel — identical UX to returning a fragment, but with **zero churn to the ~30 existing action-route tests**. The old page URLs 301-redirect to the canonical `/meetings/{id}?tab=…`.

**Tech Stack:** FastAPI, Jinja2, vanilla JS, pytest + `fastapi.testclient.TestClient`.

**This is Plan 1 of 3** for the GUI redesign (spec: `docs/superpowers/specs/2026-07-20-gui-meeting-workspace-redesign-design.md`). Plan 2 = rich kind-aware meeting IDs + kind-aware new-meeting form; Plan 3 = richer searchable library. This plan is independently shippable: after it, the whole per-meeting workflow lives in one pane.

**Deviation from spec (deliberate):** The spec says action endpoints "return the re-rendered panel fragment." This plan instead keeps them returning their existing 303 redirect and has `workspace.js` re-fetch the panel after a successful POST. The observable UX (no reload; the panel refreshes in place) is identical, the endpoints stay simple, and it avoids rewriting every action-route test. Non-JS fallback: the 303 chains through the 301 to a full server-rendered workspace — degraded but functional.

---

## Test fixtures (already exist — reuse)

`tests/conftest.py` provides `tmp_meetings_dir` (monkeypatches `src.config.MEETINGS_DIR` to a temp dir) and `tagged_meeting_dir(source, *, meeting_id, completed_stage)` (creates a meeting dir with `pipeline_state.json`). `tests/test_gui_review.py` defines a local `_write_meeting(mdir, *, clip_start=None)` helper that writes a 2-speaker `transcript_named.json`. Reuse these patterns; do not reinvent them.

Run the full GUI suite at any time with:
```bash
.venv/bin/python -m pytest tests/test_gui_review.py tests/test_gui_launch.py tests/test_gui_publish.py tests/test_gui_workspace.py -q
```
(Per project convention, always use `.venv/bin/python`, never system `python3`.)

---

## Task 1: `default_tab_for_stage` — which tab a meeting opens on

**Files:**
- Create: `gui/workspace.py`
- Test: `tests/test_gui_workspace.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_workspace.py
from __future__ import annotations

from gui.workspace import default_tab_for_stage


def test_default_tab_progress_until_identified():
    assert default_tab_for_stage(0) == "progress"
    assert default_tab_for_stage(3) == "progress"


def test_default_tab_review_once_identified():
    # Stage 4 == "Identified — ready to review" (gui.models._STAGE_LABELS).
    assert default_tab_for_stage(4) == "review"
    assert default_tab_for_stage(7) == "review"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'gui.workspace'`.

- [ ] **Step 3: Write minimal implementation**

```python
# gui/workspace.py
"""Assemble the context for the meeting workspace shell and its panels.

Pure-ish data assembly — no HTTP. Reuses the existing loaders (load_review_page,
_load_meeting_ctx, run_status, PipelineState) so the workspace and the pipeline
agree on how a meeting is read. The single source of truth for panel data,
called by both the GET /panel/{name} route and the shell route in gui.app."""
from __future__ import annotations

from typing import Optional

from src import config

from gui.models import stage_label
from gui.paths import is_safe_meeting_id


# Speakers are assigned during stage 4 ("Identified"); before that, Review is empty.
_REVIEW_READY_STAGE = 4


def default_tab_for_stage(completed_stage: int) -> str:
    """The tab a meeting opens on: Progress while still processing, Review once
    speakers have been identified (stage >= 4)."""
    return "review" if completed_stage >= _REVIEW_READY_STAGE else "progress"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -q`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add gui/workspace.py tests/test_gui_workspace.py
git commit -m "feat(gui): default_tab_for_stage for the meeting workspace"
```

---

## Task 2: `panel_context` — per-panel Jinja context

**Files:**
- Modify: `gui/workspace.py`
- Test: `tests/test_gui_workspace.py`

**Contract:** `panel_context(name, meeting_id) -> dict | None`. Returns `None` when the panel name is unknown, the id is unsafe, or the meeting doesn't exist (no `pipeline_state.json`). Otherwise a dict that always contains `meeting_id` and `active_tab`. For `review`/`details`/`publish` on a meeting that hasn't reached stage 4 (no `transcript_named.json`), it returns `{... , "not_ready": <message>}` instead of the panel data, so the panel renders a placeholder rather than 404-ing.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_workspace.py  (append)
from gui.workspace import panel_context


def test_panel_context_none_for_unknown_panel_or_meeting(tmp_meetings_dir):
    assert panel_context("bogus", "x") is None
    assert panel_context("review", "../escape") is None      # unsafe id
    assert panel_context("review", "ghost") is None          # no such meeting


def test_panel_context_progress_needs_only_the_dir(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=1)
    ctx = panel_context("progress", "2026-02-04-council")
    assert ctx["active_tab"] == "progress"
    assert ctx["meeting_id"] == "2026-02-04-council"
    assert ("diarize" in ctx["redo_stages"]) and ctx["stages"]  # stepper + redo data present


def test_panel_context_review_not_ready_before_identify(tagged_meeting_dir, tmp_meetings_dir):
    # completed_stage 2, no transcript_named.json -> placeholder, not None.
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=2)
    ctx = panel_context("review", "2026-02-04-council")
    assert ctx is not None
    assert ctx["page"] is None
    assert "not_ready" in ctx and "Identify" in ctx["not_ready"]


def test_panel_context_review_ready_returns_page(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    ctx = panel_context("review", "2026-02-04-council")
    assert ctx["page"] is not None
    assert ctx["page"].meeting_id == "2026-02-04-council"


def test_panel_context_details_and_publish(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.publish_api as pub
    monkeypatch.setattr(pub, "meeting_published_id", lambda mid: None)
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)

    d = panel_context("details", "2026-02-04-council")
    assert d["m"].city == "Bloomington" and "council" in d["event_kinds"]

    p = panel_context("publish", "2026-02-04-council")
    assert "review_status" in p and p["already_published"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -k panel_context -q`
Expected: FAIL — `ImportError: cannot import name 'panel_context'`.

- [ ] **Step 3: Write minimal implementation**

Append to `gui/workspace.py`:

```python
_PANELS = ("progress", "review", "details", "publish")


def _meeting_dir(meeting_id: str):
    """The meeting dir if the id is safe and the meeting exists (has
    pipeline_state.json), else None."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    if not (meeting_dir / "pipeline_state.json").exists():
        return None
    return meeting_dir


def _not_ready_message(meeting_dir) -> str:
    """Placeholder text for a panel whose data isn't produced yet (pre-stage-4)."""
    from src.checkpoint import PipelineState
    try:
        stage = int(PipelineState(meeting_dir).completed_stage)
    except Exception:
        stage = 0
    return (f"This step becomes available after processing reaches the Identify "
            f"stage. Currently: {stage_label(stage)}.")


def panel_context(name: str, meeting_id: str) -> Optional[dict]:
    """Jinja context for one workspace panel, or None if the panel name is
    unknown / the id is unsafe / the meeting doesn't exist. Panels that need the
    processed meeting return a 'not_ready' message before stage 4 instead of None."""
    if name not in _PANELS:
        return None
    meeting_dir = _meeting_dir(meeting_id)
    if meeting_dir is None:
        return None

    base = {"meeting_id": meeting_id, "active_tab": name}

    if name == "progress":
        from src.checkpoint import PipelineStage
        from gui import runner
        base["stages"] = [(s.value, stage_label(s.value))
                          for s in PipelineStage if s.value >= 1]
        base["redo_stages"] = list(runner.REDO_STAGES)
        return base

    # review / details / publish need the processed meeting (transcript_named.json).
    if not (meeting_dir / "transcript_named.json").exists():
        base["not_ready"] = _not_ready_message(meeting_dir)
        base["page"] = None  # review.html reads page; None + not_ready -> placeholder
        return base

    if name == "review":
        from gui.review_api import load_review_page
        base["page"] = load_review_page(meeting_id)
        return base

    if name == "details":
        from gui.review_api import _load_meeting_ctx
        from src.event_kinds import EVENT_KINDS
        ctx = _load_meeting_ctx(meeting_id)
        if ctx is None:            # malformed transcript_named -> treat as not ready
            base["not_ready"] = _not_ready_message(meeting_dir)
            return base
        base["m"] = ctx[0]
        base["event_kinds"] = list(EVENT_KINDS)
        return base

    # publish
    from gui.review_api import _load_meeting_ctx
    from gui import publish_api
    from src.checkpoint import PipelineState
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        base["not_ready"] = _not_ready_message(meeting_dir)
        return base
    state = PipelineState(ctx[1])
    base["review_status"] = state.review_status
    base["gate_pass"] = state.review_status == "pass"
    base["already_published"] = publish_api.meeting_published_id(meeting_id) is not None
    return base
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add gui/workspace.py tests/test_gui_workspace.py
git commit -m "feat(gui): panel_context assembles per-panel workspace data"
```

---

## Task 3: `header_context` — persistent header + status

**Files:**
- Modify: `gui/workspace.py`
- Test: `tests/test_gui_workspace.py`

**Contract:** `header_context(meeting_id, *, is_live=None) -> dict | None`. `None` if the meeting doesn't exist. Otherwise `{meeting_id, display_name, date, event_kind, review_status, completed_stage, gate_badge, is_live, attention_count}`. `attention_count` is 0 before stage 4 (no speakers yet) and otherwise the number of speakers needing attention. `gate_badge` reuses `gui.models.gate_badge`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_workspace.py  (append)
from gui.workspace import header_context


def test_header_context_none_for_unknown(tmp_meetings_dir):
    assert header_context("ghost") is None


def test_header_context_prestage4_no_attention(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=2)
    h = header_context("2026-02-04-council")
    assert h["completed_stage"] == 2
    assert h["attention_count"] == 0          # no speakers before Identify
    assert h["display_name"]                  # falls back to city/type/id
    assert h["is_live"] is None               # unknown unless caller passes it


def test_header_context_counts_attention_when_ready(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)  # SPEAKER_01 is unnamed -> needs attention
    h = header_context("2026-02-04-council", is_live=True)
    assert h["attention_count"] == 1
    assert h["is_live"] is True
    assert h["gate_badge"][0] in ("pass", "review", "failed", "none")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -k header -q`
Expected: FAIL — `ImportError: cannot import name 'header_context'`.

- [ ] **Step 3: Write minimal implementation**

Append to `gui/workspace.py`:

```python
def header_context(meeting_id: str, *, is_live: Optional[bool] = None) -> Optional[dict]:
    """Context for the persistent workspace header. None if the meeting is
    unknown. attention_count is 0 before stage 4 (no speakers yet)."""
    import json
    from src.checkpoint import PipelineState
    from gui.models import gate_badge

    meeting_dir = _meeting_dir(meeting_id)
    if meeting_dir is None:
        return None
    state = PipelineState(meeting_dir)
    completed = int(state.completed_stage)

    # Title: prefer transcript_named.json title, else city + meeting_type, else id.
    title = None
    named = meeting_dir / "transcript_named.json"
    if named.exists():
        try:
            t = json.loads(named.read_text(encoding="utf-8")).get("title")
            title = t if isinstance(t, str) and t.strip() else None
        except (ValueError, OSError):
            title = None
    display_name = title or " ".join(
        p for p in (state.city, state.meeting_type) if p and p.strip()
    ) or meeting_id

    attention_count = 0
    if completed >= _REVIEW_READY_STAGE:
        from gui.review_api import load_review_page
        page = load_review_page(meeting_id)
        if page is not None:
            attention_count = len(page.needs_attention)

    return {
        "meeting_id": meeting_id,
        "display_name": display_name,
        "date": state.date,
        "event_kind": state.event_kind,
        "review_status": state.review_status,
        "completed_stage": completed,
        "gate_badge": gate_badge(state.review_status, state.trusted_coverage),
        "is_live": is_live,
        "attention_count": attention_count,
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add gui/workspace.py tests/test_gui_workspace.py
git commit -m "feat(gui): header_context for the workspace header + status"
```

---

## Task 4: Extract the speaker-card macro into a shared partial

**Files:**
- Create: `gui/templates/panels/_macros.html`

The speaker-card macro currently lives inline in `gui/templates/review.html` (lines 40–116). Move it verbatim into a shared partial so the review panel can `{% import %}` it. No behavior change yet.

- [ ] **Step 1: Create the macro partial**

Create `gui/templates/panels/_macros.html` containing exactly the `{% macro card(c, meeting_id) %}` … `{% endmacro %}` block below (this is the current review.html macro with `page.meeting_id` replaced by the passed-in `meeting_id` parameter so the macro is self-contained):

```html
{% macro card(c, meeting_id) %}
<div class="card {{ 'confirmed' if c.is_confirmed else 'attention' }}">
  <div class="card-head">
    <span class="label">{{ c.label }}</span>
    <span class="cname">{{ c.display_name }}</span>
    {% if c.confidence > 0 %}<span class="conf">conf {{ '%.2f'|format(c.confidence) }}</span>{% endif %}
    <span class="mins">{{ '%.1f'|format(c.minutes) }}m · {{ c.seg_count }} segs</span>
  </div>
  {% if c.speaker_status == "unidentified" %}<span class="statusbadge unident">unidentified</span>{% endif %}
  {% if c.speaker_status == "non_speaker" %}<span class="statusbadge nonspk">not-a-speaker</span>{% endif %}
  {% for hname, hscore in c.hints %}
    <div class="hint">▸ voice match: {{ hname }} ({{ '%.2f'|format(hscore) }})</div>
  {% endfor %}
  {% if c.sample_text %}<p class="sample">“{{ c.sample_text[:200] }}”</p>{% endif %}
  {% if c.clip_seeks %}
  <div class="clips">
    {% for s in c.clip_seeks %}
    <button type="button" class="clip" data-seek="{{ '%.2f'|format(s) }}">▶ clip {{ loop.index }}</button>
    {% endfor %}
  </div>
  {% endif %}
  {% if c.is_linked %}
  <div class="linked">🔗 linked: <span class="pslug">{{ c.politician_slug or c.politician_id }}</span></div>
  {% endif %}
  <div class="actions">
    {% if not c.is_confirmed and c.accept_name %}
    <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/name">
      <input type="hidden" name="name" value="{{ c.accept_name }}">
      <button type="submit" class="accept">✓ Accept {{ c.accept_name }}</button>
    </form>
    {% endif %}
    <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/name" class="rename">
      <input type="text" name="name" placeholder="Type a name…" autocomplete="off">
      <button type="submit">Save</button>
    </form>
    {% if c.is_linked %}
    <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/unlink">
      <button type="submit" class="unlink">Unlink</button>
    </form>
    {% endif %}
    <div class="link-search"
         data-search-url="/api/politicians/search"
         data-link-action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/link">
      <input type="text" placeholder="Link politician… (type a name)" autocomplete="off">
      <div class="link-results"></div>
    </div>
    {% if all_cards|length > 1 %}
    <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/merge" class="merge">
      <select name="target">
        <option value="">Merge into…</option>
        {% for o in all_cards %}{% if o.label != c.label %}
        <option value="{{ o.label }}">{{ o.label }} — {{ o.display_name }}</option>
        {% endif %}{% endfor %}
      </select>
      <button type="submit">Merge</button>
    </form>
    {% endif %}
    <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/unidentified">
      <button type="submit" class="mark">Unidentified</button>
    </form>
    <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/not-speaker">
      <button type="submit" class="mark">Not a speaker</button>
    </form>
    {% if c.is_enrollable %}
      {% if c.is_enrolled %}
      <span class="voice-saved">✓ voice saved</span>
      {% else %}
      <form method="post" action="/meetings/{{ meeting_id }}/speakers/{{ c.label }}/enroll">
        <button type="submit" class="enroll">Save this voice for future meetings</button>
        {% if c.thin_sample %}<span class="thin">⚠ short sample</span>{% endif %}
      </form>
      {% endif %}
      <span class="profile-strength {{ c.profile_strength }}" title="{% if c.profile_strength == 'strong' %}Already well-established — enroll only if this captures a new mic/setting.{% elif c.profile_strength == 'new' %}No profile yet — enrolling a clean sample here really helps future auto-ID.{% else %}Still building — a clean sample here strengthens future auto-ID.{% endif %}">{{ c.profile_hint }}</span>
    {% endif %}
  </div>
</div>
{% endmacro %}
```

Note the macro references `all_cards` (the merge dropdown needs every card). The review panel (Task 5) sets `all_cards` in its own scope before calling `card()`.

- [ ] **Step 2: Commit** (no test yet — the panel that uses it lands in Task 5)

```bash
git add gui/templates/panels/_macros.html
git commit -m "refactor(gui): extract speaker-card macro to shared partial"
```

---

## Task 5: Review panel partial

**Files:**
- Create: `gui/templates/panels/review.html`

Content-only fragment (no `<html>`/`<head>`/header). Renders the media player, the two speaker groups via the shared macro, and the not-ready placeholder. This is the body of the current `review.html` `<main>` minus the danger-zone (Delete moves to the shell kebab in Task 7) and minus the header links.

- [ ] **Step 1: Create the partial**

Create `gui/templates/panels/review.html`:

```html
{% import "panels/_macros.html" as m %}
{% if page is none %}
  <p class="empty">{{ not_ready }}</p>
{% else %}
  {% set all_cards = page.all_cards %}
  {% if page.youtube_id %}
    <iframe id="yt-player" class="player"
            src="https://www.youtube.com/embed/{{ page.youtube_id }}"
            title="source video" frameborder="0"
            allow="accelerometer; autoplay; encrypted-media; picture-in-picture"
            allowfullscreen></iframe>
  {% elif page.hls_url %}
    <video id="player" class="player" data-hls="{{ page.hls_url }}" controls preload="metadata"></video>
  {% elif page.media_kind == "video" %}
    <video id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></video>
  {% elif page.media_kind == "audio" %}
    <audio id="player" class="player" src="/meetings/{{ page.meeting_id }}/media" controls preload="metadata"></audio>
  {% else %}
    <p class="empty">No media found for clip playback.</p>
  {% endif %}

  <section>
    <h2>Needs attention</h2>
    {% if page.needs_attention %}
      {% for c in page.needs_attention %}{{ m.card(c, page.meeting_id) }}{% endfor %}
    {% else %}<p class="empty">Nothing needs attention — every speaker is confirmed.</p>{% endif %}
  </section>

  <section>
    <h2>Confirmed</h2>
    {% for c in page.confirmed %}{{ m.card(c, page.meeting_id) }}{% endfor %}
  </section>
{% endif %}
```

Note: `{% set all_cards = page.all_cards %}` puts `all_cards` in scope for the macro's merge dropdown (the macro reads it from the caller's context).

- [ ] **Step 2: Commit** (rendered/asserted via the shell route in Task 8)

```bash
git add gui/templates/panels/review.html
git commit -m "feat(gui): review panel partial"
```

---

## Task 6: Progress, Details, and Publish panel partials

**Files:**
- Create: `gui/templates/panels/progress.html`, `gui/templates/panels/details.html`, `gui/templates/panels/publish.html`

- [ ] **Step 1: Create `gui/templates/panels/progress.html`**

Body of the current `run.html` `<main>` minus the header. Adds `data-meeting-id` so `workspace.js` can poll (Task 10).

```html
<div class="progress-panel" data-meeting-id="{{ meeting_id }}">
  <ol class="stepper" id="stepper">
    {% for value, label in stages %}<li data-stage="{{ value }}">{{ label }}</li>{% endfor %}
  </ol>
  <div id="error-banner" class="error-banner" hidden></div>
  <h2>Log</h2>
  <pre id="log" class="runlog">(waiting for output…)</pre>
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
</div>
```

- [ ] **Step 2: Create `gui/templates/panels/details.html`**

Body of the current `edit_meeting.html` `<main>` minus the header. Shows the not-ready placeholder when the meeting isn't processed yet.

```html
{% if not_ready is defined and m is not defined %}
  <p class="empty">{{ not_ready }}</p>
{% else %}
  <p class="sub">Changes save locally and, if this meeting is published, push live to the site. The URL never changes.</p>
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
{% endif %}
```

- [ ] **Step 3: Create `gui/templates/panels/publish.html`**

Body of the current `publish_confirm.html` `<main>` minus the header. Adds an empty `#publish-result` slot that `workspace.js` fills with the inline result (Task 10).

```html
{% if not_ready is defined and review_status is not defined %}
  <p class="empty">{{ not_ready }}</p>
{% else %}
  <p>Review gate:
    <span class="gate gate-{{ 'pass' if gate_pass else 'review' }}">{{ review_status or 'not scored' }}</span>
    {% if already_published %}· <strong>already published</strong> (this will update it){% endif %}
  </p>
  {% if gate_pass %}
  <form method="post" action="/meetings/{{ meeting_id }}/publish" class="publish-form">
    <button type="submit" class="enroll">Publish to site</button>
  </form>
  {% else %}
  <div class="error-banner" style="background:#fdf3e0;color:#9a6a00;border-color:#e0c07a;">
    The confidence gate did not pass ({{ review_status or 'not scored' }}). Review the speakers first,
    or override if you're sure.
  </div>
  <form method="post" action="/meetings/{{ meeting_id }}/publish" class="publish-form">
    <input type="hidden" name="force" value="1">
    <button type="submit" class="mark">Publish anyway (override gate)</button>
  </form>
  {% endif %}
  <div id="publish-result"></div>
{% endif %}
```

- [ ] **Step 4: Commit**

```bash
git add gui/templates/panels/progress.html gui/templates/panels/details.html gui/templates/panels/publish.html
git commit -m "feat(gui): progress, details, and publish panel partials"
```

---

## Task 7: Workspace shell template + CSS

**Files:**
- Create: `gui/templates/workspace.html`
- Modify: `gui/static/style.css`

The shell renders the persistent header (title, pills, kebab with Clean-up + Delete), the tab strip (anchors to `?tab=…` for no-JS + deep-linking), and includes the active panel inline via a dynamic include.

- [ ] **Step 1: Create `gui/templates/workspace.html`**

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{{ header.display_name }} — CouncilScribe</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header class="ws-header" data-meeting-id="{{ header.meeting_id }}" data-active-tab="{{ active_tab }}">
    <a class="back" href="/">← Library</a>
    <div class="ws-titlebar">
      <h1>{{ header.display_name }}</h1>
      <span class="ws-pills">
        {% if header.date %}<span class="pill">{{ header.date }}</span>{% endif %}
        {% if header.event_kind %}<span class="pill">{{ header.event_kind }}</span>{% endif %}
        {% set level, text = header.gate_badge %}
        <span id="gate-pill" class="gate gate-{{ level }}">{{ text }}</span>
        {% if header.is_live is not none %}
          <span id="live-pill" class="live-badge live-{{ 'live' if header.is_live else 'notlive' }}">{{ 'Live' if header.is_live else 'Not live' }}</span>
        {% else %}<span id="live-pill" class="live-unknown">—</span>{% endif %}
      </span>
      <details class="kebab">
        <summary>⋯</summary>
        <div class="kebab-menu">
          <form method="post" action="/meetings/{{ header.meeting_id }}/cleanup" data-navigate
                onsubmit="return confirm('Delete the local video and WAV for this meeting? A compressed audio copy is kept, and (for streamed sources) the video streams from the source.');">
            <button type="submit" class="cleanup-btn">🧹 Clean up media</button>
          </form>
          <form method="post" action="/meetings/{{ header.meeting_id }}/delete" data-navigate
                onsubmit="return confirm('Permanently delete {{ header.meeting_id }}? This cannot be undone.');">
            <input type="text" name="confirm_slug" autocomplete="off" placeholder="Type the meeting id to confirm">
            <button type="submit" class="delete-btn">Delete meeting</button>
          </form>
        </div>
      </details>
    </div>
    <nav class="tabstrip">
      {% for tab, label in [("progress","Progress"),("review","Review"),("details","Details"),("publish","Publish")] %}
      <a class="tab{% if tab == active_tab %} active{% endif %}"
         data-tab="{{ tab }}" href="/meetings/{{ header.meeting_id }}?tab={{ tab }}">{{ label }}{% if tab == "review" and header.attention_count %} <span class="dot" id="attn-dot">●</span>{% endif %}</a>
      {% endfor %}
    </nav>
  </header>
  <main class="review ws-main" id="panel">
    {% include "panels/" ~ active_tab ~ ".html" %}
  </main>
  <script src="/static/workspace.js"></script>
</body>
</html>
```

- [ ] **Step 2: Append CSS to `gui/static/style.css`**

```css
/* --- Meeting workspace --- */
.ws-titlebar { display: flex; align-items: center; gap: 0.75rem; flex-wrap: wrap; }
.ws-pills { display: flex; gap: 0.4rem; align-items: center; flex-wrap: wrap; }
.pill { font-size: 0.8rem; padding: 0.1rem 0.5rem; border-radius: 0.5rem; border: 1px solid #ddd; color: #555; }
.kebab { margin-left: auto; position: relative; }
.kebab > summary { list-style: none; cursor: pointer; border: 1px solid #ccc; border-radius: 0.4rem; padding: 0.1rem 0.6rem; }
.kebab > summary::-webkit-details-marker { display: none; }
.kebab-menu { position: absolute; right: 0; z-index: 10; background: #fff; border: 1px solid #ddd; border-radius: 0.5rem; padding: 0.6rem; display: flex; flex-direction: column; gap: 0.6rem; min-width: 16rem; box-shadow: 0 4px 16px rgba(0,0,0,0.08); }
.kebab-menu form { display: flex; flex-direction: column; gap: 0.3rem; margin: 0; }
.cleanup-btn { padding: 0.3rem 0.6rem; border: 1px solid #bbb; border-radius: 0.4rem; background: #f6f6f8; cursor: pointer; }
.delete-btn { padding: 0.3rem 0.6rem; border: 1px solid #d0a0a0; border-radius: 0.4rem; background: #fdeaea; color: #b32020; cursor: pointer; }
.tabstrip { display: flex; gap: 0.25rem; margin-top: 0.75rem; }
.tabstrip .tab { padding: 0.4rem 0.9rem; border: 1px solid #e2e2e2; border-bottom: none; border-radius: 0.5rem 0.5rem 0 0; text-decoration: none; color: #555; font-size: 0.9rem; }
.tabstrip .tab.active { background: #f4f6fb; color: #1a1a1a; font-weight: 600; }
.tabstrip .tab .dot { color: #d08b00; }
.ws-main { border: 1px solid #e2e2e2; border-radius: 0 0.5rem 0.5rem 0.5rem; padding: 1rem; }
```

- [ ] **Step 3: Commit**

```bash
git add gui/templates/workspace.html gui/static/style.css
git commit -m "feat(gui): workspace shell template + tab styling"
```

---

## Task 8: Shell route, panel-fragment route, status endpoint

**Files:**
- Modify: `gui/app.py` (add three routes near the existing `review_page` route)
- Test: `tests/test_gui_workspace.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_workspace.py  (append)
from fastapi.testclient import TestClient
from gui.app import create_app


def test_workspace_shell_renders_active_panel(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    r = client.get("/meetings/2026-02-04-council")   # no ?tab -> default (review @ stage 4)
    assert r.status_code == 200
    assert 'class="tabstrip"' in r.text
    assert "Needs attention" in r.text               # review panel rendered inline
    assert "workspace.js" in r.text


def test_workspace_shell_respects_tab_param(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council?tab=publish").text
    assert 'action="/meetings/2026-02-04-council/publish"' in body   # publish panel


def test_workspace_shell_404_unknown(tmp_meetings_dir):
    assert TestClient(create_app()).get("/meetings/ghost").status_code == 404


def test_panel_fragment_route(tagged_meeting_dir, tmp_meetings_dir):
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    r = client.get("/meetings/2026-02-04-council/panel/review")
    assert r.status_code == 200
    assert "Needs attention" in r.text
    assert "<html" not in r.text.lower()             # fragment only, no shell
    assert client.get("/meetings/2026-02-04-council/panel/bogus").status_code == 404
    assert client.get("/meetings/ghost/panel/review").status_code == 404


def test_status_endpoint_augments_run_status(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.publish_api as pub
    monkeypatch.setattr(pub, "meeting_published_id", lambda mid: None)
    from tests.test_gui_review import _write_meeting
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    st = TestClient(create_app()).get("/meetings/2026-02-04-council/status").json()
    assert st["completed_stage"] == 4
    assert st["attention_count"] == 1
    assert "review_status" in st and "is_live" in st
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -k "workspace_shell or panel_fragment or status_endpoint" -q`
Expected: FAIL (404s — routes don't exist yet).

- [ ] **Step 3: Add the routes to `gui/app.py`**

Add these imports near the top (with the other `from gui...` imports):

```python
from gui import workspace
```

Add these routes inside `create_app()` (place them just after the existing `review_page` route, before the `media` route):

```python
    @app.get("/meetings/{meeting_id}", response_class=HTMLResponse)
    def workspace_shell(request: Request, meeting_id: str, tab: str = "") -> HTMLResponse:
        header = workspace.header_context(
            meeting_id, is_live=(publish_api.meeting_published_id(meeting_id) is not None),
        )
        if header is None:
            raise HTTPException(status_code=404)
        active = tab.strip() or workspace.default_tab_for_stage(header["completed_stage"])
        ctx = workspace.panel_context(active, meeting_id)
        if ctx is None:  # bad ?tab value -> fall back to the default tab
            active = workspace.default_tab_for_stage(header["completed_stage"])
            ctx = workspace.panel_context(active, meeting_id)
        return _templates.TemplateResponse(
            request, "workspace.html", {**ctx, "header": header, "active_tab": active},
        )

    @app.get("/meetings/{meeting_id}/panel/{name}", response_class=HTMLResponse)
    def workspace_panel(request: Request, meeting_id: str, name: str) -> HTMLResponse:
        ctx = workspace.panel_context(name, meeting_id)
        if ctx is None:
            raise HTTPException(status_code=404)
        return _templates.TemplateResponse(request, f"panels/{name}.html", ctx)

    @app.get("/meetings/{meeting_id}/status")
    def workspace_status(meeting_id: str) -> JSONResponse:
        st = runner.run_status(meeting_id)
        if st is None:
            raise HTTPException(status_code=404)
        header = workspace.header_context(
            meeting_id, is_live=(publish_api.meeting_published_id(meeting_id) is not None),
        )
        st["review_status"] = header["review_status"] if header else None
        st["is_live"] = header["is_live"] if header else None
        st["attention_count"] = header["attention_count"] if header else 0
        return JSONResponse(st)
```

Note: FastAPI matches the more specific static routes (`/meetings/{id}/review`, `/media`, etc.) before the `{tab}`-less `GET /meetings/{meeting_id}` because they are declared earlier and have longer paths; the new shell route only catches the bare `/meetings/{id}`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_workspace.py
git commit -m "feat(gui): workspace shell, panel-fragment, and status routes"
```

---

## Task 9: Redirect the old page URLs to the workspace

**Files:**
- Modify: `gui/app.py` (replace the bodies of `run_page`, `review_page`, `edit_meeting_form`, `publish_confirm` with redirects; change `new_meeting_launch`'s redirect target)
- Test: `tests/test_gui_workspace.py`, and edits to `tests/test_gui_review.py`, `tests/test_gui_launch.py`

The four GET page routes become 301 redirects to the canonical workspace URL. The POST action routes are unchanged. `new_meeting_launch` now redirects to the workspace instead of `/run`.

- [ ] **Step 1: Write the failing redirect tests**

```python
# tests/test_gui_workspace.py  (append)
import pytest


@pytest.mark.parametrize("old,tab", [
    ("run", "progress"), ("review", "review"), ("edit", "details"), ("publish", "publish"),
])
def test_old_page_urls_redirect_to_workspace(tagged_meeting_dir, tmp_meetings_dir, old, tab):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    r = client.get(f"/meetings/2026-02-04-council/{old}", follow_redirects=False)
    assert r.status_code == 301
    assert r.headers["location"] == f"/meetings/2026-02-04-council?tab={tab}"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -k old_page_urls -q`
Expected: FAIL — the routes currently return 200, not 301.

- [ ] **Step 3: Replace the four GET route bodies in `gui/app.py`**

Replace the entire body of `run_page` (currently builds `stages` and renders `run.html`) with:

```python
    @app.get("/meetings/{meeting_id}/run")
    def run_page(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=progress", status_code=301)
```

Replace the body of `review_page` with:

```python
    @app.get("/meetings/{meeting_id}/review")
    def review_page(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=review", status_code=301)
```

Replace the body of `edit_meeting_form` with:

```python
    @app.get("/meetings/{meeting_id}/edit")
    def edit_meeting_form(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=details", status_code=301)
```

Replace the body of `publish_confirm` with:

```python
    @app.get("/meetings/{meeting_id}/publish", response_class=HTMLResponse)
    def publish_confirm(meeting_id: str) -> RedirectResponse:
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=publish", status_code=301)
```

In `new_meeting_launch`, change the final redirect from:

```python
        return RedirectResponse(url=f"/meetings/{meeting_id}/run", status_code=303)
```
to:
```python
        return RedirectResponse(url=f"/meetings/{meeting_id}?tab=progress", status_code=303)
```

- [ ] **Step 4: Update the handful of tests that assert on retired page bodies/links**

The old-page content tests still pass because the TestClient follows the 301 into the shell (which renders the same panel). Only these assertions must change:

In `tests/test_gui_review.py`:
- Line ~170 (`test_review_page_has_media_player_and_clip_buttons`): change `assert 'review.js' in body` to `assert 'workspace.js' in body`.
- Lines ~826–831 (`test_review_page_links_to_run`): replace the body with a tab-strip assertion:
```python
def test_review_page_links_to_run(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council").text
    assert 'data-tab="progress"' in body and 'href="/meetings/2026-02-04-council?tab=progress"' in body
```
- Lines ~833–837 (`test_review_page_links_to_publish`): similarly:
```python
def test_review_page_links_to_publish(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council").text
    assert 'data-tab="publish"' in body
```
- Lines ~487–490 (`test_review_js_references_search_and_link`): rename the read target to workspace.js:
```python
def test_review_js_references_search_and_link(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/workspace.js").read_text()
    assert "/api/politicians/search" in js
    assert "/link" in js
```

In `tests/test_gui_launch.py`:
- Line ~37 (`test_post_new_...redirects`): change the expected Location to the workspace:
```python
    assert resp.headers["location"] == "/meetings/2026-02-10-regular?tab=progress"
```
- Line ~58 (`test_run_page_and_status_json`): the GET `/run` now follows the 301 into the shell (progress tab). Change the content assertion to:
```python
    assert "workspace.js" in page.text and "stepper" in page.text.lower()
```

Leave every other test in these files untouched — the redo/continue/edit/publish POST redirect-target assertions (`/.../run`, `/.../review`) remain correct because those endpoints are unchanged and the browser/test follows the 301 chain.

- [ ] **Step 5: Run the full GUI suite**

Run:
```bash
.venv/bin/python -m pytest tests/test_gui_workspace.py tests/test_gui_review.py tests/test_gui_launch.py tests/test_gui_publish.py -q
```
Expected: PASS (all). If a content assertion fails because a follow-redirect landed on the shell without the expected panel string, confirm the panel partial contains that string.

- [ ] **Step 6: Commit**

```bash
git add gui/app.py tests/test_gui_workspace.py tests/test_gui_review.py tests/test_gui_launch.py
git commit -m "feat(gui): 301 old per-meeting URLs to the workspace tabs"
```

---

## Task 10: `workspace.js` — no-reload tabs, live status, form interception

**Files:**
- Create: `gui/static/workspace.js`
- Test: `tests/test_gui_workspace.py`

Absorbs the behavior of `run.js` (status polling + stepper/log updates) and `review.js` (clip seek, politician link search, HLS attach), and adds: tab swapping via fetch + history, and form interception (POST then re-fetch the active panel). Forms marked `data-navigate` (kebab Clean-up/Delete) submit normally.

- [ ] **Step 1: Write the failing test** (string-contract test, mirroring the existing `review.js` reference test)

```python
# tests/test_gui_workspace.py  (append)
def test_workspace_js_wires_core_endpoints(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/workspace.js").read_text()
    # tab swap + panel fetch
    assert "/panel/" in js
    # live status poll
    assert "/status" in js
    # absorbed review.js behaviors
    assert "/api/politicians/search" in js
    assert "data-hls" in js
    # form interception opt-out
    assert "data-navigate" in js
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -k workspace_js -q`
Expected: FAIL — file doesn't exist.

- [ ] **Step 3: Create `gui/static/workspace.js`**

```javascript
// One-pane meeting workspace: tab swapping (no reload), live status polling, and
// form interception (POST then re-fetch the active panel). Absorbs run.js (status
// stepper/log) and review.js (clip seek, link search, HLS attach).
(function () {
  const header = document.querySelector(".ws-header");
  if (!header) return;
  const id = header.getAttribute("data-meeting-id");
  const panel = document.getElementById("panel");
  let activeTab = header.getAttribute("data-active-tab");

  const enc = encodeURIComponent;

  // ---- Panel loading ------------------------------------------------------
  async function loadPanel(tab, push) {
    try {
      const resp = await fetch(`/meetings/${enc(id)}/panel/${enc(tab)}`);
      if (!resp.ok) return;
      panel.innerHTML = await resp.text();
    } catch (_) { return; }
    activeTab = tab;
    header.setAttribute("data-active-tab", tab);
    header.querySelectorAll(".tabstrip .tab").forEach((a) =>
      a.classList.toggle("active", a.getAttribute("data-tab") === tab));
    if (push) history.pushState({ tab }, "", `/meetings/${enc(id)}?tab=${enc(tab)}`);
    initPanel();
    refreshStatus();
  }

  header.addEventListener("click", (e) => {
    const a = e.target.closest(".tabstrip .tab");
    if (!a) return;
    e.preventDefault();
    loadPanel(a.getAttribute("data-tab"), true);
  });

  window.addEventListener("popstate", (e) => {
    const tab = (e.state && e.state.tab) || new URLSearchParams(location.search).get("tab") || activeTab;
    loadPanel(tab, false);
  });

  // ---- Form interception --------------------------------------------------
  // In-panel forms POST via fetch, then the active panel is re-fetched. Forms
  // marked data-navigate (kebab Clean up / Delete) submit normally (full nav).
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute("data-navigate")) return;         // let it navigate
    if (!panel.contains(form)) return;                       // only in-panel forms
    e.preventDefault();
    try {
      await fetch(form.action, { method: "POST", body: new FormData(form), redirect: "manual" });
    } catch (_) { /* best-effort; re-fetch shows current state */ }
    await loadPanel(activeTab, false);
  });

  // ---- Live status --------------------------------------------------------
  async function refreshStatus() {
    let st;
    try {
      const resp = await fetch(`/meetings/${enc(id)}/status`);
      if (!resp.ok) return;
      st = await resp.json();
    } catch (_) { return; }

    // Header pills.
    const gate = document.getElementById("gate-pill");
    if (gate && st.review_status) {
      gate.textContent = st.review_status === "pass" ? "passed"
        : st.review_status === "review" ? "needs review"
        : st.review_status === "failed" ? "failed" : "—";
    }
    const live = document.getElementById("live-pill");
    if (live && st.is_live != null) {
      live.textContent = st.is_live ? "Live" : "Not live";
      live.className = "live-badge live-" + (st.is_live ? "live" : "notlive");
    }
    const dot = document.getElementById("attn-dot");
    if (dot) dot.style.display = st.attention_count ? "" : "none";

    // Progress panel (if shown): update stepper + log in place, poll while running.
    const stepper = document.getElementById("stepper");
    if (stepper) {
      const logEl = document.getElementById("log");
      if (logEl && st.log_tail) { logEl.textContent = st.log_tail; logEl.scrollTop = logEl.scrollHeight; }
      stepper.querySelectorAll("li").forEach((li) => {
        const s = parseInt(li.getAttribute("data-stage"), 10);
        li.classList.toggle("done", s <= st.completed_stage);
        li.classList.toggle("current", s === st.completed_stage + 1 && st.running);
      });
      const err = document.getElementById("error-banner");
      if (err && st.exit_code != null && st.exit_code !== 0) {
        err.hidden = false;
        err.textContent = `Process exited with code ${st.exit_code}. See log below.`;
      }
      if (st.running) setTimeout(refreshStatus, 1500);
    }
  }

  // ---- Per-panel init (clip seek, link search, HLS attach) ----------------
  function initPanel() {
    attachHls();
  }

  // Clip seek: click a .clip button to seek the media (YouTube iframe or <video>/<audio>).
  document.addEventListener("click", (e) => {
    const btn = e.target.closest(".clip");
    if (!btn) return;
    const seek = parseFloat(btn.getAttribute("data-seek"));
    if (Number.isNaN(seek)) return;
    const yt = document.getElementById("yt-player");
    if (yt) { yt.src = yt.src.split("?")[0] + "?start=" + Math.floor(seek) + "&autoplay=1"; return; }
    const player = document.getElementById("player");
    if (!player) return;
    player.currentTime = seek;
    player.play();
  });

  // Politician link search: debounced query; each result is a native POST form
  // (intercepted by the submit handler above, so linking refreshes the panel).
  const DEBOUNCE = 250;
  document.addEventListener("input", (e) => {
    const input = e.target;
    if (!input.matches(".link-search input")) return;
    const widget = input.closest(".link-search");
    const results = widget.querySelector(".link-results");
    const q = input.value.trim();
    clearTimeout(widget._t);
    if (q.length < 2) { results.innerHTML = ""; return; }
    widget._t = setTimeout(async () => {
      const url = (widget.getAttribute("data-search-url") || "/api/politicians/search") + "?q=" + enc(q);
      let data;
      try { data = await (await fetch(url)).json(); }
      catch (_) { results.innerHTML = '<div class="link-msg">search unavailable</div>'; return; }
      const list = data.results || [];
      if (data.error || !list.length) {
        results.innerHTML = '<div class="link-msg">' + (data.error ? "search unavailable" : "no matches") + "</div>";
        return;
      }
      let action = widget.getAttribute("data-link-action") || "";
      if (!action.endsWith("/link")) action += "/link";
      const esc = (s) => String(s == null ? "" : s).replace(/"/g, "&quot;").replace(/</g, "&lt;");
      results.innerHTML = list.map((r) => (
        '<form method="post" action="' + action + '">' +
        '<input type="hidden" name="politician_slug" value="' + esc(r.politician_slug) + '">' +
        '<input type="hidden" name="politician_id" value="' + esc(r.politician_id) + '">' +
        '<button type="submit" class="link-result">' +
        esc([r.full_name, r.office_title, r.government_name].filter(Boolean).join(" · ")) +
        "</button></form>"
      )).join("");
    }, DEBOUNCE);
  });

  // HLS attach: <video id="player" data-hls="..."> with no src. Prefer hls.js
  // (Chrome/Firefox/Edge/desktop Safari can't play HLS natively despite a truthy
  // canPlayType), fall back to native only for iOS/older Safari.
  function attachHls() {
    const video = document.getElementById("player");
    if (!video) return;
    const src = video.getAttribute("data-hls");
    if (!src || video._hlsAttached) return;
    video._hlsAttached = true;
    const useNative = () => { if (video.canPlayType("application/vnd.apple.mpegurl")) video.src = src; };
    const script = document.createElement("script");
    script.src = "/static/hls.min.js";
    script.onload = () => {
      if (window.Hls && window.Hls.isSupported()) {
        const hls = new window.Hls(); hls.loadSource(src); hls.attachMedia(video);
      } else { useNative(); }
    };
    script.onerror = useNative;
    document.head.appendChild(script);
  }

  // Initial paint: the shell server-rendered the active panel, so just wire it up.
  initPanel();
  refreshStatus();
})();
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_workspace.py -q`
Expected: PASS (all).

- [ ] **Step 5: Manual smoke check** (the pieces JS can't unit-test)

Start the GUI and click through a processed meeting:
```bash
.venv/bin/python -m gui
```
Open `http://127.0.0.1:8000`, open a meeting, and verify: tabs switch without a full reload (URL shows `?tab=…`); browser back/forward moves between tabs; renaming a speaker refreshes the Review panel in place; the gate pill updates; on a still-processing meeting the Progress log/stepper advance live.

- [ ] **Step 6: Commit**

```bash
git add gui/static/workspace.js tests/test_gui_workspace.py
git commit -m "feat(gui): workspace.js — no-reload tabs, live status, form interception"
```

---

## Task 11: Retire the obsolete templates and scripts

**Files:**
- Delete: `gui/templates/run.html`, `gui/templates/review.html`, `gui/templates/edit_meeting.html`, `gui/templates/publish_confirm.html`, `gui/templates/publish_result.html`, `gui/static/run.js`, `gui/static/review.js`
- Modify: `gui/app.py` (drop the now-unused `stage_label_for` import path if unused; ensure no route still renders the deleted templates), `gui/templates/dedup_confirm.html` (point its "open existing meeting" link at the workspace)
- Modify: `gui/app.py` `publish_apply` still renders `publish_result.html` — replace with an inline HTML fragment response so the retired template can be deleted.

- [ ] **Step 1: Point the dedup link at the workspace**

In `gui/templates/dedup_confirm.html`, change:
```html
      <a class="enroll" href="/meetings/{{ existing_id }}/review" style="text-decoration:none;">→ Open existing meeting</a>
```
to:
```html
      <a class="enroll" href="/meetings/{{ existing_id }}" style="text-decoration:none;">→ Open existing meeting</a>
```

- [ ] **Step 2: Make `publish_apply` return a fragment instead of `publish_result.html`**

In `gui/app.py`, replace the `publish_apply` route body:
```python
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
with a small inline result fragment (matches what `workspace.js` expects to swap into `#publish-result` — it re-fetches the panel, so returning the publish panel HTML keeps behavior consistent; simplest is to return the panel):
```python
    @app.post("/meetings/{meeting_id}/publish", response_class=HTMLResponse)
    def publish_apply(request: Request, meeting_id: str, force: str = Form("")):
        result = publish_api.apply_publish(meeting_id, force=bool(force.strip()))
        if result.get("reason") == "unknown":
            raise HTTPException(status_code=404)
        if result.get("ok"):
            msg = (f"✓ Published · {result.get('segments', 0)} segments · "
                   f"{result.get('speakers', 0)} speakers")
            body = f'<div class="publish-ok">{msg}</div>'
        else:
            body = (f'<div class="error-banner">Publish failed '
                    f'({result.get("reason")}): {result.get("error", "")}</div>')
        return HTMLResponse(body)
```
Note: `workspace.js` re-fetches the whole publish panel after the POST, so this returned body is only shown for the no-JS fallback and for the direct test below. The panel's `#publish-result` slot is reserved for a future in-place result render (Plan 2+); returning a status line here keeps the endpoint self-describing.

- [ ] **Step 3: Update the publish result test**

In `tests/test_gui_publish.py`, the tests around lines 294–304 POST to `/publish` and assert on the result page. Update them to assert on the fragment. Read the two tests (`test_apply_publish_*` route tests) and change body assertions from the old result-page markup to the new fragment, e.g.:
```python
def test_publish_route_reports_success(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    mdir = _publish_meeting_ctx(tagged_meeting_dir, review_status="pass")
    monkeypatch.setattr(pub, "_db_url", lambda: "postgres://fake")
    import src.publish as sp
    from src.publish import PublishResult
    monkeypatch.setattr(sp, "publish_meeting",
                        lambda *a, **k: PublishResult(meeting_id="u", segments=142, speakers=7))
    monkeypatch.setattr(pub, "attach_thumbnail", lambda *a, **k: None)
    resp = TestClient(create_app()).post("/meetings/2026-02-04-council/publish", data={})
    assert resp.status_code == 200
    assert "Published" in resp.text and "142" in resp.text
```
(Adjust the monkeypatch targets to match the existing publish tests' setup in that file; keep whatever `PublishResult` construction they already use.)

- [ ] **Step 4: Delete the retired files**

```bash
git rm gui/templates/run.html gui/templates/review.html gui/templates/edit_meeting.html \
       gui/templates/publish_confirm.html gui/templates/publish_result.html \
       gui/static/run.js gui/static/review.js
```

- [ ] **Step 5: Grep for stale references**

Run:
```bash
grep -rn "run.js\|review.js\|publish_result\|publish_confirm\|edit_meeting.html\|\"run.html\"\|render.*review.html" gui/ tests/
```
Expected: no matches in `gui/` (tests may still reference `workspace.js`, which is fine). Fix any stray reference.

- [ ] **Step 6: Run the full GUI suite**

Run:
```bash
.venv/bin/python -m pytest tests/test_gui_workspace.py tests/test_gui_review.py tests/test_gui_launch.py tests/test_gui_publish.py tests/test_gui_runner.py tests/test_gui_library.py -q
```
Expected: PASS (all).

- [ ] **Step 7: Commit**

```bash
git add -A gui/ tests/test_gui_publish.py
git commit -m "refactor(gui): retire per-page templates and scripts, superseded by the workspace"
```

---

## Self-Review (completed during planning)

- **Spec coverage (Sections 1–2):** workspace shell (Task 7), tabs + default tab (Tasks 1, 7), header with live pills + kebab (Tasks 3, 7), panels (Tasks 4–6), fragment + status endpoints (Task 8), 301 redirects (Task 9), no-reload tabs + form interception + live status (Task 10), refactor into partials + retire old files (Tasks 4–6, 11). Inline publish result (Task 11). Not-ready placeholders (Tasks 2, 5, 6). Deep-linkable tabs (Tasks 7–10).
- **Out of scope here (later plans):** rich meeting-ID derivation, kind-aware new-meeting form, guest/race fields, modal default (Plan 2); richer/searchable library, per-row context, click-through-to-tab (Plan 3). The library currently links to `/meetings/{id}/review`, which now 301s to the workspace — so it keeps working until Plan 3 updates the link.
- **Type consistency:** `panel_context`/`header_context`/`default_tab_for_stage` signatures match their call sites in `gui/app.py` (Task 8). Panel templates read exactly the keys their `panel_context` branch sets (`page`, `not_ready`, `m`, `event_kinds`, `stages`, `redo_stages`, `review_status`, `gate_pass`, `already_published`). The shell merges `panel_context` + `{"header", "active_tab"}`.
- **Placeholder scan:** none — every code and test step contains complete content.
```
