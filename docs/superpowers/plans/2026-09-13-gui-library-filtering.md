# GUI Library Filtering & Sorting (Slice 3) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the meeting library real filtering and sorting — clickable sortable columns, a date range, a finer status set (including a "failed" bucket), one-click quick-filter chips, and clickable rows.

**Architecture:** All client-side, over the already-rendered rows (scale is small). Enrich each row's `data-*` attributes, extend `library.js` with sort + date-range + chips, and add a `failed` bucket to `MeetingSummary.status_key`. Builds on Slice 1 (tokens, `base.html`, badge macro).

**Tech Stack:** FastAPI + Jinja2 + vanilla JS. No build step, no new dependency.

## Global Constraints

- ALL work in the worktree `/Users/chrisandrews/Documents/GitHub/on-the-record/.claude/worktrees/admiring-ritchie-dbf38c`; every bash command starts with `cd` into it. NEVER touch `/Users/chrisandrews/Documents/GitHub/on-the-record`. Confirm before committing: branch `claude/zealous-roentgen-090702`, toplevel ends `/admiring-ritchie-dbf38c`.
- venv by ABSOLUTE path from the worktree cwd: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest ...`. No server / no port 8000. JS behavior verified live by the operator.
- Client-side only; do not add pagination or server-side query params. Preserve the existing filter behavior (search / kind / status) and the batch-status polling IIFE in `library.js`.
- Every existing test stays green.

## Key facts (verified in current code)

- `library.js` (`gui/static/library.js`) has two IIFEs: (1) client-side filter over `#lib-table tbody tr` by `data-search` (text), `data-kind`, `data-status` (AND), toggling `tr.hidden` and `#lib-empty-filter`; (2) `/batch/status` polling. There is NO sorting; row order is fixed to `processed_at` mtime desc from the scanner.
- `library.html` toolbar: `#lib-search`, `#lib-kind`, `#lib-status` (options: all/processing/needs-review/ready/live). Each row `<tr>` carries `data-meeting-id`, `data-kind`, `data-status`, `data-search`; columns are Meeting/Date/Processed/Kind/Speakers/Length/Review/Status/Live. Only the name anchor links to `/meetings/{id}` — the row is not clickable.
- `MeetingSummary.status_key` (`gui/models.py`) returns `live | ready | needs-review | processing` (no `failed`). Fields available per row include `date` (YYYY-MM-DD str), `speaker_count`, `duration_seconds`, `processed_at`, `display_name`, `review_status`.

---

### Task 1: Add a `failed` status bucket

**Files:**
- Modify: `gui/models.py` (`MeetingSummary.status_key`)
- Test: `tests/test_gui_library.py`

**Interfaces:**
- Produces: `status_key == "failed"` when `review_status == "failed"` (checked before the `pass`/stage buckets; `live` still wins). Existing buckets unchanged otherwise.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_library.py
def test_status_key_failed_bucket():
    from gui.models import MeetingSummary
    def s(**kw):
        base = dict(meeting_id="m", title=None, city=None, meeting_type=None, date=None,
                    event_kind=None, completed_stage=4)
        base.update(kw); return MeetingSummary(**base)
    assert s(review_status="failed").status_key == "failed"
    # live still wins over a stale failed verdict
    assert s(review_status="failed", is_live=True).status_key == "live"
    # unchanged buckets
    assert s(review_status="pass").status_key == "ready"
    assert s(completed_stage=2).status_key == "processing"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py -k failed_bucket -v`
Expected: FAIL.

- [ ] **Step 3: Add the bucket**

In `gui/models.py`, `MeetingSummary.status_key`, add the `failed` check after the `live` check:

```python
    def status_key(self) -> str:
        """Coarse lifecycle bucket for the library Status filter:
        'live' | 'failed' | 'ready' | 'needs-review' | 'processing'."""
        if self.is_live:
            return "live"
        if self.review_status == "failed":
            return "failed"
        if self.review_status == "pass":
            return "ready"
        if self.completed_stage >= 4:
            return "needs-review"
        return "processing"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/models.py tests/test_gui_library.py
