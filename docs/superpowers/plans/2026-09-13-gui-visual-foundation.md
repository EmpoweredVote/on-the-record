# GUI Visual Foundation (Slice 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the local processing GUI a design-token foundation, a shared base layout, componentized badges, a refreshed palette/type/spacing, and a de-densified review card — same pages, no rewrite.

**Architecture:** Introduce a `:root` token set and a `base.html` all pages extend, replacing the per-page `<head>` duplication and the repeated raw hex literals in `style.css`. Componentize the badge/pill markup into a Jinja macro. Then apply the actual palette/type/spacing via the frontend-design skill and restructure the speaker `card()` macro for clearer hierarchy. This is Slice 1 of the local-GUI QoL refresh; Slices 2 (review flow), 3 (library filtering) and 4 (floor import) build on the tokens, base template, and component classes defined here.

**Tech Stack:** FastAPI + Jinja2 server-render + vanilla JS. No build step. No new runtime dependencies.

## Global Constraints

- Stack is fixed: FastAPI + Jinja2 + vanilla JS, **no build step, no new runtime dependency**.
- App factory: `gui.app.create_app()` (returns a `FastAPI`). Templates via `Jinja2Templates(directory=gui/templates)` (`gui/app.py:31`).
- Tests run with `.venv/bin/python -m pytest tests/test_gui_*.py`. Use `TestClient(create_app())` and the existing fixtures `tmp_meetings_dir` and `tagged_meeting_dir` (`tests/conftest.py`). Static-file assertions read files directly, e.g. `Path("gui/static/style.css").read_text()`.
- **Subagents MUST NOT start a server or touch port 8000** — a live pipeline run may be using it. Verify only via pytest/TestClient. Live in-browser verification (visual quality, exact colors) is done by the operator, not a subagent.
- **Do not fabricate a palette.** The actual color/type/spacing values come from the `frontend-design` skill (Task 4). Earlier tasks introduce token *names* with placeholder-but-visually-neutral values that reproduce today's look.
- **Visual-neutral refactors must keep every existing test green.** All `tests/test_gui_*.py` must still pass after Tasks 1–3 (they assert on rendered classes like `gate-pass`, `live-badge`, `data-kind`, and on page content).
- No change to meeting ids, slugs, routes, or behavior. ADR-0002 (frozen slug) and ADR-0003 (kind-aware ids) are unaffected.

---

### Task 1: Design tokens in `style.css`

Introduce a `:root` token set and convert the repeated semantic color literals to `var()` references. Visually neutral — the token values equal today's colors.

**Files:**
- Modify: `gui/static/style.css` (add `:root` tokens at top; convert the `.gate-*`, `.live-*`, `.error-banner`, `.accept`, `.enroll`, `.unlink`, `.identpill*`, `.profile-strength*` color rules to `var()`).
- Test: `tests/test_gui_visual_foundation.py` (new).

**Interfaces:**
- Produces: a documented token vocabulary later tasks/slices reuse. Token names (exact):
  - Colors — semantic: `--c-pass-fg`, `--c-pass-bg`, `--c-pass-border`; `--c-review-fg`, `--c-review-bg`, `--c-review-border`; `--c-fail-fg`, `--c-fail-bg`, `--c-fail-border`; `--c-info-fg`, `--c-info-bg`, `--c-info-border`; `--c-muted-fg`.
  - Colors — surface/ink: `--c-ink`, `--c-ink-soft`, `--c-bg`, `--c-surface`, `--c-line`, `--c-link`.
  - Space scale: `--sp-1`(0.25rem), `--sp-2`(0.4rem), `--sp-3`(0.6rem), `--sp-4`(0.75rem), `--sp-5`(1rem), `--sp-6`(1.5rem).
  - Radius: `--r-sm`(0.4rem), `--r-md`(0.5rem).
  - Type: `--fs-xs`(0.78rem), `--fs-sm`(0.85rem), `--fs-md`(0.95rem), `--fs-lg`(1.25rem), `--font-ui`, `--font-mono`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_visual_foundation.py
