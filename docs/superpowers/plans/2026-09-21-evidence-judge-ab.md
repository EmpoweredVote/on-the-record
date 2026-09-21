# Judge Cog A/B Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Compare three judge implementations (deepseek, gemini-flash, Jev) on our saved gold for quality, latency, cost, and parse-error rate, and produce a report that lets Chris pick the judge — without touching the production pipeline.

**Architecture:** A `judge_jev` adapter (Jev `Score` questions → `JudgeScores`), pure harness metric functions, and an offline-testable A/B harness script that runs the three arms over gold pulled read-only from `inform.evidence_items`. `judge()` and the online runner are untouched.

**Tech Stack:** Python 3, `typesafe-sdk` (new, evaluation-only, lazy-imported), `openai`-compat providers (existing), pytest.

## Global Constraints

- **No production change.** `src/evidence/judge.py`, `pipeline.py`, and `scripts/evidence_slice.py` are NOT modified. A bad A/B cannot affect live runs. Adopting a winner is a separate later slice.
- **Jev output normalized to 0..1:** `normalized = score / (len(criteria) - 1)` (Jev `.score` ∈ [0, len-1], probability-weighted). Confidence ∈ [0,1] is carried for the report.
- **Offline tests never hit the network or a real Jev/LLM call:** inject a fake TypeSafe client and fake judge callables. The real SDK is lazy-imported only in the live path.
- **Gold is read-only** from `inform.evidence_items`; the harness writes only artifacts (a report + JSON), never the DB.
- Keys: `OPENROUTER_API_KEY` (deepseek/gemini), `TYPESAFE_API_KEY` (Jev). Run tests + harness with the MAIN checkout `.venv/bin/python`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `src/evidence/judge_jev.py` — CREATE: `judge_jev(cand, *, client=None) -> JudgeScores` + `build_questions()` + `_norm`.
- `scripts/judge_ab.py` — CREATE: gold loading, the three arms, `run_ab`, `render_report`, `main`.
- `requirements.txt` — ADD `typesafe-sdk`.
- `tests/test_judge_jev.py`, `tests/test_judge_ab.py` — CREATE.

---

### Task 1: `judge_jev` adapter (+ dependency)

**Files:**
- Create: `src/evidence/judge_jev.py`
- Modify: `requirements.txt`
- Test: `tests/test_judge_jev.py`

**Interfaces:** `judge_jev(cand, *, client=None) -> JudgeScores` (same shape as `judge()`, with per-dimension Jev confidences packed into `notes` as JSON). `build_questions() -> dict`.

- [ ] **Step 0: Install and INTROSPECT the real SDK before coding to the docs**

```bash
~/Documents/GitHub/on-the-record/.venv/bin/pip install typesafe-sdk
~/Documents/GitHub/on-the-record/.venv/bin/python - <<'PY'
import typesafe_sdk as t, inspect
print("exports:", [n for n in dir(t) if not n.startswith("_")])
from typesafe_sdk import Score, TypeSafeClient
print("Score init:", inspect.signature(Score.__init__))
print("system_one:", inspect.signature(TypeSafeClient.system_one))
PY
```

Confirm the real names/signatures match the design (`Score(instructions=, criteria=)`, `client.system_one(state=, questions=)`, `response.answers[name].score` / `.confidence`). **If the real SDK differs from the docs, adapt `judge_jev` to the REAL shape and note the deviation in the report file.** (The docs are the only prior source; the installed package is authoritative.)

- [ ] **Step 1: Write the failing normalization test (fake client, no network)**

```python
# tests/test_judge_jev.py
from src.evidence.judge_jev import judge_jev
from src.evidence.models import QuoteCandidate

class _Ans:
    def __init__(self, score, confidence): self.score=score; self.confidence=confidence
class _Resp:
    def __init__(self, answers): self.answers=answers
class _FakeClient:
    """Returns fixed score/confidence per question name; records the state it saw."""
    def __init__(self, scored): self._scored=scored; self.state=None; self.questions=None
    def system_one(self, *, state, questions):
        self.state=state; self.questions=questions
        return _Resp({k: _Ans(*self._scored[k]) for k in questions})

def _cand():
    return QuoteCandidate(text="We will build 40,000 units by cutting permit timelines.",
                          context="housing Q", issue="housing")

def test_judge_jev_normalizes_scores_to_0_1():
    # mechanism uses a 3-level scale: raw score 1.43 -> 1.43/2 = 0.715
    fake = _FakeClient({"mechanism":(1.43,0.35), "tag_ok":(1.0,0.9),
                        "context_sufficient":(1.0,0.8), "dispute_risk":(0.0,0.95)})
    js = judge_jev(_cand(), client=fake)
    assert abs(js.mechanism - 0.715) < 1e-6
    assert js.tag_ok == 1.0 and js.context_sufficient == 1.0 and js.dispute_risk == 0.0
    import json; assert "confidence" in json.loads(js.notes)          # confidences carried
    assert "QUOTE:" in fake.state and "housing" in fake.state          # state built from cand
```

