# Slice 2 — Phase 4: comparable-source recall eval (repo regression test) — design

Date: 2026-09-18
Status: Draft (pending spec review)
Area: Discovery engine eval harness (`scripts/`, `src/discovery/`, `tests/`). Phase 4 of
[[2026-09-17-slice2-comparable-source-hubs-design]]. Promotes the retained spike recall
asset into a first-class, repo-committed regression test.

## Origin

The Slice-2 spike (`docs/superpowers/spikes/2026-09-17-slice2-comparable-hubs/`) left two
retained assets: an 8-race hand-labeled ground truth of the comparable common-question
sources that exist per race (`eval/SUMMARY.md` + per-race files, distilled into
`bakeoff/ground_truth.json`), and a bakeoff harness that scored recall/precision of three
**rejected** agentic engines against it. Slice 2 shipped a different design: a maintained
hub registry polled per race (the hub lane — `src/discovery/hubs.py` +
`hub_search.py` + `classify.py`). The spike bakeoff therefore no longer measures the
shipped system.

Phase 4 replaces the bakeoff's engine comparison with a regression test of the **shipped
hub lane**: for each ground-truth race, run the real lane and measure how many of the
comparable sources it actually surfaces. Run it as hubs are added and the classifier is
tuned; a change must beat the noise band (the eval metric is noisy — average multiple
runs, per [[summary-classify-eval-interpretation]]).

## Goal

A manual (online) harness that measures **recall of comparable common-question sources per
race** for the shipped hub lane, against the hand-labeled ground truth. It exercises the
real code path end to end:

`hubs_for_race` → `hub_search.raw_items_for_race` (live Tavily) → `classify.classify_item`
(live OpenRouter + production page peek).