from pathlib import Path


def test_style_defines_design_tokens():
    css = Path("gui/static/style.css").read_text()
    assert ":root" in css
    for token in (
        "--c-pass-bg", "--c-review-bg", "--c-fail-bg", "--c-info-bg",
        "--c-ink", "--c-surface", "--c-line", "--c-link",
        "--sp-3", "--r-md", "--fs-sm", "--font-mono",
    ):
        assert token in css, f"missing token {token}"


def test_semantic_color_rules_reference_tokens():
    css = Path("gui/static/style.css").read_text()
    # The repeated gate/live palette must now come from tokens, not raw hex.
    assert ".gate-pass" in css
    gate_pass = css.split(".gate-pass")[1].split("}")[0]
    assert "var(--c-pass-bg)" in gate_pass and "var(--c-pass-fg)" in gate_pass
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_visual_foundation.py -v`
Expected: FAIL (no `:root` token block yet; `.gate-pass` still uses raw hex).

- [ ] **Step 3: Add the token block and convert semantic color rules**

At the very top of `gui/static/style.css`, extend the existing `:root` line into a full token block (values chosen to equal today's colors so nothing shifts visually):

```css
:root {
  color-scheme: light;
  --font-ui: -apple-system, system-ui, sans-serif;
  --font-mono: ui-monospace, monospace;
  --c-ink: #1a1a1a; --c-ink-soft: #666; --c-muted-fg: #999;
  --c-bg: #fff; --c-surface: #fafafc; --c-line: #e2e2e2; --c-link: #2a5db0;
  --c-pass-fg: #1b7a3d; --c-pass-bg: #e6f5ea; --c-pass-border: #2ea56a;
  --c-review-fg: #9a6a00; --c-review-bg: #fdf3e0; --c-review-border: #e6d59a;
  --c-fail-fg: #b32020; --c-fail-bg: #fdeaea; --c-fail-border: #e0a0a0;
  --c-info-fg: #24507f; --c-info-bg: #eef4fc; --c-info-border: #7aa0d0;
  --sp-1: 0.25rem; --sp-2: 0.4rem; --sp-3: 0.6rem; --sp-4: 0.75rem; --sp-5: 1rem; --sp-6: 1.5rem;
  --r-sm: 0.4rem; --r-md: 0.5rem;
  --fs-xs: 0.78rem; --fs-sm: 0.85rem; --fs-md: 0.95rem; --fs-lg: 1.25rem;
  font-family: var(--font-ui);
}
```

Then convert the semantic color rules to `var()` — the gate/live/error/accept/enroll/unlink/identpill/profile-strength color pairs. Example conversions:

```css
.gate-pass { background: var(--c-pass-bg); color: var(--c-pass-fg); }
.gate-review { background: var(--c-review-bg); color: var(--c-review-fg); }
.gate-failed { background: var(--c-fail-bg); color: var(--c-fail-fg); }
.live-live { background: var(--c-pass-bg); color: var(--c-pass-fg); }
.error-banner { background: var(--c-fail-bg); color: var(--c-fail-fg); border: 1px solid var(--c-fail-border); }
```

Keep every other rule as-is for now (Task 4 does the full sweep). Do not change any declared color value — only move it behind a token of the same value.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_visual_foundation.py -v`
Expected: PASS.
Run: `.venv/bin/python -m pytest tests/test_gui_library.py tests/test_gui_workspace.py tests/test_gui_review.py -q`
Expected: PASS (visual-neutral; existing class-based assertions unaffected).

- [ ] **Step 5: Commit**

```bash
git add gui/static/style.css tests/test_gui_visual_foundation.py
git commit -m "refactor(gui): introduce design tokens in style.css (visual-neutral)"
```

---

### Task 2: Base layout template

Add `base.html` and convert every page to extend it, eliminating the duplicated `<head>`.

**Files:**
- Create: `gui/templates/base.html`.
- Modify: `gui/templates/library.html`, `gui/templates/workspace.html`, `gui/templates/new_meeting.html`, `gui/templates/dedup_confirm.html`, `gui/templates/discovery.html`.
- Test: `tests/test_gui_visual_foundation.py` (extend).

