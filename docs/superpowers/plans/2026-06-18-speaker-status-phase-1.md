# Speaker Status — Phase 1: Status field, non-speaker exclusion, collision-safe unidentified handles

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop the voice-profile DB from enrolling non-persons and from merging distinct unidentified speakers, by adding a `speaker_status` to `SpeakerMapping` and keying unidentified enrollment off a unique handle instead of the typed name.

**Architecture:** One optional field (`speaker_status`: `None`/`"unidentified"`/`"non_speaker"`) drives three behaviors — non-speakers skip enrollment and gate eligibility; unidentified speakers enroll under a unique generated `local_slug`; enrollment keys local people by `local_slug` rather than `_name_to_slug(name)`. Status is set via pure helpers in `src/review.py`, wired to keybindings in the review loop.

**Tech Stack:** Python 3, pytest. Tests run with `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-18-unidentified-and-nonspeaker-status-design.md`

---

## File Structure

- `src/models.py` — add `speaker_status` to `SpeakerMapping` (+ to_dict/from_dict).
- `src/enroll.py` — `resolve_mapping_enrollment` keys by `local_slug`; `enroll_speakers`/`enroll_confirmed` skip non-speakers.
- `reenroll_profiles.py` — skip non-speakers in the eligibility loop.
- `src/quality.py` — exclude non-speakers from gate-eligible speech.
- `src/review.py` — `make_unidentified_slug`, `mark_unidentified`, `mark_non_speaker` (pure, testable).
- `run_local.py` — wire two review keybindings to the helpers (thin).
- `tests/test_speaker_status.py` — new.

---

## Task 1: `speaker_status` field on `SpeakerMapping`

**Files:**
- Modify: `src/models.py` (SpeakerMapping dataclass + to_dict + from_dict)
- Test: `tests/test_speaker_status.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_speaker_status.py
from __future__ import annotations
from src.models import SpeakerMapping


def test_speaker_status_defaults_none_and_round_trips():
    m = SpeakerMapping(speaker_label="S0", speaker_name="X")
    assert m.speaker_status is None
    assert "speaker_status" not in m.to_dict()  # omitted when None

    m2 = SpeakerMapping(speaker_label="S1", speaker_name="Music", speaker_status="non_speaker")
    d = m2.to_dict()
    assert d["speaker_status"] == "non_speaker"
    assert SpeakerMapping.from_dict(d).speaker_status == "non_speaker"


def test_from_dict_without_status_is_none():
    m = SpeakerMapping.from_dict({"speaker_label": "S0", "speaker_name": "X"})
    assert m.speaker_status is None
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -v`
Expected: FAIL — `TypeError: unexpected keyword argument 'speaker_status'`.

- [ ] **Step 3: Add the field**

In `src/models.py`, add to the `SpeakerMapping` dataclass after `local_role`:

```python
    local_role: Optional[str] = None        # 'candidate' | 'moderator' | 'panelist'
    speaker_status: Optional[str] = None    # None=normal | 'unidentified' | 'non_speaker'
```

In `to_dict`, after the `local_role` block:

```python
        if self.local_role is not None:
            d["local_role"] = self.local_role
        if self.speaker_status is not None:
            d["speaker_status"] = self.speaker_status
        return d
```

In `from_dict`, add the kwarg:

```python
            local_role=d.get("local_role"),
            speaker_status=d.get("speaker_status"),
        )
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/models.py tests/test_speaker_status.py
git commit -m "feat(models): add speaker_status to SpeakerMapping"
```

---

## Task 2: Non-speakers never enroll

**Files:**
- Modify: `src/enroll.py:243-254` (`enroll_speakers` loop) and `enroll_confirmed` (~415-435)
- Modify: `reenroll_profiles.py:145-157` (eligibility loop)
- Test: `tests/test_speaker_status.py`

- [ ] **Step 1: Write the failing test**

```python
import numpy as np
from src.enroll import ProfileDB, enroll_speakers
from src.models import Segment, SpeakerMapping


def _seg(label):
    return Segment(segment_id=0, start_time=0.0, end_time=30.0, speaker_label=label, text="hi")


def test_non_speaker_is_not_enrolled():
    emb = {"S0": np.array([1.0, 0.0, 0.0]), "S1": np.array([0.0, 1.0, 0.0])}
    mappings = {
        "S0": SpeakerMapping(speaker_label="S0", speaker_name="Real Person",
                             confidence=1.0, id_method="human_review"),
        "S1": SpeakerMapping(speaker_label="S1", speaker_name="Outro Music",
                             confidence=1.0, id_method="human_review",
                             speaker_status="non_speaker"),
    }
    segs = [_seg("S0"), _seg("S1")]
    db = enroll_speakers(ProfileDB(), emb, mappings, "m1", segs, roster=None)
    keys = list(db.profiles.keys())
    assert any("Real Person".lower().replace(" ", "_") in k or "person_real" == k for k in keys)
    assert not any("music" in k.lower() for k in keys)  # non-speaker excluded
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py::test_non_speaker_is_not_enrolled -v`
Expected: FAIL — the "Outro Music" profile is enrolled.

