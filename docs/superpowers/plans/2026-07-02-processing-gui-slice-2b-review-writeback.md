# Processing GUI — Slice 2b: Review Write-Back (Accept / Rename) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

Turns the read-only review page (Slice 2a) into an *actionable* one for the core case: **accept a guess** or **type a name**, persisted correctly to disk with the confidence gate recomputed. A named speaker gets confidence 1.0 (`review.rename_speaker`) so it moves from "Needs attention" to "Confirmed" on reload, and the library's review badge updates.

**Explicitly deferred (own slices, they build on this persistence path):**
- **2c** — politician linking (essentials `search_politicians` endpoint + pick/link/unlink UI).
- **2d** — merge / unidentified / not-a-speaker.
- **2e** — enrollment checkboxes.

**Goal:** Rename/accept a speaker in the browser; it persists to `transcript_named.json` and re-scores the gate.

**Architecture:** Server-rendered **Post/Redirect/Get** — a `POST` mutates via `src/review.py`, persists, and 303-redirects back to the review page (no client-state sync, no new JS). `gui/review_api.py` gains `_load_meeting_ctx` (load `Meeting` + roster), `persist_review` (sync segments → write `transcript_named.json` → best-effort re-export → best-effort gate recompute, mirroring `run_local`'s `--review` save + `_apply_gate`), and `apply_rename`. `gui/app.py` gains one `POST` route. `SpeakerCard` gains `accept_name` + link-display fields; the template gains an accept button + a rename form per card. Builds on Slices 1/1b/2a.

**Tech Stack:** FastAPI `Form`/`RedirectResponse`; `src.review.rename_speaker`, `src.quality.evaluate_meeting`, `src.roster.load_roster`, `src.export.export_all` (best-effort). Tests: `pytest` + `TestClient` (`follow_redirects=False`), fixtures via `Meeting(...).to_dict()`.

---

### Task 1: `persist_review` + meeting-context loader

**Files:**
- Modify: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from gui.review_api import _load_meeting_ctx, persist_review


def test_load_meeting_ctx_returns_none_for_unsafe_or_missing(tmp_meetings_dir):
    assert _load_meeting_ctx("../x") is None
    assert _load_meeting_ctx("ghost") is None


def test_persist_review_syncs_segments_and_writes_named(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    ctx = _load_meeting_ctx("2026-02-04-council")
    assert ctx is not None
    meeting, meeting_dir, _roster = ctx

    # Simulate a rename having happened on the mapping only.
    meeting.speakers["SPEAKER_01"].speaker_name = "Clerk Smith"
    meeting.speakers["SPEAKER_01"].confidence = 1.0
    meeting.speakers["SPEAKER_01"].id_method = "human_review"

    persist_review(meeting, meeting_dir)

    # transcript_named.json now carries the new name on BOTH mapping and segment.
    import json as _json
    data = _json.loads((meeting_dir / "transcript_named.json").read_text())
    assert data["speakers"]["SPEAKER_01"]["speaker_name"] == "Clerk Smith"
    seg01 = [s for s in data["segments"] if s["speaker_label"] == "SPEAKER_01"][0]
    assert seg01["speaker_name"] == "Clerk Smith"


def test_persist_review_recomputes_gate_quality_json(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    meeting, meeting_dir, _ = _load_meeting_ctx("2026-02-04-council")
    persist_review(meeting, meeting_dir)
    # Gate ran (best-effort): quality.json written and state mirrored.
    assert (meeting_dir / "quality.json").exists()
    from src.checkpoint import PipelineState
    assert PipelineState(meeting_dir).review_status in ("pass", "review", "failed")
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "load_meeting_ctx or persist_review" -v`
Expected: FAIL — `cannot import name '_load_meeting_ctx'`.

- [ ] **Step 3: Implement in `gui/review_api.py`**

Add near the top (module scope):

```python
def _load_roster_for(meeting_dir: Path):
    """Load the meeting's roster (by persisted body_slug) for name normalization,
    or None. Best-effort — never raises."""
    state_file = meeting_dir / "pipeline_state.json"
    body_slug = None
    if state_file.exists():
        try:
            body_slug = json.loads(state_file.read_text(encoding="utf-8")).get("body_slug")
        except (ValueError, OSError, AttributeError):
            body_slug = None
    if not body_slug:
        return None
    try:
        from src.roster import load_roster
        return load_roster(body_slug=body_slug)
    except Exception:
        return None


def _load_meeting_ctx(meeting_id: str):
    """(meeting, meeting_dir, roster) for a write-back, or None if unsafe/missing/malformed."""
    if not is_safe_meeting_id(meeting_id):
        return None
    meeting_dir = config.MEETINGS_DIR / meeting_id
    named = meeting_dir / "transcript_named.json"
    if not named.exists():
        return None
    try:
        meeting = Meeting.from_dict(json.loads(named.read_text(encoding="utf-8")))
    except (ValueError, OSError, KeyError, TypeError, AttributeError):
        return None
    return meeting, meeting_dir, _load_roster_for(meeting_dir)


def persist_review(meeting, meeting_dir: Path) -> None:
    """Persist review edits: sync segment fields from mappings, write
    transcript_named.json, then best-effort re-export + gate recompute.

    Mirrors run_local's --review save + _apply_gate. The transcript write is
    authoritative and must succeed; export and gate are best-effort so a quirk
    in either can't lose the user's correction."""
    for seg in meeting.segments:
        m = meeting.speakers.get(seg.speaker_label)
        if m and m.speaker_name:
            seg.speaker_name = m.speaker_name
            seg.confidence = m.confidence
            seg.id_method = m.id_method

    (meeting_dir / "transcript_named.json").write_text(
        json.dumps(meeting.to_dict(), indent=2), encoding="utf-8"
    )

    try:
        from src.export import export_all
        export_all(meeting, meeting_dir / "exports")
    except Exception:
        pass  # exports regenerate at publish time; never block a save

    try:
        from src import quality
        from src.checkpoint import PipelineState
        report = quality.evaluate_meeting(meeting)
        (meeting_dir / "quality.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        state = PipelineState(meeting_dir)
        state.review_status = report.get("verdict")
        state.trusted_coverage = report.get("trusted_coverage")
        state.save()
    except Exception:
        pass  # gate is best-effort; the transcript write above is the source of truth
```

`Meeting`, `config`, `is_safe_meeting_id`, `json`, `Path` are already imported in this module (Slice 2a). Confirm and add any missing.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "load_meeting_ctx or persist_review" -v`
Expected: PASS. (If `quality.evaluate_meeting` raises on the minimal fixture, the best-effort guard swallows it and `quality.json` won't exist — if the gate test then fails, that's real signal: report it rather than removing the assertion, and we'll enrich the fixture.)

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): persist_review + meeting-context loader for write-back

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `apply_rename` (mutate via review.py + persist)

**Files:**
- Modify: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from gui.review_api import apply_rename


def test_apply_rename_sets_name_and_confirms(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_rename("2026-02-04-council", "SPEAKER_01", "Clerk Smith") is True

    # Reload the page: SPEAKER_01 is now named + confident -> Confirmed group.
    page = load_review_page("2026-02-04-council")
    conf_labels = [c.label for c in page.confirmed]
    assert "SPEAKER_01" in conf_labels
    card = [c for c in page.confirmed if c.label == "SPEAKER_01"][0]
    assert card.name == "Clerk Smith"
    assert card.confidence == 1.0


def test_apply_rename_rejects_unknown_meeting_label_and_empty(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_rename("ghost", "SPEAKER_00", "X") is False          # no meeting
    assert apply_rename("2026-02-04-council", "SPEAKER_99", "X") is False  # unknown label
    assert apply_rename("2026-02-04-council", "SPEAKER_00", "   ") is False  # empty name
    assert apply_rename("../x", "SPEAKER_00", "X") is False           # unsafe id
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_rename" -v`
Expected: FAIL — `cannot import name 'apply_rename'`.

- [ ] **Step 3: Implement in `gui/review_api.py`**

```python
def apply_rename(meeting_id: str, label: str, new_name: str) -> bool:
    """Rename a speaker (human-authoritative) and persist. Returns False on
    unsafe/unknown meeting, unknown label, or empty name (caller maps to 404/no-op)."""
    name = (new_name or "").strip()
    if not name:
        return False
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, roster = ctx

    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False

    from src import review
    review.rename_speaker(meeting.speakers, meeting.segments, label, name, roster=roster)
    persist_review(meeting, meeting_dir)
    return True
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_rename" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): apply_rename mutates via review.rename_speaker + persists

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `POST` rename route (Post/Redirect/Get)

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_post_name_renames_and_redirects(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())

    resp = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/name",
                       data={"name": "Clerk Smith"}, follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/meetings/2026-02-04-council/review"

    # Follow-up GET shows the new name.
    body = client.get("/meetings/2026-02-04-council/review").text
    assert "Clerk Smith" in body


def test_post_name_empty_is_noop_redirect(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    resp = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/name",
                       data={"name": "   "}, follow_redirects=False)
    assert resp.status_code == 303  # back to the page, no change


def test_post_name_unknown_meeting_or_label_404(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    assert client.post("/meetings/ghost/speakers/SPEAKER_00/name",
                       data={"name": "X"}, follow_redirects=False).status_code == 404
    assert client.post("/meetings/2026-02-04-council/speakers/SPEAKER_99/name",
                       data={"name": "X"}, follow_redirects=False).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "post_name" -v`
Expected: FAIL — route doesn't exist (405/404 mismatch).

- [ ] **Step 3: Add the route to `gui/app.py`**

Add imports:

```python
from fastapi import Form
from fastapi.responses import RedirectResponse

from gui import review_api
```

Inside `create_app()`, after the media route:

```python
    @app.post("/meetings/{meeting_id}/speakers/{label}/name")
    def set_speaker_name(meeting_id: str, label: str, name: str = Form("")):
        redirect = RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
        if not name.strip():
            return redirect  # empty submission: no-op, back to the page
        if not review_api.apply_rename(meeting_id, label, name):
            raise HTTPException(status_code=404)  # unknown meeting / unsafe id / unknown label
        return redirect
```

(If `load_review_page`/`find_meeting_media` were imported as names in Slice 2a, keep that import and add `from gui import review_api` for the new functions, or import `apply_rename` explicitly — either is fine as long as it's consistent.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "post_name" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_review.py
git commit -m "feat(gui): POST rename route with Post/Redirect/Get

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: `accept_name` on `SpeakerCard` + rename UI

**Files:**
- Modify: `gui/models.py`
- Modify: `gui/templates/review.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_accept_name_prefers_current_name_then_hint():
    from gui.models import SpeakerCard
    c1 = SpeakerCard(label="S", name="Mayor Johnson", confidence=0.5, method=None,
                     minutes=1, seg_count=1)
    assert c1.accept_name == "Mayor Johnson"
    c2 = SpeakerCard(label="S", name=None, confidence=0.0, method=None,
                     minutes=1, seg_count=1, hints=[("Ada Lovelace", 0.7)])
    assert c2.accept_name == "Ada Lovelace"
    c3 = SpeakerCard(label="S", name=None, confidence=0.0, method=None, minutes=1, seg_count=1)
    assert c3.accept_name is None


def test_review_page_has_rename_form_and_accept_button(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    # A rename form posts to the name endpoint for the unnamed speaker.
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_01/name"' in body
    assert 'name="name"' in body
    # SPEAKER_00 is named at high conf -> confirmed, still editable (rename form present).
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_00/name"' in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "accept_name or rename_form" -v`
Expected: FAIL — `accept_name` missing / forms not in template.

- [ ] **Step 3: Add `accept_name` to `SpeakerCard` in `gui/models.py`**

```python
    @property
    def accept_name(self) -> Optional[str]:
        """Best one-click name to accept: the current name, else the top voice hint."""
        if self.name and self.name.strip() not in ("", _UNIDENTIFIED):
            return self.name.strip()
        if self.hints:
            return self.hints[0][0]
        return None
```

- [ ] **Step 4: Add the controls to `gui/templates/review.html`**

Inside the `card` macro, after the `.clips` block and before the closing `</div>`, add:

```html
      <div class="actions">
        {% if not c.is_confirmed and c.accept_name %}
        <form method="post" action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/name">
          <input type="hidden" name="name" value="{{ c.accept_name }}">
          <button type="submit" class="accept">✓ Accept {{ c.accept_name }}</button>
        </form>
        {% endif %}
        <form method="post" action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/name" class="rename">
          <input type="text" name="name" placeholder="Type a name…" autocomplete="off">
          <button type="submit">Save</button>
        </form>
      </div>
```

- [ ] **Step 5: Append styles to `gui/static/style.css`**

```css
.actions { display: flex; gap: 0.5rem; align-items: center; margin-top: 0.5rem; flex-wrap: wrap; }
.actions form { display: inline-flex; gap: 0.3rem; margin: 0; }
.actions .accept { background: #e6f5ea; border: 1px solid #2ea56a; color: #1b7a3d; border-radius: 0.4rem; padding: 0.2rem 0.6rem; cursor: pointer; font-size: 0.85rem; }
.actions input[type=text] { padding: 0.2rem 0.4rem; border: 1px solid #ccc; border-radius: 0.4rem; font-size: 0.85rem; }
.actions .rename button { padding: 0.2rem 0.6rem; border: 1px solid #bbb; border-radius: 0.4rem; background: #f6f6f8; cursor: pointer; font-size: 0.85rem; }
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "accept_name or rename_form" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/models.py gui/templates/review.html gui/static/style.css tests/test_gui_review.py
git commit -m "feat(gui): accept button + rename form on review cards

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1, 1b, 2a, 2b), no regressions.

- [ ] **Step 2: Manual smoke**

Run: `.venv/bin/python -m gui`; open a meeting with an unnamed/low-confidence speaker → click "✓ Accept …" or type a name + Save → the page reloads, the speaker shows the name and jumps to **Confirmed**; the Library's review badge reflects the recomputed gate. Ctrl-C to stop (no listener left on 8000).

---

## Self-Review

**Spec coverage:** Accept (Task 4 accept button posts `accept_name`; Task 2 `apply_rename`) ✅ · Rename by typing (Task 4 form + Task 3 route + Task 2) ✅ · correct persistence mirroring `--review` save (Task 1 `persist_review`: segment sync + `transcript_named.json` + best-effort export) ✅ · gate recompute so the library badge updates (Task 1, mirrors `_apply_gate`) ✅ · confirmed/needs regrouping on reload (rename sets conf 1.0 → `is_confirmed`) ✅ · Post/Redirect/Get, no client-state (Task 3) ✅ · guards: unsafe id / unknown meeting / unknown label → 404, empty → no-op (Task 2/3) ✅ · politician linking correctly deferred to 2c ✅.

**Placeholder scan:** none — complete code + exact commands throughout.

**Type consistency:** `apply_rename(meeting_id, label, new_name) -> bool` used identically in `review_api` and the route; `persist_review(meeting, meeting_dir)` and `_load_meeting_ctx` signatures consistent across tasks/tests. `accept_name` property added to the same `SpeakerCard` from Slice 2a. Route redirects to `/meetings/{id}/review` (matches Slice 2a's GET). Malformed-JSON guard in `_load_meeting_ctx` uses the same broadened exception tuple established by the Slice 2a fix.
