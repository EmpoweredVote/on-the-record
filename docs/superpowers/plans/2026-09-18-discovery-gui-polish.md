# Discovery review GUI — visual polish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax. NOTE: this is a visual task — verification is (a) the existing render tests staying green and (b) real browser-preview screenshots. Prefer inline execution so the browser preview can be driven and shown to the user.

**Goal:** Make `/discovery` look finished — lift it onto the design-token system with the mockup's card hierarchy and iconed count chips (light palette), and make Trust-outlet vs Approve-quote-source clear.

**Architecture:** Pure presentation + one small behavior fix. Move `discovery.html`'s inline `style="..."` onto named classes in `gui/static/style.css`; add reusable primitives (count chip + inline-SVG icons, trust badges, a `.btn` set); gate the Trust-outlet button where it structurally cannot work. No routes, no data flow, no DB changes.

**Tech Stack:** FastAPI + Jinja2 templates, a single hand-written `style.css` design-token system (light only), pytest render tests via `.venv/bin/python -m pytest`.

## Global Constraints

- **Run tests** with the main checkout's venv: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest` from this worktree's cwd. NEVER system `python3`.
- **Light palette only.** Use the existing `:root` tokens in `style.css` (`--c-*`, `--sp-*`, `--r-*`, `--fs-*`). Add NO dark-theme blocks.
- **No web fonts, no build step, no new dependency.** Icons are inline SVG (currentColor). Native font stack stays.
- **Preserve existing tests' assertions.** `tests/test_gui_discovery.py` asserts on rendered text and form `action`s. Keep all button text ("Approve → quote source", "Approve → ingest", "Trust outlet", "watch this channel", "Reject"), the "(+N more)" / "apply to all" hints, the prior-cycle badge text, the state-index / locality counts, the `&#10003;` done check, and the header last-run pill's conditional `background:#c0392b`. Keep the `.enroll` class on the ingest button and `.delete-btn` on reject (tests assert those). The ONE test that must change is `test_discovery_state_view_shows_trust_button_for_unknown_outlet` (Task 5), because it codifies the pre-fix behavior.
- **Count-chip labels live in `title`.** Render each per-race count as icon + number, with `title="{{ n }} {{ word }}"` so the strings `"5 candidates"`, `"4 quote sources"`, `"2 ingested"`, `"7 pending"` stay in the body (tests at lines 1112-1120 pass unchanged).
- **`discovery.html` overlaps open PR #241** (adds an "Add as hub" flywheel form, absent on `main`). Out of scope here; the new `.btn` classes are available for it to adopt at merge reconciliation.
- **Commit after each task.** End commit messages with the Co-Authored-By trailer.

---

### Task 1: Shared primitives — CSS + inline-SVG icon macro

Add the reusable classes and the icon set. Nothing consumes them yet, so all templates still render and every existing test stays green — this task is safely reviewable on its own.

**Files:**
- Create: `gui/templates/_icons.html`
- Modify: `gui/static/style.css` (append a discovery section)
- Modify: `gui/templates/discovery.html:3` (import the icon macro)

**Interfaces (produced):** Jinja macro `icon(name)` for `name ∈ {people, quote, mic, screen, check, chevron}`; CSS classes `.count-chips`, `.count-chip`(+`.is-zero`), `.badge--trusted`, `.badge--unknown`, `.btn`(+`--primary`/`--ghost`/`--danger`), `.disc-layout`, `.disc-nav`, `.disc-main`, `.disc-section-title`, `.disc-race`, `.disc-race-body`, `.disc-outlet`, `.disc-outlet-head`, `.disc-outlet-name`, `.disc-trust-note`, `.disc-item`, `.disc-meta`, `.disc-why`, `.disc-legend`.

- [ ] **Step 1: Create `gui/templates/_icons.html`**

