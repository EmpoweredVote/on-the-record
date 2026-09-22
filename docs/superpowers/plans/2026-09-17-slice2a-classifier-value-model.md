# Slice 2A — teach the discovery classifier the value model — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Update the discovery classifier + lane logic so comparable common-question sources are recognized and ranked per Chris's value model: `questionnaire` is a first-class kind laned as a high-value written quote source, and prior-cycle ("stale") content is rejected.

**Architecture:** Pure on-the-record code — no schema migration, no prod DB change. Three focused edits: (1) add `questionnaire` to `src/event_kinds.py::EVENT_KINDS`; (2) `src/discovery/lanes.py::content_lane` gains a `questionnaire` lane and the two consumers (`gui/app.py` review tagging, `src/discovery/autoapprove.py` eligibility) handle it; (3) the classifier prompt (`src/discovery/classify.py`) gains a current-cycle/stale check keyed on the `race_label` it already receives.

**Tech Stack:** Python 3, `src/discovery/` (classifier + lanes + autoapprove), `gui/` (FastAPI review page), pytest.

## Global Constraints

- `.venv/bin/python -m pytest`. Never system `python3`.
- **No DB migration and no schema change** — `questionnaire` is a classifier-emitted `event_kind_guess` value already stored in `discovered_sources.event_kind_guess` (free text); the value model rides existing columns (`source_tier_guess`, `route`, `original_vs_clip`). The Slice-2 hub registry is a SEPARATE later plan.
- Test harness: no local DB (conftest deletes `DATABASE_URL`). Pure functions tested directly; classifier prompt changes tested by (a) prompt-content assertions and (b) `parse_verdict` unit tests; behavioral classifier quality is validated by the existing eval harness (`scripts/eval_discovery_classifier.py` + its jsonl), run manually (LLM cost) — a gated step, not an automated test.
- **Value model** ([[source-priority-comparable-questions]]): prize sources where candidates answer the SAME questions, always the candidate's own words; rank multi-candidate debates/town-halls/forums (tier 1) ≥ interviews (tier 2) ≥ written questionnaires (tier 2, high) > sympathetic/prepared (tier 3) > op-eds (tier 4). The classifier's existing tier prompt already encodes most of this — do NOT rewrite it; only add what's missing.
- Do NOT auto-approve questionnaires — they are high-value, human-curated (the Slice-1 auto-approve lane is `news_clip` only; keep questionnaires out of it).
- Reclassifying the ~existing rows to pick up the new logic is an OPTIONAL manual follow-up (`scripts/reclassify` run), NOT part of this plan.

## File structure
- Modify: `src/event_kinds.py` — add `questionnaire` to `EVENT_KINDS` (+ a role set if the pattern needs it).
- Modify: `src/discovery/lanes.py` — `content_lane` gains a `questionnaire` lane.
- Modify: `src/discovery/classify.py` — prompt gains a current-cycle/stale instruction; verify `ALLOWED_KINDS` includes `questionnaire`.
- Modify: `gui/app.py` — the review-page lane tagging/grouping recognizes the `questionnaire` lane.
- Modify (verify only): `src/discovery/autoapprove.py` — confirm `questionnaire` can never satisfy `ELIGIBLE_LANE_SQL`.
- Tests: `tests/test_discovery_lanes.py`, `tests/test_event_kinds.py` (or wherever EVENT_KINDS is tested), `tests/test_discovery_classify.py`, `tests/test_gui_discovery.py`; add a stale example to `tests/` / the classifier eval jsonl.

---

### Task 1: `questionnaire` as a first-class event kind

**Files:**
- Modify: `src/event_kinds.py`
- Verify: `src/discovery/classify.py` (`ALLOWED_KINDS`)
- Test: `tests/test_discovery_classify.py` (or `tests/test_event_kinds.py`)

**Interfaces:**
- Produces: `"questionnaire" in src.event_kinds.EVENT_KINDS`; `parse_verdict` preserves `event_kind_guess == "questionnaire"`.

- [ ] **Step 1: Write the failing test**

