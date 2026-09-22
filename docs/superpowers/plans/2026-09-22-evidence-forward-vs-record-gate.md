# Forward-vs-Record Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `forward_looking` judge dimension that flags past-record recitations (which green today via high `mechanism`) as `judge:record-not-forward`, without dropping them.

**Architecture:** New `JudgeScores.forward_looking` (default 1.0) + `GateResults.judge_forward_looking` (default None) + a `FORWARD_MIN` check in `disposition.decide`; the judge prompt/parse produce it; `pipeline._evaluate_quote` passes it; `judge_jev` produces it too. Flag, not drop. Model-agnostic.

**Tech Stack:** Python 3, pytest.

## Global Constraints

- **`forward_looking`: 1.0 = forward stance/proposal (what they WOULD do); 0.0 = past record/accomplishment (what they DID).** A record recitation must **flag** (`judge:record-not-forward`), never green, even with high mechanism. **Flag, not drop.**
- **Non-breaking field addition:** `JudgeScores.forward_looking: float = 1.0` (defaulted, positioned before `notes`) so existing `JudgeScores(...)` sites keep working and omitted → forward/inert. `parse_judge` sets it explicitly with a **0.0** worst-default. `GateResults.judge_forward_looking: Optional[float] = None` (omitted → gate inert), so existing `GateResults(...)` sites and disposition tests are unaffected.
- **Separate axis from `mechanism`** (a record can have high mechanism) and **model-agnostic** (works under deepseek today, gemini-flash after the pending swap).
- No schema change; no ev-accounts change. Run tests with the MAIN checkout `.venv/bin/python`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `src/evidence/models.py` — ADD `JudgeScores.forward_looking = 1.0`; `GateResults.judge_forward_looking = None`.
- `src/evidence/disposition.py` — ADD `FORWARD_MIN = 0.5` + the check/reason.
- `src/evidence/judge.py` — ADD the `forward_looking` prompt bullet + JSON key + `parse_judge` field.
- `src/evidence/judge_jev.py` — ADD a `forward_looking` `Score` + set it.
- `src/evidence/pipeline.py` — pass `judge_forward_looking=js.forward_looking` in `_evaluate_quote`.
- Tests: `tests/test_evidence_disposition.py`, `tests/test_evidence_judge.py`, `tests/test_judge_jev.py`, `tests/test_evidence_pipeline.py`.

---

### Task 1: Models + disposition gate

**Files:**
- Modify: `src/evidence/models.py`, `src/evidence/disposition.py`
- Test: `tests/test_evidence_disposition.py`

- [ ] **Step 1: Write the failing tests**

Reuse the file's existing `_full(**kw)` helper and `SourceType.PRIMARY` (already imported at the top). `_full()` omits `judge_forward_looking`, so it defaults `None` → the existing green tests stay green (gate inert).

```python
# tests/test_evidence_disposition.py  (append)
def test_record_recitation_flags_even_with_high_mechanism():
    # a record names concrete actions (high mechanism) but is not forward → flag, not green
    status, reasons = decide(_full(judge_mechanism=0.95, judge_forward_looking=0.2), SourceType.PRIMARY)
    assert status == Status.FLAGGED.value and "judge:record-not-forward" in reasons

def test_forward_stance_greens():
    status, reasons = decide(_full(judge_forward_looking=0.9), SourceType.PRIMARY)
    assert status == Status.GREEN.value and reasons == []

def test_forward_gate_inert_when_none():
    # judge_forward_looking absent (None) → gate does not fire (backward compatible)
    status, reasons = decide(_full(judge_forward_looking=None), SourceType.PRIMARY)
    assert status == Status.GREEN.value and "judge:record-not-forward" not in reasons
```

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_disposition.py -k "record or forward" -v`
Expected: FAIL (`GateResults` has no `judge_forward_looking`; `decide` has no forward check).

- [ ] **Step 3: Implement**

In `src/evidence/models.py`, add to `JudgeScores` (before `notes`):
```python
    forward_looking: float = 1.0
```
and to `GateResults` (after `judge_mechanism`):
```python
    judge_forward_looking: Optional[float] = None
```

In `src/evidence/disposition.py`, add the constant and the check (after the mechanism check, before the return):
```python
FORWARD_MIN = 0.5
...
    if gates.judge_forward_looking is not None and gates.judge_forward_looking < FORWARD_MIN:
        reasons.append("judge:record-not-forward")
```

- [ ] **Step 4: Run to verify pass + the full disposition file**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_disposition.py -v`
Expected: PASS (new + all existing; existing tests omit `judge_forward_looking` → None → inert).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/models.py src/evidence/disposition.py tests/test_evidence_disposition.py
git commit -m "feat(evidence): forward_looking gate flags record recitations (judge:record-not-forward)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Judge prompt + parse

