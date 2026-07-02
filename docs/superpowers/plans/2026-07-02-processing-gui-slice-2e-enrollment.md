# Processing GUI — Slice 2e: Voice Enrollment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

## Scope

The recognition flywheel, and the last review sub-slice. On a named speaker's card, a **"Save this voice for future meetings"** action enrolls that speaker's embedding into the voice-profile DB (`src/enroll`), so the next meeting auto-identifies them. Defaulted-by-speech-length per the design: enough speech → prominent button; a very short sample → shown but flagged "short sample". Already-enrolled-from-this-meeting → shown as saved (idempotent, never double-counts).

**Goal:** Enroll a reviewed speaker's voice from the review page, idempotently, with clear per-speaker state.

**Architecture:** Enrollment is a *separate* concern from the transcript write — it only mutates the profile DB, so it does **not** go through `persist_review`. `apply_enroll` loads the meeting + embeddings + roster, and calls `src.enroll._enroll_mapping(db, mapping, embedding, meeting_dir.name, seg_count, roster)` then `save_profiles(db)`. **Idempotency is enforced in `apply_enroll`**, not in `enroll`: since `_enroll_one` appends a new `EmbeddingRecord` on every call (only `meetings_seen` dedupes), `apply_enroll` skips when this meeting already contributed to the resolved profile key — preventing centroid-biasing duplicates from repeat clicks. `SpeakerCard` gains `is_enrollable` / `is_enrolled` / `thin_sample`, computed in `load_review_page` from the already-loaded `profile_db` + embeddings + roster (keys resolved identically to `apply_enroll` so display and write agree). One POST route. Builds on 2a–2d. Enrollment is keyed on the meeting **directory name** (calibration leave-one-out depends on it).

**Tech Stack:** `src.enroll` (`_enroll_mapping`, `resolve_mapping_enrollment`, `load_profiles`, `save_profiles`); `numpy`. Tests rely on conftest's autouse `_isolate_voice_profiles` fixture (points `src.enroll._db_path` at a tmp dir), so enrollment writes to an isolated DB.

---

### Task 1: `SpeakerCard` enroll fields + compute in `load_review_page`

**Files:**
- Modify: `gui/models.py`
- Modify: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from gui.models import ENROLL_MIN_SPEECH_SECONDS


def test_enroll_min_speech_threshold():
    assert ENROLL_MIN_SPEECH_SECONDS == 30.0