- [ ] **Step 3: Skip non-speakers in `enroll_speakers`**

In `src/enroll.py`, inside the `enroll_speakers` loop, add the guard right after the `speaker_name` check:

```python
    for label, mapping in mappings.items():
        if not mapping.speaker_name:
            continue
        if mapping.speaker_status == "non_speaker":
            continue
        if mapping.confidence < config.VOICE_MATCH_THRESHOLD:
            continue
```

Add the same guard in `enroll_confirmed`, right after its `if not mapping or not mapping.speaker_name: continue`:

```python
        if mapping.speaker_status == "non_speaker":
            continue
```

- [ ] **Step 4: Skip non-speakers in `reenroll_profiles.py`**

In `reenroll_profiles.py`, in the eligibility loop, add after the `"unknown"/"unidentified"/"n/a"` check:

```python
            if m.speaker_status == "non_speaker":
                continue
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/enroll.py reenroll_profiles.py tests/test_speaker_status.py
git commit -m "feat(enroll): never enroll non_speaker mappings"
```

---

## Task 3: Non-speakers excluded from gate eligibility

**Files:**
- Modify: `src/quality.py:109-114` (`evaluate_meeting` eligibility)
- Test: `tests/test_speaker_status.py`

- [ ] **Step 1: Write the failing test**

```python
from src.models import Meeting, Segment, SpeakerMapping
from src import quality


def test_non_speaker_excluded_from_gate_eligibility():
    segs = [Segment(0, 0, 120, "S0", "x", speaker_name="Real"),
            Segment(1, 120, 240, "S1", "x", speaker_name="Outro Music")]
    speakers = {
        "S0": SpeakerMapping("S0", "Real", 1.0, "human_review"),
        "S1": SpeakerMapping("S1", "Outro Music", 1.0, "human_review",
                             speaker_status="non_speaker"),
    }
    m = Meeting(meeting_id="m", city="C", date="2026-01-01",
                event_kind="council", segments=segs, speakers=speakers)
    rep = quality.evaluate_meeting(m)
    # S1's 120s must not count toward eligible speech.
    assert rep["eligible_speech_seconds"] == 120.0
    per = {p["label"]: p for p in rep["per_speaker"]}
    assert per["S1"]["eligible"] is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py::test_non_speaker_excluded_from_gate_eligibility -v`
Expected: FAIL — `eligible_speech_seconds == 240.0` (S1 counted).

- [ ] **Step 3: Exclude non-speakers from `eligible`**

In `src/quality.py::evaluate_meeting`, replace the eligibility block (lines 109-114) with:

```python
    # Eligible (principal) speakers: above the incidental floor, excluding any
    # speaker explicitly marked not-a-speaker (music, pledge, station IDs).
    def _is_non_speaker(label: str) -> bool:
        m = meeting.speakers.get(label)
        return bool(m and m.speaker_status == "non_speaker")

    eligible = {l: s for l, s in secs_by_label.items()
                if s >= floor and not _is_non_speaker(l)}
    if not eligible:
        eligible = {l: s for l, s in secs_by_label.items() if not _is_non_speaker(l)}
    eligible_total = sum(eligible.values())
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/quality.py tests/test_speaker_status.py
git commit -m "feat(quality): exclude non_speaker labels from gate eligibility"
```

---

## Task 4: Unidentified unique handle + enroll by `local_slug`

**Files:**
- Modify: `src/review.py` (add `make_unidentified_slug`)
- Modify: `src/enroll.py:183-196` (`resolve_mapping_enrollment`)
- Test: `tests/test_speaker_status.py`

- [ ] **Step 1: Write the failing test**

