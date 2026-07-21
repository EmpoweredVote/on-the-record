# Richer, Searchable Library — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the meeting library richer and filterable — each row shows the context it was missing (city / body / org / race), a client-side filter bar (search + Kind + Status) narrows the list instantly, and clicking a row opens the workspace on the tab that matches its stage.

**Architecture:** FastAPI + Jinja + vanilla JS. `MeetingSummary` (in `gui/models.py`) gains `event_orgs`/`body_slug`/`race_id`/`race_label` and two display properties (`context_line`, `status_key`). The scanner (`gui/library.py`) populates the first three from local files (staying DB-free). The library route batch-resolves race labels via a new best-effort `gui/races.py::race_labels` (mirroring how it already passes `live_slugs`). `library.html` renders a context subline + per-row `data-*` attributes; a small `library.js` filters rows client-side. Row links change from `/meetings/{id}/review` to the bare `/meetings/{id}`, so the workspace shell's existing stage-based default-tab logic handles click-through.

**Tech Stack:** FastAPI, Jinja2, psycopg2, vanilla JS, pytest + `fastapi.testclient.TestClient`.

**This is Plan 3 of 3.** Plans 1 (workspace) & 2 (rich IDs + kind-aware form) are complete on branch `feat/gui-meeting-workspace`. This plan reuses Plan 2's `gui/races.py` helpers. Independently shippable.

## Grounding facts

`tests/conftest.py::tagged_meeting_dir(source, *, meeting_id, completed_stage)` sets `pipeline_state.json` with `body_slug = source`. `PipelineState` exposes `body_slug`, `race_id`, `city`, `date`, `meeting_type`, `event_kind`, `review_status`, `trusted_coverage`, `completed_stage`. `transcript_named.json` (the Meeting dict) holds `title`, `duration_seconds`, `speakers`, and `event_orgs`. The current `library.html` links each row to `/meetings/{id}/review` (Plan 1 makes that 301 to the workspace). `gui/races.py` (Plan 2) has `race_display`, `_db_url`, and `search_races_safe`. Run Python via `.venv/bin/python`.

---

## Task 1: `MeetingSummary` context fields + `context_line` + `status_key`

**Files:**
- Modify: `gui/models.py`
- Test: `tests/test_gui_library.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_library.py  (append)
def test_meeting_summary_context_line_composes_available_fields():
    s = MeetingSummary(
        meeting_id="m", title=None, city="Bloomington", meeting_type="Regular Session",
        date="2026-02-10", event_kind="council", completed_stage=4,
        body_slug="bloomington-common-council",
    )
    # city + prettified body, de-duplicated, joined with ' · '
    assert s.context_line == "Bloomington · Bloomington Common Council"

    s2 = MeetingSummary(
        meeting_id="m2", title=None, city=None, meeting_type="Interview", date="2026-05-01",
        event_kind="news_clip", completed_stage=5,
        event_orgs=["CBS"], race_label="CA Governor · 2026",
    )
    assert s2.context_line == "CBS · CA Governor · 2026"

    s3 = MeetingSummary(meeting_id="m3", title=None, city=None, meeting_type=None,
                        date=None, event_kind="floor", completed_stage=3)
    assert s3.context_line == ""   # nothing to show


def test_meeting_summary_status_key():
    def s(**kw):
        base = dict(meeting_id="m", title=None, city=None, meeting_type=None, date=None,
                    event_kind=None, completed_stage=0)
        base.update(kw)
        return MeetingSummary(**base)
    assert s(completed_stage=2).status_key == "processing"          # pre-identify
    assert s(completed_stage=4).status_key == "needs-review"        # reviewable, gate not passed
    assert s(completed_stage=5, review_status="pass").status_key == "ready"
    assert s(completed_stage=7, review_status="pass", is_live=True).status_key == "live"
    assert s(completed_stage=7, review_status="review").status_key == "needs-review"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -k "context_line or status_key" -q`
Expected: FAIL — `MeetingSummary` has no `body_slug`/`event_orgs`/`race_label`; no `context_line`/`status_key`.

- [ ] **Step 3: Add the fields + properties**

In `gui/models.py`, add these fields to the `MeetingSummary` dataclass (after `is_live`):

```python
    # Slice 3: library context. All optional so older/partial meetings still build.
    event_orgs: list = field(default_factory=list)
    body_slug: Optional[str] = None
    race_id: Optional[str] = None
    race_label: Optional[str] = None
```