**Files:**
- Modify: `src/evidence/judge.py`
- Test: `tests/test_evidence_judge.py`

- [ ] **Step 1: Write the failing tests**

`parse_judge` and `json` are already imported at the top of the file — do not re-import.

```python
# tests/test_evidence_judge.py  (append)
def test_parse_judge_reads_forward_looking():
    js = parse_judge(json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,
                                 "mechanism":0.9,"forward_looking":0.2,"notes":""}))
    assert js.forward_looking == 0.2

def test_parse_judge_forward_looking_worst_default_on_failure():
    js = parse_judge("not json")          # total parse failure → worst defaults
    assert js.forward_looking == 0.0       # record → contributes to flagging, like the others
```

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_judge.py -k forward -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

In `src/evidence/judge.py` `_INSTRUCTIONS`, add this bullet after the `mechanism` bullet:
```
- forward_looking: is this a FORWARD-LOOKING stance — what the candidate WOULD do or believes SHOULD
  happen ("I will…", "we should…", "as mayor I would…") — score near 1? Or a recitation of PAST RECORD
  / accomplishment — what they already DID ("I did…", "we have…", "on day one I declared…", "we've
  moved thousands off the streets", a stat of results) — score near 0? Judge the DOMINANT orientation;
  a forward proposal that mentions past action in passing is still forward.
```
and change the Return-JSON line to include the key:
```
Return JSON: {{"tag_ok","context_sufficient","dispute_risk","mechanism","forward_looking","notes"}}.
```
In `parse_judge`, add to the `JudgeScores(...)` return:
```python
        forward_looking=_clamp(d.get("forward_looking"), 0.0),
```

- [ ] **Step 4: Run to verify pass**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_judge.py -v`
Expected: PASS. Confirm `build_judge_prompt` still formats (no stray brace): `~/Documents/GitHub/on-the-record/.venv/bin/python -c "from src.evidence.judge import build_judge_prompt; from src.evidence.models import QuoteCandidate; build_judge_prompt(QuoteCandidate(text='x',context='y',issue='z')); print('ok')"`

- [ ] **Step 5: Commit**

```bash
git add src/evidence/judge.py tests/test_evidence_judge.py
git commit -m "feat(evidence): judge scores forward_looking (forward stance vs past record)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Pipeline wiring + judge_jev

**Files:**
- Modify: `src/evidence/pipeline.py:65-68`, `src/evidence/judge_jev.py`
- Test: `tests/test_evidence_pipeline.py`, `tests/test_judge_jev.py`

**Depends on Tasks 1+2** (uses `parse_judge` reading `forward_looking` and the `judge_forward_looking` gate). Run tasks in order.

- [ ] **Step 1: Write the failing tests**

Pipeline — a record recitation flags end-to-end. Mirror the existing `test_primary_quote_with_only_a_goal_is_flagged_no_mechanism` (same `run_source` + `fetcher` + `_providers` shape already in the file); only the judge JSON differs (`forward_looking` low):

```python
# tests/test_evidence_pipeline.py  (append)
def test_primary_quote_reciting_record_is_flagged_not_forward():
    # high tag/context/mechanism, low dispute — but the judge scores forward_looking
    # low (a past-record recitation, not a forward stance), so the item must FLAG.
    extract = json.dumps({"quotes": [{"text":"We will build 30,000 units of housing",
        "context": SRC, "issue":"housing","date":"2026","setting":"campaign site",
        "is_own_words":True,"is_primary_venue":True,"reported_event":None,
        "primary_handle":None}]})
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,
                        "tag_ok":True,"issue":"housing","notes":""})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,
                      "mechanism":0.9,"forward_looking":0.1})
    items, leads = run_source(politician_id="p1",
        source_url="https://karenbass.com/housing", cited_via=None,
        providers=_providers(extract, cross, jud), fetcher=lambda u: SRC,
        candidate_name="Karen Bass", batch_id="b1")
    assert len(items) == 1 and items[0].status == Status.FLAGGED.value
    assert "judge:record-not-forward" in items[0].status_reasons
```

judge_jev — returns `forward_looking`. **Also update the existing `test_judge_jev_normalizes_scores_to_0_1`**: its `_FakeClient` `_scored` dict must gain a `"forward_looking"` entry, because `build_questions()` now has 5 questions and `_FakeClient.system_one` does `self._scored[k] for k in questions` (a missing key raises `KeyError`).