```jinja
{% macro icon(name) -%}
{%- if name == 'people' -%}
<svg class="icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><circle cx="5.5" cy="5" r="2.4"/><circle cx="11" cy="6" r="2"/><path d="M1 14c0-2.6 2-4.2 4.5-4.2S10 11.4 10 14z"/><path d="M10.6 14c0-1.7.5-3 1.6-3.7 1.8.1 2.8 1.6 2.8 3.7z"/></svg>
{%- elif name == 'quote' -%}
<svg class="icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><path d="M3 4h4v4c0 2-1 3.2-3 4l-.7-1.3C4.4 10.2 5 9.5 5 8.5H3zM9 4h4v4c0 2-1 3.2-3 4l-.7-1.3c1.1-.5 1.7-1.2 1.7-2.2H9z"/></svg>
{%- elif name == 'mic' -%}
<svg class="icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true"><rect x="6" y="1.5" width="4" height="8" rx="2"/><path d="M3.7 7.5a4.3 4.3 0 0 0 8.6 0h-1.3a3 3 0 0 1-6 0zM7.3 12.4h1.4V15H7.3z"/></svg>
{%- elif name == 'screen' -%}
<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" aria-hidden="true"><rect x="1.6" y="2.6" width="12.8" height="8.4" rx="1"/><path d="M5.5 14h5M8 11v3"/></svg>
{%- elif name == 'check' -%}
<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.8" aria-hidden="true"><path d="M3 8.5l3 3 6.5-7.5"/></svg>
{%- elif name == 'chevron' -%}
<svg class="icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.6" aria-hidden="true"><path d="M6 3.5l5 4.5-5 4.5"/></svg>
{%- endif -%}
{%- endmacro %}
```

- [ ] **Step 2: Import it at the top of `discovery.html`**

Immediately after line 2 (`{% block title %}…{% endblock %}`), add:

```jinja
{% from "_icons.html" import icon %}
```

- [ ] **Step 3: Append the discovery CSS section to `gui/static/style.css`**