```python
from src.review import make_unidentified_slug
from src.enroll import resolve_mapping_enrollment


def test_unidentified_slug_is_unique_per_meeting_and_label():
    a = make_unidentified_slug("2026-02-04-council", "SPEAKER_07")
    b = make_unidentified_slug("2026-05-06-debate", "SPEAKER_07")
    assert a != b
    assert a == make_unidentified_slug("2026-02-04-council", "SPEAKER_07")  # deterministic
    assert a.startswith("unidentified-")


def test_resolve_keys_unidentified_by_local_slug_not_name():
    m1 = SpeakerMapping(speaker_label="S0", speaker_name="Interviewee 1",
                        local_slug="unidentified-mA-S0", speaker_status="unidentified")
    m2 = SpeakerMapping(speaker_label="S0", speaker_name="Interviewee 1",
                        local_slug="unidentified-mB-S0", speaker_status="unidentified")
    k1, s1, _ = resolve_mapping_enrollment(m1, roster=None)
    k2, s2, _ = resolve_mapping_enrollment(m2, roster=None)
    assert k1 == "local:unidentified-mA-S0"
    assert k2 == "local:unidentified-mB-S0"
    assert k1 != k2          # two "Interviewee 1"s never merge
    assert s1 is None and s2 is None


def test_resolve_prefers_politician_slug_over_local():
    m = SpeakerMapping(speaker_label="S0", speaker_name="Jane Adams",
                       politician_slug="jane-adams", politician_id="uuid",
                       local_slug="should-be-ignored")
    assert resolve_mapping_enrollment(m, roster=None) == ("essentials:jane-adams", "jane-adams", "uuid")
```

(`SpeakerMapping` is already imported at the top of the test module from Task 2.)

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -k "unidentified_slug or resolve" -v`
Expected: FAIL — `make_unidentified_slug` missing; resolve keys by `_name_to_slug` (`interviewee_1`), so `k1 == k2`.

- [ ] **Step 3: Add `make_unidentified_slug` in `src/review.py`**

At module scope in `src/review.py` (near the top, after imports):

```python
import re as _re


def make_unidentified_slug(meeting_id: str, label: str) -> str:
    """Unique, deterministic handle for an unidentified speaker.

    Keyed by (meeting, diarization label) so two different unknowns never share a
    slug (no merge), while re-running review on the same meeting is idempotent.
    """
    base = _re.sub(r"[^a-z0-9]+", "-", f"{meeting_id}-{label}".lower()).strip("-")
    return f"unidentified-{base}"
```

- [ ] **Step 4: Key enrollment by `local_slug` in `resolve_mapping_enrollment`**

In `src/enroll.py`, replace the body of `resolve_mapping_enrollment`:

```python
    if mapping.politician_slug:
        return f"essentials:{mapping.politician_slug}", mapping.politician_slug, mapping.politician_id
    if mapping.local_slug:
        # Key local people (incl. unidentified handles) by their stable slug, not
        # the typed name — so identical labels in different meetings never merge.
        return f"local:{mapping.local_slug}", None, None
    return resolve_enrollment_key(mapping.speaker_name, roster)
```

- [ ] **Step 5: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -v`
Expected: PASS.

- [ ] **Step 6: Run the full suite (this changes local-person enrollment keys)**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS. If a test asserts a local person's old `_name_to_slug` enrollment key (e.g. `public_john`), update it to the new `local:<local_slug>` key — that is the intended behavior change.

- [ ] **Step 7: Commit**

```bash
git add src/review.py src/enroll.py tests/test_speaker_status.py
git commit -m "feat(enroll): key local/unidentified speakers by local_slug to prevent label collisions"
```

---

## Task 5: Review helpers + keybindings to set status

**Files:**
- Modify: `src/review.py` (add `mark_unidentified`, `mark_non_speaker`)
- Modify: `run_local.py` (`_interactive_speaker_review` loop — wire two keys)
- Test: `tests/test_speaker_status.py`

- [ ] **Step 1: Write the failing test**

```python
from src.review import mark_unidentified, mark_non_speaker


def test_mark_unidentified_sets_unique_handle_and_status():
    segs = [Segment(0, 0, 5, "S0", "hi")]
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="City Council District 3")}
    mark_unidentified(mappings, segs, "S0", "2026-02-04-council", display_label="Unknown Commenter")
    m = mappings["S0"]
    assert m.speaker_status == "unidentified"
    assert m.local_slug == "unidentified-2026-02-04-council-s0"
    assert m.speaker_name == "Unknown Commenter"
    assert m.politician_slug is None
    assert m.id_method == "human_review" and m.confidence == 1.0
    assert segs[0].speaker_name == "Unknown Commenter"


def test_mark_unidentified_defaults_label():
    mappings = {"S0": SpeakerMapping(speaker_label="S0")}
    mark_unidentified(mappings, [], "S0", "m1", display_label=None)
    assert mappings["S0"].speaker_name == "Unidentified Speaker"


def test_mark_non_speaker_clears_identity_and_sets_status():
    mappings = {"S0": SpeakerMapping(speaker_label="S0", speaker_name="Outro Music",
                                     politician_slug="stale", local_slug="stale")}
    mark_non_speaker(mappings, "S0")
    m = mappings["S0"]
    assert m.speaker_status == "non_speaker"
    assert m.politician_slug is None and m.local_slug is None
    assert m.id_method == "human_review"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -k "mark_" -v`
