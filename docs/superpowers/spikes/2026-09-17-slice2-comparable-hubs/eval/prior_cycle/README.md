# Flag-vs-guard eval (prior-cycle behavior)

Measures the discovery classifier's **prior-cycle** behavior after the 2026-09-18
flag-not-guard change (Slice 2 spec, "Decided 2026-09-18"):

- a tracked candidate's **current-cycle** own answers → `relevant=true`, `prior_cycle=false`;
- their own **prior-cycle** answers → `relevant=true`, `prior_cycle=true` (flagged, not rejected);
- a **wrong-contest** page (evaluated against a race whose candidates it is not about) → `relevant=false` (guarded).

Each case in `cases.json` is a real Ballotpedia page fetched through the production
peek (which carries the cycle year), classified by the production model.

## Run

```bash
OPENROUTER_API_KEY=... .venv/bin/python \
  docs/superpowers/spikes/2026-09-17-slice2-comparable-hubs/eval/prior_cycle/run.py --runs 5
```

Live network + LLM (not a pytest test). The metric is noisy — use `--runs N`
(majority vote per case) and treat a change as real only when it beats the noise.

## Baseline 2026-09-18 (deepseek, runs=3): 2/4

Surfaced two calibration issues to fix before the flag is fully trustworthy:

1. **Over-flagging current as prior.** `bass-current` (LA Mayor 2026, her survey is
   2026) is flagged `prior_cycle=true, year=2026` — but 2026 is the current cycle.
   Fix: the classify prompt should set `prior_cycle` true ONLY when the content's
   `source_cycle_year` is EARLIER than the race's cycle year; equal years → false.
2. **Wrong-contest guard leak.** `wrong-contest-bass-vs-tx-senate` sometimes returns
   `relevant=true` because the page carries the candidate's own words — the guard
   must reject when the page's candidate is not in the race's roster, own-words or
   not. (In production the name-prefilter catches most of this before classify; the
   classifier guard is the backstop.)

`gomez-prior` (prior_cycle=true, 2020) and `raman-current` (prior_cycle=false, 2026)
pass. Re-run after the prompt refinement to confirm the trade.
