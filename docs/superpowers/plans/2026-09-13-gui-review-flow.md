# GUI Review Flow (Slice 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the speaker review page keep your place (update one card in place after an action instead of re-rendering the whole panel and jumping to the top) and take fewer clicks per speaker.

**Architecture:** Add a per-card fragment route that renders a single `card()` macro; `workspace.js` swaps just that card's DOM node after a card-scoped action, leaving scroll untouched. Merge (which relabels/removes other cards) keeps the full-panel reload. Reduce clicks by defaulting the roster search panel open for not-yet-identified speakers and letting Enter pick the top search result. Builds on Slice 1 (the `card()` macro at `gui/templates/panels/_macros.html`, the tokens, `base.html`).

**Tech Stack:** FastAPI + Jinja2 + vanilla JS. No build step, no new dependency.

## Global Constraints

- ALL work in the worktree `/Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/admiring-ritchie-dbf38c`; every bash command starts with `cd` into it. NEVER touch `/Users/chrisandrews/Documents/GitHub/on-the-record` (a different checkout/branch). Confirm before committing: `git rev-parse --abbrev-ref HEAD` = `claude/zealous-roentgen-090702` and `git rev-parse --show-toplevel` ends in `/admiring-ritchie-dbf38c`.
- venv by ABSOLUTE path from the worktree cwd: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest ...`. No server / no port 8000 (a live run may use it). JS behavior (scroll, swap, Enter) is verified live by the operator, not a subagent.
- **Preserve every review behavior hook.** Do not rename or remove any form `action`, `<input>`/`<select>` `name`, element `id`, `data-*`, or CSS class the review JS or tests use: `.card`, `.card-head`, `.ident`, `.ident-panel[data-ident]`, `.ident-chips`, `.ident-chip`, `.actions`, `.accept`, `.rename`, `.link-search`, `.link-results`, `button.enroll`, `.merge`/`.merge-inline`, `data-seek`, `data-merge-mismatch`, radios named `ident-<label>`. Only ADD (a `data-label` attr, a fragment route, JS branches).
- The mutation routes and their semantics are unchanged — this slice only changes how the client refreshes after them and one default-visibility + one keyboard affordance.
- Every existing test stays green.

## Key facts (verified in current code)

- `workspace.js` intercepts in-panel form submits (`gui/static/workspace.js:44-82`): it POSTs via fetch then calls `loadPanel(activeTab)` which re-fetches `/meetings/{id}/panel/{tab}` and replaces `panel.innerHTML` — resetting scroll. The publish form is special (keeps a result fragment). A `data-merge-mismatch` confirm runs before the POST.
- The review panel renders cards via `m.card(c, page.meeting_id, page.all_cards, page.local_role_options)` (`gui/templates/panels/review.html:35,48`). `page` is `ReviewPageData` (`gui/models.py:321`): `meeting_id`, `needs_attention`, `confirmed`, `all_cards` (property = needs_attention+confirmed), `local_role_options`.
- Panel context comes from `workspace.panel_context("review", meeting_id)` → a dict with a `page` key (or `page=None` + `not_ready`), exactly what `app.py:277-282`'s `/panel/{name}` route passes to the template.
- The card root is `<div class="card {{ 'confirmed' if c.is_confirmed else 'attention' }}">` (`_macros.html:56`) — it currently has NO stable id/data-attr. All card forms post to `/meetings/{id}/speakers/{label}/…`; radios are `ident-{{ c.label }}`.
- The identity chooser reveals one panel per checked radio (`_macros.html:104,125,182,198` use `hidden` unless `identity_kind == kind`); when `identity_kind == 'none'` ALL four panels are hidden, so an unnamed speaker needs a reveal-click before the roster search is usable. The reveal switch is `workspace.js:205-214` (delegated `change`).
- The link search is `workspace.js:149-199` (debounced; renders one POST form per result into `.link-results`).

---

### Task 1: Per-card fragment route

Add a route that renders one speaker's card, so the client can refresh a single card.

**Files:**
- Create: `gui/templates/panels/_card_fragment.html`
- Modify: `gui/app.py` (new GET route near the `/panel/{name}` route at line 277)
- Modify: `gui/templates/panels/_macros.html` (add `data-label` to the card root)
- Test: `tests/test_gui_review.py`

**Interfaces:**
- Consumes: `workspace.panel_context("review", meeting_id)` → `ctx["page"]` (a `ReviewPageData` or None), `page.all_cards`.
- Produces: `GET /meetings/{meeting_id}/panel/review/card/{label}` → 200 with the rendered `card()` HTML for that label; 404 if the meeting isn't reviewable (`page is None`) or no card has that label. The card root now carries `data-label="<label>"`.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_review.py
from fastapi.testclient import TestClient
from gui.app import create_app


def test_card_fragment_route_renders_one_card(tagged_meeting_dir, tmp_meetings_dir):
    # Mirror the setup the existing review-panel tests use to get a reviewable
    # meeting (see test_load_review_page_groups_and_orders in this file).
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    # Discover a real label from the full review panel, then fetch just its card.
    panel = client.get("/meetings/2026-02-04-council/panel/review").text
    import re
    m = re.search(r'data-label="([^"]+)"', panel)
    assert m, "review panel should render cards carrying data-label"
    label = m.group(1)
    frag = client.get(f"/meetings/2026-02-04-council/panel/review/card/{label}")
    assert frag.status_code == 200
    assert f'data-label="{label}"' in frag.text
    assert f"/speakers/{label}/name" in frag.text   # the card's forms are present
    # It is ONE card, not the whole panel.
    assert "Needs attention" not in frag.text and "Confirmed" not in frag.text


def test_card_fragment_route_404_unknown_label(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    assert client.get("/meetings/2026-02-04-council/panel/review/card/NOPE").status_code == 404


def test_card_fragment_route_404_not_reviewable(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-03-01-council", completed_stage=2)  # pre-identify
    client = TestClient(create_app())
    assert client.get("/meetings/2026-03-01-council/panel/review/card/SPEAKER_00").status_code == 404
```

