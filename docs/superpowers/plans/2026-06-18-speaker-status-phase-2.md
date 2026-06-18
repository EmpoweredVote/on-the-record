# Speaker Status — Phase 2: Returning-unknown recognition, promote-to-person, calibration treatment

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a recurring unidentified speaker be recognized across meetings (confirm-only), let a handle be promoted to a real person once known, and make calibration treat unidentified/non-speaker correctly.

**Architecture:** Builds on Phase 1 (`speaker_status`, `local:<handle>` enrollment keys). Recurrence reuses the existing soft-match hint path — an unidentified handle's profile already surfaces as a soft hint; we carry its key so the reviewer can confirm-link to the *same* handle (never auto-merge). Promotion reuses `merge_profiles`. Calibration counts an unidentified handle toward coverage but never scores it for named-identity precision.

**Tech Stack:** Python 3, pytest. Tests run with `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-18-unidentified-and-nonspeaker-status-design.md`
**Depends on:** `2026-06-18-speaker-status-phase-1.md` (must be merged first).

---

## File Structure

- `src/review.py` — `link_to_unidentified_handle` helper; surface handle key in review state.
- `src/identify.py` — soft hints expose the profile key (so an unidentified handle is linkable, not just displayable).
- `src/enroll.py` — `promote_unidentified_handle`.
- `bench/calibrate_gate.py` — `compare()` treats unidentified/non-speaker per spec.
- `tests/test_speaker_status_phase2.py` — new.

---

## Task 1: Confirm-to-link a returning unidentified handle

**Files:**
- Modify: `src/identify.py::soft_match_voice_profiles` (return profile_id alongside name/score)
- Modify: `src/review.py` (`build_review_state` soft_hints carry key; add `link_to_unidentified_handle`)
- Test: `tests/test_speaker_status_phase2.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_speaker_status_phase2.py
from __future__ import annotations
from src.models import Segment, SpeakerMapping
from src.review import link_to_unidentified_handle


def test_link_to_unidentified_handle_reuses_existing_slug():
    segs = [Segment(0, 0, 5, "S0", "hi")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    # The handle came from an earlier meeting's enrollment key "local:unidentified-mA-s3".
    link_to_unidentified_handle(mappings, segs, "S0",
                                handle_key="local:unidentified-mA-s3",
                                display_name="Unidentified Speaker")
    m = mappings["S0"]
    assert m.local_slug == "unidentified-mA-s3"   # strips the 'local:' prefix
    assert m.speaker_status == "unidentified"
    assert m.politician_slug is None
    assert m.id_method == "human_confirmed"
    assert segs[0].speaker_name == "Unidentified Speaker"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speaker_status_phase2.py -v`
Expected: FAIL — `link_to_unidentified_handle` missing.

- [ ] **Step 3: Add `link_to_unidentified_handle` in `src/review.py`**

```python
def link_to_unidentified_handle(mappings, segments, label, handle_key, display_name):
    """Link a speaker to an EXISTING unidentified handle (a returning unknown).

    handle_key is the stored profile key, e.g. 'local:unidentified-<m>-<lbl>'.
    Reuses that handle's slug so the recurring speaker enrolls into the same
    profile. Confirm-only — never called without reviewer action.
    """
    from src.models import SpeakerMapping
    slug = handle_key[len("local:"):] if handle_key.startswith("local:") else handle_key
    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    mapping.speaker_name = display_name or "Unidentified Speaker"
    mapping.local_slug = slug
    mapping.politician_slug = None
    mapping.politician_id = None
    mapping.speaker_status = "unidentified"
    mapping.id_method = "human_confirmed"
    mapping.confidence = 1.0
    mapping.needs_review = False
    mappings[label] = mapping
    for seg in segments:
        if seg.speaker_label == label:
            seg.speaker_name = mapping.speaker_name
```

- [ ] **Step 4: Surface the handle key in soft hints**

In `src/identify.py::soft_match_voice_profiles`, change each hint tuple to include the profile id: append `(name, score, profile_id)` instead of `(name, score)`. Update the docstring's return type to `[(display_name, similarity, profile_id), ...]`.

In `src/review.py::build_review_state`, update the `soft_hints` consumption to keep the 3-tuple (the field already stores whatever the matcher returns; just ensure no code unpacks exactly two values — grep `soft_hints` and update any `for name, score in` to `for name, score, pid in`).

- [ ] **Step 5: Run to verify it passes + full suite**

