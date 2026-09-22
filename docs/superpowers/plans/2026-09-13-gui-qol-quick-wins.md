# GUI QoL Quick Wins (Slice 5) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Three small, feedback-driven refinements to the local GUI: a visible sort-direction indicator, auto-fill the local-person slug from the typed name, and add interview/anchor local-person roles.

**Architecture:** Small additive changes on top of Slices 1–4. Sort indicator = `aria-sort` + CSS caret in `library.js`/`style.css`. Auto-slug = a delegated input handler in `workspace.js` + a `data-autoslug` marker on the new-local-person slug field. Interview roles = a new role set in `src/event_kinds.py`.

**Tech Stack:** FastAPI + Jinja2 + vanilla JS. No build step, no new dependency. No DB migration.

## Global Constraints

- ALL work in the worktree `/Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/admiring-ritchie-dbf38c`; every bash command starts with `cd` into it. NEVER touch `/Users/chrisandrews/Documents/GitHub/on-the-record`. Before committing: branch `claude/zealous-roentgen-090702`, toplevel ends `/admiring-ritchie-dbf38c`.
- venv by ABSOLUTE path from the worktree cwd: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest ...`. No server / no port 8000. JS behavior verified live by the operator.
- Preserve every existing hook (Slices 1–4). These tasks only ADD. Every existing test stays green.
- New local-role words must match `LOCAL_ROLE_PATTERN = ^[a-z][a-z0-9_]{0,39}$` — no DB migration (the ev-accounts CHECK is a shape regex, not an allowlist).

---

### Task 1: Sort-direction indicator

Make the active sort column show its direction and read as clickable.

**Files:**
- Modify: `gui/static/library.js` (the sort handler — set `aria-sort`)
- Modify: `gui/static/style.css` (caret + clickable affordance, token-based)
- Test: `tests/test_gui_library.py`

**Interfaces:**
- Consumes: the existing `th[data-sort]` click-sort (Slice 3).
- Produces: on sort, the active `th` gets `aria-sort="ascending"|"descending"` and the others have it cleared; CSS renders a ▲/▼ via `th[aria-sort]::after` and a hover affordance on `th[data-sort]`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_library.py
def test_library_js_sets_aria_sort_indicator():
    from pathlib import Path
    js = Path("gui/static/library.js").read_text()
    assert "aria-sort" in js            # active column shows its direction
    css = Path("gui/static/style.css").read_text()
    assert 'aria-sort' in css           # a caret rule keys off it
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py -k aria_sort -v`
Expected: FAIL.

- [ ] **Step 3: Set `aria-sort` in the sort handler**

In `gui/static/library.js`, inside the `th[data-sort]` click handler (after computing `sortKey`/`sortDir` and reordering), clear `aria-sort` from all sortable headers and set it on the clicked one:

```javascript
      table.querySelectorAll("th[data-sort]").forEach((h) => h.removeAttribute("aria-sort"));
      th.setAttribute("aria-sort", sortDir === 1 ? "ascending" : "descending");
```

- [ ] **Step 4: Add the CSS (tokens only)**

In `gui/static/style.css`:

```css
table.library th[data-sort] { cursor: pointer; user-select: none; }
table.library th[data-sort]:hover { color: var(--c-link); }
table.library th[aria-sort="ascending"]::after { content: " ▲"; font-size: var(--fs-xs); color: var(--c-ink-soft); }
table.library th[aria-sort="descending"]::after { content: " ▼"; font-size: var(--fs-xs); color: var(--c-ink-soft); }
```

