# Processing GUI — Slice 3d: Candidate (ID-only) Linking + Review↔Run Navigation

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Two bugs found in real use:

1. **Candidates can't be linked.** Essentials candidates have a `politician_id` but **no `politician_slug`** (slug is null for ~all candidates). But (a) the search widget filters out slug-less results (added in Slice 2c) and (b) `apply_link` requires a slug — so no candidate can be linked by hand, which is exactly who appears in debates/forums. **Fix: link by id-or-slug; stop hiding slug-less results.**
2. **No way back to a meeting's run/progress from review**, and the run page 404s for meetings not launched via the GUI. **Fix: `run_status` works for any meeting with `pipeline_state.json`; add a review→run link.**

GUI-only (no pipeline/schema change). **Do not start any server or touch port 8000** — a real run may be in progress; verify via `pytest`/`TestClient` only.

**Goal:** Search finds candidates and links them (by id); from a review page you can jump to that meeting's processing log/progress.

---

### Task 1: `apply_link` + link route accept id-or-slug

**Files:**
- Modify: `gui/review_api.py`
- Modify: `gui/app.py`
- Test: `tests/test_gui_review.py` (modify the existing guard test + add id-only tests)

- [ ] **Step 1: Update/add tests in `tests/test_gui_review.py`**

Find `test_apply_link_guards` and change the empty-slug assertion (empty slug is now OK if an id is present); add id-only coverage. Replace the empty-slug line and add tests:

```python
def test_apply_link_by_id_only(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    # candidate: no slug, only an essentials id
    assert apply_link("2026-02-04-council", "SPEAKER_01", "", "uuid-becerra") is True
    page = load_review_page("2026-02-04-council")
    card = [c for c in (page.confirmed + page.needs_attention) if c.label == "SPEAKER_01"][0]
    assert card.politician_id == "uuid-becerra"
    assert card.is_linked is True          # linked by id, even without a slug


def test_apply_link_requires_slug_or_id(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_link("2026-02-04-council", "SPEAKER_00", "", "") is False   # neither → no-op
    assert apply_link("2026-02-04-council", "SPEAKER_99", "s", "i") is False  # unknown label
    assert apply_link("ghost", "SPEAKER_00", "s", "i") is False              # unknown meeting
```

