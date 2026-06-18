# Speaker Status — Phase 3: Review-flow UX hardening

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make identity mistakes visible and catchable during review: show each speaker's resolved identity in the table, flag mismatches/duplicates before enrolling, and allow per-speaker undo.

**Architecture:** Three independent, mostly-pure additions to `src/review.py` (an `identity_label`, an `enrollment_warnings` checker, and a mapping snapshot/restore), each wired thinly into `run_local.py`'s review loop / enroll-confirmation. The warning checker reuses the name↔slug consistency heuristic from `bench/repair_stale_links.py`.

**Tech Stack:** Python 3, pytest. Tests run with `.venv/bin/python -m pytest`.

**Spec:** `docs/superpowers/specs/2026-06-18-unidentified-and-nonspeaker-status-design.md`
**Depends on:** Phase 1 (`speaker_status`, `local:` keys). Phase 2 optional.

---

## File Structure

- `src/review.py` — `identity_label`, `enrollment_warnings`, `snapshot_mapping`/`restore_mapping`.
- `run_local.py` — render the `Identity` column; print warnings before the `Enroll?` prompt; wire `[B]ack`.
- `tests/test_review_ux.py` — new.

---

## Task 1: Identity column in the review table

**Files:**
- Modify: `src/review.py` (add `identity_label`)
- Modify: `run_local.py` (review table header + rows in `_interactive_speaker_review` / its printer)
- Test: `tests/test_review_ux.py` (new)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_review_ux.py
from __future__ import annotations
from src.models import SpeakerMapping
from src.review import identity_label


def test_identity_label_for_each_status():
    assert identity_label(SpeakerMapping("S0", "Jane", politician_slug="jane-adams")) == "essentials:jane-adams"
    assert identity_label(SpeakerMapping("S0", "Bob", local_slug="bob-smith")) == "local:bob-smith"
    assert identity_label(SpeakerMapping("S0", "Unknown", local_slug="unidentified-m-s0",
                                         speaker_status="unidentified")) == "unidentified"
    assert identity_label(SpeakerMapping("S0", "Music", speaker_status="non_speaker")) == "non-speaker"
    assert identity_label(SpeakerMapping("S0", "Someone")) == "unlinked"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_review_ux.py -v`
Expected: FAIL — `identity_label` missing.

- [ ] **Step 3: Add `identity_label` in `src/review.py`**

```python
def identity_label(mapping) -> str:
    """One-word resolved identity for the review table."""
    if mapping is None:
        return "unlinked"
    if mapping.speaker_status == "non_speaker":
        return "non-speaker"
    if mapping.speaker_status == "unidentified":
        return "unidentified"
    if mapping.politician_slug:
        return f"essentials:{mapping.politician_slug}"
    if mapping.local_slug:
        return f"local:{mapping.local_slug}"
    return "unlinked"
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_review_ux.py -v`
Expected: PASS.

- [ ] **Step 5: Render the column in the review table**

In `run_local.py::_interactive_speaker_review`, where the speaker summary table is printed (the `# Label Current Name Segs Speech Conf Method` header and its rows), add an `Identity` column that calls `review.identity_label(mappings.get(v.label))` per row. Keep the column width modest and truncate long essentials slugs to fit. (Pure display; covered by `identity_label` tests.)

- [ ] **Step 6: Verify + commit**

Run: `.venv/bin/python -m py_compile run_local.py && .venv/bin/python -m pytest tests/test_review_ux.py -q`

```bash
git add src/review.py run_local.py tests/test_review_ux.py
git commit -m "feat(review): show resolved Identity column in the speaker table"
```

---

## Task 2: Pre-enroll safety check

**Files:**
- Modify: `src/review.py` (add `enrollment_warnings`)
- Modify: `run_local.py` (print warnings before the `Enroll these speakers? [Y/n]` prompt)
- Test: `tests/test_review_ux.py`

- [ ] **Step 1: Write the failing test**

