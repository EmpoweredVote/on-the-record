# Evidence Pipeline Concurrency Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Parallelize the per-quote cross-check+judge calls with a bounded thread pool so a transcript-heavy candidate runs ~4–6× faster, with byte-identical output.

**Architecture:** A small order-preserving `_concurrent_map` (ThreadPoolExecutor) in `pipeline.py`; `run_source` and `run_transcript_source` map `_evaluate_quote` over their quotes through it; extraction and the runner stay sequential so one worker cap governs total concurrency. A `--workers` flag plumbs the cap.

**Tech Stack:** Python 3 `concurrent.futures.ThreadPoolExecutor`, pytest.

## Global Constraints

- **Output must be identical to sequential.** `_concurrent_map` returns results in input order (via `ThreadPoolExecutor.map`); providers run at temperature 0. No change to which calls are made — only that they run concurrently. No cost or accuracy change.
- **Bound total concurrency with one cap.** Extraction (`extract_quotes`) stays sequential and the runner stays sequential over candidates and over sources within a candidate — so the only concurrency is per-quote within a source, bounded by `max_workers`. No nested pools.
- **Default cap 6**, overridable via `EVIDENCE_MAX_WORKERS` env and the runner `--workers` flag. New `max_workers` params are keyword-only with default `None` (→ default) so existing callers/tests are unaffected.
- **Real provider clients are used concurrently** (OpenAI-compatible clients are safe for concurrent requests; the client retries 429). **Test fakes must be made thread-safe.**
- Run tests + runner with the MAIN checkout venv: `~/Documents/GitHub/on-the-record/.venv/bin/python`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

## File Structure

- `src/evidence/pipeline.py` — ADD `_concurrent_map` + `_DEFAULT_WORKERS` + imports; rewire `run_source`/`run_transcript_source` per-quote loops; add `max_workers=None` to those two and `run_candidate`.
- `scripts/evidence_slice.py` — ADD `--workers`; thread into `run_candidate`.
- `tests/test_evidence_pipeline.py` — make the `FP` fake thread-safe; add concurrency tests.
- `tests/test_evidence_slice.py` — extend for `--workers`.

---

### Task 1: `_concurrent_map` — bounded, order-preserving

**Files:**
- Modify: `src/evidence/pipeline.py`
- Test: `tests/test_evidence_pipeline.py`

**Interfaces:** `_concurrent_map(fn, items, max_workers=None) -> list` and module `_DEFAULT_WORKERS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_evidence_pipeline.py
import time
from src.evidence.pipeline import _concurrent_map

def test_concurrent_map_preserves_input_order():
    # later items finish sooner; ex.map must still return in input order
    def fn(n): time.sleep((5 - n) * 0.02); return n
    assert _concurrent_map(fn, [0,1,2,3,4], max_workers=4) == [0,1,2,3,4]

def test_concurrent_map_runs_every_item():
    seen = []
    import threading; lock = threading.Lock()
    def fn(x):
        with lock: seen.append(x)
        return x * 2
    out = _concurrent_map(fn, [1,2,3], max_workers=3)
    assert out == [2,4,6] and sorted(seen) == [1,2,3]

def test_concurrent_map_sequential_fastpath():
    calls = []
    def fn(x): calls.append(x); return x
    assert _concurrent_map(fn, [7], max_workers=8) == [7]      # single item → no pool
    assert _concurrent_map(fn, [1,2], max_workers=1) == [1,2]  # workers<=1 → sequential
```

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -k concurrent_map -v`
Expected: FAIL (ImportError).

- [ ] **Step 3: Implement**

```python
# src/evidence/pipeline.py  (top-of-file additions)
import os
from concurrent.futures import ThreadPoolExecutor

_DEFAULT_WORKERS = int(os.environ.get("EVIDENCE_MAX_WORKERS", "6"))