```css

/* --- Discovery review page ------------------------------------------------ */
.disc-layout { display: flex; gap: var(--sp-6); align-items: flex-start; }
.disc-nav { min-width: 14rem; flex: none; position: sticky; top: var(--sp-3); }
.disc-nav h2 { font-size: var(--fs-lg); margin: var(--sp-3) 0; }
.disc-nav ul { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: var(--sp-1); }
.disc-nav li { display: flex; justify-content: space-between; align-items: center; gap: var(--sp-2); padding: var(--sp-2) var(--sp-3); border-radius: var(--r-sm); }
.disc-nav li:hover { background: var(--c-surface); }
.disc-nav a { color: var(--c-ink); text-decoration: none; font-size: var(--fs-md); }
.disc-nav a:hover { color: var(--c-link); }
.disc-main { flex: 1; min-width: 0; }
.disc-section-title { font-size: var(--fs-2xs); text-transform: uppercase; letter-spacing: 0.06em; color: var(--c-muted-fg); font-weight: 600; margin: var(--sp-6) 0 var(--sp-3); }
.disc-section-title small { text-transform: none; letter-spacing: 0; font-weight: 400; }

.count-chips { display: inline-flex; gap: var(--sp-3); align-items: center; }
.count-chip { display: inline-flex; align-items: center; gap: var(--sp-1); font-size: var(--fs-xs); color: var(--c-ink-soft); font-variant-numeric: tabular-nums; }
.count-chip .icon { width: 14px; height: 14px; flex: none; }
.count-chip.is-zero { color: var(--c-muted-fg); opacity: 0.5; }

.disc-race { border: 1px solid var(--c-line); border-radius: var(--r-md); margin-bottom: var(--sp-3); background: var(--c-bg); }
.disc-race > summary { list-style: none; cursor: pointer; display: flex; align-items: center; gap: var(--sp-3); padding: var(--sp-3) var(--sp-4); border-radius: var(--r-md); }
.disc-race > summary::-webkit-details-marker { display: none; }
.disc-race > summary:hover { background: var(--c-surface); }
.disc-race .race-name { font-weight: 600; flex: 1; min-width: 0; }
.disc-race .chevron { color: var(--c-muted-fg); transition: transform 0.15s ease; }
.disc-race[open] > summary .chevron { transform: rotate(90deg); }
.disc-race[open] > summary { border-radius: var(--r-md) var(--r-md) 0 0; border-bottom: 1px solid var(--c-line-soft); }
.disc-race-body { padding: 0 var(--sp-4) var(--sp-3); }

.disc-outlet { border: 1px solid var(--c-line-soft); border-radius: var(--r-md); padding: var(--sp-3) var(--sp-4); margin-top: var(--sp-3); background: var(--c-surface); }
.disc-outlet-head { display: flex; justify-content: space-between; align-items: center; gap: var(--sp-3); }
.disc-outlet-name { font-weight: 600; font-size: var(--fs-md); }
.disc-trust { display: flex; flex-direction: column; align-items: flex-end; gap: var(--sp-0); }
.disc-trust-note { font-size: var(--fs-2xs); color: var(--c-muted-fg); margin: 0; }

.disc-item { display: flex; gap: var(--sp-3); padding: var(--sp-3) 0; border-top: 1px solid var(--c-line-soft); }
.disc-item:first-of-type { border-top: none; }
.disc-item-body { flex: 1; min-width: 0; }
.disc-meta { font-size: var(--fs-xs); color: var(--c-ink-soft); }
.disc-why { font-size: var(--fs-sm); color: var(--c-ink-soft); font-style: italic; margin-top: var(--sp-1); }

.badge--trusted, .badge--unknown { font-size: var(--fs-2xs); padding: var(--sp-0) var(--sp-3); border-radius: var(--r-pill); text-transform: uppercase; letter-spacing: 0.03em; }
.badge--trusted { background: var(--c-pass-bg); color: var(--c-pass-fg); border: 1px solid var(--c-pass-border); }
.badge--unknown { background: var(--c-control-bg); color: var(--c-ink-soft); border: 1px solid var(--c-line); }

.btn { font-size: var(--fs-sm); padding: var(--sp-1) var(--sp-3); border-radius: var(--r-sm); border: 1px solid var(--c-control-border); background: var(--c-control-bg); color: var(--c-ink); cursor: pointer; }
.btn:hover { background: var(--c-control-bg-hover); }
.btn--primary { background: var(--c-info-bg); border-color: var(--c-info-border); color: var(--c-info-fg); }
.btn--primary:hover { background: var(--c-select-bg); }
.btn--ghost { background: transparent; border-color: var(--c-control-border); color: var(--c-ink-soft); }
.btn--ghost:hover { background: var(--c-surface); color: var(--c-ink); }
.btn--danger { background: var(--c-fail-bg); border-color: var(--c-fail-border); color: var(--c-fail-fg); }
.btn:disabled { opacity: 0.55; cursor: not-allowed; }

.disc-legend { display: flex; gap: var(--sp-4); align-items: center; flex-wrap: wrap; margin: var(--sp-2) 0 var(--sp-5); }
.disc-legend .count-chip { color: var(--c-ink-soft); }
```

- [ ] **Step 4: Verify templates still render + tests green**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py tests/test_gui_coverage.py -q`
Expected: PASS (nothing uses the new classes yet; the `{% from %}` import must not break rendering).

- [ ] **Step 5: Commit**

```bash
git add gui/templates/_icons.html gui/static/style.css gui/templates/discovery.html
git commit -m "feat(gui): discovery design primitives — count chips, icons, badges, .btn set

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Race rows → cards with iconed count chips

Restyle the `race_block` macro so each race is a card with a title + four `.count-chip`s and a rotating chevron.

**Files:**
- Modify: `gui/templates/discovery.html:144-156` (`race_block` macro)
- Test: `tests/test_gui_discovery.py:1112-1120` stays green (title strings)

**Interfaces (consumed):** `icon()`, `.disc-race`, `.count-chip`, `.disc-race-body` from Task 1.

- [ ] **Step 1: Replace the `race_block` macro**