```python
from src.review import enrollment_warnings
from src.roster import Roster, RosterMember


def _roster():
    return Roster(city="", body="B", members=[
        RosterMember(name="Hopi Stosberg", aliases=["Stosberg"],
                     politician_slug="hopi-h-stosberg", politician_id="u1"),
    ])


def test_warns_on_name_slug_mismatch():
    mappings = {"S0": SpeakerMapping("S0", "Isak Nti Asare", politician_slug="hopi-h-stosberg")}
    warns = enrollment_warnings(mappings, roster=None)
    assert any(w["kind"] == "name_slug_mismatch" and w["label"] == "S0" for w in warns)


def test_warns_on_duplicate_name_across_labels():
    mappings = {
        "S0": SpeakerMapping("S0", "Jane Adams", politician_slug="jane-adams"),
        "S1": SpeakerMapping("S1", "Jane Adams", politician_slug="jane-adams"),
    }
    warns = enrollment_warnings(mappings, roster=None)
    assert any(w["kind"] == "duplicate_name" for w in warns)


def test_warns_on_named_but_unlinked_roster_match():
    mappings = {"S0": SpeakerMapping("S0", "Hopi Stosberg")}  # matches roster, no link
    warns = enrollment_warnings(mappings, roster=_roster())
    assert any(w["kind"] == "unlinked_roster_match" and w["label"] == "S0" for w in warns)


def test_clean_mappings_have_no_warnings():
    mappings = {"S0": SpeakerMapping("S0", "Jane Adams", politician_slug="jane-adams")}
    assert enrollment_warnings(mappings, roster=None) == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_review_ux.py -k warn -v`
Expected: FAIL — `enrollment_warnings` missing.

- [ ] **Step 3: Implement `enrollment_warnings` in `src/review.py`**

```python
import re as _re2


def _name_tokens(s):
    stop = {"councilmember", "council", "president", "vice", "mayor", "clerk",
            "the", "of", "common", "city", "member", "district", "association",
            "office", "at", "large"}
    return set(_re2.sub(r"[^a-z0-9]+", " ", (s or "").lower()).split()) - stop


def _slug_tokens(slug):
    return set(_re2.sub(r"[^a-z0-9]+", " ", (slug or "").lower()).split()) - {"h", "j", "s"}


def enrollment_warnings(mappings, roster=None) -> list[dict]:
    """Flag suspicious states before enrollment. Returns list of
    {kind, label, detail}. kinds: name_slug_mismatch, duplicate_name,
    unlinked_roster_match."""
    warns = []
    # name/slug mismatch (linked slug shares no token with the name)
    for label, m in mappings.items():
        if m.politician_slug and m.speaker_name:
            nt, st = _name_tokens(m.speaker_name), _slug_tokens(m.politician_slug)
            if nt and st and not (nt & st):
                warns.append({"kind": "name_slug_mismatch", "label": label,
                              "detail": f"{m.speaker_name!r} linked to {m.politician_slug!r}"})
    # duplicate name across labels (excluding non-speakers)
    by_name = {}
    for label, m in mappings.items():
        if m.speaker_name and m.speaker_status != "non_speaker":
            by_name.setdefault(m.speaker_name.strip().lower(), []).append(label)
    for name, labels in by_name.items():
        if len(labels) > 1:
            warns.append({"kind": "duplicate_name", "label": ",".join(sorted(labels)),
                          "detail": f"{len(labels)} labels named {name!r} (merge?)"})
    # named but unlinked, yet matches a roster member
    if roster is not None:
        from src.roster import correct_speaker_name
        for label, m in mappings.items():
            if m.speaker_name and not m.politician_slug and not m.local_slug \
               and m.speaker_status not in ("non_speaker", "unidentified"):
                corrected = correct_speaker_name(m.speaker_name, roster)
                if any(corrected == mem.name and mem.politician_slug for mem in roster.members):
                    warns.append({"kind": "unlinked_roster_match", "label": label,
                                  "detail": f"{m.speaker_name!r} matches a roster member but isn't linked"})
    return warns
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_review_ux.py -k warn -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Print warnings before the Enroll prompt**

In `run_local.py`, just before each `Enroll these speakers? [Y/n]` prompt (the post-review enrollment path; there are enroll-confirmation prompts around the `enroll_speakers`/`enroll_confirmed` calls), call `review.enrollment_warnings(mappings, roster)` and, if non-empty, print each as `  ⚠ [<labels>] <detail>` so the reviewer sees mismatches/duplicates before confirming. Non-blocking (informational).

- [ ] **Step 6: Verify + commit**

Run: `.venv/bin/python -m py_compile run_local.py && .venv/bin/python -m pytest tests/test_review_ux.py -q`

```bash
git add src/review.py run_local.py tests/test_review_ux.py
git commit -m "feat(review): pre-enroll warnings for name/slug mismatch, duplicates, unlinked roster matches"
```

---

## Task 3: Per-speaker undo / back

**Files:**
- Modify: `src/review.py` (add `snapshot_mapping`/`restore_mapping`)
- Modify: `run_local.py` (`[B]ack` action in the review loop)
- Test: `tests/test_review_ux.py`

- [ ] **Step 1: Write the failing test**

```python
from src.review import snapshot_mapping, restore_mapping
from src.models import Segment