**Interfaces:**
- Produces: `base.html` with Jinja blocks — `{% block title %}`, `{% block body_class %}`, `{% block content %}`, `{% block scripts %}`. Pages set title + content; workspace/library/new_meeting add their script tag in `{% block scripts %}`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_visual_foundation.py
from fastapi.testclient import TestClient
from gui.app import create_app


def test_pages_have_a_single_stylesheet_link(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    for path in ("/", "/meetings/2026-02-04-council", "/new", "/discovery"):
        body = client.get(path).text
        assert body.count('rel="stylesheet"') == 1, f"{path} should link the stylesheet once"
        assert body.count("<!doctype html>") == 1


def test_base_template_exists_and_is_extended():
    from pathlib import Path
    assert Path("gui/templates/base.html").exists()
    for page in ("library.html", "workspace.html", "new_meeting.html",
                 "dedup_confirm.html", "discovery.html"):
        text = Path(f"gui/templates/{page}").read_text()
        assert 'extends "base.html"' in text, f"{page} must extend base.html"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_visual_foundation.py -v`
Expected: FAIL (no base.html; pages still hand-roll their head).

- [ ] **Step 3: Create `base.html`**

```html
<!-- gui/templates/base.html -->
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{% block title %}CouncilScribe{% endblock %}</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body class="{% block body_class %}{% endblock %}">
  {% block content %}{% endblock %}
  {% block scripts %}{% endblock %}
</body>
</html>
```

- [ ] **Step 4: Convert each page to extend it**

For `library.html`: replace lines 1–9 (`<!doctype>`…`<body>`) and the trailing `</body></html>`, wrapping the existing `<header>`+`<main>` in `{% block content %}` and the `<script src="/static/library.js">` in `{% block scripts %}`:

```html
{% extends "base.html" %}
{% block title %}Meeting Library — CouncilScribe{% endblock %}
{% block content %}
  <header><h1>Meeting Library</h1></header>
  <main>
    {# ...existing library body unchanged... #}
  </main>
{% endblock %}
{% block scripts %}<script src="/static/library.js"></script>{% endblock %}
```

Do the same for the other four pages, preserving each page's exact body markup:
- `workspace.html`: title `{{ header.display_name }} — CouncilScribe`; content = the `<header class="ws-header">` + `<main class="review ws-main" id="panel">`; scripts = `<script src="/static/workspace.js"></script>`.
- `new_meeting.html`: keep its title; content = its body; scripts = `<script src="/static/new_meeting.js"></script>`.
- `dedup_confirm.html` and `discovery.html`: title + content; `discovery.html` moves any script into `{% block scripts %}`.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_visual_foundation.py tests/test_gui_library.py tests/test_gui_workspace.py tests/test_gui_discovery.py -q`
Expected: PASS (page content unchanged; existing content assertions still hold).

- [ ] **Step 6: Commit**

```bash
git add gui/templates/base.html gui/templates/library.html gui/templates/workspace.html gui/templates/new_meeting.html gui/templates/dedup_confirm.html gui/templates/discovery.html tests/test_gui_visual_foundation.py
git commit -m "refactor(gui): shared base.html layout, drop per-page head duplication"
```

---

### Task 3: Badge component macro

Componentize the repeated badge/pill markup so gate/live/stage/pill render through one macro.

**Files:**
- Create: `gui/templates/_ui.html` (macros).
- Modify: `gui/templates/library.html`, `gui/templates/workspace.html` (use the macro for gate + live badges).
- Test: `tests/test_gui_visual_foundation.py` (extend).

**Interfaces:**
- Consumes: token classes from Task 1 (`.gate-*`, `.live-*`).
- Produces: macro `badge(css_class, text)` in `_ui.html`, imported as `{% from "_ui.html" import badge %}`. Renders `<span class="badge {{ css_class }}">{{ text }}</span>`. The existing `.gate`/`.live-badge` class names are preserved as `css_class` values so current CSS and tests keep working.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_visual_foundation.py
def test_ui_macro_file_defines_badge():
    from pathlib import Path
    text = Path("gui/templates/_ui.html").read_text()
    assert "macro badge" in text


def test_library_and_workspace_import_the_badge_macro():
    from pathlib import Path
    lib = Path("gui/templates/library.html").read_text()
    ws = Path("gui/templates/workspace.html").read_text()
    assert 'import badge' in lib and 'import badge' in ws
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_visual_foundation.py -k badge -v`
Expected: FAIL (`_ui.html` does not exist).

- [ ] **Step 3: Create the macro and use it**

```html
<!-- gui/templates/_ui.html -->
{% macro badge(css_class, text) -%}
<span class="badge {{ css_class }}">{{ text }}</span>
{%- endmacro %}
```

In `library.html` (top of `{% block content %}`) and `workspace.html` add `{% from "_ui.html" import badge %}`. Replace the gate badge span:

```html
{% set level, text = m.gate_badge %}
{{ badge("gate gate-" ~ level, text) }}
```

and the live badge similarly (`badge("live-badge live-" ~ live[0], live[1])`). Keep the `.gate`/`.live-badge` classes so existing CSS and tests (`gate-pass`, `Live`/`Not live`) still match. Add a base `.badge { display:inline-block; }` rule to `style.css` if needed (no color — colors stay on the semantic classes).

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_visual_foundation.py tests/test_gui_library.py tests/test_gui_workspace.py -q`
Expected: PASS (rendered classes unchanged; the badge markup is equivalent).

- [ ] **Step 5: Commit**

```bash
git add gui/templates/_ui.html gui/templates/library.html gui/templates/workspace.html gui/static/style.css tests/test_gui_visual_foundation.py
git commit -m "refactor(gui): badge macro for gate/live pills"
```

---

### Task 4: Palette / typography / spacing refresh

Choose and apply the actual visual values via the frontend-design skill. This is the aesthetic pass; values are not pre-baked here.

**Files:**
- Modify: `gui/static/style.css` (token values + the remaining literal color/spacing usages converted to tokens).
- Test: `tests/test_gui_visual_foundation.py` (structural only).

**Interfaces:**
- Consumes: the token vocabulary from Task 1.
- Produces: final token values + a fully token-driven stylesheet.

- [ ] **Step 1: Load the frontend-design skill**

Invoke `frontend-design` and follow it to design a cohesive, legible light-theme palette, type scale, and spacing rhythm for a dense internal review tool. Update the Task-1 token values accordingly. Convert any remaining raw-hex color and ad-hoc spacing usages in `style.css` to the tokens.

- [ ] **Step 2: Write the structural test**

```python
# add to tests/test_gui_visual_foundation.py
def test_style_has_no_stray_semantic_hex_after_refresh():
    from pathlib import Path
    css = Path("gui/static/style.css").read_text()
    # After the refresh, the old repeated gate/live hexes must live only in :root.
    root = css.split("}")[0]  # the :root block
    body = css[len(root):]
    for hexval in ("#e6f5ea", "#fdf3e0", "#fdeaea", "#eef4fc"):
        assert hexval not in body, f"{hexval} should be tokenized, not repeated in rules"
```

- [ ] **Step 3: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_gui_visual_foundation.py -v`
Expected: PASS. Then the full GUI suite: `.venv/bin/python -m pytest tests/test_gui_*.py -q` — Expected: PASS.

- [ ] **Step 4: Live verification (operator, not subagent)**

The operator runs `python -m gui` and confirms in the browser: library, workspace (all four tabs), new-meeting, and discovery pages render with the refreshed look, are legible, and nothing is broken. (A subagent must not start the server.)

- [ ] **Step 5: Commit**

```bash
git add gui/static/style.css tests/test_gui_visual_foundation.py
git commit -m "style(gui): refreshed palette, typography, and spacing via tokens"
```

---

### Task 5: De-densify the review card hierarchy

Restructure the speaker `card()` macro for clearer visual hierarchy without changing any action semantics.

**Files:**
- Modify: `gui/templates/panels/_macros.html` (the `card()` macro layout/grouping), `gui/static/style.css` (card spacing/hierarchy via tokens).
- Optionally modify: `gui/templates/panels/review.html` (wrap the "Confirmed" section in a `<details>` collapsed by default).
- Test: `tests/test_gui_review.py` (extend) or `tests/test_gui_visual_foundation.py`.

**Interfaces:**
- Consumes: tokens (Task 1/4). Preserves every form action, field name, and CSS hook the review JS and existing tests rely on (`.card`, `.card-head`, `.ident-*`, `.actions`, `.accept`, `.link-search`, `.enroll`, merge select, mark buttons).

- [ ] **Step 1: Write the test**

```python
# add to tests/test_gui_review.py (or the visual-foundation file)
def test_confirmed_section_is_collapsible(review_meeting_dir, tmp_meetings_dir):
    # review_meeting_dir: existing fixture that yields a reviewable meeting id.
    from fastapi.testclient import TestClient
    from gui.app import create_app
    mid = review_meeting_dir()
    body = TestClient(create_app()).get(f"/meetings/{mid}/panel/review").text
    # The confirmed group collapses by default to reduce the wall-of-text.
    assert "<details" in body and "Confirmed" in body
```

If `tests/test_gui_review.py` has no `review_meeting_dir` fixture, reuse whatever fixture its existing review-panel tests use to produce a stage≥4 meeting; match that file's setup exactly rather than inventing one.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py -k collapsible -v`
Expected: FAIL (no `<details>` wrapper yet).

- [ ] **Step 3: Restructure the card + collapse Confirmed**

In `_macros.html`, regroup the `card()` body into three clearly separated regions — **identity head** (label, name, identity pill, confidence, minutes), **evidence** (voice hints, sample text, clip buttons), **actions** (the identity chooser + "Also") — using the spacing tokens and clearer headings. Do not remove or rename any form, input, or class the JS/tests depend on. In `review.html`, wrap the "Confirmed" section body in `<details class="confirmed-group">` (with a `<summary>` count) so it starts collapsed.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_gui_review.py tests/test_review_hls.py -q`
Expected: PASS (all existing review assertions plus the new collapsible one).

- [ ] **Step 5: Live verification (operator)**

Operator opens a reviewable meeting and confirms the card reads cleanly, the Confirmed group is collapsed by default, and every action still works.

- [ ] **Step 6: Commit**

```bash
git add gui/templates/panels/_macros.html gui/templates/panels/review.html gui/static/style.css tests/test_gui_review.py
git commit -m "style(gui): de-densify the speaker review card; collapse Confirmed by default"
```

---

## Self-Review

- **Spec coverage (Slice 1 = "Retheme + tidy hierarchy"):** design tokens (Task 1) ✓; base layout template killing per-page `<head>` (Task 2) ✓; componentized badges/buttons (Task 3) ✓; palette/type/spacing refresh (Task 4) ✓; de-densified review card + optional collapsed Confirmed (Task 5) ✓. Stack unchanged, no build step ✓.
- **Placeholder scan:** color/type/spacing values are deliberately deferred to the frontend-design skill in Task 4 (not a placeholder — the design decision belongs there); every other step has concrete code. No TODO/TBD left.
- **Type/name consistency:** token names, the `badge(css_class, text)` macro signature, and the `base.html` block names (`title`/`body_class`/`content`/`scripts`) are used consistently across tasks. Preserved CSS hooks (`.gate-*`, `.live-badge`, `.card`, `.ident-*`, `.actions`) keep existing tests and the review JS working.

## Notes for later slices

- Slice 2 (review flow) will add a single-card fragment route and swap one `card()` in place; it depends on the `card()` structure from Task 5 and the `.card`/`.ident-*` hooks.
- Slice 3 (library filtering) depends on the badge macro and tokens here, and will enrich the library `data-*` attributes.