```jinja
{% macro count_chip(n, word, glyph) -%}
<span class="count-chip{% if not n %} is-zero{% endif %}" title="{{ n }} {{ word }}">{{ icon(glyph) }} {{ n }}</span>
{%- endmacro %}

{% macro race_block(race, groups_by_race, state='') %}
{% set groups = groups_by_race.get(race.race_id, []) %}
<details class="disc-race" id="race-{{ race.race_id }}">
  <summary>
    {{ icon('chevron') }}<span class="race-name">{{ race.position_name or "Unlabeled race" }}</span>
    <span class="count-chips">
      {{ count_chip(race.candidates, 'candidates', 'people') }}
      {{ count_chip(race.quote_sources, 'quote sources', 'quote') }}
      {{ count_chip(race.ingested, 'ingested', 'mic') }}
      {{ count_chip(race.pending, 'pending', 'screen') }}
    </span>
  </summary>
  <div class="disc-race-body">{{ outlet_groups(groups, race, state) }}</div>
</details>
{% endmacro %}
```

(Put the `count_chip` helper macro directly above `race_block`. The chevron sits in the summary and rotates via the `.disc-race[open]` CSS from Task 1.)

- [ ] **Step 2: Verify**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "race_row or race_block or counts or state_view" -q`
Expected: PASS — `"5 candidates"`, `"4 quote sources"`, `"2 ingested"`, `"7 pending"` are present via each chip's `title`.

- [ ] **Step 3: Commit**

```bash
git add gui/templates/discovery.html
git commit -m "feat(gui): discovery race rows as cards with iconed count chips

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Outlet sub-cards, lane rows, and the button system

Restyle `outlet_groups`, `pending_row`, and `row_actions` onto the new classes; apply `.btn` variants to the currently-bare buttons while keeping `.enroll` (ingest) and `.delete-btn` (reject) as the tests require.

**Files:**
- Modify: `gui/templates/discovery.html` — `row_actions` (50-98), `pending_row` (100-117), `outlet_groups` (119-142), and reuse the `.disc-item` treatment in `deferred_row` (174-190) and `auto_kept_row` (192-202).
- Test: preserve `tests/test_gui_discovery.py` button/text assertions.

**Interfaces (consumed):** `.disc-outlet`, `.disc-item`, `.disc-meta`, `.disc-why`, `.btn*`, `.badge--*` from Task 1.

- [ ] **Step 1: Restyle `outlet_groups`** (the trust-button gating itself is Task 5; here only the container classes + badge shell change)

```jinja
{% macro outlet_groups(groups, race, state='') %}
  {% if not groups %}
  <p class="empty">No pending items{% if race %} for this race{% endif %}.</p>
  {% endif %}
  {% for group in groups %}
  <div class="disc-outlet">
    <div class="disc-outlet-head">
      <span class="disc-outlet-name">{{ group.name }}
        {% if group.trusted %}<span class="badge--trusted">trusted</span>{% else %}<span class="badge--unknown">unknown</span>{% endif %}
      </span>
      {# trust control added in Task 5 #}
    </div>
    {% if group.muted %}
    <p class="empty"><small>{{ group.muted|length }} auto-kept as quote sources</small></p>
    {% endif %}
    {% for item in group.open %}
      {{ pending_row(item, race, state) }}
    {% endfor %}
  </div>
  {% endfor %}
{% endmacro %}
```

- [ ] **Step 2: Restyle `pending_row`** (swap inline styles → `.disc-item`; keep the checkbox, thumb, kind pill, meta, why, prior-cycle badge, and `row_actions` call)