If `tagged_meeting_dir(... completed_stage=4)` alone does not yield a card with a `data-label` (no speakers), replicate the exact transcript/speaker setup that `test_load_review_page_groups_and_orders` uses in this same file so the review panel renders at least one card.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k card_fragment -v`
Expected: FAIL (route + `data-label` absent).

- [ ] **Step 3: Add `data-label` to the card root**

In `gui/templates/panels/_macros.html:56`, change the card root to carry the label:

```html
<div class="card {{ 'confirmed' if c.is_confirmed else 'attention' }}" data-label="{{ c.label }}">
```

- [ ] **Step 4: Add the fragment template**

```html
<!-- gui/templates/panels/_card_fragment.html -->
{% import "panels/_macros.html" as m %}{{ m.card(c, page.meeting_id, page.all_cards, page.local_role_options) }}
```

- [ ] **Step 5: Add the route**

In `gui/app.py`, next to `workspace_panel` (line 277), add:

```python
    @app.get("/meetings/{meeting_id}/panel/review/card/{label}", response_class=HTMLResponse)
    def review_card_fragment(request: Request, meeting_id: str, label: str) -> HTMLResponse:
        ctx = workspace.panel_context("review", meeting_id)
        page = ctx.get("page") if ctx else None
        if page is None:
            raise HTTPException(status_code=404)
        card = next((c for c in page.all_cards if c.label == label), None)
        if card is None:
            raise HTTPException(status_code=404)
        return _templates.TemplateResponse(
            request, "panels/_card_fragment.html", {"page": page, "c": card})
```

Note: register this BEFORE the generic `/meetings/{meeting_id}/panel/{name}` route if the framework matches by declaration order for overlapping paths; FastAPI matches the more specific literal path (`/panel/review/card/{label}`) regardless, but keep it adjacent for readability. Confirm `HTTPException`, `Request`, `HTMLResponse` are already imported in `app.py` (they are — the existing routes use them).

- [ ] **Step 6: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -q`
Expected: PASS (new + existing).

- [ ] **Step 7: Commit**

```bash
git add gui/app.py gui/templates/panels/_card_fragment.html gui/templates/panels/_macros.html tests/test_gui_review.py
git commit -m "feat(gui): per-card review fragment route + data-label on the card"
```
(add the `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>` trailer)

---

### Task 2: Keep-your-place — swap one card, don't reload the panel

Make card-scoped actions refresh only their card; keep the full reload for merge and non-card forms.

**Files:**
- Modify: `gui/static/workspace.js` (the submit handler, `:44-82`)
- Test: `tests/test_gui_review.py` (static assertions on workspace.js) + operator live-verify