Run: `.venv/bin/python -m pytest tests/test_speaker_status_phase2.py -v && .venv/bin/python -m pytest -q`
Expected: PASS. Fix any 2-tuple unpacking of `soft_hints` surfaced by the suite.

- [ ] **Step 6: Wire the confirm action in `run_local.py`**

In `_interactive_speaker_review`, when a speaker's top soft hint is an unidentified handle (its `profile_id` starts with `local:unidentified-`), print it as `* Returning unidentified speaker: <name> (<score>)` and offer an accept key (e.g. `[Y]` already accepts the top hint — route it to `review.link_to_unidentified_handle(mappings, segments, label, handle_key=pid, display_name=name)` when the hint is an unidentified handle, instead of the normal rename). Thin wiring; the helper is already tested.

- [ ] **Step 7: Commit**

```bash
git add src/identify.py src/review.py run_local.py tests/test_speaker_status_phase2.py
git commit -m "feat(review): confirm-link returning unidentified speakers to their existing handle"
```

---

## Task 2: Promote an unidentified handle to a real person

**Files:**
- Modify: `src/enroll.py` (add `promote_unidentified_handle`)
- Test: `tests/test_speaker_status_phase2.py`

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
from src.enroll import ProfileDB, StoredProfile, EmbeddingRecord, promote_unidentified_handle


def test_promote_merges_handle_into_target_identity():
    db = ProfileDB(profiles={
        "local:unidentified-mA-s3": StoredProfile(
            speaker_id="local:unidentified-mA-s3", display_name="Unidentified Speaker",
            embeddings=[EmbeddingRecord(np.array([1.0, 0.0]), "mA")], meetings_seen=["mA"]),
        "essentials:jane-adams": StoredProfile(
            speaker_id="essentials:jane-adams", display_name="Jane Adams",
            embeddings=[EmbeddingRecord(np.array([0.0, 1.0]), "mB")], meetings_seen=["mB"],
            politician_slug="jane-adams", politician_id="uuid-ja"),
    })
    ok = promote_unidentified_handle(db, "local:unidentified-mA-s3", "essentials:jane-adams")
    assert ok is True
    assert "local:unidentified-mA-s3" not in db.profiles          # handle removed
    target = db.profiles["essentials:jane-adams"]
    assert {r.meeting_id for r in target.embeddings} == {"mA", "mB"}  # embeddings carried over


def test_promote_returns_false_for_missing_handle():
    db = ProfileDB()
    assert promote_unidentified_handle(db, "local:nope", "essentials:x") is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speaker_status_phase2.py -k promote -v`
Expected: FAIL — `promote_unidentified_handle` missing.

- [ ] **Step 3: Implement `promote_unidentified_handle`**

In `src/enroll.py`:

```python
def promote_unidentified_handle(db: ProfileDB, handle_key: str, target_key: str) -> bool:
    """Fold an unidentified handle's profile into a now-known identity.

    Merges all of the handle's embeddings/meetings into target_key (creating
    target as a bare profile if absent) and removes the handle. Returns False if
    the handle doesn't exist.
    """
    if handle_key not in db.profiles or handle_key == target_key:
        return False
    if target_key not in db.profiles:
        src = db.profiles[handle_key]
        db.profiles[target_key] = StoredProfile(speaker_id=target_key, display_name=src.display_name)
    return merge_profiles(db, handle_key, target_key)
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_speaker_status_phase2.py -k promote -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/enroll.py tests/test_speaker_status_phase2.py
git commit -m "feat(enroll): promote_unidentified_handle merges a handle into a known identity"
```

---

## Task 3: Calibration treats unidentified/non-speaker correctly

**Files:**
- Modify: `bench/calibrate_gate.py::compare`
- Test: `tests/test_calibrate_gate.py`

- [ ] **Step 1: Write the failing test**

```python
def test_unidentified_counts_coverage_but_not_precision():
    segs = [Segment(0, 0, 600, "S0", "x", speaker_name="Unidentified Speaker"),
            Segment(1, 600, 1200, "S1", "x", speaker_name="Jane")]
    speakers = {
        "S0": SpeakerMapping("S0", "Unidentified Speaker", 1.0, "human_review",
                             local_slug="unidentified-m-s0", speaker_status="unidentified"),
        "S1": SpeakerMapping("S1", "Jane", 1.0, "human_confirmed", politician_slug="jane"),
    }
    truth = Meeting(meeting_id="m", city="C", date="2026-01-01",
                    event_kind="council", segments=segs, speakers=speakers)
    auto = {
        "S0": SpeakerMapping("S0", "Unidentified Speaker", 0.9, "human_review",
                             local_slug="unidentified-m-s0", speaker_status="unidentified"),
        "S1": SpeakerMapping("S1", "Jane", 0.95, "voice_profile", politician_slug="jane"),
    }
    res = calibrate_gate.compare(truth, auto)
    # S0 (unidentified) counts toward trusted coverage but is NOT a precision claim;
    # only S1's 600s is claimed, and it's correct -> precision 100%.
    assert res["trusted_claimed_seconds"] == 600.0
    assert res["trusted_precision"] == 1.0
    assert res["trusted_coverage"] == 1.0   # both speakers' time is trusted-covered


