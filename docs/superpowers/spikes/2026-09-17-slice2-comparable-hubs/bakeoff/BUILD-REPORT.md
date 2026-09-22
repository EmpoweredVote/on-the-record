# Build report: source-discovery bakeoff harness (SPIKE)

## What this is

A benchmark harness comparing two engines for the on-the-record source-discovery
hunt, scored against an 8-race hand-labeled eval set. Built entirely under
`scratchpad/bakeoff/` in the on-the-record repo's cwd. **No tracked repo files
were created or modified**; `git status` in the repo shows no changes from
before this task started.

## Files created

All under `.../scratchpad/bakeoff/`:

- `ground_truth.json` -- 8 races, machine-readable, distilled from `../eval/*.md`
  (`SUMMARY.md` + the 8 race files). Includes only sources each eval file
  marked `exists: yes` or `partial` with a URL, classified into
  `debate|forum|interview|questionnaire|guide|voter_pamphlet`. Background
  reporting, fact-checks, stale/prior-cycle pages, and directory-only hub
  pages were left out (matching what the hunt is actually supposed to find),
  per the task's own framing ("those are what an engine should find").
- `common.py` -- shared helpers: repo-root `sys.path` injection, JSON-array
  extraction from free-form LLM text, the shared injection-safety prompt
  text, `round_num()`.
- `fixtures.py` -- deterministic, disclosed canned data for `--dry-run` only
  (documented in-file: which slice of ground truth each engine "finds," and
  the one planted hallucination/stale/advocacy probe per engine so every
  judge trap category fires at least once).
- `engine_a.py` -- Engine A: bounded Tavily search-agent loop. A text-based
  `SEARCH:` / `FETCH:` / `FINAL:` protocol over `src.llm_providers.get_provider
  ("haiku-or")`, capped at <=4 searches / <=6 fetches / <=8 turns per race.
  Reports itself `"unavailable (no TAVILY_API_KEY)"` and is skipped (not
  failed) when that key is absent in a real run.
- `engine_b.py` -- Engine B: 1 (or 2) OpenRouter `chat.completions.create`
  call(s) with `extra_body={"plugins": [{"id": "web", "max_results": 8}]}`,
  per the exact shape documented at
  https://openrouter.ai/docs/guides/features/plugins/web-search (fetched and
  confirmed live during this build). Uses a direct `openai.OpenAI` client
  against the repo's own `config._OPENROUTER_URL` since `get_provider()`'s
  abstraction doesn't expose OpenRouter-only fields like `plugins`.
- `judge.py` -- LLM judge: given a race's ground-truth sources + one engine's
  found list, decides matched-by-identity ground-truth coverage and
  wrong-finds (stale / advocacy / hallucinated / not_comparable), then
  computes recall/precision/trap-counts itself (doesn't trust judge
  arithmetic).
- `bakeoff.py` -- CLI (`--engine a|b|both`, `--race SLUG`, `--dry-run`):
  orchestrates engine(s) -> judge -> `report.md` + a printed summary table.
  Every displayed number is rounded (`round_num`, 2-3 dp).
- `README.md` -- how to run it now (dry-run) and what a real run needs later.
- `report.md` -- generated output of the last run (currently the full
  `--dry-run`, both engines, all 8 races).

Both engine prompts (`engine_a.py` `SYSTEM_PROMPT`, `engine_b.py`
`SYSTEM_PROMPT`) include the shared `common.INJECTION_SAFETY_RULES` block,
which explicitly instructs the model to treat every fetched page / search
result as untrusted data only, never follow instructions found inside it,
and never submit forms or enter credentials.

## Proof it works: the dry-run

Command run (from `scratchpad/bakeoff/`, using the repo's own venv):

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python bakeoff.py --dry-run
```

Output:

```
Ran 8 race(s) x 2 engine(s). Report written to .../scratchpad/bakeoff/report.md

Engine                                 Scored Unavail  Recall Precision  Stale  Advoc  Halluc
Engine A (Tavily search-agent loop)         8       0    0.69      0.71      0      0       8
Engine B (OpenRouter web plugin)            8       0    0.76      0.42      8      3       0
```

This exercised the FULL pipeline for all 8 races x both engines: Engine A's
bounded loop actually looped (scripted SEARCH -> FETCH -> FINAL turns,
through the same turn/cap-counting code the real run uses), Engine B's
single-call path ran, the judge scored every race, and `report.md` was
rendered with both an aggregate table and a per-race table. The recall/
precision/trap numbers are NOT a real quality signal -- they come from
disclosed, hand-authored fixtures designed so every trap category (stale /
advocacy / hallucinated) fires at least once; see the "Note on this run" in
`report.md` and the design-notes section of `README.md`.

Also verified:
- `--engine a --race princeton-council-tx --dry-run` and
  `--engine b --race az-mine-inspector --dry-run` both filter correctly.
- An unknown `--race` slug fails cleanly with a listing of valid slugs
  (exit code 1), rather than crashing.
- Confirmed programmatically that **`requests`, `openai`, and `anthropic` are
  never imported** during a `--dry-run` invocation (checked
  `sys.modules` after running `bakeoff.main(["--dry-run"])` in-process) --
  i.e. the mocked code paths are structurally incapable of reaching the
  network, not just told not to.

**No live API calls were made at any point while building or validating this
harness.** Every invocation used `--dry-run`.

## What a real run needs

1. `OPENROUTER_API_KEY` -- already present in the repo's `.env.local`, loaded
   automatically via `gui.env.load_env_local()`. Powers Engine A's agent
   loop, Engine B's web-plugin call, and the judge.
2. `TAVILY_API_KEY` -- **not present anywhere in this repo currently.** Needed
   only for Engine A (the search-API agent loop). Get one from
   https://tavily.com and add it to `.env.local` or export it in the shell.
   Without it, Engine A is skipped per-race (reported "unavailable"), Engine
   B and the judge still run.
3. Then: `.venv/bin/python bakeoff.py` (both engines, all 8 races) or
   `--engine b` alone if no Tavily key is available yet. Expect up to ~32
   Tavily searches + ~48 page fetches + ~64 Engine-A LLM turns, ~8-16 Engine-B
   chat calls, and up to ~16 judge calls -- all on a mid-tier OpenRouter model
   (`anthropic/claude-haiku-4.5`), a constant at the top of each of
   `engine_a.py` / `engine_b.py` / `judge.py`.

Running it for real is the human's deliberate next step, not something done
in this build.