- [ ] **Step 2: Run to verify it fails**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_judge_jev.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement (adapt to the Step-0 real shape if it differs)**

```python
# src/evidence/judge_jev.py
from __future__ import annotations
import json
from .models import JudgeScores

_MECHANISM = ["a bare goal, target/metric, or vague direction — no concrete lever",
              "gestures at an approach but names no specific instrument",
              "names a specific, contestable policy lever the candidate would use"]
_TAG = ["off-question / does not address the issue", "defensibly about the issue"]
_CONTEXT = ["context too thin to vet the quote", "enough context to vet the quote"]
_DISPUTE = ["clearly the speaker's own on-record position",
            "some ambiguity about attribution or context",
            "easily disownable / high out-of-context risk"]
_LEVELS = {"mechanism": _MECHANISM, "tag_ok": _TAG,
           "context_sufficient": _CONTEXT, "dispute_risk": _DISPUTE}


def build_questions() -> dict:
    from typesafe_sdk import Score  # lazy: optional dependency
    q = {
        "mechanism": "Does the quote name a specific, contestable policy lever the candidate would use?",
        "tag_ok": "Is the ISSUE tag defensible for this quote?",
        "context_sufficient": "Is there enough context to vet the quote?",
        "dispute_risk": "How easily could the speaker disown this as out-of-context or never said?",
    }
    return {k: Score(instructions=q[k], criteria=_LEVELS[k]) for k in q}


def _norm(ans, levels) -> float:
    denom = (len(levels) - 1) or 1
    return max(0.0, min(1.0, float(ans.score) / denom))


def judge_jev(cand, *, client=None) -> JudgeScores:
    if client is None:
        from typesafe_sdk import TypeSafeClient  # lazy
        client = TypeSafeClient()
    state = f"ISSUE: {cand.issue}\nQUOTE: {cand.text}\nCONTEXT: {cand.context}"
    a = client.system_one(state=state, questions=build_questions()).answers
    conf = {k: getattr(a[k], "confidence", None) for k in _LEVELS}
    return JudgeScores(
        tag_ok=_norm(a["tag_ok"], _TAG),
        context_sufficient=_norm(a["context_sufficient"], _CONTEXT),
        dispute_risk=_norm(a["dispute_risk"], _DISPUTE),
        mechanism=_norm(a["mechanism"], _MECHANISM),
        notes=json.dumps({"confidence": conf}))
```

Add `typesafe-sdk` to `requirements.txt`.

- [ ] **Step 4: Run to verify pass + import check**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_judge_jev.py -v` and `~/Documents/GitHub/on-the-record/.venv/bin/python -c "import src.evidence.judge_jev"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/judge_jev.py tests/test_judge_jev.py requirements.txt
git commit -m "feat(evidence): judge_jev — Jev Score-question judge adapter (eval)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Pure harness metric functions

**Files:**
- Create/extend: `scripts/judge_ab.py` (metric helpers only in this task)
- Test: `tests/test_judge_ab.py`

**Interfaces (pure):**
- `mechanism_separation(rows) -> {"accept_mean","goalonly_mean","gap"}` — mean `mechanism` on accepted items vs on `goal-only`-rejected items.
- `human_agreement(rows) -> {"n","agree","rate"}` — over the judge-relevant subset (accepted + `goal-only`-rejected), a green disposition should match `accepted`, a flagged should match `rejected`.
- `parse_error_rate(rows) -> float`.
- `inter_judge_agreement(rows_by_arm) -> {pair: rate}` — pairwise green/flag agreement across arms over the sample; plus `divergent_ids(rows_by_arm) -> list`.

Each `row` is `{id, human_status, review_reason, arm, mechanism, disposition, parse_ok, latency}`.

- [ ] **Step 1: Write failing tests** (small fixtures exercising each metric: a known accept + a known goal-only reject give a positive `gap`; agreement counts green↔accept / flag↔reject; parse_error_rate counts `parse_ok=False`; inter-judge agreement is 1.0 when two arms match on every id and <1 with one divergence, and `divergent_ids` lists it).

- [ ] **Step 2: Run to verify fail.** `pytest tests/test_judge_ab.py -v` → FAIL.

- [ ] **Step 3: Implement the pure helpers** in `scripts/judge_ab.py` (plain dict/list math; no I/O).

- [ ] **Step 4: Run to verify pass.** `pytest tests/test_judge_ab.py -v` → PASS.

- [ ] **Step 5: Commit** (`test(evidence): A/B metric functions`).

---

### Task 3: The A/B harness (`scripts/judge_ab.py`)

**Files:**
- Modify: `scripts/judge_ab.py`
- Test: `tests/test_judge_ab.py`