```jinja
{% macro pending_row(item, race, state='') %}
{% set r = item.row %}
<div class="disc-item">
  <input type="checkbox" name="row_ids" value="{{ r.id }}" form="bulk-pending-form">
  {% if r.thumb_url %}<img class="thumb" src="{{ r.thumb_url }}" alt="" loading="lazy">{% endif %}
  <div class="disc-item-body">
    <span class="pill">{{ item.lane | humanize_kind }}</span>
    {% if r.safe_url %}<a href="{{ r.safe_url }}" target="_blank" rel="noopener">{{ r.title or r.url }}</a>{% else %}{{ r.title or r.url }}{% endif %}
    <div class="disc-meta">{{ r.channel_name or "?" }} · {{ r.duration_label }} ·
      {{ (r.published_at or "?")[:10] }} · {{ r.event_kind_guess or "?" }}
      {% if r.source_tier_guess %} · tier {{ r.source_tier_guess }}{% endif %}
      {% if r.prior_cycle %} · <span class="pill" title="A candidate's own answers from an earlier cycle — attribute the quote to this cycle, do not present it as current.">prior cycle {{ r.source_cycle_year or "?" }}</span>{% endif %}
      · conf {{ r.confidence_label }} · via {{ r.discovered_via }} · {{ r.route }}</div>
    <div class="disc-why">{{ r.why }}</div>
    {{ row_actions(r, race, state, lane=item.lane) }}
  </div>
</div>
{% endmacro %}
```

- [ ] **Step 2b:** Apply the same `.disc-item` / `.disc-meta` / `.disc-why` swap to `deferred_row` and `auto_kept_row` (mechanical: replace their inline `style="display:flex;…"` and `<small>` wrappers with the classes; keep every `{{ }}` expression and the checkbox `form=` targets unchanged).

- [ ] **Step 3: In `row_actions`, put the currently-bare buttons on the `.btn` system.** Keep `class="enroll"` on the ingest button and `class="delete-btn"` on reject (unchanged — tests assert them). Change only:
  - the quote-source button → `<button type="submit" class="btn btn--primary">Approve &rarr; quote source{% if r.family_count %} (+{{ r.family_count }} more){% endif %}</button>`
  - the watch-channel button → `<button type="submit" class="btn btn--ghost" title="Add this channel to the watchlist">+ Watch this channel</button>`
  - wrap the action row container class from the inline-styled `div` to `<div class="row-actions">` and add to `style.css`: `.row-actions { display: flex; gap: var(--sp-2); flex-wrap: wrap; align-items: center; margin-top: var(--sp-3); } .row-actions form { margin: 0; } .row-actions select { font-size: var(--fs-sm); padding: var(--sp-1) var(--sp-2); border: 1px solid var(--c-control-border); border-radius: var(--r-sm); }`

- [ ] **Step 4: Verify**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -q`
Expected: PASS — button text, `(+N more)`, `apply to all`, `class="enroll" disabled`, "watch this channel", and the muted/questionnaire assertions all still hold.

- [ ] **Step 5: Commit**

```bash
git add gui/templates/discovery.html gui/static/style.css
git commit -m "feat(gui): discovery outlet sub-cards, lane rows, and consistent buttons

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Sidebar nav, header legend, state index, and flat views

Restyle the remaining chrome: the per-state left nav, a header count legend, the choose-a-state list, and the deferred / auto-kept / unmatched sections.

**Files:**
- Modify: `gui/templates/discovery.html` — the state view wrapper (262-306), the state index (236-249), and the header block (add the legend near line 24).
- Test: preserve state-index (1071-1078), statewide (1091-1094), and locality-rail (1097-1109) assertions.

- [ ] **Step 1: State view wrapper + nav.** Replace the inline-styled `<div style="display:flex…">` / `<nav style="…">` (262-280) with `<div class="disc-layout">` + `<nav class="disc-nav">`, and the content `<div style="flex:1…">` with `<div class="disc-main">`. Keep the `<h2>{{ state | state_name }}</h2>`, the "All states" link (`href="/discovery"`), and each `<li>` with its `href="#..."` anchor + the `{{ locality_pending[loc] }} pending` / `&#10003; done` badge (wrap the badge in `<span class="pill">` as today — assertions rely on the text `"3 pending"` and `"&#10003;"`).

- [ ] **Step 2: Section titles.** Change the `<h2>Statewide &amp; federal <small class="empty">— tracked once…</small></h2>` and `<h2>{{ loc }}</h2>` to `class="disc-section-title"` (keep the text — `"tracked once"` and the locality names are asserted).

- [ ] **Step 3: Header legend.** After the `</header>` / near the top of `<main>`, add a legend (only on the pending state view is fine, but simplest is always):