**Interfaces:**
- Consumes: the fragment route from Task 1; `data-label` on `.card`.
- Produces: after a card action (a form inside a `.card`, except merge), the client POSTs then replaces that `.card` node's `outerHTML` with the fragment — scroll untouched. Merge forms (`action` ends in `/merge`) and non-card forms keep `loadPanel`. Publish behavior unchanged.

- [ ] **Step 1: Write the static test**

```python
# add to tests/test_gui_review.py
def test_workspace_js_does_per_card_swap():
    from pathlib import Path
    js = Path("gui/static/workspace.js").read_text()
    assert "/panel/review/card/" in js          # fetches the single-card fragment
    assert "data-label" in js                    # locates the card to replace
    assert "outerHTML" in js                     # swaps the node in place (keeps scroll)
    assert "/merge" in js                         # merge is special-cased to full reload
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k per_card_swap -v`
Expected: FAIL.

- [ ] **Step 3: Rewrite the submit handler**

Replace the body of the `document.addEventListener("submit", …)` handler in `gui/static/workspace.js` (currently `:44-82`) with a version that adds the per-card branch while preserving the merge-confirm and publish behavior:

```javascript
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!(form instanceof HTMLFormElement)) return;
    if (form.hasAttribute("data-navigate")) return;          // let it navigate
    if (!panel.contains(form)) return;                        // only in-panel forms
    e.preventDefault();

    const isPublish = form.matches(".publish-form");
    const body = new FormData(form);

    // Destructive merge confirm (unchanged): the server refuses an unconfirmed
    // voice mismatch; turn that into a decision instead of a silent no-op.
    const mismatches = (form.getAttribute("data-merge-mismatch") || "")
      .split(",").filter(Boolean);
    if (mismatches.length) {
      const target = (body.get("target") || "").toString();
      if (target && mismatches.includes(target)) {
        const from = form.action.split("/speakers/")[1].split("/")[0];
        if (!window.confirm(
              `${from} and ${target} do not sound like the same person.\n\n` +
              `Merging is destructive and cannot be undone. Merge anyway?`)) return;
        body.append("confirm", "1");
      }
    }

    // A merge relabels/removes OTHER cards, so it needs the whole panel; a
    // card-scoped action (any other form inside a .card) only changes that card,
    // so swap just it and keep the reviewer's scroll position.
    const card = form.closest(".card");
    const label = card && card.getAttribute("data-label");
    const isMerge = /\/merge$/.test(form.action);
    const cardScoped = !!(card && label && !isMerge && !isPublish);

    let publishResult = "";
    try {
      const r = await fetch(form.action, { method: "POST", body, redirect: "manual" });
      if (isPublish) publishResult = await r.text();
    } catch (_) { /* best-effort; refresh shows current state */ }

    if (cardScoped) {
      try {
        const resp = await fetch(`/meetings/${enc(id)}/panel/review/card/${enc(label)}`);
        if (resp.ok) { card.outerHTML = await resp.text(); return; }
      } catch (_) { /* fall through to a full reload */ }
    }
    await loadPanel(activeTab, false);
    if (isPublish) {
      const slot = document.getElementById("publish-result");
      if (slot) slot.innerHTML = publishResult;
    }
  });
```

Everything else in `workspace.js` stays as-is (the `change` reveal handler, clip seek, link search, HLS, status polling).

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py tests/test_gui_workspace.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/static/workspace.js tests/test_gui_review.py
git commit -m "feat(gui): keep-your-place review — swap one card in place after an action"
```
(add the Co-Authored-By trailer)

- [ ] **Step 6: Operator live verification (not a subagent)**

Operator reviews a multi-speaker meeting: accept/rename/link/mark a speaker low in the list and confirm the page does NOT jump to the top and the acted card updates; merge still re-renders the whole panel.

---

### Task 3: Fewer clicks — default the roster search open + Enter picks the top result

**Files:**
- Modify: `gui/templates/panels/_macros.html` (roster panel default visibility)
- Modify: `gui/static/workspace.js` (Enter-to-pick in the link search)
- Test: `tests/test_gui_review.py`

**Interfaces:**
- Consumes: the identity chooser markup + `.link-search` handler.
- Produces: for a not-yet-identified speaker (`identity_kind == 'none'`) the roster `.ident-panel` renders WITHOUT `hidden` (search box immediately usable, no reveal-click); pressing Enter in a `.link-search` input submits the first `.link-results` form.

- [ ] **Step 1: Write the failing tests**

```python
# add to tests/test_gui_review.py
def test_unidentified_speaker_shows_roster_search_without_reveal(tagged_meeting_dir, tmp_meetings_dir):
    # Build a meeting with an unnamed/unlinked speaker (identity_kind == 'none').
    # Reuse the setup an existing test uses for an unconfirmed, unlinked card.
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    from fastapi.testclient import TestClient
    from gui.app import create_app
    panel = TestClient(create_app()).get("/meetings/2026-02-04-council/panel/review").text
    # The roster panel for a 'none' card must not be hidden — find a card whose
    # identity pill is unlinked and assert its roster panel is open. Simplest
    # robust check: at least one roster ident-panel is rendered without `hidden`.
    import re
    assert re.search(r'data-ident="roster"(?![^>]*hidden)', panel), \
        "an unidentified speaker's roster search should be open by default"