```python
def test_questionnaire_is_a_known_event_kind():
    from src.event_kinds import EVENT_KINDS
    assert "questionnaire" in EVENT_KINDS

def test_parse_verdict_keeps_questionnaire_kind():
    from src.discovery.classify import parse_verdict
    v = parse_verdict('{"relevant": true, "confidence": 0.8, "candidates_present": [],'
                      ' "event_kind": "questionnaire", "source_tier": 2,'
                      ' "original_vs_clip": "original", "route": "quote_source", "why": "x"}')
    assert v.event_kind_guess == "questionnaire"
    assert v.route == "quote_source"
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -k questionnaire -v`
Expected: FAIL — `questionnaire` not in `EVENT_KINDS` (and, if `ALLOWED_KINDS` lacks it, `event_kind_guess` is None).

- [ ] **Step 3: Implement**

In `src/event_kinds.py`, add `"questionnaire"` to the `EVENT_KINDS` tuple (it is a written candidate-Q&A source). If `EVENT_KINDS`-derived role sets or CHECK constraints reference it, mirror the existing style. In `src/discovery/classify.py`, confirm `ALLOWED_KINDS` (line ~23) contains `"questionnaire"`; add it if missing so `parse_verdict` preserves the kind.

- [ ] **Step 4: Run to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -k questionnaire -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/event_kinds.py src/discovery/classify.py tests/test_discovery_classify.py
git commit -m "feat(discovery): questionnaire is a first-class event kind

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: `content_lane` gains a `questionnaire` lane + consumers handle it

**Files:**
- Modify: `src/discovery/lanes.py`
- Modify: `gui/app.py` (line ~104, the `content_lane(...)` tagging)
- Verify: `src/discovery/autoapprove.py`
- Test: `tests/test_discovery_lanes.py`, `tests/test_gui_discovery.py`

**Interfaces:**
- Consumes: `EVENT_KINDS` (Task 1).
- Produces: `content_lane(original_vs_clip, event_kind_guess)` returns `"questionnaire"` when `event_kind_guess == "questionnaire"` (regardless of `original_vs_clip`, since a questionnaire is written, not a video clip/original). Existing returns (`full_event`/`event_clip`/`news_clip`/`unknown`) are unchanged for other kinds.

- [ ] **Step 1: Write the failing test**

```python
import pytest
from src.discovery.lanes import content_lane

@pytest.mark.parametrize("ovc,kind,expected", [
    ("original", "questionnaire", "questionnaire"),
    ("clip", "questionnaire", "questionnaire"),
    (None, "questionnaire", "questionnaire"),
    ("original", "debate", "full_event"),   # unchanged
    ("clip", "news_clip", "news_clip"),      # unchanged
])
def test_content_lane_questionnaire(ovc, kind, expected):
    assert content_lane(ovc, kind) == expected
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_lanes.py -k questionnaire -v`
Expected: FAIL — today `content_lane("original","questionnaire")` returns `"full_event"`.

- [ ] **Step 3: Implement**

In `src/discovery/lanes.py::content_lane`, add the questionnaire check FIRST:

```python
def content_lane(original_vs_clip, event_kind_guess):
    if event_kind_guess == "questionnaire":
        return "questionnaire"          # written comparable Q&A — a high-value quote source
    if original_vs_clip == "original":
        return "full_event"
    if original_vs_clip == "clip":
        if event_kind_guess in FORMAL_EVENT_KINDS:
            return "event_clip"
        return "news_clip"
    return "unknown"
```

Update the module docstring to name the new lane.

- [ ] **Step 4: Wire the review-page consumer**

In `gui/app.py` where each pending row is tagged (`lane = content_lane(...)`, ~line 104) and grouped, ensure the `questionnaire` lane renders as a **high-value quote-source** row (not the ingest-glance group) — a small label/branch, mirroring how the other lanes are shown. Do NOT offer `approve → ingest` on a questionnaire (it is a web page, not a video); it is a `quote_source`.

- [ ] **Step 5: Verify auto-approve exclusion**