Add these two properties to `MeetingSummary`:

```python
    @property
    def context_line(self) -> str:
        """One-line context under the row name: city · body · org(s) · race,
        de-duplicated, only what's present."""
        parts: list[str] = []
        if self.city and self.city.strip():
            parts.append(self.city.strip())
        if self.body_slug and self.body_slug.strip():
            parts.append(self.body_slug.replace("-", " ").title())
        for org in (self.event_orgs or []):
            if org and str(org).strip():
                parts.append(str(org).strip())
        if self.race_label and self.race_label.strip():
            parts.append(self.race_label.strip())
        seen: list[str] = []
        for p in parts:
            if p not in seen:
                seen.append(p)
        return " · ".join(seen)

    @property
    def status_key(self) -> str:
        """Coarse lifecycle bucket for the library Status filter:
        'live' | 'ready' | 'needs-review' | 'processing'."""
        if self.is_live:
            return "live"
        if self.review_status == "pass":
            return "ready"
        if self.completed_stage >= 4:
            return "needs-review"
        return "processing"
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -k "context_line or status_key" -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/models.py tests/test_gui_library.py
git commit -m "feat(gui): MeetingSummary context_line + status_key + context fields"
```

---

## Task 2: Scanner populates the context fields

**Files:**
- Modify: `gui/library.py`
- Test: `tests/test_gui_library.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gui_library.py  (append)
def test_scan_meetings_populates_context_fields(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("bloomington-common-council",
                              meeting_id="2026-02-10-council", completed_stage=4)
    # body_slug comes from state (tagged_meeting_dir sets it to the source arg);
    # race_id from state; event_orgs from transcript_named.
    state = mdir / "pipeline_state.json"
    data = json.loads(state.read_text())
    data.update({"city": "Bloomington", "race_id": "uuid-r"})
    state.write_text(json.dumps(data))
    (mdir / "transcript_named.json").write_text(json.dumps(
        {"title": "Council", "event_orgs": ["CATS", "WFHB"]}))

    s = scan_meetings(tmp_meetings_dir)[0]
    assert s.body_slug == "bloomington-common-council"
    assert s.race_id == "uuid-r"
    assert s.event_orgs == ["CATS", "WFHB"]
    assert "Bloomington Common Council" in s.context_line


def test_scan_meetings_context_fields_absent_are_graceful(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-11-council", completed_stage=1)
    s = scan_meetings(tmp_meetings_dir)[0]
    assert s.event_orgs == [] and s.race_id is None
```

Note: `tagged_meeting_dir("x", ...)` sets `body_slug="x"`; the second test only checks event_orgs/race_id defaults, so the body_slug value is irrelevant there.

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -k "populates_context or context_fields_absent" -q`
Expected: FAIL — the scanner doesn't set these yet.

- [ ] **Step 3: Populate the fields in `_summarize`**

In `gui/library.py`'s `_summarize`, after reading `named`, extract `event_orgs`:

```python
    event_orgs = []
    if isinstance(named, dict) and isinstance(named.get("event_orgs"), list):
        event_orgs = [o for o in named["event_orgs"] if isinstance(o, str) and o.strip()]
```

and add these keyword args to the `MeetingSummary(...)` construction:

```python
        event_orgs=event_orgs,
        body_slug=state.body_slug,
        race_id=state.race_id,
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -q`
Expected: PASS (all — including the pre-existing scanner tests).

- [ ] **Step 5: Commit**

```bash
git add gui/library.py tests/test_gui_library.py
git commit -m "feat(gui): scanner reads event_orgs / body_slug / race_id"
```

---

## Task 3: `race_labels` batch helper in `gui/races.py`

**Files:**
- Modify: `gui/races.py`
- Test: `tests/test_gui_races.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_races.py  (append)
def test_race_labels_empty_and_no_db(monkeypatch):
    assert races.race_labels([]) == {}
    monkeypatch.setattr(races, "_db_url", lambda: None)
    assert races.race_labels(["uuid-1"]) == {}