def test_snapshot_restore_round_trips_mapping_and_segments():
    segs = [Segment(0, 0, 5, "S0", "hi", speaker_name="Old")]
    mappings = {"S0": SpeakerMapping("S0", "Old", confidence=0.5, id_method="llm")}
    snap = snapshot_mapping(mappings, segs, "S0")

    # mutate (simulate a rename)
    mappings["S0"].speaker_name = "New"; mappings["S0"].id_method = "human_review"
    segs[0].speaker_name = "New"

    restore_mapping(mappings, segs, "S0", snap)
    assert mappings["S0"].speaker_name == "Old"
    assert mappings["S0"].id_method == "llm"
    assert segs[0].speaker_name == "Old"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_review_ux.py -k snapshot -v`
Expected: FAIL — helpers missing.

- [ ] **Step 3: Implement snapshot/restore in `src/review.py`**

```python
import copy as _copy


def snapshot_mapping(mappings, segments, label):
    """Capture a speaker's mapping + its segments' names, for one-step undo."""
    m = mappings.get(label)
    return {
        "label": label,
        "mapping": _copy.deepcopy(m) if m is not None else None,
        "seg_names": {i: s.speaker_name for i, s in enumerate(segments)
                      if s.speaker_label == label},
    }


def restore_mapping(mappings, segments, label, snap):
    """Revert to a snapshot taken by snapshot_mapping."""
    if snap["mapping"] is None:
        mappings.pop(label, None)
    else:
        mappings[label] = _copy.deepcopy(snap["mapping"])
    for i, s in enumerate(segments):
        if i in snap["seg_names"]:
            s.speaker_name = snap["seg_names"][i]
```

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_review_ux.py -k snapshot -v`
Expected: PASS.

- [ ] **Step 5: Wire `[B]ack` in the review loop**

In `run_local.py::_interactive_speaker_review`, before applying a change to speaker `i`, push `review.snapshot_mapping(mappings, segments, label)` onto a `history` stack (also record the index). Add a `[B]` command that pops the last snapshot, calls `review.restore_mapping(...)`, drops the matching entry from `changes`, sets `i` back to that speaker, and re-presents it. Add `[B]ack` to the command help.

- [ ] **Step 6: Verify + commit**

Run: `.venv/bin/python -m py_compile run_local.py && .venv/bin/python -m pytest -q`

```bash
git add src/review.py run_local.py tests/test_review_ux.py
git commit -m "feat(review): per-speaker [B]ack undo via mapping snapshot/restore"
```

---

## Self-Review

**Spec coverage (Phase 3 / UX hardening portion):**
- Identity column in the review table → Task 1 (`identity_label`). ✓
- Pre-enroll safety check (name/slug mismatch, duplicate name, unlinked roster match) → Task 2 (`enrollment_warnings`). ✓
- Per-speaker undo/back → Task 3 (`snapshot_mapping`/`restore_mapping` + `[B]`). ✓

**Placeholder scan:** none — full code + commands per step. Wiring steps (5/5/5) describe exact locations and call the already-tested pure helpers.

**Type consistency:** `enrollment_warnings` returns `{kind,label,detail}` dicts with the kinds asserted in tests; `identity_label` strings match the statuses from Phase 1; snapshot dict shape (`label`/`mapping`/`seg_names`) consistent between `snapshot_mapping` and `restore_mapping`.