**Interfaces:**
- `load_gold(conn) -> list[dict]` — read `verbatim_text, context, issue, review_status, review_reason` for `review_status IN ('accepted','rejected')` (read-only).
- `ARMS` — `{"deepseek": fn, "gemini-flash": fn, "jev": fn}`, each `cand -> (JudgeScores, parse_ok)` (deepseek/gemini via `judge()` with the provider, detecting a parse failure as all-worst-default; jev via `judge_jev`, `parse_ok` always True).
- `run_ab(gold, arms, *, decide_fn) -> rows_by_arm` — for each item×arm: time the call, get `JudgeScores`, compute disposition via `decide_fn` with the item's other gates held definitional-true (own_words/primary/in_context/tag_agree=True, so the JUDGE drives green/flag), record the row.
- `render_report(rows_by_arm, gold) -> str` — the markdown using Task-2 metrics.

- [ ] **Step 1: Write the failing test** — `run_ab` over a tiny fake `gold` (2 items: one accepted, one `goal-only` rejected) with **fake arm callables** (return canned `JudgeScores` + timing) and a real `decide`; assert `rows_by_arm` has one row per item per arm with the right `disposition`/`human_status`, and that `render_report` returns a string containing each arm name and the separation/agreement numbers. No DB, no LLM.

- [ ] **Step 2: Run to verify fail.**

- [ ] **Step 3: Implement** `load_gold` (parameterized SQL), `ARMS` (deepseek/gemini wrap `judge()`; a parse failure is detected as `JudgeScores` with the worst defaults `tag_ok==0 and mechanism==0 and dispute_risk==1`; jev wraps `judge_jev`), `run_ab` (wall-clock per call; `decide` with definitional other-gates), `render_report`, and `main` (`--env-file`, `--sample N` for the inter-judge sample size, `--out`). Lazy-import providers/`judge_jev` inside `main`/arm construction so the tests (which pass fake arms) need no keys.

- [ ] **Step 4: Run tests + import check.** `pytest tests/test_judge_ab.py -v`; `python -c "import scripts.judge_ab"`. Full suite green.

- [ ] **Step 5: Commit** (`feat(evidence): judge_ab A/B harness (deepseek/gemini/jev)`).

---

### Task 4: Human-gated A/B run

**Not TDD; spends LLM (OpenRouter + TypeSafe); needs Chris. Do NOT run automatically.**

- [ ] **Step 1: Run the harness**

```bash
export OPENROUTER_API_KEY=$(grep -E '^OPENROUTER_API_KEY=' ~/Documents/GitHub/on-the-record/.env.local | head -1 | cut -d= -f2- | tr -d '"'"'"'\r')
export TYPESAFE_API_KEY=<Chris's Jev key>   # from Chris; do not hardcode
~/Documents/GitHub/on-the-record/.venv/bin/python scripts/judge_ab.py \
  --env-file ~/Documents/GitHub/ev-accounts/backend/.env --sample 40 \
  --out docs/superpowers/spikes/2026-09-19-evidence-trust-core/judge-ab
```

- [ ] **Step 2: Report to Chris** — the per-arm table (mechanism separation, human agreement on the judge-relevant subset, parse-error rate, latency, cost), the inter-judge divergences to spot-check, and Jev's confidence distribution. Recommend keep-deepseek vs switch-to-gemini vs switch-to-Jev. Adoption in the pipeline is a separate follow-up. Watch the OpenRouter ~$6.54 monthly remaining; keep `--sample` bounded.

---

## Self-Review

**Spec coverage:** three arms (Task 1 jev + Task 3 deepseek/gemini) ✓; Jev Score→0..1 normalization + confidence (Task 1) ✓; gold from DB read-only + judge-relevant subset via review_reason (Task 3 load_gold + Task 2 human_agreement) ✓; inter-judge agreement + divergences (Task 2) ✓; latency/cost/parse-error (Task 3 run_ab + Task 2) ✓; report + recommendation (Tasks 3–4) ✓; no production change (Global Constraints; judge.py/pipeline/runner untouched) ✓.

**Placeholder scan:** none — the one genuine unknown (real SDK shape) is handled by Task 1 Step 0 introspection with an explicit "adapt to real shape" instruction, not a guess left in code.

**Type consistency:** `judge_jev` returns `JudgeScores` (pipeline shape) so the arms are interchangeable in `run_ab`; `decide_fn` is the real `decide`; rows carry the fields Task-2 metrics consume.

**Honesty:** the real Jev behavior + criteria quality are only confirmed in the human-gated Task 4; Task 1 tests the normalization against a faked client (documented shape). Thin accept-gold is acknowledged; inter-judge + spot-check compensate.

## Execution Handoff
1. **Subagent-Driven (recommended)** — fresh subagent per task, review after each, final review.
2. **Inline Execution** — with checkpoints.
