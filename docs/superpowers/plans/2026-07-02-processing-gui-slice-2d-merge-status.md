# Processing GUI — Slice 2d: Merge / Unidentified / Not-a-Speaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

The remaining corrective review actions:
- **Merge** — fold one speaker label into another (fixes over-segmentation, e.g. the inflated 39-speaker debates). Relabels segments, combines embeddings, drops the source.
- **Mark unidentified** — a distinct-but-unnamed person (gets a stable local handle).
- **Mark not-a-speaker** — music/pledge/station-ID (never a person).

All via `src/review.py` (`merge_speakers`, `mark_unidentified`, `mark_non_speaker`), persisted through the 2b path — **extended for merge**, which must also rewrite `diarization.json` + `embeddings.json` (not just `transcript_named.json`).

**Deferred:** 2e enrollment; create-local-person.

**Goal:** Merge two speakers, or mark a speaker unidentified / not-a-speaker, from the review page — persisted correctly (including diarization + embeddings for merge).

**Architecture:** Extend `persist_review` with an optional `embeddings` arg: when given (merge), it also rewrites `diarization.json` (from `meeting.segments`) and `embeddings.json` — mirroring `run_local._persist_after_review`. New `review_api` functions `apply_merge` / `apply_mark_unidentified` / `apply_mark_non_speaker`; a `_load_embeddings` helper; an `_atomic_write_text` helper (factored from 2b's transcript write, reused for all three files). `SpeakerCard` gains `speaker_status` for a badge. Three POST routes (Post/Redirect/Get). Template: a merge `<select>` of the other speakers + Unidentified / Not-a-speaker buttons. Builds on 2a/2b/2c.

**Tech Stack:** `src.review.merge_speakers/mark_unidentified/mark_non_speaker`; `numpy`. Tests: `pytest` + `TestClient`, fixtures via `Meeting(...).to_dict()` + a written `embeddings.json`.

---

### Task 1: `_atomic_write_text` + `persist_review(embeddings=...)` + `_load_embeddings`

**Files:**
- Modify: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
import numpy as np


def _write_embeddings(mdir, dim=8, labels=("SPEAKER_00", "SPEAKER_01")):
    import json as _json
    emb = {lbl: list(np.linspace(i, i + 1, dim)) for i, lbl in enumerate(labels)}
    (mdir / "embeddings.json").write_text(_json.dumps(emb))


def test_persist_review_with_embeddings_rewrites_diar_and_emb(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    _write_embeddings(mdir)
    from gui.review_api import _load_embeddings, persist_review
    meeting, meeting_dir, _ = _load_meeting_ctx("2026-02-04-council")
    embeddings = _load_embeddings(meeting_dir)
    # drop a label from embeddings to prove the file is rewritten from the arg
    embeddings.pop("SPEAKER_01", None)

    persist_review(meeting, meeting_dir, embeddings=embeddings)

    import json as _json
    emb_on_disk = _json.loads((meeting_dir / "embeddings.json").read_text())
    assert "SPEAKER_01" not in emb_on_disk         # rewritten from the passed dict
    assert (meeting_dir / "diarization.json").exists()  # written from meeting.segments


def test_persist_review_without_embeddings_leaves_emb_untouched(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    _write_embeddings(mdir)
    from gui.review_api import persist_review
    meeting, meeting_dir, _ = _load_meeting_ctx("2026-02-04-council")
    persist_review(meeting, meeting_dir)  # no embeddings -> rename/link path
    import json as _json
    emb = _json.loads((meeting_dir / "embeddings.json").read_text())
    assert set(emb) == {"SPEAKER_00", "SPEAKER_01"}  # untouched
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "with_embeddings_rewrites or without_embeddings_leaves or _load_embeddings" -v`
Expected: FAIL — `persist_review` takes no `embeddings` kwarg / `_load_embeddings` missing.

- [ ] **Step 3: Implement in `gui/review_api.py`**

Add `import os` and `import numpy as np` at module top if not present (embeddings need numpy; os for atomic write). Add helpers:

```python
def _atomic_write_text(path: Path, text: str) -> None:
    """Crash-safe write: temp file in the same dir, then os.replace."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _load_embeddings(meeting_dir: Path) -> dict:
    """embeddings.json -> {label: np.ndarray}, or {} if absent/malformed."""
    emb_path = meeting_dir / "embeddings.json"
    if not emb_path.exists():
        return {}
    try:
        return {k: np.array(v) for k, v in json.loads(emb_path.read_text()).items()}
    except (ValueError, OSError, TypeError, AttributeError):
        return {}
```

Refactor `persist_review` to take an optional `embeddings` and use the atomic helper:

```python
def persist_review(meeting, meeting_dir: Path, embeddings: dict | None = None) -> None:
    """Persist review edits. Always: sync segments + write transcript_named.json
    (authoritative). When embeddings is given (a merge relabeled segments +
    combined embeddings), also rewrite diarization.json + embeddings.json,
    mirroring run_local._persist_after_review. Export + gate are best-effort."""
    import logging

    for seg in meeting.segments:
        m = meeting.speakers.get(seg.speaker_label)
        if m and m.speaker_name:
            seg.speaker_name = m.speaker_name
            seg.confidence = m.confidence
            seg.id_method = m.id_method

    _atomic_write_text(
        meeting_dir / "transcript_named.json",
        json.dumps(meeting.to_dict(), indent=2),
    )

    if embeddings is not None:
        # Merge changed segment labels + embeddings — keep the caches consistent.
        try:
            _atomic_write_text(
                meeting_dir / "diarization.json",
                json.dumps([s.to_dict() for s in meeting.segments], indent=2),
            )
            emb_out = {k: (v.tolist() if hasattr(v, "tolist") else v) for k, v in embeddings.items()}
            _atomic_write_text(meeting_dir / "embeddings.json", json.dumps(emb_out))
        except Exception:
            logging.getLogger(__name__).warning(
                "Failed to rewrite diarization/embeddings for %s after merge", meeting_dir.name,
                exc_info=True,
            )

    try:
        from src.export import export_all
        export_all(meeting, meeting_dir / "exports")
    except Exception:
        pass

    try:
        from src import quality
        from src.checkpoint import PipelineState
        report = quality.evaluate_meeting(meeting)
        _atomic_write_text(meeting_dir / "quality.json", json.dumps(report, indent=2))
        state = PipelineState(meeting_dir)
        state.review_status = report.get("verdict")
        state.trusted_coverage = report.get("trusted_coverage")
        state.save()
    except Exception:
        logging.getLogger(__name__).warning(
            "Gate recompute failed for %s; library badge may be stale", meeting_dir.name,
            exc_info=True,
        )
```

(This replaces 2b's inline `os.replace` block with the `_atomic_write_text` helper — behavior identical; the 2b temp-file test still passes because the temp name is now `transcript_named.json.tmp` via `with_suffix` → confirm: `Path("transcript_named.json").with_suffix(".json.tmp")` yields `transcript_named.json.tmp`. If your Path stem handling differs, keep the explicit `meeting_dir / (name + ".tmp")` form the 2b test asserts.)

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "with_embeddings or without_embeddings or persist_review or temp_file" -v`
Expected: PASS (including 2b's `test_persist_review_leaves_no_temp_file`).

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): persist_review rewrites diarization+embeddings on merge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `apply_merge`

**Files:**
- Modify: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from gui.review_api import apply_merge


def test_apply_merge_folds_source_into_target(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    _write_embeddings(mdir)

    assert apply_merge("2026-02-04-council", "SPEAKER_01", "SPEAKER_00") is True

    import json as _json
    data = _json.loads((mdir / "transcript_named.json").read_text())
    # SPEAKER_01 is gone from speakers; its segments now belong to SPEAKER_00.
    assert "SPEAKER_01" not in data["speakers"]
    assert all(s["speaker_label"] != "SPEAKER_01" for s in data["segments"])
    emb = _json.loads((mdir / "embeddings.json").read_text())
    assert "SPEAKER_01" not in emb  # dropped from embeddings too


def test_apply_merge_guards(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_merge("2026-02-04-council", "SPEAKER_00", "SPEAKER_00") is False  # self-merge
    assert apply_merge("2026-02-04-council", "SPEAKER_99", "SPEAKER_00") is False  # unknown source
    assert apply_merge("2026-02-04-council", "SPEAKER_00", "SPEAKER_99") is False  # unknown target
    assert apply_merge("ghost", "SPEAKER_00", "SPEAKER_01") is False               # unknown meeting
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_merge" -v`
Expected: FAIL — `cannot import name 'apply_merge'`.

- [ ] **Step 3: Implement in `gui/review_api.py`**

```python
def apply_merge(meeting_id: str, source_label: str, target_label: str) -> bool:
    """Merge source speaker into target and persist (incl. diarization+embeddings).
    False on unsafe/unknown meeting, unknown/equal labels, or merge failure."""
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if source_label not in known or target_label not in known or source_label == target_label:
        return False
    embeddings = _load_embeddings(meeting_dir)
    from src import review
    try:
        review.merge_speakers(meeting.segments, embeddings, meeting.speakers, source_label, target_label)
    except ValueError:
        return False
    persist_review(meeting, meeting_dir, embeddings=embeddings)
    return True
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_merge" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): apply_merge folds speakers via review.merge_speakers

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: `apply_mark_unidentified` / `apply_mark_non_speaker` + `SpeakerCard.speaker_status`

**Files:**
- Modify: `gui/review_api.py`
- Modify: `gui/models.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from gui.review_api import apply_mark_non_speaker, apply_mark_unidentified


def test_apply_mark_unidentified(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_mark_unidentified("2026-02-04-council", "SPEAKER_01", "Man in blue") is True
    import json as _json
    sp = _json.loads((mdir / "transcript_named.json").read_text())["speakers"]["SPEAKER_01"]
    assert sp["speaker_status"] == "unidentified"
    assert sp["local_slug"]  # a stable handle was assigned
    assert sp["speaker_name"] == "Man in blue"


def test_apply_mark_non_speaker(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_mark_non_speaker("2026-02-04-council", "SPEAKER_01", "Pledge") is True
    import json as _json
    sp = _json.loads((mdir / "transcript_named.json").read_text())["speakers"]["SPEAKER_01"]
    assert sp["speaker_status"] == "non_speaker"


def test_mark_guards(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    assert apply_mark_unidentified("ghost", "SPEAKER_00", "") is False
    assert apply_mark_unidentified("2026-02-04-council", "SPEAKER_99", "") is False
    assert apply_mark_non_speaker("../x", "SPEAKER_00", "") is False


def test_speaker_card_exposes_status(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    apply_mark_non_speaker("2026-02-04-council", "SPEAKER_01", "Pledge")
    page = load_review_page("2026-02-04-council")
    card = [c for c in (page.confirmed + page.needs_attention) if c.label == "SPEAKER_01"][0]
    assert card.speaker_status == "non_speaker"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "mark_unidentified or mark_non_speaker or mark_guards or exposes_status" -v`
Expected: FAIL — functions/field missing.

- [ ] **Step 3: Implement**

In `gui/models.py`, add to `SpeakerCard` (after `politician_id`):

```python
    speaker_status: Optional[str] = None  # None | "unidentified" | "non_speaker"
```

In `gui/review_api.py`, populate it in `load_review_page`'s `SpeakerCard(...)` call:

```python
            speaker_status=getattr(mapping, "speaker_status", None) if mapping else None,
```

Add the two functions:

```python
def _mark(meeting_id: str, label: str, fn) -> bool:
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, _roster = ctx
    known = {s.speaker_label for s in meeting.segments} | set(meeting.speakers)
    if label not in known:
        return False
    fn(meeting, meeting_dir)
    persist_review(meeting, meeting_dir)
    return True


def apply_mark_unidentified(meeting_id: str, label: str, display_label: str = "") -> bool:
    from src import review

    def fn(meeting, meeting_dir):
        review.mark_unidentified(
            meeting.speakers, meeting.segments, label,
            meeting_dir.name, display_label=(display_label or "").strip() or None,
        )
    return _mark(meeting_id, label, fn)


def apply_mark_non_speaker(meeting_id: str, label: str, display_label: str = "") -> bool:
    from src import review

    def fn(meeting, meeting_dir):
        review.mark_non_speaker(
            meeting.speakers, meeting.segments, label,
            display_label=(display_label or "").strip() or None,
        )
    return _mark(meeting_id, label, fn)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "mark_unidentified or mark_non_speaker or mark_guards or exposes_status" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py gui/models.py tests/test_gui_review.py
git commit -m "feat(gui): apply_mark_unidentified/non_speaker + SpeakerCard status

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: POST routes (merge / unidentified / not-speaker)

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_merge_route(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir); _write_embeddings(mdir)
    client = TestClient(create_app())
    r = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/merge",
                    data={"target": "SPEAKER_00"}, follow_redirects=False)
    assert r.status_code == 303
    import json as _json
    assert "SPEAKER_01" not in _json.loads((mdir / "transcript_named.json").read_text())["speakers"]
    # self-merge / unknown target -> 404
    assert client.post("/meetings/2026-02-04-council/speakers/SPEAKER_00/merge",
                       data={"target": "SPEAKER_00"}, follow_redirects=False).status_code == 404


def test_unidentified_and_not_speaker_routes(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    client = TestClient(create_app())
    assert client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/unidentified",
                       data={"display_label": "Man in blue"}, follow_redirects=False).status_code == 303
    assert client.post("/meetings/2026-02-04-council/speakers/SPEAKER_00/not-speaker",
                       data={"display_label": ""}, follow_redirects=False).status_code == 303
    assert client.post("/meetings/ghost/speakers/SPEAKER_00/not-speaker",
                       data={}, follow_redirects=False).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "merge_route or unidentified_and_not_speaker" -v`
Expected: FAIL — routes don't exist.

- [ ] **Step 3: Add routes to `gui/app.py`** (after the link/unlink routes)

```python
    @app.post("/meetings/{meeting_id}/speakers/{label}/merge")
    def merge_speaker_route(meeting_id: str, label: str, target: str = Form("")):
        if not review_api.apply_merge(meeting_id, label, target.strip()):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/speakers/{label}/unidentified")
    def unidentified_route(meeting_id: str, label: str, display_label: str = Form("")):
        if not review_api.apply_mark_unidentified(meeting_id, label, display_label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)

    @app.post("/meetings/{meeting_id}/speakers/{label}/not-speaker")
    def not_speaker_route(meeting_id: str, label: str, display_label: str = Form("")):
        if not review_api.apply_mark_non_speaker(meeting_id, label, display_label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "merge_route or unidentified_and_not_speaker" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_review.py
git commit -m "feat(gui): merge / unidentified / not-speaker POST routes

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: UI — merge select, unidentified/not-speaker buttons, status badge

**Files:**
- Modify: `gui/models.py`
- Modify: `gui/templates/review.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing test**

Append to `tests/test_gui_review.py`:

```python
def test_review_page_has_merge_and_status_controls(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    # merge form + a target option referencing the OTHER speaker
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_01/merge"' in body
    assert 'value="SPEAKER_00"' in body  # a merge target option
    # unidentified + not-speaker forms
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_01/unidentified"' in body
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_01/not-speaker"' in body


def test_status_badge_renders(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    apply_mark_non_speaker("2026-02-04-council", "SPEAKER_01", "Pledge")
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    assert "not-a-speaker" in body or "non-speaker" in body  # a visible status badge
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "merge_and_status or status_badge" -v`
Expected: FAIL — controls/badge absent.

- [ ] **Step 3: Add `all_cards` to `ReviewPageData` in `gui/models.py`**

```python
    @property
    def all_cards(self) -> list["SpeakerCard"]:
        return self.needs_attention + self.confirmed
```

- [ ] **Step 4: Add controls + badge to `gui/templates/review.html`**

In the `card` macro, add a status badge near the top (after the `card-head`):

```html
      {% if c.speaker_status == "unidentified" %}<span class="statusbadge unident">unidentified</span>{% endif %}
      {% if c.speaker_status == "non_speaker" %}<span class="statusbadge nonspk">not-a-speaker</span>{% endif %}
```

In the `.actions` block, after the link controls, add merge + mark forms:

```html
        {% if page.all_cards|length > 1 %}
        <form method="post" action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/merge" class="merge">
          <select name="target">
            <option value="">Merge into…</option>
            {% for o in page.all_cards %}{% if o.label != c.label %}
            <option value="{{ o.label }}">{{ o.label }} — {{ o.display_name }}</option>
            {% endif %}{% endfor %}
          </select>
          <button type="submit">Merge</button>
        </form>
        {% endif %}
        <form method="post" action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/unidentified">
          <button type="submit" class="mark">Unidentified</button>
        </form>
        <form method="post" action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/not-speaker">
          <button type="submit" class="mark">Not a speaker</button>
        </form>
```

(The merge `<select>` default option has an empty value; `apply_merge` treats an empty/unknown target as False → 404. To avoid a 404 on an accidental empty submit, the `not target.strip()` returns False → 404; acceptable, but if you prefer a no-op redirect on empty target, guard it in the route like the rename route does. Keep the route as specced unless the manual smoke shows the 404 is annoying.)

- [ ] **Step 5: Append styles to `gui/static/style.css`**

```css
.statusbadge { font-size: 0.75rem; padding: 0.05rem 0.4rem; border-radius: 0.4rem; margin-left: 0.4rem; }
.statusbadge.unident { background: #eef; color: #445; }
.statusbadge.nonspk { background: #eee; color: #777; }
.actions select { font-size: 0.85rem; padding: 0.2rem; border: 1px solid #ccc; border-radius: 0.4rem; }
.actions .mark { padding: 0.2rem 0.6rem; border: 1px solid #bbb; border-radius: 0.4rem; background: #f6f6f8; cursor: pointer; font-size: 0.85rem; }
```

- [ ] **Step 6: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "merge_and_status or status_badge" -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add gui/models.py gui/templates/review.html gui/static/style.css tests/test_gui_review.py
git commit -m "feat(gui): merge dropdown, unidentified/not-speaker buttons, status badge

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 6: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1–2d), no regressions.

- [ ] **Step 2: Manual smoke**

Run: `.venv/bin/python -m gui`; open a debate meeting with many speakers → pick "Merge into…" on a fragment and select the real speaker → they combine (count drops); "Not a speaker" on a music/pledge label → badge appears. Ctrl-C to stop (no listener left on 8000).

---

## Self-Review

**Spec coverage:** Merge folding segments+embeddings (Task 2 `apply_merge` via `review.merge_speakers`) with correct persistence incl. diarization+embeddings (Task 1 `persist_review(embeddings=...)`) ✅ · mark unidentified (Task 3) ✅ · mark not-a-speaker (Task 3) ✅ · status badge (Task 3 field + Task 5 render) ✅ · merge target picker from other speakers (Task 5 `all_cards`) ✅ · Post/Redirect/Get routes with guards → 303/404 (Task 4) ✅ · atomic writes for all rewritten files (Task 1 `_atomic_write_text`) ✅ · scope: no enrollment/local-person ✅.

**Placeholder scan:** none.

**Type consistency:** `persist_review(meeting, meeting_dir, embeddings=None)` — the new optional arg is backward-compatible with all 2b/2c callers (rename/link pass no embeddings). `apply_merge`/`apply_mark_unidentified`/`apply_mark_non_speaker` return `bool`; routes map False→404. `_load_embeddings` returns `{label: np.ndarray}` matching `merge_speakers`'s expectation. `SpeakerCard.speaker_status` used in `review_api` load, template badge, tests. `ReviewPageData.all_cards` used by the merge `<select>`. `_atomic_write_text` reused for transcript (authoritative), diarization/embeddings (best-effort), and quality.json — 2b's `test_persist_review_leaves_no_temp_file` still holds (temp sibling removed by `os.replace`).