def _concurrent_map(fn, items, max_workers=None) -> list:
    """Map fn over items with a bounded thread pool, returning results in INPUT
    order (ThreadPoolExecutor.map preserves order). Falls back to a sequential
    list comprehension for a single item or workers<=1, so unit tests and the
    common single-quote source stay pool-free and deterministic."""
    items = list(items)
    workers = max_workers or _DEFAULT_WORKERS
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))
```

- [ ] **Step 4: Run to verify pass**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -k concurrent_map -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/evidence/pipeline.py tests/test_evidence_pipeline.py
git commit -m "feat(evidence): _concurrent_map — bounded, order-preserving parallel map

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 2: Parallelize per-quote evaluation in run_source + run_transcript_source

**Files:**
- Modify: `src/evidence/pipeline.py`
- Test: `tests/test_evidence_pipeline.py`

**Interfaces:** `run_source`, `run_transcript_source`, `run_candidate` each gain keyword-only `max_workers=None`, passed down to `_concurrent_map`.

- [ ] **Step 1: Make the `FP` fake thread-safe + add a deterministic multi-quote fake**

At the top of `tests/test_evidence_pipeline.py`, guard `FP`'s pop with a lock:

```python
import threading
class FP:
    def __init__(self, r): self._r=list(r); self.prompts=[]; self._lock=threading.Lock()
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        with self._lock:
            self.prompts.append(prompt)
            return self._r.pop(0)
```

For concurrency determinism, add a content-addressed fake (order-independent, thread-safe):

```python
class RoleFP:
    """Returns a fixed reply regardless of call order — keyed by nothing, safe under threads."""
    def __init__(self, reply): self._reply=reply; self.prompts=[]; self._lock=threading.Lock()
    def complete(self, prompt, *, max_tokens, temperature, system=None):
        with self._lock: self.prompts.append(prompt)
        return self._reply
```

- [ ] **Step 2: Write the failing determinism tests**

```python
# tests/test_evidence_pipeline.py
def _role_providers(extract, cross, jud):
    return Providers(extractor=FP([extract]), crosschecker=RoleFP(cross), judge=RoleFP(jud))

def _two_quote_extract():
    return json.dumps({"quotes":[
        {"text":"We will build 40,000 units by cutting permit timelines.","context":"h","issue":"housing",
         "is_own_words":True,"is_primary_venue":True},
        {"text":"We will hire 250 civilian staff to free up officers.","context":"p","issue":"policing",
         "is_own_words":True,"is_primary_venue":True}]})

def test_run_source_identical_workers_1_vs_4():
    cross = json.dumps({"own_words":True,"in_context":True,"primary":True,"tag_ok":True})
    jud = json.dumps({"tag_ok":0.9,"context_sufficient":0.9,"dispute_risk":0.1,"mechanism":0.9})
    def run(w):
        items, leads = run_source(politician_id="p1", source_url="https://karenbass.com/x",
            cited_via=None, providers=_role_providers(_two_quote_extract(), cross, jud),
            fetcher=lambda u: "src", candidate_name="Karen Bass", batch_id="b1", max_workers=w)
        return [(i.issue, i.status) for i in items]
    assert run(1) == run(4)
    assert len(run(4)) == 2 and all(s==Status.GREEN.value for _,s in run(4))
```

(Add the analogous `test_run_transcript_source_identical_workers_1_vs_4` using a `TranscriptSource` whose `full_text` contains both quote texts and `segments` covering them.)

- [ ] **Step 3: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -k "workers_1_vs_4" -v`
Expected: FAIL (`max_workers` not accepted yet).

- [ ] **Step 4: Implement the rewire**

Add `max_workers=None` (keyword-only) to `run_source`, `run_transcript_source`, `run_candidate`.

`run_source` — partition leads (no LLM) then parallelize the evaluated quotes:

```python
    cands = list(extract_quotes(text, candidate_name=candidate_name,
                                provider=providers.extractor))
    def _is_lead(c): return (not c.is_primary_venue) or c.reported_event
    leads = [to_lead(c, politician_id=politician_id, secondary_url=source_url)
             for c in cands if _is_lead(c)]
    to_eval = [c for c in cands if not _is_lead(c)]
    items = _concurrent_map(
        lambda c: _evaluate_quote(c, text, politician_id=politician_id, source_url=source_url,
            cited_via=cited_via, deep_link=source_url, source_type=source_type,
            providers=providers, candidate_name=candidate_name, prov=prov),
        to_eval, max_workers=max_workers)
    return items, leads
```

`run_transcript_source`:

```python
    cands = list(extract_quotes(source.full_text, candidate_name=candidate_name,
                                provider=providers.extractor))
    items = _concurrent_map(
        lambda c: _evaluate_quote(c, source.full_text, politician_id=politician_id,
            source_url=source.source_url, cited_via=source.meeting_id,
            deep_link=_deep_link(source, c.text), source_type=SourceType.PRIMARY.value,
            providers=providers, candidate_name=candidate_name, prov=prov,
            crosscheck_text=_local_window(source.full_text, c.text), definitional_primary=True),
        cands, max_workers=max_workers)
    return items, []
```

`run_candidate`: add `max_workers=None` and pass `max_workers=max_workers` into each `run_source` / `run_transcript_source` call.

- [ ] **Step 5: Run pipeline tests + full evidence suite**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_pipeline.py -v` then `tests/ -k evidence -q`
Expected: PASS (new determinism tests + all existing tests unchanged — single-quote cases hit the sequential fast-path; multi-quote/thread-safety covered by the locked fakes).

- [ ] **Step 6: Commit**

```bash
git add src/evidence/pipeline.py tests/test_evidence_pipeline.py
git commit -m "feat(evidence): run quotes concurrently (bounded pool), identical output

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 3: Runner `--workers` flag

**Files:**
- Modify: `scripts/evidence_slice.py`
- Test: `tests/test_evidence_slice.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_evidence_slice.py
import scripts.evidence_slice as s
def test_workers_flag_default_and_parse():
    assert s.build_parser().parse_args([]).workers is None or isinstance(s.build_parser().parse_args([]).workers, int)
    assert s.build_parser().parse_args(["--workers","3"]).workers == 3
```

- [ ] **Step 2: Run to verify fail**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_slice.py -k workers -v`
Expected: FAIL (no `workers` arg).

- [ ] **Step 3: Implement**

In `build_parser`: `ap.add_argument("--workers", type=int, default=None, help="Max concurrent per-quote LLM calls (default: EVIDENCE_MAX_WORKERS or 6)")`.
In `main`, pass `max_workers=args.workers` into each `run_candidate(...)` call.

- [ ] **Step 4: Verify**

Run: `~/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_evidence_slice.py -v` then `tests/ -k evidence -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/evidence_slice.py tests/test_evidence_slice.py
git commit -m "feat(evidence): evidence_slice --workers (concurrency cap)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

### Task 4: Human-gated timing validation

**Not TDD; spends LLM + network; needs Chris. Do NOT run automatically.**

- [ ] **Step 1: Re-run one transcript candidate and time it**

```bash
export OPENROUTER_API_KEY=$(grep -E '^OPENROUTER_API_KEY=' ~/Documents/GitHub/on-the-record/.env.local | head -1 | cut -d= -f2- | tr -d '"'"'"'\r')
time ~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py \
  --candidate 26dbe16a-9dff-42c0-939f-5b5e529063ca --source transcripts --workers 6 \
  --env-file ~/Documents/GitHub/ev-accounts/backend/.env \
  --out docs/superpowers/spikes/2026-09-19-evidence-trust-core/transcripts-conc-raman
```

- [ ] **Step 2: Report to Chris**

Report wall-clock vs the prior sequential Raman run, and confirm the green/flagged counts are **identical** (25 green baseline) — same items, faster. If counts differ, stop and investigate (determinism regression).

---

## Self-Review

**Spec coverage:** `_concurrent_map` bounded+ordered (Task 1); per-quote parallelism in both run functions with leads partitioned out (Task 2); one cap, extraction+runner sequential (Task 2 leaves them serial); `--workers` + default 6 + env (Tasks 1,3); determinism tests + thread-safe fakes (Task 2); timing validation (Task 4). ✓

**Placeholder scan:** none — helper, rewires, and tests are concrete.

**Type consistency:** `max_workers=None` keyword-only added consistently to `run_source`/`run_transcript_source`/`run_candidate`; `_concurrent_map(fn, items, max_workers)` used identically at both sites; `_evaluate_quote`/`_deep_link`/`_local_window` signatures unchanged.

**Honesty:** output-identical claim is enforced by the workers=1-vs-4 determinism tests + `ex.map` order preservation; the timing win is confirmed only in the human-gated Task 4, not asserted in a unit test.

## Execution Handoff
1. **Subagent-Driven (recommended)** — fresh subagent per task, review after each, final review.
2. **Inline Execution** — with checkpoints.
