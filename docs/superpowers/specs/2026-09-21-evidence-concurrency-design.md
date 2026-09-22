# Evidence Pipeline Concurrency — parallelize per-quote evaluation

**Status:** Draft for review (brainstormed with Chris 2026-09-21)
**Repo:** on-the-record only — `src/evidence/pipeline.py` (+ a small helper), `scripts/evidence_slice.py`
(a `--workers` flag), tests. No schema change; no ev-accounts change; no model/prompt change.
**Follows:** the transcript lane (#252). A transcript-heavy candidate takes ~20 min because the
pipeline makes its LLM calls one at a time. This parallelizes the dominant call path with no change to
cost or output.

## Why

For each source the pipeline evaluates every extracted quote with two LLM calls (cross-check + judge)
**in series**. On Bass that is ~350 per-quote calls versus ~14 extraction windows — so the per-quote
step is ~96% of the calls and essentially all of the wall-clock. These calls are independent and
I/O-bound (waiting on the network), so running a bounded number concurrently cuts wall-clock ~4–6×
with **no change to what is computed** (same calls, temperature 0, results reassembled in order).

## Scope

- **Parallelize the per-quote `_evaluate_quote` calls** across quotes, in both `run_source` and
  `run_transcript_source`.
- **Leave extraction (`extract_quotes`) sequential** — ~14 window calls is a small fraction, and
  keeping it serial avoids ordering/edge-case churn for almost no lost speed. (Revisit only if needed.)
- **Leave the runner sequential over candidates and over sources within a candidate** — so total
  in-flight LLM calls stay bounded by the single worker cap (extraction and per-quote phases within a
  source do not overlap, and sources run one at a time).

## Design

### A bounded, order-preserving map — `src/evidence/pipeline.py`
```
_DEFAULT_WORKERS = int(os.environ.get("EVIDENCE_MAX_WORKERS", "6"))

def _concurrent_map(fn, items, max_workers=None) -> list:
    items = list(items)
    workers = max_workers or _DEFAULT_WORKERS
    if workers <= 1 or len(items) <= 1:
        return [fn(x) for x in items]            # sequential fast-path (and single-item tests)
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(fn, items))            # ex.map preserves input order
```
`ex.map` returns results in input order, so output is deterministic and identical to the sequential
version. The sequential fast-path keeps single-quote calls (most unit tests) pool-free.

### `run_transcript_source`
Replace the per-quote `for` loop with:
```
items = _concurrent_map(
    lambda cand: _evaluate_quote(cand, source.full_text, ..., definitional_primary=True), cands,
    max_workers=max_workers)
```
(where `cands = extract_quotes(source.full_text, ...)`).

### `run_source`
The lead branch (`not cand.is_primary_venue or cand.reported_event`) does no LLM work — partition
first, then parallelize only the evaluated quotes:
```
cands = extract_quotes(text, ...)
leads = [to_lead(c, ...) for c in cands if (not c.is_primary_venue) or c.reported_event]
to_eval = [c for c in cands if not ((not c.is_primary_venue) or c.reported_event)]
items = _concurrent_map(lambda c: _evaluate_quote(c, text, ..., deep_link=source_url,
                        source_type=source_type), to_eval, max_workers=max_workers)
```
Order of `items`/`leads` is preserved (partition keeps source order).

### Worker cap plumbing
- `run_source` / `run_transcript_source` / `run_candidate` gain a keyword-only `max_workers=None`
  (None → `_DEFAULT_WORKERS`); existing callers/tests are unaffected.
- `scripts/evidence_slice.py` adds `--workers N` (default `_DEFAULT_WORKERS`) and threads it into
  `run_candidate`.

### Thread-safety
- The real provider clients (OpenAI-compatible) are safe for concurrent requests — each `.complete`
  is an independent HTTP call. OpenRouter rate limits are respected by the bounded worker count plus
  the client's existing 429 retry.
- **Test fakes** that `pop` from a response list are not thread-safe; make their `complete` guard the
  pop with a `threading.Lock` (small, keeps determinism since results are reassembled in order). A
  multi-quote test should key its fake responses by prompt content rather than pop-order where order
  would otherwise be ambiguous.

## Tests (offline)
- `_concurrent_map`: preserves input order under concurrency (e.g. `fn` sleeps inversely to input so a
  naive pool would reorder — assert output equals input order); runs `fn` for every item; the
  `workers<=1` / single-item path runs sequentially.
- `run_source` / `run_transcript_source`: outcomes are **identical** with `max_workers=1` and
  `max_workers=4` for a multi-quote input (determinism), using a thread-safe fake; leads/items order
  preserved.
- Existing pipeline tests stay green unchanged (single-quote cases hit the sequential fast-path).
- Full suite green.

## Validation (light, human-gated)
Re-run one transcript candidate (e.g. Raman) with `--source transcripts` and compare wall-clock and
**identical green/flagged counts** against the prior sequential run (28/25 baseline). Expect a ~4–6×
time drop with the same items. Spends LLM only for the timing datapoint.

## Risks / decisions
- **Determinism** hinges on `ex.map` preserving order + temperature-0 providers — both hold; the tests
  lock it in.
- **Rate limits:** if OpenRouter 429s under load, lower `--workers`; the client already retries. Default
  6 is conservative.
- **No nested pools:** extraction stays sequential and the runner stays sequential over sources/
  candidates, so a single cap governs total concurrency and there is no pool-within-pool deadlock risk.
- **Cost unchanged:** identical calls; only wall-clock changes.

## Success criteria
- Offline: `_concurrent_map` order/coverage/fast-path tests pass; run_source/run_transcript_source give
  identical outcomes at workers=1 vs 4; full suite green.
- Live (light): the transcript re-run is markedly faster with identical green/flagged counts.

## Execution notes
- Run with the MAIN checkout `.venv/bin/python`; `OPENROUTER_API_KEY`; `--env-file <ev-accounts>/backend/.env`.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