Read `src/discovery/autoapprove.py::ELIGIBLE_LANE_SQL` and confirm a questionnaire can never match it (it requires `original_vs_clip = 'clip'` AND event kind not-in FORMAL; a questionnaire is `original`, so it is already excluded). Add a test asserting a trusted-outlet questionnaire row is NOT auto-approved (mirror the existing autoapprove fake-cursor tests: the WHERE excludes `original`).

- [ ] **Step 6: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_lanes.py tests/test_gui_discovery.py tests/test_discovery_autoapprove.py -v`
Expected: PASS (including the unchanged-lane cases). Then run the full suite once and confirm green.

- [ ] **Step 7: Commit**

```bash
git add src/discovery/lanes.py gui/app.py tests/
git commit -m "feat(discovery): questionnaire content lane (high-value written quote source)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: current-cycle / stale-content rejection in the classifier

**Files:**
- Modify: `src/discovery/classify.py` (`_PROMPT_TEMPLATE`)
- Test: `tests/test_discovery_classify.py`; add a stale example to the classifier eval jsonl used by `scripts/eval_discovery_classifier.py`.

**Interfaces:**
- Produces: the classifier prompt instructs the model to reject prior-cycle ("stale") content by setting `relevant=false`, keyed on the `race_label` (which already carries the cycle/year, e.g. `"AZ · U.S. Senate · General · 2026"`). A `relevant=false` verdict already becomes `status='auto_filtered'` in the engine — no new field.

- [ ] **Step 1: Write the failing test (prompt content)**

```python
def test_prompt_has_current_cycle_check():
    from src.discovery.classify import build_prompt
    from src.discovery.models import RawItem
    item = RawItem(url="https://ballotpedia.org/x", title="2022 debate", description="prior cycle")
    prompt = build_prompt(item, race_label="AZ · U.S. Senate · General · 2026", roster_names=["A","B"])
    low = prompt.lower()
    assert "current" in low and ("cycle" in low or "prior" in low)
    assert "AZ · U.S. Senate · General · 2026" in prompt  # the check is keyed on race_label
```

- [ ] **Step 2: Run to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -k current_cycle -v`
Expected: FAIL — no current-cycle instruction in the prompt today.

- [ ] **Step 3: Implement**

Add to `_PROMPT_TEMPLATE` (near the relevance rules) a concise instruction, e.g.:

> Current cycle: the tracked race is **{race_label}**. If this item is about a PRIOR or different election cycle (a wrong year or a past contest, e.g. an archived page still showing an earlier cycle's candidates), set `relevant` to false — it is stale, not this race's current comparable source.

`{race_label}` is already a format field. No new parse fields.

- [ ] **Step 4: Add a stale eval example**

Add one example to the classifier eval jsonl (the file `scripts/eval_discovery_classifier.py` loads): a prior-cycle item for a current race, `expected relevant=false`. This is the behavioral check; it runs when the eval is run manually (LLM cost), not in the unit suite.

- [ ] **Step 5: Run tests**

Run: `.venv/bin/python -m pytest tests/test_discovery_classify.py -v`
Expected: PASS. Full suite green.

- [ ] **Step 6: Commit**

```bash
git add src/discovery/classify.py scripts/ tests/test_discovery_classify.py
git commit -m "feat(discovery): reject stale prior-cycle sources (current-cycle check keyed on race_label)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-review

- **Spec coverage (Part 2 of the Slice-2 spec):** questionnaire first-class → Task 1; ranked comparable sources → the classifier's existing tier prompt (unchanged by design; Global Constraints note it already encodes the model) + questionnaire laned high → Task 2; reject stale prior-cycle → Task 3; always candidate's own words → the existing `relevant=true only for originals` rule (unchanged). The hub registry (Part 1) is correctly out of scope (separate plan).
- **Placeholder scan:** no TBDs. The one manual step (run the classifier eval to validate stale-rejection behavior) is explicitly a gated LLM-cost step, not a hidden gap.
- **Type consistency:** `content_lane` returns the same string set plus `"questionnaire"`, consumed by `gui/app.py` (tagging) and unaffected in `autoapprove.py` (which keys on `original_vs_clip`/`FORMAL_EVENT_KINDS`, never the lane string). `EVENT_KINDS` and `ALLOWED_KINDS` both gain `questionnaire`.