(If `--fs-xs` / `--c-ink-soft` / `--c-link` are absent, use the nearest existing token — they exist per Slice 1/4.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py tests/test_gui_visual_foundation.py -q`
Expected: PASS (incl. the Slice-1 stray-hex guard — the new CSS uses tokens).

- [ ] **Step 6: Commit**

```bash
git add gui/static/library.js gui/static/style.css tests/test_gui_library.py
git commit -m "feat(gui): sort-direction indicator on library columns"
```
(add the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer)

---

### Task 2: Auto-fill the local-person slug from the name

When creating a NEW local person, the slug follows the typed name until the operator edits the slug by hand.

**Files:**
- Modify: `gui/templates/panels/_macros.html` (mark the new-person slug input)
- Modify: `gui/static/workspace.js` (name→slug sync)
- Test: `tests/test_gui_review.py`

**Interfaces:**
- Consumes: the local-person form (`.local-person`) with `input[name="name"]` and `input[name="slug"]`.
- Produces: for a NEW local person (`identity_kind != 'local'`), the slug input carries `data-autoslug`; typing in Name sets Slug to a slugified name until the operator edits Slug (which marks it dirty and stops the sync). Editing an EXISTING local person (`identity_kind == 'local'`) is untouched — no `data-autoslug`, no sync (protects the real slug).

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_gui_review.py
def test_new_local_person_slug_marked_for_autofill(tagged_meeting_dir, tmp_meetings_dir):
    # A meeting with an unnamed/unlinked speaker (identity_kind == 'none') — reuse
    # the same setup other review tests in this file use for such a speaker.
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    from fastapi.testclient import TestClient
    from gui.app import create_app
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/panel/review").text
    # The new-local-person slug input opts into auto-fill; the JS keys off this.
    assert "data-autoslug" in body


def test_workspace_js_autoslugs_name():
    from pathlib import Path
    js = Path("gui/static/workspace.js").read_text()
    assert "data-autoslug" in js and 'name="slug"' in js
```

If the fixture yields no `identity_kind == 'none'` speaker, mirror an existing unconfirmed-speaker test in this file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k "autoslug or autofill" -v`
Expected: FAIL.

- [ ] **Step 3: Mark the new-person slug input**

In `gui/templates/panels/_macros.html`, the local-person Slug input (currently prefilled with `default_slug` for a non-local card). Add `data-autoslug` ONLY when the card is not an existing local person, so the sync applies to new persons only:

```html
        <label>Slug
          <input type="text" name="slug" value="{{ '' if is_marked else (c.local_slug if c.identity_kind == 'local' else c.default_slug) }}" required
                 {{ 'data-autoslug' if c.identity_kind != 'local' }}
                 pattern="[a-z0-9][a-z0-9_-]{0,99}"
                 title="lowercase letters, digits, hyphen or underscore"></label>
```

- [ ] **Step 4: Sync name→slug in workspace.js**

Add a delegated `input` handler in `gui/static/workspace.js` (near the other input handlers):

```javascript
  // New-local-person: the slug follows the typed name until the operator edits
  // the slug by hand. Only the new-person form's slug carries data-autoslug.
  function slugify(s) {
    return String(s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "").slice(0, 100);
  }
  document.addEventListener("input", (e) => {
    const el = e.target;
    if (!(el instanceof HTMLInputElement)) return;
    const form = el.closest(".local-person");
    if (!form) return;
    const slug = form.querySelector('input[name="slug"][data-autoslug]');
    if (!slug) return;                                  // existing person, or already dirty
    if (el.name === "slug") { slug.removeAttribute("data-autoslug"); return; }  // manual edit → stop
    if (el.name === "name") slug.value = slugify(el.value);
  });
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py tests/test_gui_workspace.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/templates/panels/_macros.html gui/static/workspace.js tests/test_gui_review.py
git commit -m "feat(gui): auto-fill a new local person's slug from the typed name"
```
(add the Co-Authored-By trailer)

---

### Task 3: Interview / news-anchor local roles

Give news-clip (and podcast) meetings host/anchor/guest role options.

**Files:**
- Modify: `src/event_kinds.py` (add an interview role set + map `news_clip` / `podcast`)
- Test: `tests/test_event_kinds.py` (or wherever `local_roles_for` is tested; create the test file if none)

**Interfaces:**
- Produces: `local_roles_for("news_clip")` and `local_roles_for("podcast")` return interview roles including `anchor` and `host`. Other kinds unchanged. All new role words match `LOCAL_ROLE_PATTERN`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_event_kinds_interview_roles.py  (new file; or append to an existing event-kinds test)
import re
from src.event_kinds import local_roles_for, LOCAL_ROLE_PATTERN


def test_news_clip_offers_interview_roles():
    roles = local_roles_for("news_clip")
    assert "anchor" in roles and "host" in roles and "guest" in roles


def test_podcast_offers_interview_roles():
    assert "host" in local_roles_for("podcast")


def test_interview_roles_match_the_stored_pattern():
    pat = re.compile(LOCAL_ROLE_PATTERN)
    for r in local_roles_for("news_clip"):
        assert pat.match(r), f"{r} must match the storable role pattern"


def test_civic_and_campaign_kinds_unchanged():
    assert local_roles_for("council")[0] == "public_comment"
    assert local_roles_for("debate") == ("candidate", "moderator", "panelist")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_event_kinds_interview_roles.py -v`
Expected: FAIL (news_clip currently falls back to DEFAULT_LOCAL_ROLES; podcast is campaign roles).

- [ ] **Step 3: Add the interview role set**

In `src/event_kinds.py`, add after `_CAMPAIGN_ROLES`:

```python
# Interview/host-guest formats: the local people tagged are usually the show's
# own on-air staff (anchor/host/correspondent) or a non-roster guest, not
# campaign roles. Default is "host" (the recurring on-air person).
_INTERVIEW_ROLES = ("host", "anchor", "guest", "correspondent", "interviewee")
```

Then map the interview kinds in `LOCAL_ROLE_SETS`:

```python
    "news_clip": _INTERVIEW_ROLES,
    "podcast": _INTERVIEW_ROLES,
```

(Replace the existing `"podcast": _CAMPAIGN_ROLES` line; add the `news_clip` entry. Leave `press_conference` as-is — it already has its own official/staff set.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_event_kinds_interview_roles.py tests/test_gui_review.py -q`
Expected: PASS. Also run any existing event-kinds test file if present.

- [ ] **Step 5: Commit**

```bash
git add src/event_kinds.py tests/test_event_kinds_interview_roles.py
git commit -m "feat(review): interview/anchor local-person roles for news_clip + podcast"
```
(add the Co-Authored-By trailer)

---

## Self-Review

- **Coverage:** sort-direction indicator (Task 1) ✓; auto-slug-from-name for new local persons, protecting existing ones (Task 2) ✓; interview/anchor roles, no migration (Task 3) ✓. All additive; existing hooks + tests preserved.
- **Placeholder scan:** every step has concrete code; the two fixture caveats point at real existing tests to mirror.
- **Consistency:** `data-autoslug`, `slugify`, the `aria-sort` attribute, and `_INTERVIEW_ROLES` are used consistently and match the code they extend.

## Dependencies

Builds on Slices 1–4 (tokens, the library sort, the review card, the local-role plumbing). Independent tasks; any order.
