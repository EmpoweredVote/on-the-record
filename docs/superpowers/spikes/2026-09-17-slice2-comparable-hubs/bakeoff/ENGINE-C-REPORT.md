# Engine C build report -- HUB-DIRECTED Tavily agent loop

SPIKE work, scratchpad only. Nothing in the on-the-record repo was touched.

## Files created

- `hubs.json` -- DATA config of source hubs: `global` (Ballotpedia, VOTE411/LWV,
  Vote Smart), `by_state` (AZ, OR, TX, CA, UT -- covers all 5 states present
  in `ground_truth.json`'s 8 races), and `local_types` (5 generic hub-type
  templates: local LWV chapter forum, local newspaper Q&A, local public
  radio/TV coverage, chamber-of-commerce forum, government voter pamphlet).
  Each entry has `name`, `kind` (reuses `common.SOURCE_TYPES`:
  debate|forum|interview|questionnaire|guide|voter_pamphlet), `hint` (a query
  template with `<angle-bracket>` placeholders), and `note`. Seeded from the
  task's bullet list, grounded in `../eval/*.md`.
- `engine_c.py` -- hub-directed variant of engine_a's loop. Same bounded
  SEARCH/FETCH/FINAL protocol, same `_RealLLM` (via `src.llm_providers`),
  same real Tavily search/fetch functions, same `FOUND_SOURCE_SHAPE` /
  `INJECTION_SAFETY_RULES` from `common.py`. Caps raised to
  `MAX_SEARCHES = 8` (from A's 4), `MAX_FETCHES = 6`, `MAX_TURNS = 12`.
  Same `run(race, dry_run=False) -> dict` interface and the same
  `TAVILY_API_KEY` missing -> `{"status": "unavailable", ...}` handling as
  engine_a.
- `ENGINE-C-REPORT.md` -- this file.

## Files changed

- `bakeoff.py` -- imports `engine_c`; `ENGINES["c"] = engine_c`;
  `ENGINE_NAMES["c"] = "Engine C (Tavily hub-directed loop)"`; `--engine`
  choices extended to `{a, b, c, both, all}` (`both` kept as the legacy a+b
  default, `all` = a+b+c); engine-key selection branches on `both`/`all`/
  single letter; the two hardcoded "two engines"/"both engines" strings in
  the rendered report now read the actual engine count so the report text
  stays correct for 1, 2, or 3 engines. `judge.py` and the report renderer
  needed no other changes -- both already loop generically over
  `engine_keys`/`ENGINE_NAMES`.
- `fixtures.py` -- added `engine_c_found(race_slug)`: returns ~85% of a
  race's ground-truth sources (vs. A's ~60%, B's ~40%), modeling "checking
  named hubs directly finds more of what's actually there." No new planted
  probe -- A's hallucination probe and B's stale/advocacy probes already
  exercise every judge trap category across the run, so C's fixture only
  needed to carry the recall story. Docstring updated to describe all three
  engine fixtures.
- `README.md` -- updated file table, run commands, and the "running it for
  real" section (both engines' costs, both need `TAVILY_API_KEY`) to cover
  three engines.

## Hub selection logic

`engine_c.hubs_for_race(hubs_config, race)` reads only `hubs_config` plus
`race["state"]` and returns `{"global": [...], "state": [...], "local_types":
[...]}` -- `global` always included, `state` looked up by the race's 2-letter
`state` (falls back to `[]` if that state has no entry), `local_types` always
included as generic templates. It deliberately never reads `race["hubs"]`
(ground_truth.json's own hand-labeled hub list for that race), since that
would leak the answer to the engine.

`format_known_hubs()` renders this into a "KNOWN HUBS TO CHECK" text block
(hub name, kind, hint, note) that's appended to the per-race context prompt
(`race_context_with_hubs_prompt`), NOT baked into the static `SYSTEM_PROMPT`
constant (which stays race-independent, matching engine_a's design -- race
specifics go in the transcript's first message, not the system prompt). The
`SYSTEM_PROMPT` itself instructs the agent to fill in each hub's `hint`
template with the race's actual candidates/locality/year and search those
BEFORE any free-form query, and to confirm current-cycle + actual-candidate
fill (via FETCH) before counting a hub hit.

## Dry-run validation (no live calls made)

Compiled every touched/new module first:

```
.venv/bin/python -m py_compile bakeoff.py engine_c.py fixtures.py engine_a.py engine_b.py common.py judge.py
```
-> `ALL COMPILE OK`

Then ran the dry-run matrix with `OPENROUTER_API_KEY`/`TAVILY_API_KEY`
unset AND all HTTP(S) proxy env vars pointed at a closed local port
(`127.0.0.1:1`), so any code path that accidentally attempted a real network
call would fail immediately rather than silently succeed:

```
env -u OPENROUTER_API_KEY -u TAVILY_API_KEY \
  http_proxy=http://127.0.0.1:1 https_proxy=http://127.0.0.1:1 \
  HTTP_PROXY=http://127.0.0.1:1 HTTPS_PROXY=http://127.0.0.1:1 \
  .venv/bin/python bakeoff.py --engine c --dry-run
```
-> `Ran 8 race(s) x 1 engine(s).` Engine C: scored 8/8, unavailable 0,
recall 1.00, precision 1.00, stale 0, advocacy 0, hallucinated 0.

```
env -u OPENROUTER_API_KEY -u TAVILY_API_KEY \
  http_proxy=http://127.0.0.1:1 https_proxy=http://127.0.0.1:1 \
  HTTP_PROXY=http://127.0.0.1:1 HTTPS_PROXY=http://127.0.0.1:1 \
  .venv/bin/python bakeoff.py --engine all --dry-run
```
-> `Ran 8 race(s) x 3 engine(s).` report renders all three rows:

| Engine | Scored | Unavail | Recall | Precision | Stale | Advoc | Halluc |
|---|---|---|---|---|---|---|---|
| Engine A (Tavily search-agent loop) | 8 | 0 | 0.69 | 0.71 | 0 | 0 | 8 |
| Engine B (OpenRouter web plugin) | 8 | 0 | 0.52 | 0.58 | 8 | 3 | 0 |
| Engine C (Tavily hub-directed loop) | 8 | 0 | 1.00 | 1.00 | 0 | 0 | 0 |

Also spot-checked `--engine c --race princeton-council-tx --dry-run` (single
race, single engine) and the legacy `--dry-run` (defaults to `both` = a+b) --
both ran clean. No errors, no proxy-connection-refused messages (which would
indicate an attempted real call) in any run.

**Engine C's dry-run recall/precision of 1.00/1.00 is an artifact of the
fixture's ~85%-slice design with no planted probe for C specifically -- not a
real quality claim.** Like A and B's dry-run numbers, this only proves the
harness's plumbing (hub selection, prompt injection, the bounded loop, JSON
extraction, judge scoring, report rendering) works end-to-end. A real run
(`TAVILY_API_KEY` + `OPENROUTER_API_KEY` set, then `bakeoff.py --engine all`)
is the human's deliberate next step -- not performed in this session.