```jinja
<div class="disc-legend">
  <span class="count-chip">{{ icon('people') }} candidates</span>
  <span class="count-chip">{{ icon('quote') }} quote sources</span>
  <span class="count-chip">{{ icon('mic') }} ingested</span>
  <span class="count-chip">{{ icon('screen') }} pending</span>
</div>
```

- [ ] **Step 4: State index + choose-a-state list.** Replace the inline-styled `<ul>`/`<li>` (241-248) with `<ul class="disc-nav">`-style list or a simple `.disc-statelist` (add `.disc-statelist { list-style:none; padding:0; display:flex; flex-direction:column; gap:var(--sp-2); max-width:28rem; } .disc-statelist li { display:flex; justify-content:space-between; align-items:center; padding:var(--sp-3) var(--sp-4); border:1px solid var(--c-line); border-radius:var(--r-md); }` to `style.css`). Keep `href="/discovery?state={{ s.state }}"`, the `{{ s.state | state_name }}` link, and the `{{ s.pending }} pending` pill.

- [ ] **Step 5: Deferred / auto-kept `<h2>`s** → `class="disc-section-title"` (keep the `{{ rows|length }} deferred` pill text).

- [ ] **Step 6: Verify**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -q`
Expected: PASS (state index links + counts, "Indiana", "tracked once", "City of Bloomington, Indiana", "3 pending", "&#10003;" all present).

- [ ] **Step 7: Commit**

```bash
git add gui/templates/discovery.html gui/static/style.css
git commit -m "feat(gui): discovery sidebar nav, header legend, state index, flat views

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Trust / Approve clarity + gate the Trust-outlet button

Show "Trust outlet" only where it can work (an outlet- or channel-backed group), give it a one-line explanation, and rely on the Task-3 trusted badge for the visible result. This is the one behavior change; it updates the test that codified the old show-everywhere behavior.

**Files:**
- Modify: `gui/app.py:82-116` (`_outlet_groups_for` — add `trustable`)
- Modify: `gui/templates/discovery.html` — the `disc-outlet-head` trust control (in the Task-3 `outlet_groups`)
- Modify: `tests/test_gui_discovery.py:1123-1132` (update) + add one companion test

**Interfaces (consumed/produced):** group dict gains `"trustable": bool` (family key is `outlet` or `channel`). Template gates the trust form on `group.trustable`.

- [ ] **Step 1: Write the failing/updated tests**

Replace `test_discovery_state_view_shows_trust_button_for_unknown_outlet` and add a companion:

```python
def test_discovery_trust_button_hidden_for_channelless_web_source(monkeypatch):
    # A plain web source (no outlet_id, no channel_id) cannot be "trusted" as an
    # outlet — trust_from_row would fail. So the button must NOT be offered.
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race("r1")])
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [
        _row(id="d1", race_id="r1", channel_name="Random Blog", outlet_id=None,
            channel_id=None, event_kind_guess="other")])
    client = TestClient(create_app())
    body = client.get("/discovery?state=TX").text
    assert "Trust outlet" not in body
    assert "unknown" in body            # still labeled as an unknown source


def test_discovery_trust_button_shown_for_channel_backed_source(monkeypatch):
    # A YouTube-channel-backed untrusted source CAN be trusted -> button shown.
    monkeypatch.setattr(coverage, "races_for_state", lambda state: [_race("r1")])
    monkeypatch.setattr(discovery, "pending_rows", lambda status="pending": [
        _row(id="d1", race_id="r1", channel_name="Some Channel",
            channel_id="UCabc", outlet_id=None, event_kind_guess="other")])
    client = TestClient(create_app())
    body = client.get("/discovery?state=TX").text
    assert "Trust outlet" in body
    assert 'action="/discovery/d1/trust"' in body
```

- [ ] **Step 2: Run to verify the first fails**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -k "trust_button" -q`
Expected: `test_discovery_trust_button_hidden_for_channelless_web_source` FAILS ("Trust outlet" currently shows for the channel-less row).

- [ ] **Step 3: Add `trustable` in `_outlet_groups_for`** (`gui/app.py`). In the loop, `key = family_key(r) or ("row", r.id)`; set it when the group is created:

```python
            groups[key] = {"name": r.channel_name or "source", "trusted": r.outlet_trusted,
                           "trustable": key[0] in ("outlet", "channel"),
                           "muted": [], "open": [], "trust_row_id": r.id}