def test_non_speaker_excluded_from_calibration_totals():
    segs = [Segment(0, 0, 600, "S0", "x", speaker_name="Jane"),
            Segment(1, 600, 1200, "S1", "x", speaker_name="Outro Music")]
    speakers = {
        "S0": SpeakerMapping("S0", "Jane", 1.0, "human_confirmed", politician_slug="jane"),
        "S1": SpeakerMapping("S1", "Outro Music", 1.0, "human_review", speaker_status="non_speaker"),
    }
    truth = Meeting(meeting_id="m", city="C", date="2026-01-01",
                    event_kind="council", segments=segs, speakers=speakers)
    auto = {"S0": SpeakerMapping("S0", "Jane", 0.95, "voice_profile", politician_slug="jane")}
    res = calibrate_gate.compare(truth, auto)
    assert res["trusted_coverage"] == 1.0   # 600/600, non-speaker's 600s excluded from total
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_calibrate_gate.py -k "unidentified or non_speaker" -v`
Expected: FAIL — unidentified is counted as a precision claim; non-speaker inflates the total.

- [ ] **Step 3: Update `compare()`**

In `bench/calibrate_gate.py::compare`, replace the per-label loop body so non-speakers are excluded from the denominator and unidentified is coverage-only:

```python
    secs = _speech_by_label(truth.segments)
    # Drop non-speaker labels (music/pledge) from all totals.
    def _status(m, label):
        sm = m.get(label) if isinstance(m, dict) else m.speakers.get(label)
        return getattr(sm, "speaker_status", None) if sm else None

    secs = {l: v for l, v in secs.items() if _status(truth.speakers, l) != "non_speaker"}
    claimed = 0.0
    correct = 0.0
    trusted_secs = 0.0
    total = sum(secs.values()) or 0.0

    for label, label_secs in secs.items():
        auto = auto_mappings.get(label)
        tier = quality.classify_method(auto.id_method) if (auto and auto.speaker_name) else quality.TIER_UNKNOWN
        if tier == quality.TIER_TRUSTED:
            trusted_secs += label_secs
        # An unidentified handle is a consistent-speaker attribution, not a named
        # identity: it counts toward coverage but is never a precision claim.
        is_unidentified = (getattr(auto, "speaker_status", None) == "unidentified"
                           or _status(truth.speakers, label) == "unidentified")
        if tier in (quality.TIER_TRUSTED, quality.TIER_PROBABLE) and not is_unidentified:
            claimed += label_secs
            if _same_identity(auto, truth.speakers.get(label)):
                correct += label_secs
```

(The `return {...}` block below is unchanged.)

- [ ] **Step 4: Run to verify it passes + full suite**

Run: `.venv/bin/python -m pytest tests/test_calibrate_gate.py -q && .venv/bin/python -m pytest -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add bench/calibrate_gate.py tests/test_calibrate_gate.py
git commit -m "feat(calibrate): unidentified = coverage-only; exclude non-speakers from totals"
```

---

## Self-Review

**Spec coverage (Phase 2 portion):**
- Returning-unknown recognition, confirm-only → Task 1 (`link_to_unidentified_handle` + soft-hint key + accept wiring). ✓
- Promote handle → person → Task 2 (`promote_unidentified_handle`). ✓
- Gate/calibration: unidentified counts coverage, never precision; non-speaker excluded → Task 3. ✓

**Placeholder scan:** none — full code + commands per step.

**Type consistency:** `local:<slug>` handle-key form matches Phase 1's `resolve_mapping_enrollment`; `speaker_status` values consistent; `soft_match_voice_profiles` 3-tuple return updated at its one consumer (`build_review_state`) and verified by the full-suite run in Step 5.