Expected: FAIL — helpers missing.

- [ ] **Step 3: Add the helpers in `src/review.py`**

```python
def mark_unidentified(mappings, segments, label, meeting_id, display_label=None):
    """Mark a speaker as a distinct-but-unnamed person: unique handle, enrolled."""
    from src.models import SpeakerMapping
    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    name = (display_label or "").strip() or "Unidentified Speaker"
    mapping.speaker_name = name
    mapping.local_slug = make_unidentified_slug(meeting_id, label)
    mapping.local_role = None
    mapping.politician_slug = None
    mapping.politician_id = None
    mapping.speaker_status = "unidentified"
    mapping.id_method = "human_review"
    mapping.confidence = 1.0
    mapping.needs_review = False
    mappings[label] = mapping
    for seg in segments:
        if seg.speaker_label == label:
            seg.speaker_name = name


def mark_non_speaker(mappings, label):
    """Mark a label as not-a-person (music/pledge/station ID); never enrolled."""
    from src.models import SpeakerMapping
    mapping = mappings.get(label) or SpeakerMapping(speaker_label=label)
    mapping.speaker_status = "non_speaker"
    mapping.politician_slug = None
    mapping.politician_id = None
    mapping.local_slug = None
    mapping.local_role = None
    mapping.id_method = "human_review"
    mapping.confidence = 1.0
    mapping.needs_review = False
    mappings[label] = mapping
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_speaker_status.py -k "mark_" -v`
Expected: PASS.

- [ ] **Step 5: Wire keybindings in the review loop**

In `run_local.py::_interactive_speaker_review`, add two branches in the command dispatch (alongside the existing `m`/`y`/quit branches; mirror their structure). Use `[U]` and `[X]`:

```python
                elif choice.lower() == "u":
                    lbl = input("    Optional label (Enter for 'Unidentified Speaker'): ").strip()
                    review.mark_unidentified(mappings, segments, label, meeting_id, display_label=lbl or None)
                    changes.append({"label": label, "old_name": name, "new_name": mappings[label].speaker_name})
                    print(f"  Unidentified: {label} -> {mappings[label].speaker_name} (handle {mappings[label].local_slug})")
                    break
                elif choice.lower() == "x":
                    review.mark_non_speaker(mappings, label)
                    changes.append({"label": label, "old_name": name, "new_name": "(non-speaker)"})
                    print(f"  Marked non-speaker: {label}")
                    break
```

Add `[U]nidentified` and `[X]=not a speaker` to the printed command help and to the per-speaker prompt string. The review loop already has `meeting_id` in scope via its caller; if not, thread `meeting_id` in the same way `event_kind` was threaded (pass `meeting.meeting_id` / `meeting_dir.name` from each `_interactive_speaker_review` call site).

- [ ] **Step 6: Verify wiring compiles + full suite**

Run: `.venv/bin/python -m py_compile run_local.py && .venv/bin/python -m pytest -q`
Expected: `OK` + all tests pass.

- [ ] **Step 7: Commit**

```bash
git add src/review.py run_local.py tests/test_speaker_status.py
git commit -m "feat(review): [U] mark unidentified and [X] mark non-speaker actions"
```

---

## Self-Review

**Spec coverage (Phase 1 portion):**
- `speaker_status` field + round-trip → Task 1. ✓
- Non-speaker excluded from enrollment → Task 2. ✓
- Non-speaker excluded from gate eligibility → Task 3. ✓
- Unidentified unique handle → Task 4 (`make_unidentified_slug`). ✓
- Enroll by `local_slug` (collision-safe; also fixes named local people) → Task 4. ✓
- Review UX to set both statuses → Task 5. ✓
- Deferred to Phase 2: recurrence (returning-unidentified via Wire 1), promote-to-person, calibrate precision treatment. Deferred to Phase 3: identity column, pre-enroll safety check, undo. (Tracked in their own plans.)

**Placeholder scan:** none — every step has full code + commands.

**Type consistency:** `speaker_status` values (`"unidentified"`/`"non_speaker"`) used identically across models, enroll, quality, review; `make_unidentified_slug(meeting_id, label)` signature matches its callers; enrollment key form `local:<local_slug>` consistent between Task 4 and the slug produced by `make_unidentified_slug`.

## Operational note

After Phase 1 lands, re-mark the already-polluted speakers (`Outro Music`, `Moderator (Right)`, etc.) as non-speaker/unidentified in review, then rebuild: `mv ~/CouncilScribe/profiles/speaker_profiles.pkl{,.bak}` and re-run `reenroll_profiles.py --methods human_review`.