git commit -m "feat(gui): add a 'failed' library status bucket"
```
(add the Co-Authored-By trailer)

---

### Task 2: Enrich row data + toolbar (status option, date range, quick chips)

**Files:**
- Modify: `gui/templates/library.html`
- Test: `tests/test_gui_library.py`

**Interfaces:**
- Produces: each `<tr>` also carries `data-date` (`m.date or ""`), `data-speakers` (`m.speaker_count or ""`), `data-length` (`m.duration_seconds or ""`), `data-name` (`m.display_name|lower`). The `#lib-status` select gains a `failed` option. New toolbar controls: `#lib-date-from` and `#lib-date-to` (`type="date"`), and a `.lib-chips` row of buttons (`data-chip="needs-review|live|failed|all"`). `<th>` cells for Date/Processed/Kind/Speakers/Length/Status gain `data-sort` keys (`date|processed|kind|speakers|length|status`) — behavior wired in Task 3.

- [ ] **Step 1: Write the failing test**

```python
# add to tests/test_gui_library.py
def test_library_toolbar_and_row_data_enriched(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    import json
    (mdir / "transcript_named.json").write_text(json.dumps(
        {"title": "Council", "duration_seconds": 3600, "speakers": {"A": {}, "B": {}}}))
    st = mdir / "pipeline_state.json"
    data = json.loads(st.read_text()); data.update({"date": "2026-02-04", "event_kind": "council"})
    st.write_text(json.dumps(data))
    from fastapi.testclient import TestClient
    from gui.app import create_app
    body = TestClient(create_app()).get("/").text
    # richer status option
    assert 'value="failed"' in body
    # date-range + chips controls
    assert 'id="lib-date-from"' in body and 'id="lib-date-to"' in body
    assert 'data-chip="needs-review"' in body and 'data-chip="all"' in body
    # sortable headers
    assert 'data-sort="date"' in body and 'data-sort="speakers"' in body
    # enriched row data
    assert 'data-date="2026-02-04"' in body
    assert 'data-speakers="2"' in body
    assert 'data-length="3600' in body     # duration_seconds (may be float-formatted)
    assert 'data-name="council"' in body   # display_name lowercased
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py -k toolbar_and_row_data -v`
Expected: FAIL.

- [ ] **Step 3: Enrich the toolbar**

In `gui/templates/library.html`, in the `.lib-toolbar`, add a `failed` option to `#lib-status`:

```html
        <option value="failed">Failed</option>
```
(place it after `needs-review`, before `ready`). Then add, right after the toolbar, a chips row and the date-range inputs (use existing tokenized classes; new classes get styles in Task 3's CSS if needed):

```html
    <div class="lib-chips">
      <button type="button" class="chip" data-chip="all">All</button>
      <button type="button" class="chip" data-chip="needs-review">Needs review</button>
      <button type="button" class="chip" data-chip="live">Live</button>
      <button type="button" class="chip" data-chip="failed">Failed</button>
      <label class="lib-daterange">From <input type="date" id="lib-date-from"></label>
      <label class="lib-daterange">To <input type="date" id="lib-date-to"></label>
    </div>
```

- [ ] **Step 4: Sortable headers + enriched row data**

In the `<thead>`, add `data-sort` to the sortable `<th>` cells:

```html
        <tr><th>Meeting</th><th data-sort="date">Date</th><th data-sort="processed">Processed</th><th data-sort="kind">Kind</th><th data-sort="speakers">Speakers</th><th data-sort="length">Length</th><th>Review</th><th data-sort="status">Status</th><th>Live</th></tr>
```

In the row `<tr>`, extend the existing `data-*` set:

```html
        <tr data-meeting-id="{{ m.meeting_id }}" data-kind="{{ m.event_kind or '' }}" data-status="{{ m.status_key }}"
            data-date="{{ m.date or '' }}" data-speakers="{{ m.speaker_count if m.speaker_count is not none else '' }}"
            data-length="{{ m.duration_seconds if m.duration_seconds is not none else '' }}"
            data-name="{{ m.display_name|lower }}"
            data-search="{{ [m.display_name, m.meeting_id, m.context_line, m.event_kind]|select|join(' ')|lower }}">
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/templates/library.html tests/test_gui_library.py
git commit -m "feat(gui): richer library toolbar (failed status, date range, chips) + row data"
```
(add the Co-Authored-By trailer)

---

### Task 3: Sorting, date-range filtering, chips, and clickable rows in library.js

**Files:**
- Modify: `gui/static/library.js` (extend the first IIFE only; leave the batch-poll IIFE untouched)
- Modify: `gui/static/style.css` (minimal token-based styling for `.lib-chips`/`.chip`/`.lib-daterange` and a sort-indicator; optional but keep it clean)
- Test: `tests/test_gui_library.py` (static assertions) + operator live-verify

**Interfaces:**
- Consumes: the enriched `data-*` and toolbar controls from Task 2.
- Produces: clicking a `th[data-sort]` sorts the visible rows by that key, toggling asc/desc on repeat; the date-range inputs filter by `data-date`; a `.chip[data-chip]` sets the status filter (`all` clears it) and re-applies; clicking a row (not on an `<a>`/`<button>`/`<input>`/`<form>`) navigates to `/meetings/<data-meeting-id>`. Existing search/kind/status filtering and the empty-message toggle still work.

- [ ] **Step 1: Write the static test**

```python
# add to tests/test_gui_library.py
def test_library_js_has_sort_daterange_chips_rowclick():
    from pathlib import Path
    js = Path("gui/static/library.js").read_text()
    assert "data-sort" in js                       # column sorting
    assert "lib-date-from" in js and "lib-date-to" in js   # date range
    assert "data-chip" in js                        # quick chips
    assert "data-meeting-id" in js and ("location" in js or "href" in js)  # row click nav
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py -k sort_daterange_chips -v`
Expected: FAIL.

- [ ] **Step 3: Extend the filter IIFE**

Rewrite the FIRST IIFE of `gui/static/library.js` (the filter block, currently lines 1-31) to add sorting, date-range, chips, and row-click, keeping the existing search/kind/status logic. Leave the second (batch-poll) IIFE exactly as-is.

```javascript
// Client-side library: search + Kind + Status + date range + quick chips,
// clickable rows, and sortable columns. Instant, no reload.
(function () {
  const search = document.getElementById("lib-search");
  const kindSel = document.getElementById("lib-kind");
  const statusSel = document.getElementById("lib-status");
  const dateFrom = document.getElementById("lib-date-from");
  const dateTo = document.getElementById("lib-date-to");
  const table = document.getElementById("lib-table");
  if (!table) return;  // empty library
  const tbody = table.querySelector("tbody");
  const rows = Array.from(tbody.querySelectorAll("tr"));
  const emptyMsg = document.getElementById("lib-empty-filter");

  function apply() {
    const q = (search.value || "").trim().toLowerCase();
    const kind = kindSel.value;
    const status = statusSel.value;
    const from = (dateFrom && dateFrom.value) || "";
    const to = (dateTo && dateTo.value) || "";
    let visible = 0;
    rows.forEach((tr) => {
      const hay = tr.getAttribute("data-search") || "";
      const d = tr.getAttribute("data-date") || "";
      const show = (!q || hay.includes(q))
        && (!kind || tr.getAttribute("data-kind") === kind)
        && (!status || tr.getAttribute("data-status") === status)
        && (!from || (d && d >= from))          // ISO YYYY-MM-DD compares lexically
        && (!to || (d && d <= to));
      tr.hidden = !show;
      if (show) visible++;
    });
    if (emptyMsg) emptyMsg.hidden = visible !== 0;
  }

  [search, kindSel, statusSel, dateFrom, dateTo].forEach((el) => {
    if (!el) return;
    el.addEventListener("input", apply);
    el.addEventListener("change", apply);
  });

  // Quick-filter chips: set the Status select (or clear it) and re-apply.
  document.querySelectorAll(".lib-chips .chip").forEach((chip) => {
    chip.addEventListener("click", () => {
      const v = chip.getAttribute("data-chip");
      statusSel.value = v === "all" ? "" : v;
      apply();
    });
  });

  // Column sorting: click a th[data-sort] to sort; click again to reverse.
  const NUMERIC = new Set(["speakers", "length"]);
  let sortKey = null, sortDir = 1;
  function cellVal(tr, key) {
    if (key === "processed") return tr;   // fallback below
    const map = { date: "data-date", kind: "data-kind", speakers: "data-speakers",
                  length: "data-length", status: "data-status" };
    return tr.getAttribute(map[key] || "") || "";
  }
  table.querySelectorAll("th[data-sort]").forEach((th) => {
    th.style.cursor = "pointer";
    th.addEventListener("click", () => {
      const key = th.getAttribute("data-sort");
      sortDir = (sortKey === key) ? -sortDir : 1;
      sortKey = key;
      const sorted = rows.slice().sort((a, b) => {
        let av = cellVal(a, key), bv = cellVal(b, key);
        if (NUMERIC.has(key)) { av = parseFloat(av) || 0; bv = parseFloat(bv) || 0; return (av - bv) * sortDir; }
        return String(av).localeCompare(String(bv)) * sortDir;
      });
      sorted.forEach((tr) => tbody.appendChild(tr));   // reorder in place
    });
  });

  // Whole-row click navigates to the meeting (ignore clicks on interactive els).
  tbody.addEventListener("click", (e) => {
    if (e.target.closest("a, button, input, select, form, label")) return;
    const tr = e.target.closest("tr");
    const mid = tr && tr.getAttribute("data-meeting-id");
    if (mid) location.href = "/meetings/" + encodeURIComponent(mid);
  });
})();
```

(Drop the unused `processed` sort key from the header if you don't wire a `data-processed` value — simplest is to NOT put `data-sort="processed"` on the Processed column. Adjust Task 2's thead accordingly so every `data-sort` key has a backing value: keep `date`, `kind`, `speakers`, `length`, `status`; omit `processed`. Update the Task-2 test's asserted keys to match if you change them.)

- [ ] **Step 4: Minimal styling**

In `gui/static/style.css`, add token-based rules for the new controls (no raw hex/spacing — use `var(--sp-*)`, `var(--c-*)`, `var(--r-*)`):

```css
.lib-chips { display: flex; gap: var(--sp-2); align-items: center; flex-wrap: wrap; margin-bottom: var(--sp-4); }
.lib-chips .chip { padding: var(--sp-1) var(--sp-3); border: 1px solid var(--c-line); border-radius: var(--r-pill, 999px); background: var(--c-surface); cursor: pointer; font-size: var(--fs-sm); }
.lib-daterange { font-size: var(--fs-sm); color: var(--c-ink-soft); }
table.library th[data-sort] { user-select: none; }
table.library tbody tr { cursor: pointer; }
```
(If `--r-pill` isn't defined, use `999px` or an existing radius token.)

- [ ] **Step 5: Run tests to verify they pass**

Run: `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_gui_library.py tests/test_gui_visual_foundation.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/static/library.js gui/static/style.css tests/test_gui_library.py
git commit -m "feat(gui): library sorting, date range, quick chips, clickable rows"
```
(add the Co-Authored-By trailer)

- [ ] **Step 7: Operator live verification (not a subagent)**

Operator confirms in the browser: clicking a column header sorts (and reverses on a second click); the date range narrows the list; chips switch the status filter; clicking a row opens the meeting; search/kind/status still work together.

---

## Self-Review

- **Spec coverage (Slice 3):** sortable columns (Task 3) ✓; date range (Tasks 2–3) ✓; finer status incl. `failed` (Tasks 1–2) ✓; quick-filter chips (Tasks 2–3) ✓; clickable rows (Task 3) ✓; enriched `data-*` driving it (Task 2) ✓. Client-side only, existing filter + batch-poll preserved ✓.
- **Placeholder scan:** every step has concrete code. The one adjustable point (whether to wire a `processed` sort column) is called out with the exact follow-through so no `data-sort` key is left without a backing value.
- **Type/name consistency:** control ids (`lib-date-from`/`lib-date-to`), `data-sort` keys, `data-chip` values, and the enriched `data-*` names are used identically across Tasks 2 and 3, and `status_key`'s new `failed` value matches the `failed` option and chip.

## Dependencies

Depends on Slice 1 (tokens, base template, badge macro). Independent of Slice 2.