If `test_apply_link_guards` still contains `assert apply_link("2026-02-04-council", "SPEAKER_00", "", "i") is False`, delete that line (it's now `True`, covered by the new id-only test).

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_link_by_id_only or requires_slug_or_id" -v`
Expected: FAIL — `apply_link` currently returns False for empty slug.

- [ ] **Step 3: Fix `apply_link` in `gui/review_api.py`**

```python
def apply_link(meeting_id: str, label: str, politician_slug: str, politician_id: str) -> bool:
    """Link a speaker to an essentials politician/candidate and persist. Accepts a
    slug OR an id (candidates have an id but no slug). False on unsafe/unknown
    meeting or label, or when BOTH slug and id are empty."""
    slug = (politician_slug or "").strip()
    pid = (politician_id or "").strip()
    if not slug and not pid:
        return False
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    from src import review
    review.link_speaker(meeting.speakers, label, slug or None, pid or None)
    persist_review(meeting, meeting_dir)
    return True
```

- [ ] **Step 4: Fix the link route guard in `gui/app.py`**

Change the empty check in `link_speaker_route` from slug-only to slug-and-id:

```python
        if not politician_slug.strip() and not politician_id.strip():
            return redirect  # nothing to link
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_link or link_and_unlink" -v`
Expected: PASS (the id-only tests + the existing link/unlink route tests).

- [ ] **Step 6: Commit**

```bash
git add gui/review_api.py gui/app.py tests/test_gui_review.py
git commit -m "fix(gui): link speakers by politician_id when there's no slug (candidates)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `is_linked` by id; show slug-or-id; stop hiding slug-less results

**Files:**
- Modify: `gui/models.py`
- Modify: `gui/templates/review.html`
- Modify: `gui/static/review.js`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_review.py`:

```python
def test_speaker_card_is_linked_by_id_only():
    from gui.models import SpeakerCard
    c = SpeakerCard(label="S", name="Xavier Becerra", confidence=1.0, method="human_review",
                    minutes=2, seg_count=3, politician_slug=None, politician_id="uuid-b")
    assert c.is_linked is True
    assert SpeakerCard(label="S", name=None, confidence=0, method=None,
                       minutes=0, seg_count=0).is_linked is False


def test_review_page_shows_link_for_id_only_speaker(tagged_meeting_dir, tmp_meetings_dir):
    import json as _json
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    data = _json.loads((mdir / "transcript_named.json").read_text())
    data["speakers"]["SPEAKER_00"]["politician_id"] = "uuid-b"
    data["speakers"]["SPEAKER_00"]["politician_slug"] = None
    (mdir / "transcript_named.json").write_text(_json.dumps(data))
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    # linked state shows (unlink form present) even though there's no slug
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_00/unlink"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "is_linked_by_id_only or shows_link_for_id_only" -v`
Expected: FAIL — `is_linked` only checks slug.

- [ ] **Step 3: Fix `is_linked` in `gui/models.py`**

```python
    @property
    def is_linked(self) -> bool:
        return bool(self.politician_slug or self.politician_id)
```

- [ ] **Step 4: Update the linked display in `gui/templates/review.html`**

The linked line currently shows only the slug (blank for candidates). Show slug if present, else the id:

```html
      {% if c.is_linked %}
      <div class="linked">🔗 linked: <span class="pslug">{{ c.politician_slug or c.politician_id }}</span></div>
      {% endif %}
```

- [ ] **Step 5: Remove the slug-less filter in `gui/static/review.js`**

Change the results handling so slug-less (candidate) results are shown and clickable (they link by id via the hidden `politician_id` field, which is already sent):

```javascript
      const results_list = data.results || [];
      if (data.error || !results_list.length) {
        results.innerHTML = '<div class="link-msg">' + (data.error ? "search unavailable" : "no matches") + "</div>";
        return;
      }
      let action = widget.getAttribute("data-link-action") || "";
      if (!action.endsWith(LINK_ROUTE_SUFFIX)) action += LINK_ROUTE_SUFFIX;
      results.innerHTML = results_list.map((r) => {
```

(i.e. replace the `const linkable = (data.results || []).filter((r) => r.politician_slug);` line and the subsequent `linkable`/`!linkable.length` references with `results_list` and no slug filter. The result form already includes both `politician_slug` and `politician_id` hidden inputs — keep both and keep the `esc()` escaping.)

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "is_linked_by_id_only or shows_link_for_id_only" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/models.py gui/templates/review.html gui/static/review.js tests/test_gui_review.py
git commit -m "fix(gui): treat id-only links as linked; show candidates in search results

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Run page works for any meeting + review→run link

**Files:**
- Modify: `gui/runner.py`
- Modify: `gui/templates/review.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_runner.py` + `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_runner.py`:

```python
def test_run_status_works_without_sidecar(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    # a meeting with pipeline_state but NO gui_run.json sidecar (e.g. CLI-processed
    # or reviewed) still returns a status snapshot, not None.
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    st = runner.run_status("2026-02-04-council")
    assert st is not None
    assert st["completed_stage"] == 5
    assert st["running"] is False
    # truly-unknown meeting is still None
    assert runner.run_status("no-such-meeting") is None
```

Append to `tests/test_gui_review.py`:

```python
def test_review_page_links_to_run(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    assert 'href="/meetings/2026-02-04-council/run"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "without_sidecar" tests/test_gui_review.py -k "links_to_run" -v`
Expected: FAIL — `run_status` returns None without a sidecar; review page has no run link.

- [ ] **Step 3: Relax `run_status` in `gui/runner.py`**

Change the early-return guard so a meeting with `pipeline_state.json` qualifies:

```python
    meeting_dir = config.MEETINGS_DIR / meeting_id
    has_state = (meeting_dir / "pipeline_state.json").exists()
    if not has_state and not (meeting_dir / _SIDE_NAME).exists() and meeting_id not in _RUNS:
        return None
```

(The rest is unchanged: `completed_stage` from `pipeline_state.json`, running/exit from the registry + pid fallback, `log_tail` returns "" when there's no `gui_run.log`.)

- [ ] **Step 4: Add the review→run link in `gui/templates/review.html`**

In the `<header>`, next to the existing back link:

```html
    <a class="back" href="/">← Library</a>
    <a class="back runlink" href="/meetings/{{ page.meeting_id }}/run">Processing log &amp; progress →</a>
```

Append to `gui/static/style.css`:

```css
a.runlink { margin-left: 1rem; }
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_runner.py -k "without_sidecar" tests/test_gui_review.py -k "links_to_run" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/runner.py gui/templates/review.html gui/static/style.css tests/test_gui_runner.py tests/test_gui_review.py
git commit -m "feat(gui): run/progress page works for any meeting + review->run link

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Full-suite regression

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (no regressions). Do NOT start a server or launch a run.

---

## Self-Review

**Spec coverage:** link candidates by id (Task 1 `apply_link` slug-or-id + route) ✅ · is_linked by id + show id when no slug + unhide slug-less search results (Task 2) ✅ · run page works for any meeting with pipeline_state (Task 3 `run_status`) ✅ · review→run navigation link (Task 3) ✅ · GUI-only, no server started ✅.

**Placeholder scan:** none.

**Type consistency:** `apply_link(meeting_id, label, slug, id) -> bool` now guards on `not slug and not pid`; route mirrors it. `SpeakerCard.is_linked = bool(slug or id)`. `run_status` returns a dict for any meeting with `pipeline_state.json`; None only when truly absent. review.js result forms still send both `politician_slug`+`politician_id` (escaped); removing the filter changes only which results render. review→run link uses `page.meeting_id`, matching the `/meetings/{id}/run` route from 3a.
