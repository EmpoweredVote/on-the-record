# Processing GUI — Slice 3b-2: Event-Label Defaults Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Fix the confusing "Meeting type" field. It's required for every event kind (pipeline + publish need it) and it's really *the short label shown on the site + the URL slug* — not the category (that's Event kind). So: **relabel** it "Event label (shown on the site)" and **auto-fill a sensible per-kind default** (forum → "Candidate Forum", debate → "Debate", council → "Regular Session", …), editable, without ever clobbering a value the user typed. GUI-only; the backend field stays `meeting_type`. Also add a one-line glossary clarification.

**Goal:** Picking an event kind pre-fills a natural label, so the user rarely types it and never wonders "why do I need a meeting type for a forum?"

**Architecture:** `gui/formmeta.py` gains `MEETING_TYPE_DEFAULTS` (keys == EVENT_KINDS). The template relabels the field and injects the defaults as a JS global. `new_meeting.js` applies the default on event-kind change *only when the field is empty or still holds a known default* (so custom text survives). No server change — `meeting_type` still submits as before, still required.

**Tech Stack:** Jinja2 + vanilla JS. Tests: `pytest` (formmeta coverage + form renders new label; JS wiring by string check).

---

### Task 1: `MEETING_TYPE_DEFAULTS` in `gui/formmeta.py`