Non-goals / explicitly out of scope:
- Comparing engines (the spike bakeoff's job — the rejected A/B/C loops are not run).
- Scoring the feed lane (public-media / YouTube outlets polled via `source_outlets`) — this
  eval covers only the `scoped_search` hub lane, so overall recall is low by construction.
- Being a pytest test. The harness makes live Tavily + OpenRouter calls. Pytest stays
  offline (conftest deletes `DATABASE_URL`); only the **pure scorer** is unit-tested.
- Scraping ToS-barred sources. VOTE411/LWV is pointer-only and is never searched by the
  lane, so its ground-truth entries are, by construction, unaddressable (see Matching).

## What it measures — the three numbers

For each race, and pooled (micro-averaged) across all scored races, the harness reports
three recalls. Definitions use these per-race sets, where **GT** is the set of comparable
ground-truth sources for the race (only `exists: yes|partial` sources with a URL):

- **A (addressable)** = GT sources whose registrable domain is the `domain` of some
  `scoped_search` hub applicable to the race (from `hubs_for_race` over the loaded
  registry). Read from the registry-under-test at run time, so A re-scopes automatically
  when a domain hub is added.
- **R (retrieved)** = GT sources whose URL (or an `accept_urls` alternate) appears in the
  raw Tavily items the lane produced, before classification.
- **V (verified)** = GT sources in R that the classifier then **accepted** — mirroring
  production's `pending` rule exactly: `verdict.rejected_reason is None and
  verdict.relevant and verdict.confidence >= DISCOVERY_CONFIDENCE_FLOOR`. Prior-cycle
  flagged items count as accepted (they stay pending).

The three reported numbers:

1. **Addressable recall (headline)** = |V ∩ A| / |A|. The actionable signal: of the
   sources the registry can actually reach, how many the lane finds and verifies. Moves
   when a hub is added or the classifier is tuned.
2. **Overall recall (context)** = |V| / |GT|. Honest total coverage across every comparable
   source that exists, whether or not a hub covers it. Low by design.
3. **Retrieval-only recall (diagnostic)** = |R| / |GT| (and |R ∩ A| / |A|). Whether Tavily
   surfaced the source at all, isolating a search miss from a classifier reject.

A secondary **precision** line (accepted items that match any GT source ÷ all accepted
items) is reported per race as context; recall is the graded metric.

## Matching (deterministic, no LLM judge)

The spike bakeoff used an LLM judge to match found URLs to ground truth. This eval matches
**deterministically** so results are reproducible and add no cost or noise:

- Normalize a URL: lowercase host, strip scheme, strip a leading `www.`, strip a trailing
  slash, drop the fragment, drop the query string.
- A found URL matches a GT source when `normalize(found)` equals `normalize(src.url)` or
  `normalize(any of src.accept_urls)`, OR when host + path are equal after normalization.
- The ground-truth fixture gains a per-source `accept_urls` list (the same source at a
  different URL — e.g. the live `princetonherald.com` page vs. the Wayback copy the labels
  recorded, or a debate carried by two outlets). These are curated once from the existing
  eval notes, which already list the alternates in prose.

Registrable-domain extraction for the **A** set uses a small effective-TLD-aware helper
(handles `co.uk`-style suffixes conservatively; the ground truth is all plain `.gov`/`.org`/
`.com` domains, so a last-two-labels rule with a short multi-part-suffix allowlist suffices).

## Architecture / files

Split mirrors the existing `src/discovery/eval.py` (pure) + `scripts/eval_discovery_classifier.py`
(I/O runner) pair:

- **`src/discovery/hub_recall_eval.py`** — pure, offline, importable. URL normalization,
  registrable-domain extraction, source matching, the A/R/V set computation, recall +
  precision math, and majority-vote aggregation across runs. No network, no filesystem.
- **`scripts/eval_hub_recall.py`** — the runner. Loads env, loads the ground truth and the
  hub registry, runs the live lane per race over N runs, scores via `hub_recall_eval`,
  prints a per-race + pooled table with per-run spread. Not a pytest test.
- **`tests/fixtures/hub_recall_ground_truth.json`** — curated ground truth (8 races),
  derived from `bakeoff/ground_truth.json`: keep only `exists: yes|partial` sources with a
  URL; add `accept_urls`; carry `candidates`, `state`, `race_label`, election year, and per
  source `tier` / `tos` / `type`.
- **`tests/fixtures/hub_recall_hubs_snapshot.json`** — a committed snapshot of the seed
  `essentials.source_hubs` registry (the 17 seed rows), so the eval runs with **no DB** and
  is reproducible. Captured from the live registry via `--hubs db --print-hubs`, or
  reconstructed from ev-accounts migration `1869_source_hubs.sql`.
- **`tests/test_hub_recall_eval.py`** — offline unit tests for the pure scorer (matching,
  domain extraction, A/R/V math, majority vote). Pytest-safe; proves the logic with zero
  API spend.

### Registry source
- **Default: the committed snapshot** (`hub_recall_hubs_snapshot.json`). A regression test
  must be reproducible: you compare against a baseline, and a hub someone toggled in prod
  must not masquerade as a code regression. The snapshot is version-controlled, so a recall
  change is attributable to a specific registry change in git history.
- **`--hubs db`** reads the live `essentials.source_hubs` via `hubs.load_hubs` (needs
  `DATABASE_URL`) for the current-reality view.
- **`--print-hubs`** dumps the loaded hub list as JSON and exits — the mechanism to refresh
  the snapshot after hubs are added (`--hubs db --print-hubs > tests/fixtures/hub_recall_hubs_snapshot.json`).

### CLI
```
.venv/bin/python scripts/eval_hub_recall.py [--races SLUG...] [--runs N]
    [--budget N] [--no-classify] [--hubs snapshot|db] [--print-hubs] [--env-file PATH]
```
- `--runs N` (default 3): repeat every race N times; aggregate by majority vote per GT
  source per stage (retrieved in a majority of runs; accepted in a majority). Also print
  each run's headline recall so the spread is visible. Use `--runs 5` for a tuning decision.
- `--budget N` (default `config.DISCOVERY_HUB_BUDGET` = 6): scoped searches per race, passed
  straight to `raw_items_for_race`.
- `--no-classify`: retrieval only (skips OpenRouter) for a cheap registry/search check;
  reports retrieval-only recall.
- `--races`: limit to a subset by slug, to bound cost while iterating.

## Noise band + how many runs

The metric is noisy on both stages: Tavily results vary run-to-run and the classifier is
nondeterministic (the sibling classify eval measured run-to-run agreement shift ≈ 0.02–0.03;
retrieval adds more). The harness therefore:
- averages `--runs N` by majority vote per source (the `prior_cycle` eval precedent), and
- prints each run's headline recall so the spread is visible in the output.

The exact band is measured on the first real baseline and recorded in the runner docstring
(as the `prior_cycle` README records its "Baseline" section). Interim rule until then: treat
a headline-recall change smaller than the observed run-to-run spread (or ~0.10, whichever is
larger) as noise, and require `--runs 5` for any tuning decision.

## Keys, env, and the worktree/main split

- The retrieval stage needs `TAVILY_API_KEY`; the verify stage needs `OPENROUTER_API_KEY`.
  Both are already present (non-empty) in the main checkout's `.env.local`. There is no
  `.env` file in the repo — the key the harness needs is already where the loader looks.
- The runner calls `gui.env.load_env_local()` and accepts `--env-file PATH`. It then
  asserts the required keys are in the environment and **fails loudly** with a one-line
  remediation if a key is missing (Tavily always; OpenRouter unless `--no-classify`).
- `gui.env.load_env_local()` resolves `.env.local` relative to the importing package's repo
  root. When the harness is run from a worktree (whose gitignored `.env.local` does not
  exist), pass `--env-file /Users/chrisandrews/Documents/GitHub/on-the-record/.env.local`,
  or `set -a; . <main>/.env.local; set +a` before the run so the keys are exported.

## The baseline run (final task)

After the harness and unit tests are green, run it live (8 races, `--runs 3`) to produce the
regression baseline: the three recalls pooled + per-race, plus the measured run-to-run
spread. Record it in the runner docstring and in the Slice-2 memory note. This proves the
harness end to end and gives the number future tuning is compared against. This step needs
`TAVILY_API_KEY` at run time (present in `.env.local`).

## Risks / open questions

- **Snapshot fidelity.** The committed snapshot must match the real seed registry. Capture
  it from the live DB (`--hubs db --print-hubs`) or migration `1869_source_hubs.sql`; do not
  hand-transcribe from the spike's `hubs.json` (different schema, different row set).
- **Low addressable N.** With the seed registry, few GT sources are domain-addressable
  (Ballotpedia dominates; Vote Smart rarely filled). Addressable recall is a small-
  denominator number early on; the per-race table and retrieval-only diagnostic keep it
  interpretable. It grows in usefulness as domain hubs are added.
- **Tavily cost.** 8 races × budget 6 ≈ up to 48 searches per run, × N runs, plus up to
  ~2 classify calls per found item. `--races` and `--no-classify` bound this while iterating.
- **Ground-truth staleness.** The labels are a 2026-09-17 snapshot; some `partial` sources
  were "scheduled, not yet aired." That is acceptable for a recall regression baseline (the
  target set is fixed); note it in the fixture's `_about`.

## Amendment 2026-09-18 — the recall target set is FILLED sources only (Phase 4a)

Decided with Chris after the first baseline came back with addressable recall 0.00. The
addressable set was dominated by Ballotpedia pages that are **unfilled** for these races,
and an unfilled questionnaire can never verify (no candidate's own words), so the classifier
correctly rejects it — which pinned the headline at the floor and made it uninformative.

**Decision:** the recall **target set** is only the sources that actually carry the
candidates' own words — `exists == "yes"` in the ground truth. Every `partial` source
(incompletely filled, scheduled-not-yet-aired, access-blocked, or unconfirmed) is excluded
from **every** recall denominator (addressable, overall, retrieval-only) and from precision
matching. "filled" is defined as the objective `exists == "yes"` label — no per-source hand
judgment; `partial` genuinely means "not a clean, complete comparable source."

**Implementation:** a pure `hub_recall_eval.filled_targets(gt_sources)` helper
(`[s for s in gt_sources if s.get("exists") == "yes"]`, order-preserving), unit-tested
offline. The runner filters `race["sources"]` through it before `score_run` and `precision`.
The scorer is otherwise unchanged; the fixture is unchanged — `partial` sources stay in it as
provenance (a record of the full landscape) and can power an all-source diagnostic later.

**Consequence:** the filled target is 14 of 29 sources; only 2 are filled **and** addressable
under the seed registry (both filled Ballotpedia pages: la-mayor Bass/Raman, ut-sboe-14 Isom),
because the registry has only five `scoped_search` domain hubs. The headline is therefore
small-N (denominator 2) but meaningful — those are the filled comparable sources the lane
should land — and it grows as domain hubs are added. The all-GT baseline is retained in the
runner docstring for reference; the filled-target baseline replaces it as the headline number.