def test_workspace_js_enter_picks_top_result():
    from pathlib import Path
    js = Path("gui/static/workspace.js").read_text()
    assert "keydown" in js and "requestSubmit" in js
```

If the `tagged_meeting_dir(completed_stage=4)` fixture yields only confirmed/linked speakers, adjust the transcript/speaker setup so at least one speaker is unnamed and unlinked (`identity_kind == 'none'`), mirroring an existing unconfirmed-card test in this file.

- [ ] **Step 2: Run tests to verify they fail**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -k "roster_search_without_reveal or enter_picks_top" -v`
Expected: FAIL.

- [ ] **Step 3: Default the roster panel open for `none`**

In `gui/templates/panels/_macros.html:104`, widen the roster panel's visibility so a not-yet-identified speaker shows it (the search is the common first action):

```html
    <div class="ident-panel" data-ident="roster" {{ 'hidden' if c.identity_kind not in ('roster', 'none') }}>
```

Leave the other three panels (`local`, `unidentified`, `non_speaker`) unchanged — they stay hidden until their chip is chosen. This only reveals the roster search for `identity_kind == 'none'`; `ident_cost(c, 'roster')` already renders nothing when `identity_kind == 'none'` (its inner branches match only local/roster/unidentified/non_speaker), so no spurious "Saving this drops…" text appears.

- [ ] **Step 4: Add Enter-to-pick in the link search**

In `gui/static/workspace.js`, add a delegated keydown handler (next to the existing `.link-search` input handler at `:149`):

```javascript
  // Enter in the link-search box submits the top result (standard typeahead).
  document.addEventListener("keydown", (e) => {
    if (e.key !== "Enter") return;
    const input = e.target;
    if (!(input instanceof HTMLElement) || !input.matches(".link-search input")) return;
    const widget = input.closest(".link-search");
    const firstForm = widget && widget.querySelector(".link-results form");
    if (firstForm) { e.preventDefault(); firstForm.requestSubmit(); }
  });
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_review.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/templates/panels/_macros.html gui/static/workspace.js tests/test_gui_review.py
git commit -m "feat(gui): fewer clicks — roster search open for unidentified + Enter picks top match"
```
(add the Co-Authored-By trailer)

- [ ] **Step 7: Operator live verification (not a subagent)**

Operator opens a meeting with an unnamed speaker: the roster search is usable without a reveal-click; typing a name and pressing Enter links the top match (and, with Task 2, updates just that card).

---

## Self-Review

- **Spec coverage (Slice 2):** keep-your-place via per-card fragment swap (Tasks 1–2) ✓; merge/publish keep full reload (Task 2) ✓; fewer clicks — roster search open for `none` + Enter-to-pick (Task 3) ✓. Section-move simplification honored: a card that becomes Confirmed is swapped in place and re-sorts on the next full panel load (no special handling) ✓.
- **Placeholder scan:** every step has concrete code; the two fixture caveats point at a real existing test to mirror, not a TODO. Operator live-verify steps are genuine human gates (JS behavior on port 8000), not code placeholders.
- **Type/name consistency:** the fragment route path `/meetings/{id}/panel/review/card/{label}`, `data-label`, `page.all_cards`/`page.meeting_id`/`page.local_role_options`, and the preserved hooks are used identically across tasks and match current code.

## Dependencies

Depends on Slice 1 (the `card()` macro, tokens, `base.html`). Independent of Slice 3.