**Files:**
- Modify: `gui/formmeta.py`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_launch.py`:

```python
def test_meeting_type_defaults_cover_all_kinds():
    from gui.formmeta import MEETING_TYPE_DEFAULTS
    from src.event_kinds import EVENT_KINDS
    assert set(MEETING_TYPE_DEFAULTS) == set(EVENT_KINDS)
    # deliberative + electoral kinds get a non-empty suggestion
    assert MEETING_TYPE_DEFAULTS["forum"] == "Candidate Forum"
    assert MEETING_TYPE_DEFAULTS["council"] == "Regular Session"
    assert MEETING_TYPE_DEFAULTS["debate"] == "Debate"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "meeting_type_defaults" -v`
Expected: FAIL — `MEETING_TYPE_DEFAULTS` missing.

- [ ] **Step 3: Add to `gui/formmeta.py`**

```python
# Sensible default event labels per kind. The field is required (pipeline +
# publish), but it's really "a short label shown on the site", so we pre-fill a
# natural default the user can edit. Keys must equal EVENT_KINDS (test-enforced).
MEETING_TYPE_DEFAULTS = {
    "council": "Regular Session",
    "school_board": "Board Meeting",
    "debate": "Debate",
    "forum": "Candidate Forum",
    "community_meeting": "Community Meeting",
    "news_clip": "Interview",
    "press_conference": "Press Conference",
    "other": "",
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "meeting_type_defaults" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/formmeta.py tests/test_gui_launch.py
git commit -m "feat(gui): per-event-kind default event labels

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Relabel field + inject defaults (template + app)

**Files:**
- Modify: `gui/app.py` (pass `MEETING_TYPE_DEFAULTS` to the template)
- Modify: `gui/templates/new_meeting.html`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_launch.py`:

```python
def test_new_form_relabels_event_label_field(tmp_meetings_dir):
    body = TestClient(create_app()).get("/new").text
    assert "Event label" in body                 # relabeled (was "Meeting type")
    assert 'name="meeting_type"' in body          # backend field name unchanged
    assert "Candidate Forum" in body              # a default injected for JS/examples
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "relabels_event_label" -v`
Expected: FAIL — label/default not present.

- [ ] **Step 3: Pass defaults in `gui/app.py` `new_meeting_form`**

Add `MEETING_TYPE_DEFAULTS` to the import from `gui.formmeta` and to the template context:

```python
        from gui.formmeta import (EVENT_KIND_HELP, COMPUTE_HELP, DIARIZER_HELP,
                                   CITY_REQUIRED_KINDS, MEETING_TYPE_DEFAULTS)
        return _templates.TemplateResponse(
            request, "new_meeting.html",
            {
                "event_kinds": list(EVENT_KINDS),
                "event_kind_help": EVENT_KIND_HELP,
                "compute_help": COMPUTE_HELP,
                "diarizer_help": DIARIZER_HELP,
                "city_required_kinds": sorted(CITY_REQUIRED_KINDS),
                "meeting_type_defaults": MEETING_TYPE_DEFAULTS,
            },
        )
```

- [ ] **Step 4: Relabel the field + inject defaults in `gui/templates/new_meeting.html`**

Change the meeting-type label line from:

```html
      <label>Meeting type (shown on the site, e.g. "Regular Session")
        <input type="text" name="meeting_type" id="f-mtype" placeholder="Regular Session" required>
      </label>
```

to:

```html
      <label>Event label — shown on the site (e.g. Regular Session, Candidate Forum, Debate)
        <input type="text" name="meeting_type" id="f-mtype" placeholder="Regular Session" required>
        <small class="help">A short label for this specific event. The <em>category</em> is "Event kind" above.</small>
      </label>
```

Add the defaults to the JS globals block (next to the existing `window.__*_HELP` lines):

```html
    window.__MEETING_TYPE_DEFAULTS = {{ meeting_type_defaults|tojson }};
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "relabels_event_label" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/app.py gui/templates/new_meeting.html tests/test_gui_launch.py
git commit -m "feat(gui): relabel 'Meeting type' -> 'Event label' + inject defaults

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Auto-fill default on kind change (new_meeting.js)

**Files:**
- Modify: `gui/static/new_meeting.js`
- Test: `tests/test_gui_launch.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_launch.py`:

```python
def test_new_meeting_js_applies_label_default():
    from pathlib import Path
    js = Path("gui/static/new_meeting.js").read_text()
    assert "__MEETING_TYPE_DEFAULTS" in js
    # only overwrite when empty or still a known default (don't clobber custom text)
    assert "f-mtype" in js
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "applies_label_default" -v`
Expected: FAIL — JS doesn't reference the defaults.

- [ ] **Step 3: Add the auto-fill to `gui/static/new_meeting.js`**

At the top of the IIFE (after `const $ = ...` and the `input` map), add a set of all known defaults and an `applyKindDefault` helper; call it on kind change *before* `refresh()`:

```javascript
  const DEFAULTS = window.__MEETING_TYPE_DEFAULTS || {};
  const DEFAULT_VALUES = new Set(Object.values(DEFAULTS).filter(Boolean));

  function applyKindDefault() {
    const cur = input.mtype.value.trim();
    // Only auto-fill when the field is empty or still holds an auto-applied
    // default — never clobber a label the user typed.
    if (cur === "" || DEFAULT_VALUES.has(cur)) {
      const def = DEFAULTS[input.kind.value] || "";
      input.mtype.value = def;
    }
  }

  input.kind.addEventListener("change", () => { applyKindDefault(); refresh(); });
```

And on initial load, apply the default for the starting kind if the field is empty — add before the final `refresh()`:

```javascript
  applyKindDefault();
```

(Keep the existing `main.querySelectorAll("input, select")` input/change listeners and the final `refresh()`.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_launch.py -k "applies_label_default" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/static/new_meeting.js tests/test_gui_launch.py
git commit -m "feat(gui): auto-fill event label from kind (keeps custom text)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Glossary clarification

**Files:**
- Modify: `CONTEXT.md`

- [ ] **Step 1: Add a short entry under "Event concepts"** (near the Event Kind entry), so the distinction is documented:

```markdown
**Meeting label** (`meeting_type` column)
A short human label for a specific event — e.g. "Regular Session", "Candidate Forum", "Debate". Required (it's shown on the site as `{city} {meeting_type} · {date}` and forms the meeting's URL slug). Distinct from [Event Kind](#event-kind): the *kind* is the controlled category (council/forum/…), the *label* is the free-text name of this particular event. The GUI pre-fills a sensible label per kind.
```

- [ ] **Step 2: Commit**

```bash
git add CONTEXT.md
git commit -m "docs: clarify meeting_type (event label) vs event_kind in glossary

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (no regressions).

- [ ] **Step 2: Manual smoke** (form only)

Run: `.venv/bin/python -m gui`, open `/new`. The field now reads **"Event label"**. Pick **Event kind = forum** → the label field auto-fills **"Candidate Forum"**; switch to **debate** → it becomes **"Debate"**; type a custom value like "LWV Governor Forum" then switch kinds → your custom text is **kept** (not clobbered). Ctrl-C to stop.

---

## Self-Review

**Spec coverage:** relabel field to "Event label" (Task 2) ✅ · per-kind defaults, editable (Task 1 + Task 3) ✅ · never clobber custom text (Task 3 `DEFAULT_VALUES` guard) ✅ · backend `meeting_type` unchanged/still required (no server change) ✅ · glossary clarification (Task 4) ✅ · GUI-only ✅.

**Placeholder scan:** none.

**Type consistency:** `MEETING_TYPE_DEFAULTS` keys == EVENT_KINDS (test-enforced), injected via `|tojson` as `window.__MEETING_TYPE_DEFAULTS`, read by `new_meeting.js`. Field `name="meeting_type"` unchanged → POST handler + `RunParams` untouched. `applyKindDefault` guards on the `DEFAULT_VALUES` set so user-typed labels survive kind switches. Preview/derived-id (from 3b) still update via the existing `refresh()`.
