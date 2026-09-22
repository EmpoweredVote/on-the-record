# Judge Cog A/B — deepseek vs gemini-flash vs Jev

**Status:** Draft for review (brainstormed with Chris 2026-09-21)
**Repo:** on-the-record only — a new `judge_jev` adapter (`src/evidence/judge_jev.py`), an A/B harness
(`scripts/judge_ab.py`), `typesafe-sdk` as an **evaluation-only** dependency, tests. **No change to the
production pipeline** (`judge()` and the online runner are untouched); this measures a possible swap.
**Follows:** the concurrency slice (#253). Timing/latency work showed the **judge (deepseek, ~3.1s/call)
is the slow, throttle-prone hop** (gemini-flash ~0.7s, haiku ~1.0s). This A/B decides whether a
faster/more-reliable judge holds quality.

## Why

The judge cog gates mechanism/tag/context/dispute and is the pipeline's slowest LLM hop and its
parse-error source (unparseable JSON → worst-default flooding, seen earlier). Two candidates could be
better on speed **and** reliability: **gemini-flash** (4× faster, already integrated — a free baseline)
and **Jev** (a structured-decision model: typed answers + confidence, **0 parse errors by
construction**). This slice compares all three on the same gold and picks the judge on evidence.

## The three arms

- **deepseek** (current): `judge(cand, provider=get_provider("deepseek"))`.
- **gemini-flash**: `judge(cand, provider=get_provider("gemini-flash"))` — same prompt, faster model.
- **Jev**: a new `judge_jev(cand) -> JudgeScores` using `typesafe-sdk`.

### `judge_jev` (`src/evidence/judge_jev.py`)
One `TypeSafeClient().system_one(state=<quote + issue + context>, questions={...})` call with our four
dimensions as **`Score`** questions (ordered `criteria` levels; the model sees only descriptions). Read
back each `.answers[name].score` **normalized to 0..1** as `score / (len(criteria) - 1)`, and keep
`.confidence`. Returns the same `JudgeScores` shape the pipeline uses (plus confidences carried in
`notes`/a sidecar for the harness). No JSON parsing.

Starting criteria (the plan tunes them against gold):
- **mechanism** (3 levels): `["a bare goal, target/metric, or vague direction — no concrete lever",
  "gestures at an approach but names no specific instrument", "names a specific, contestable policy
  lever the candidate would use"]` → normalized /2 (maps to `MECHANISM_MIN=0.7`).
- **tag_ok** (2 levels): `["off-question / does not address the issue", "defensibly about the issue"]`.
- **context_sufficient** (2 levels): `["context too thin to vet the quote", "enough context to vet"]`.
- **dispute_risk** (3 levels, ordered safe→risky): `["clearly the speaker's own on-record position",
  "some ambiguity", "easily disownable / high out-of-context risk"]` → normalized /2 (our gate wants
  LOW dispute_risk).

`Score`, `confidence`, and the endpoint come from TypeSafe (`pip install typesafe-sdk`,
`TYPESAFE_API_KEY`, `POST https://api.typesafe.ai/v1/systemone`, model `jev-latest`).

## Gold

64 human-decided items in `inform.evidence_items` (`review_status IN ('accepted','rejected')` +
`review_reason`), pulled read-only as `(verbatim_text, context, issue, review_status, review_reason)`.
Only **2 accepted vs 62 rejected**, so quality is measured with three lenses rather than one:

1. **Agreement with the human verdict** on the **judge-relevant subset** — the 2 accepts (should score
   high mechanism, on-tag) and the rejects whose `review_reason = 'goal-only'` (the mechanism gate's
   job → should score low mechanism). Rejects for `not-verbatim`/`not-primary` are extractor/cross-check
   failures the judge does not assess — reported separately, not counted as judge misses.
2. **Inter-judge agreement** across the three arms on a **broader unlabeled sample** (the committed
   green+flagged items), with a **human spot-check of the divergences** — stress-tests quality beyond 64
   labels without needing more.
3. **Model properties**, independent of gold size: per-call **latency**, **cost**, and **parse-error
   rate** (deepseek/gemini can fail to parse; Jev = 0).

## Harness — `scripts/judge_ab.py`

Read-only DB → gold + sample. For each item and each arm: call the judge, record `JudgeScores`
(all four), wall-clock latency, whether the reply parsed (deepseek/gemini), and — via `decide()` with
the item's other gates held at their recorded values — the resulting green/flag. Then emit a report:

- **Per arm:** mean mechanism on accepts vs on `goal-only` rejects (separation), agreement with the
  human verdict on the judge-relevant subset, parse-error rate, mean/median latency, est. cost.
- **Inter-judge:** pairwise agreement on green/flag over the sample; a list of the divergent items for
  Chris to spot-check.
- **Jev extra:** mean confidence, and how many items are low-confidence (a possible "route to human"
  signal).

Writes a markdown report + a JSON of per-item per-arm scores to `docs/superpowers/spikes/`.

## Tests (offline)

- `judge_jev` score-normalization: given a stubbed TypeSafe client returning known `.score`/`.confidence`
  per question, `judge_jev` returns the correct 0..1 `JudgeScores` (e.g. a 3-level score of 1.43 → 0.715).
  (SDK client injected/mocked — no network.)
- The harness's metric functions (separation, agreement, parse-error counting, inter-judge agreement) are
  pure and unit-tested on small fixtures.
- Full suite green. The production `judge()`/pipeline are untouched (assert no diff there).

## Validation (human-gated)

Run `scripts/judge_ab.py` (spends LLM on the judge only, over ~64 gold + a bounded sample). Present the
report to Chris; he spot-checks the inter-judge divergences and picks the judge. Adopting the winner in
the pipeline is a **separate follow-up** (swap `get_provider("deepseek")`/add a Jev judge path in
`run_source`/`run_transcript_source`), not this slice.

## Risks / decisions

- **Thin accept gold (n=2):** mitigated by the inter-judge + spot-check lens and by the strong
  reject/`goal-only` signal; the A/B is directional on "greens real greens," conclusive on "flags
  goal-only" and on latency/cost/reliability.
- **Jev criteria wording drives its scores:** the starting criteria above are a first cut; the plan
  tunes them against a couple of known items before the full run.
- **New dependency (`typesafe-sdk`):** evaluation-only in this slice; committed to the main pipeline
  only if Jev wins.
- **Cost:** judge-only on gold + a bounded sample (~a few hundred calls). Note the OpenRouter key's
  ~$6.54 monthly remaining — keep the sample bounded; Jev billing is separate (TypeSafe).
- **No production change:** `judge()` and the runner are untouched, so a bad A/B can't affect live runs.

## Success criteria

- Offline: `judge_jev` normalizes scores correctly (mocked client); harness metric functions unit-tested;
  production judge/pipeline unchanged; full suite green.
- Live (gated): the report gives, per arm, mechanism-separation + human agreement + parse-error rate +
  latency + cost, plus inter-judge divergences to spot-check — enough for Chris to choose the judge.

## Execution notes

- Keys: `OPENROUTER_API_KEY` (deepseek/gemini) + `TYPESAFE_API_KEY` (Jev). Run with the MAIN checkout
  `.venv/bin/python`; `--env-file <ev-accounts>/backend/.env` for the read-only gold.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