```python
# tests/test_judge_jev.py
#   (1) in the EXISTING test, add to the _scored dict:  "forward_looking": (1.0, 0.9)
#   (2) append this new test:
def test_judge_jev_returns_forward_looking():
    fake = _FakeClient({"mechanism":(1.0,0.9), "tag_ok":(1.0,0.9), "context_sufficient":(1.0,0.9),
                        "dispute_risk":(0.0,0.9), "forward_looking":(0.0,0.9)})  # 2-level → 0/1 = 0.0
    js = judge_jev(_cand(), client=fake)
    assert js.forward_looking == 0.0
```

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest "tests/test_evidence_pipeline.py::test_primary_quote_reciting_record_is_flagged_not_forward" "tests/test_judge_jev.py::test_judge_jev_returns_forward_looking" -v`
Expected: FAIL (pipeline doesn't pass `judge_forward_looking`; `judge_jev` doesn't score it).

- [ ] **Step 3: Implement**

In `src/evidence/pipeline.py`, extend the `GateResults(...)` at lines 65-68 — its last line is `judge_mechanism=js.mechanism)`; add the new argument:
```python
        judge_mechanism=js.mechanism, judge_forward_looking=js.forward_looking)
```

In `src/evidence/judge_jev.py`:
1. Add the criteria list (ordered record → forward, so index/(len-1) gives 0.0 for record, 1.0 for forward):
```python
_FORWARD = ["a recitation of past record or accomplishment — what the candidate already did",
            "a forward-looking stance or proposal — what the candidate would do or believes should happen"]
```
2. Add it to `_LEVELS`: `"forward_looking": _FORWARD`.
3. Add to the `q` dict in `build_questions`:
```python
        "forward_looking": "Is this a forward-looking stance/proposal, or a recitation of past record?",
```
4. Add to the returned `JudgeScores`:
```python
        forward_looking=_norm(a["forward_looking"], _FORWARD),
```
(`conf` already iterates `_LEVELS`, so it picks up the new key automatically.)

- [ ] **Step 4: Run to verify pass + full suite**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py tests/test_judge_jev.py -v` then `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q`
Expected: PASS; full suite green (including `test_judge_ab.py`, whose `JudgeScores(...)` sites rely on the `forward_looking=1.0` field default).

- [ ] **Step 5: Commit**

```bash
git add src/evidence/pipeline.py src/evidence/judge_jev.py tests/test_evidence_pipeline.py tests/test_judge_jev.py
git commit -m "feat(evidence): wire forward_looking through the pipeline + judge_jev

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Human-gated validation

**Not TDD; spends LLM; needs Chris. Do NOT run automatically.**

- [ ] **Step 1: Score the labeled gold + known record cases with the new judge**

Run the judge (deepseek, or gemini-flash if the swap has landed) over the 15-item labeled gold **plus** the known record cases — #13 ("I made a commitment… I did the first tax credit under Arnold") and a Bass record line ("On my first day I declared a state of emergency… we've moved thousands off the streets… doubled units in the pipeline"). A small script that builds each `QuoteCandidate`, calls `judge`, and prints `forward_looking` + whether `decide` now flags `judge:record-not-forward`. (Reuse the A/B harness pattern; judge-only, ~20 calls.)

- [ ] **Step 2: Report to Chris**

Report the `forward_looking` distribution: record cases (#13, Bass record) should score `< 0.5` and flag `judge:record-not-forward`; the forward accepts (#5, #7, #14) should score `≥ 0.5` and not gain the record flag; blended-but-forward quotes should not be over-flagged. Recommend keeping or tuning `FORWARD_MIN`. Then this gate is ready to merge (and it composes with the pending gemini-flash judge swap).

---

## Self-Review

**Spec coverage:** new `forward_looking` dimension (Task 2 judge, Task 1 models) ✓; disposition `FORWARD_MIN` + `judge:record-not-forward` (Task 1) ✓; flag-not-drop (a `reasons` append → FLAGGED, never a drop/green) ✓; pipeline wiring (Task 3) ✓; judge_jev parity (Task 3) ✓; model-agnostic (a judge-prompt/score dimension, no model-specific code) ✓; human-gated validation + threshold tuning (Task 4) ✓.

**Placeholder scan:** none — field defaults, prompt bullet, parse line, and disposition check are concrete; `FORWARD_MIN=0.5` is a stated tunable validated in Task 4.

**Type consistency:** `JudgeScores.forward_looking` (default 1.0) set by `parse_judge`/`judge_jev`; `GateResults.judge_forward_looking` (default None) set only by `_evaluate_quote`; `decide` reads it None-guarded — matching every construction site found (pipeline, tests, judge_ab) so nothing breaks.

**Non-breaking check:** field/param defaults mean the existing `JudgeScores(...)` (test_judge_ab) and `GateResults(...)` (disposition/report/models/eval tests, judge_ab) sites compile and behave unchanged (forward=1.0 inert, judge_forward_looking=None inert).

## Execution Handoff
1. **Subagent-Driven (recommended)** — fresh subagent per task, review after each, final review.
2. **Inline Execution** — with checkpoints.