```

- [ ] **Step 4: Gate + annotate the trust control** in `outlet_groups` (the `{# trust control #}` placeholder from Task 3):

```jinja
      {% if not group.trusted and group.trustable %}
      <div class="disc-trust">
        <form method="post" action="/discovery/{{ group.trust_row_id }}/trust" style="margin:0;">
          <input type="hidden" name="state" value="{{ state or '' }}">
          <button type="submit" class="btn btn--ghost" title="Auto-approve every future item from {{ group.name }} as a quote source">Trust outlet</button>
        </form>
        <p class="disc-trust-note">auto-approves everything this outlet sends</p>
      </div>
      {% endif %}
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py -q`
Expected: PASS — the two new trust tests, plus the muted-outlet test (`"Trust outlet" not in body` for a trusted outlet) and everything else.

- [ ] **Step 6: Commit**

```bash
git add gui/app.py gui/templates/discovery.html tests/test_gui_discovery.py
git commit -m "feat(gui): gate Trust-outlet to channel/outlet-backed sources + clarify vs Approve

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full-suite + visual verification

**Files:** none (verification).

- [ ] **Step 1: Full GUI suite green**

Run: `.venv/bin/python -m pytest tests/test_gui_discovery.py tests/test_gui_coverage.py tests/test_gui_launch.py -q`
Expected: PASS (no failures; pre-existing live-DB skips unchanged).

- [ ] **Step 2: Render the real page.** Start the GUI dev server in the browser preview (`.claude/launch.json` for the GUI, or `uvicorn gui.asgi:app`), open `/discovery`, `/discovery?state=IN` (a state with local races, cards expanded), `/discovery?show=deferred`, and `/discovery?show=auto-kept`. Confirm: race cards, iconed count chips (zeros dimmed), outlet sub-cards with trusted/unknown badges, consistent buttons, styled sidebar. Screenshot each and share with the user.

- [ ] **Step 3: Confirm the Trust/Approve fix visually** — a channel-backed group shows "Trust outlet" + the helper line; a channel-less web source shows the `unknown` badge and no trust button.

- [ ] **Step 4: Commit** (only if any fixup was needed)

```bash
git add -A
git commit -m "chore(gui): discovery polish verification fixups

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**1. Spec coverage** (against `docs/superpowers/specs/2026-09-18-discovery-gui-polish-design.md`):
- Shared primitives (count chip, icons, badges, `.btn`) → Task 1. ✓
- Discovery restyle: race cards → T2; outlet sub-cards + lane rows + buttons → T3; sidebar/header/state-index/flat views → T4. ✓
- Trust/Approve clarity + gating → T5. ✓
- Light palette only, no web fonts/build step, preserve tests → Global Constraints + each task's verify step. ✓
- Out of scope (dark mode, cross-state tree, other pages) → not planned. ✓

**2. Placeholder scan:** No TBD/TODO; CSS and macro rewrites are given in full; the one behavior change carries its exact test edit. ✓

**3. Consistency:** Class names used in Tasks 2-5 (`.disc-race`, `.count-chip`, `.disc-outlet`, `.disc-item`, `.btn--*`, `.badge--*`) are all defined in Task 1. `trustable` is produced in T5 Step 3 and consumed in T5 Step 4. The count-chip `title="{{ n }} {{ word }}"` keeps the exact strings the 1112-1120 tests assert. The ingest button keeps `class="enroll"` (test 1178) and reject keeps `.delete-btn`. ✓

**Behavior-change flag:** Task 5 changes one user-visible behavior (Trust-outlet no longer shown for channel-less web sources) and updates the test that codified the old behavior. This is the fix the user asked for ("Trust doesn't seem to do anything"), and it is in the approved spec — but it IS a behavior change, called out here so a reviewer/executor sees it deliberately.