def test_race_labels_swallows_db_errors(monkeypatch):
    monkeypatch.setattr(races, "_db_url", lambda: "postgres://fake")
    monkeypatch.setattr(races.psycopg2, "connect",
                        lambda url: (_ for _ in ()).throw(RuntimeError("down")))
    assert races.race_labels(["uuid-1"]) == {}
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_races.py -k race_labels -q`
Expected: FAIL — `race_labels` not defined.

- [ ] **Step 3: Implement**

Append to `gui/races.py`:

```python
def race_labels(race_ids) -> dict:
    """{race_id: display label} for the given ids, best-effort ({} on empty /
    no-DB / error). One query for the whole set — used to enrich the library."""
    ids = [str(r) for r in race_ids if r]
    if not ids:
        return {}
    url = _db_url()
    if not url:
        return {}
    try:
        conn = psycopg2.connect(url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT r.id, r.position_name,
                           EXTRACT(YEAR FROM e.election_date)::int AS yr
                    FROM essentials.races r
                    LEFT JOIN essentials.elections e ON e.id = r.election_id
                    WHERE r.id::text = ANY(%s)
                    """,
                    (ids,),
                )
                rows = cur.fetchall()
        finally:
            conn.close()
    except Exception:
        return {}
    return {str(rid): race_display(name, yr) for (rid, name, yr) in rows}
```

- [ ] **Step 4: Run to verify pass**

Run: `.venv/bin/python -m pytest tests/test_gui_races.py -q`
Expected: PASS (all).

- [ ] **Step 5: Commit**

```bash
git add gui/races.py tests/test_gui_races.py
git commit -m "feat(gui): race_labels batch helper for the library"
```

---

## Task 4: Library route + searchable template + filter JS + CSS

**Files:**
- Modify: `gui/app.py` (library route attaches race labels)
- Modify: `gui/templates/library.html` (filter bar, context subline, data attrs, bare link)
- Create: `gui/static/library.js`
- Modify: `gui/static/style.css` (toolbar)
- Test: `tests/test_gui_library.py`, and update `tests/test_gui_review.py::test_library_links_to_review`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_gui_library.py  (append)
def test_library_route_renders_filter_bar_and_context(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.races as races
    monkeypatch.setattr(races, "race_labels", lambda ids: {"uuid-r": "CA Governor · 2026"})
    mdir = tagged_meeting_dir("bloomington-common-council",
                              meeting_id="2026-02-10-council", completed_stage=4)
    st = mdir / "pipeline_state.json"
    data = json.loads(st.read_text()); data.update({"city": "Bloomington"}); st.write_text(json.dumps(data))
    body = TestClient(create_app()).get("/").text
    # filter bar
    assert 'id="lib-search"' in body and 'id="lib-kind"' in body and 'id="lib-status"' in body
    assert "library.js" in body
    # per-row data attributes for client-side filtering
    assert 'data-status="needs-review"' in body
    assert 'data-kind="council"' in body
    # context subline rendered
    assert "Bloomington Common Council" in body
    # row links to the bare workspace URL (stage-aware), NOT /review
    assert 'href="/meetings/2026-02-10-council"' in body


def test_library_route_attaches_race_label(tagged_meeting_dir, tmp_meetings_dir, monkeypatch):
    import gui.races as races
    seen = {}
    monkeypatch.setattr(races, "race_labels",
                        lambda ids: seen.setdefault("ids", set(ids)) or {"uuid-r": "TX Senate · 2026"})
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-01-interview", completed_stage=5)
    st = mdir / "pipeline_state.json"
    data = json.loads(st.read_text()); data.update({"race_id": "uuid-r", "event_kind": "news_clip"})
    st.write_text(json.dumps(data))
    body = TestClient(create_app()).get("/").text
    assert "uuid-r" in seen["ids"]           # route asked for the label
    assert "TX Senate · 2026" in body        # and rendered it


def test_library_js_filters_by_search_kind_status(tmp_meetings_dir):
    from pathlib import Path
    js = Path("gui/static/library.js").read_text()
    assert "lib-search" in js and "lib-kind" in js and "lib-status" in js
    assert "data-search" in js
```

Also update `tests/test_gui_review.py::test_library_links_to_review` (it asserts the old `/review` link). Replace its body with:

```python
def test_library_links_to_review(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    body = TestClient(create_app()).get("/").text
    # library now links to the bare workspace URL; the shell picks the tab by stage.
    assert 'href="/meetings/2026-02-04-council"' in body
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/bin/python -m pytest tests/test_gui_library.py -k "filter_bar or attaches_race or library_js" tests/test_gui_review.py -k "links_to_review" -q`
(run the two selections separately if `-k` across files is awkward)
Expected: FAIL — filter bar / data attrs / bare link / library.js absent.

- [ ] **Step 3: Attach race labels in the library route**

In `gui/app.py`'s `library` route, after `meetings = scan_meetings(...)`:

```python
        from gui import races
        race_ids = {m.race_id for m in meetings if m.race_id}
        labels = races.race_labels(race_ids) if race_ids else {}
        for m in meetings:
            if m.race_id:
                m.race_label = labels.get(m.race_id)
        from src.event_kinds import EVENT_KINDS
        return _templates.TemplateResponse(
            request, "library.html", {"meetings": meetings, "event_kinds": list(EVENT_KINDS)},
        )
```

(Replace the existing `return _templates.TemplateResponse(request, "library.html", {"meetings": meetings})` with the block above.)

- [ ] **Step 4: Rewrite `gui/templates/library.html`**

Replace the file. Preserves every existing column/badge/empty-state; adds the toolbar, the context subline, per-row `data-*`, and the bare row link.

```html
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>CouncilScribe — Meeting Library</title>
  <link rel="stylesheet" href="/static/style.css">
</head>
<body>
  <header><h1>Meeting Library</h1></header>
  <main>
    <div class="lib-toolbar">
      <input type="search" id="lib-search" class="lib-search" placeholder="🔍  Search name, city, org, id…" autocomplete="off">
      <select id="lib-kind" class="lib-sel">
        <option value="">Kind: all</option>
        {% for k in event_kinds %}<option value="{{ k }}">{{ k }}</option>{% endfor %}
      </select>
      <select id="lib-status" class="lib-sel">
        <option value="">Status: all</option>
        <option value="processing">Processing</option>
        <option value="needs-review">Needs review</option>
        <option value="ready">Ready to publish</option>
        <option value="live">Published/Live</option>
      </select>
      <span class="lib-spacer"></span>
      <a class="newlink" href="/new">+ New meeting</a>
      <form method="post" action="/cleanup-all" class="cleanup-all"
            onsubmit="return confirm('Compress audio and delete source video + WAV for ALL finalized meetings? This frees disk space and cannot be undone without reprocessing.');">
        <button type="submit" class="cleanup-btn">🧹 Clean up all finalized media</button>
      </form>
    </div>
    {% if meetings %}
    <table class="library" id="lib-table">
      <thead>
        <tr><th>Meeting</th><th>Date</th><th>Kind</th><th>Speakers</th><th>Length</th><th>Review</th><th>Status</th><th>Live</th></tr>
      </thead>
      <tbody>
        {% for m in meetings %}
        <tr data-kind="{{ m.event_kind or '' }}" data-status="{{ m.status_key }}"
            data-search="{{ [m.display_name, m.meeting_id, m.context_line, m.event_kind]|select|join(' ')|lower }}">
          <td class="name">
            {% if m.has_thumbnail %}
            <img class="thumb" src="/meetings/{{ m.meeting_id }}/thumbnail" alt="" loading="lazy">
            {% endif %}
            <div>
              <div><a class="mlink" href="/meetings/{{ m.meeting_id }}">{{ m.display_name }}</a></div>
              {% if m.context_line %}<div class="ctx">{{ m.context_line }}</div>{% endif %}
              <div class="mid">{{ m.meeting_id }}</div>
            </div>
          </td>
          <td>{{ m.date or "—" }}</td>
          <td>{{ m.event_kind or "—" }}</td>
          <td>{{ m.speakers_label }}</td>
          <td>{{ m.duration_label }}</td>
          <td>
            {% set level, text = m.gate_badge %}
            <span class="gate gate-{{ level }}">{{ text }}</span>
          </td>
          <td><span class="stage stage-{{ m.completed_stage }}">{{ m.stage_label }}</span></td>
          <td>
            {% set live = m.live_badge %}
            {% if live %}<span class="live-badge live-{{ live[0] }}">{{ live[1] }}</span>{% else %}<span class="live-unknown">—</span>{% endif %}
          </td>
        </tr>
        {% endfor %}
      </tbody>
    </table>
    <p class="lib-empty-filter" id="lib-empty-filter" hidden>No meetings match the current filter.</p>
    {% else %}
    <p class="empty">No meetings processed yet.</p>
    {% endif %}
  </main>
  <script src="/static/library.js"></script>
</body>
</html>
```

Note: the `data-search` uses Jinja's `select` filter to drop falsy fields before joining, then lowercases — a safe, empty-tolerant concatenation of the searchable text.

- [ ] **Step 5: Create `gui/static/library.js`**

```javascript
// Client-side library filtering: search text + Kind + Status. Instant, no reload.
(function () {
  const search = document.getElementById("lib-search");
  const kindSel = document.getElementById("lib-kind");
  const statusSel = document.getElementById("lib-status");
  const table = document.getElementById("lib-table");
  if (!table) return;  // empty library
  const rows = Array.from(table.querySelectorAll("tbody tr"));
  const emptyMsg = document.getElementById("lib-empty-filter");

  function apply() {
    const q = (search.value || "").trim().toLowerCase();
    const kind = kindSel.value;
    const status = statusSel.value;
    let visible = 0;
    rows.forEach((tr) => {
      const hay = tr.getAttribute("data-search") || "";
      const show = (!q || hay.includes(q))
        && (!kind || tr.getAttribute("data-kind") === kind)
        && (!status || tr.getAttribute("data-status") === status);
      tr.hidden = !show;
      if (show) visible++;
    });
    if (emptyMsg) emptyMsg.hidden = visible !== 0;
  }

  [search, kindSel, statusSel].forEach((el) => {
    el.addEventListener("input", apply);
    el.addEventListener("change", apply);
  });
})();
```

- [ ] **Step 6: Append CSS to `gui/static/style.css`**

```css
/* --- Library filter toolbar + context subline --- */
.lib-toolbar { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 1rem; flex-wrap: wrap; }
.lib-search { flex: 1; min-width: 12rem; padding: 0.4rem 0.6rem; border: 1px solid #ccc; border-radius: 0.5rem; font-size: 0.9rem; }
.lib-sel { padding: 0.4rem 0.6rem; border: 1px solid #ccc; border-radius: 0.5rem; font-size: 0.85rem; }
.lib-spacer { flex: 1; }
table.library .ctx { font-size: 0.8rem; color: #667; margin: 0.1rem 0; }
.lib-empty-filter { color: #888; padding: 1rem 0; }
.cleanup-all { margin: 0; }
```

- [ ] **Step 7: Run the tests**

Run:
```bash
.venv/bin/python -m pytest tests/test_gui_library.py tests/test_gui_review.py -q
```
Expected: PASS (all, including the updated `test_library_links_to_review`). Also verify the template parses and (if node present) the JS:
```bash
.venv/bin/python -c "from jinja2 import Environment, FileSystemLoader; Environment(loader=FileSystemLoader('gui/templates')).get_template('library.html')"
node --check gui/static/library.js
```

- [ ] **Step 8: Manual smoke check** (JS behavior tests can't cover)

Start the GUI (`.venv/bin/python -m gui`), open `/`. Verify: typing in the search box filters rows live; the Kind and Status dropdowns filter; a filter with no matches shows "No meetings match the current filter."; clicking a still-processing meeting opens the workspace on **Progress**, a finished one on **Review**.

- [ ] **Step 9: Commit**

```bash
git add gui/app.py gui/templates/library.html gui/static/library.js gui/static/style.css tests/test_gui_library.py tests/test_gui_review.py
git commit -m "feat(gui): searchable library with per-row context + stage-aware links"
```

---

## Self-Review (completed during planning)

- **Spec coverage (Section 5):** filter bar with search + Kind + Status (Task 4), client-side filtering (Task 4 `library.js`), per-row context subline from city/body/org/race (Tasks 1–4), best-effort race label (Task 3 + route attach, degrades to omitted when DB absent), stage-aware click-through (bare `/meetings/{id}` link + the workspace shell's existing default-tab logic). Status filter values processing/needs-review/ready/live (Task 1 `status_key` + Task 4 dropdown).
- **Regression safety:** the library.html rewrite preserves every assertion the existing `test_gui_library.py` route tests rely on — thumbnail src, gate badge text, duration, speaker count (`>3<`), `stage-{n}`/"Exported", the live-badge conditional (`live-badge` only when `is_live` is not None), the "No meetings processed yet" empty state, and `href="/new"`. The one intentional break — `test_library_links_to_review` — is updated in Task 4 to the new bare link.
- **DB-free scanner preserved:** `gui/library.py` stays pure-filesystem; race-label DB lookup happens in the route (mirroring how `live_slugs` is passed in), so the scanner's tests need no DB.
- **Type consistency:** `MeetingSummary.event_orgs/body_slug/race_id/race_label` (Task 1) are set by the scanner (Task 2) + route (Task 4) and read by `context_line`/`status_key` (Task 1) and the template (Task 4). `race_labels(ids) -> {id: label}` (Task 3) is called by the route (Task 4) with matching keys.
- **Placeholder scan:** none — every step has complete code.
```