def test_load_review_page_marks_enrollable_and_thin(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    _write_embeddings(mdir)  # gives SPEAKER_00 + SPEAKER_01 embeddings
    page = load_review_page("2026-02-04-council")
    cards = {c.label: c for c in (page.confirmed + page.needs_attention)}
    # SPEAKER_00: named, has embedding, 60s of speech (from _write_meeting) -> enrollable, not thin, not yet enrolled
    assert cards["SPEAKER_00"].is_enrollable is True
    assert cards["SPEAKER_00"].is_enrolled is False
    # SPEAKER_01 in _write_meeting speaks 80..95 = 15s -> thin; but unnamed -> not enrollable
    assert cards["SPEAKER_01"].is_enrollable is False
    assert cards["SPEAKER_01"].thin_sample is True


def test_load_review_page_enrollable_false_without_embedding(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)  # no embeddings.json
    page = load_review_page("2026-02-04-council")
    card = [c for c in page.confirmed if c.label == "SPEAKER_00"][0]
    assert card.is_enrollable is False  # no embedding to enroll
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "enroll_min_speech or marks_enrollable or enrollable_false" -v`
Expected: FAIL — `ENROLL_MIN_SPEECH_SECONDS` / card fields missing.

- [ ] **Step 3: Add to `gui/models.py`**

Near `CONFIDENT_THRESHOLD`:

```python
# Below this much confirmed speech, a voice sample is too thin to enroll cleanly
# (guards against the profile pollution calibration found). Still allowed, but flagged.
ENROLL_MIN_SPEECH_SECONDS = 30.0
```

Add three fields to `SpeakerCard` (after `speaker_status`):

```python
    is_enrollable: bool = False   # named, not a non-speaker, has an embedding
    is_enrolled: bool = False     # this meeting already contributed to the voice profile
    thin_sample: bool = False     # < ENROLL_MIN_SPEECH_SECONDS of speech
```

- [ ] **Step 4: Compute them in `gui/review_api.py` `load_review_page`**

At the top of `load_review_page`, after `profile_db = load_profiles()`, also load the roster so enrollment keys resolve identically to `apply_enroll`:

```python
    roster = _load_roster_for(meeting_dir)
```

Add an import near the other `src.enroll` import:

```python
    from src.enroll import resolve_mapping_enrollment
```

In the per-view loop, compute the flags and pass them to `SpeakerCard(...)`:

```python
        mapping = meeting.speakers.get(v.label)
        has_emb = v.label in embeddings
        named = bool(mapping and mapping.speaker_name and mapping.speaker_name.strip())
        not_nonspeaker = not (mapping and getattr(mapping, "speaker_status", None) == "non_speaker")
        is_enrollable = named and not_nonspeaker and has_emb
        is_enrolled = False
        if is_enrollable:
            key, _slug, _id = resolve_mapping_enrollment(mapping, roster)
            prof = profile_db.profiles.get(key)
            is_enrolled = prof is not None and meeting_dir.name in getattr(prof, "meetings_seen", [])
        card = SpeakerCard(
            ...
            speaker_status=getattr(mapping, "speaker_status", None) if mapping else None,
            is_enrollable=is_enrollable,
            is_enrolled=is_enrolled,
            thin_sample=v.total_speech_seconds < ENROLL_MIN_SPEECH_SECONDS,
        )
```

Add `ENROLL_MIN_SPEECH_SECONDS` to the `from gui.models import ...` line.

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "enroll_min_speech or marks_enrollable or enrollable_false" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/models.py gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): SpeakerCard enroll flags (enrollable/enrolled/thin)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `apply_enroll` (idempotent)

**Files:**
- Modify: `gui/review_api.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
from gui.review_api import apply_enroll


def test_apply_enroll_writes_profile_and_is_idempotent(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    _write_embeddings(mdir)
    from src.enroll import load_profiles, resolve_mapping_enrollment

    assert apply_enroll("2026-02-04-council", "SPEAKER_00") is True
    db = load_profiles()
    # find SPEAKER_00's profile
    from src.models import SpeakerMapping
    import json as _json
    sp = _json.loads((mdir / "transcript_named.json").read_text())["speakers"]["SPEAKER_00"]
    key, _, _ = resolve_mapping_enrollment(SpeakerMapping(**{k: sp.get(k) for k in
        ("speaker_label","speaker_name","confidence","id_method","politician_slug","politician_id","local_slug","local_role","speaker_status")}))
    assert key in db.profiles
    assert "2026-02-04-council" in db.profiles[key].meetings_seen
    n_records = len(db.profiles[key].embeddings)

    # Second enroll from the SAME meeting must be a no-op (no duplicate record).
    assert apply_enroll("2026-02-04-council", "SPEAKER_00") is True
    db2 = load_profiles()
    assert len(db2.profiles[key].embeddings) == n_records  # unchanged


def test_apply_enroll_guards(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    _write_embeddings(mdir)
    assert apply_enroll("ghost", "SPEAKER_00") is False              # unknown meeting
    assert apply_enroll("2026-02-04-council", "SPEAKER_99") is False  # unknown label
    assert apply_enroll("2026-02-04-council", "SPEAKER_01") is False  # SPEAKER_01 is unnamed
    assert apply_enroll("../x", "SPEAKER_00") is False               # unsafe id


def test_apply_enroll_skips_non_speaker(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir)
    _write_embeddings(mdir)
    apply_mark_non_speaker("2026-02-04-council", "SPEAKER_00", "Music")
    assert apply_enroll("2026-02-04-council", "SPEAKER_00") is False  # non-speaker not enrollable
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_enroll" -v`
Expected: FAIL — `cannot import name 'apply_enroll'`.

- [ ] **Step 3: Implement in `gui/review_api.py`**

```python
def apply_enroll(meeting_id: str, label: str) -> bool:
    """Enroll a named speaker's voice into the profile DB (idempotent per meeting).
    False on unsafe/unknown meeting, unknown label, no name, non-speaker, or no embedding."""
    ctx = _load_meeting_ctx(meeting_id)
    if ctx is None:
        return False
    meeting, meeting_dir, roster = ctx
    mapping = meeting.speakers.get(label)
    if mapping is None or not (mapping.speaker_name and mapping.speaker_name.strip()):
        return False
    if getattr(mapping, "speaker_status", None) == "non_speaker":
        return False
    embeddings = _load_embeddings(meeting_dir)
    emb = embeddings.get(label)
    if emb is None:
        return False

    from src.enroll import _enroll_mapping, load_profiles, resolve_mapping_enrollment, save_profiles
    db = load_profiles()
    key, _slug, _id = resolve_mapping_enrollment(mapping, roster)
    prof = db.profiles.get(key)
    if prof is not None and meeting_dir.name in getattr(prof, "meetings_seen", []):
        return True  # already enrolled from this meeting — idempotent no-op (no duplicate record)

    seg_count = sum(1 for s in meeting.segments if s.speaker_label == label)
    _enroll_mapping(db, mapping, emb, meeting_dir.name, seg_count, roster=roster)
    save_profiles(db)
    return True
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "apply_enroll" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/review_api.py tests/test_gui_review.py
git commit -m "feat(gui): apply_enroll saves a voice profile, idempotent per meeting

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: POST enroll route

**Files:**
- Modify: `gui/app.py`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_enroll_route(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir); _write_embeddings(mdir)
    client = TestClient(create_app())
    r = client.post("/meetings/2026-02-04-council/speakers/SPEAKER_00/enroll", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/meetings/2026-02-04-council/review"
    from src.enroll import load_profiles
    assert load_profiles().profiles  # a profile now exists

    # unknown / non-enrollable -> 404
    assert client.post("/meetings/2026-02-04-council/speakers/SPEAKER_01/enroll",
                       follow_redirects=False).status_code == 404  # unnamed
    assert client.post("/meetings/ghost/speakers/SPEAKER_00/enroll",
                       follow_redirects=False).status_code == 404
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "enroll_route" -v`
Expected: FAIL — route missing.

- [ ] **Step 3: Add the route to `gui/app.py`** (after the not-speaker route)

```python
    @app.post("/meetings/{meeting_id}/speakers/{label}/enroll")
    def enroll_route(meeting_id: str, label: str):
        if not review_api.apply_enroll(meeting_id, label):
            raise HTTPException(status_code=404)
        return RedirectResponse(url=f"/meetings/{meeting_id}/review", status_code=303)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "enroll_route" -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add gui/app.py tests/test_gui_review.py
git commit -m "feat(gui): POST enroll route

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Enroll UI — button / saved state / thin-sample flag

**Files:**
- Modify: `gui/templates/review.html`
- Modify: `gui/static/style.css`
- Test: `tests/test_gui_review.py` (append)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_gui_review.py`:

```python
def test_review_page_shows_enroll_button(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir); _write_embeddings(mdir)
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    assert 'action="/meetings/2026-02-04-council/speakers/SPEAKER_00/enroll"' in body
    assert "Save this voice" in body


def test_review_page_shows_saved_state_after_enroll(tagged_meeting_dir, tmp_meetings_dir):
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    _write_meeting(mdir); _write_embeddings(mdir)
    apply_enroll("2026-02-04-council", "SPEAKER_00")
    body = TestClient(create_app()).get("/meetings/2026-02-04-council/review").text
    assert "✓ voice saved" in body
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "enroll_button or saved_state" -v`
Expected: FAIL — enroll UI absent.

- [ ] **Step 3: Add the enroll control to `gui/templates/review.html`**

In the `.actions` block (after the mark buttons), add:

```html
        {% if c.is_enrollable %}
          {% if c.is_enrolled %}
          <span class="voice-saved">✓ voice saved</span>
          {% else %}
          <form method="post" action="/meetings/{{ page.meeting_id }}/speakers/{{ c.label }}/enroll">
            <button type="submit" class="enroll">Save this voice for future meetings</button>
            {% if c.thin_sample %}<span class="thin">⚠ short sample</span>{% endif %}
          </form>
          {% endif %}
        {% endif %}
```

- [ ] **Step 4: Append styles to `gui/static/style.css`**

```css
button.enroll { padding: 0.2rem 0.6rem; border: 1px solid #7aa0d0; border-radius: 0.4rem; background: #eef4fc; color: #24507f; cursor: pointer; font-size: 0.85rem; }
.voice-saved { font-size: 0.85rem; color: #1b7a3d; }
.thin { font-size: 0.78rem; color: #9a6a00; margin-left: 0.3rem; }
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/pytest tests/test_gui_review.py -k "enroll_button or saved_state" -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add gui/templates/review.html gui/static/style.css tests/test_gui_review.py
git commit -m "feat(gui): enroll button, saved state, and thin-sample flag

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 5: Full-suite regression + manual smoke

- [ ] **Step 1: Run the whole suite**

Run: `.venv/bin/pytest -q`
Expected: all pass (Slices 1–2e), no regressions.

- [ ] **Step 2: Manual smoke** (read-only unless you choose to enroll)

Run: `.venv/bin/python -m gui`; open a meeting → named speakers show "Save this voice for future meetings" (short ones flagged "⚠ short sample"); non-speakers/unnamed show none. NOTE: clicking enroll writes to the REAL profile DB — only do so intentionally. Ctrl-C to stop.

---

## Self-Review

**Spec coverage:** Save-voice per named speaker (Task 2 `apply_enroll` via `_enroll_mapping` + Task 3 route + Task 4 button) ✅ · defaulted/flagged by speech length (Task 1 `thin_sample` + Task 4 flag) ✅ · idempotent per meeting — no duplicate embedding records (Task 2 `meetings_seen` guard) ✅ · saved-state feedback (Task 1 `is_enrolled` + Task 4) ✅ · non-speakers/unnamed not enrollable (Task 1/2 guards) ✅ · keyed on meeting directory name (Task 2) ✅ · profiles isolated in tests (conftest autouse) ✅ · enrollment separate from transcript write — not routed through persist_review ✅.

**Placeholder scan:** none.

**Type consistency:** `apply_enroll(meeting_id, label) -> bool`; route maps False→404, success→303 to `/meetings/{id}/review`. `SpeakerCard.is_enrollable/is_enrolled/thin_sample` computed in `load_review_page` and read in the template. Key resolution uses `resolve_mapping_enrollment(mapping, roster)` in BOTH `load_review_page` (display) and `apply_enroll` (write) with the same `roster` (from `_load_roster_for`), so `is_enrolled` display matches what `apply_enroll` wrote. Reuses `_load_meeting_ctx`/`_load_embeddings`/`is_safe_meeting_id`. `ENROLL_MIN_SPEECH_SECONDS` defined in `models.py`, used in `review_api`.
