# Evidence Pipeline Robustness — whole-source extraction + rendered fetch

**Status:** Draft for review (brainstormed with Chris 2026-09-20)
**Repo:** on-the-record only (`src/evidence/extract.py`, `src/discovery/feeds.py`, runner, tests,
`requirements`). No `inform.evidence_items` schema change; no ev-accounts change. The write-path
committer (`scripts/commit_evidence.py`) and the review surface already work.
**Follows:** the write-path (PR #247) and review surface (PR #569). The first live review of Nithya
Raman's 10 items validated the pipeline (green precision 0.80). The attempt to **scale to the second
LA Mayor candidate (Karen Bass)** produced **0 usable evidence** and exposed two blocking defects that
this slice fixes.

## Why (the scale test that forced this)

Running the existing pipeline on Karen Bass (36 cited sources) yielded 13 items, **all dropped**
(verbatim pass rate 0.00). Diagnosis found two distinct, real defects — not merely thin sources:

1. **Silent extraction loss on large/rich sources.** `extract.extract_quotes` sends the whole page
   (capped at 60K chars) to the model in one call with `max_tokens=1500`, and `parse_extract` returns
   `[]` on any `JSONDecodeError`. Bass's Ballotpedia *Candidate Connection* page (her own words, full
   of concrete levers — *"declared a state of emergency on homelessness … Inside Safe … end all street
   encampments … appoint and empower one individual"*) fetches perfectly (161K chars; the survey sits
   at char 24, inside the 60K cap), but the model's reply overflows the token cap (still truncated at
   `max_tokens=6000`, raw 25K), the JSON never closes, and the parser silently returns zero. This bites
   **any** large/rich single-page source, for every candidate. Raman escaped it only because her
   sources are small per-issue campaign pages that fit in one reply.
2. **Blocked primary sources.** `mayor.lacity.gov` (6 of Bass's sources) closes our connection outright
   (`RemoteDisconnected` — a TLS/UA-fingerprint block, past even the existing browser-UA fallback);
   `ontheissues.org` returns empty (and is a banned pointer anyway). These need a real browser engine
   (JavaScript + a genuine browser fingerprint) to fetch.

**Coverage-equity stakes:** an incumbent's record lives in government press releases (blocked) and a
Ballotpedia survey (silently dropped), while a challenger's campaign issues site fetches and extracts
well. Left unfixed, the evidence layer would systematically under-cover officeholders — the opposite
of a defensible, comprehensive record.

## Goals

- **Whole-source extraction:** read the entire fetched page (not the first 60K), and never lose a
  window's quotes to a truncated reply.
- **Rendered fetch fallback:** when the plain HTTP fetch fails or returns too little text, render the
  page with headless Playwright Chromium and extract text the same way — as an opt-in, pluggable tier.
- **Re-run Bass** with both fixes and confirm real evidence, giving two LA Mayor candidates on the same
  issues (the comparability read-rank and compass views need).

## Non-goals (deferred)

- A managed render API (Firecrawl / ScrapFly). The fetch chain is built so one can be added as a third
  tier later, behind a flag, **only if** evidence shows Playwright falls short. Not added now (avoids a
  monthly quota, per-page cost, an API key, and a third party).
- Playwright in CI / off-Mac automation. Noted as a later execution concern; not built here.
- Any change to the trust gates (verbatim / cross-check / judge / disposition), the schema, the
  committer, or the review surface. The judge's one known blind spot ("expand the office") stays a
  separate follow-up.
- Fixing every blocked site. Playwright covers ordinary blocks (fingerprint, JS challenge); a site with
  enterprise anti-bot may still resist — logged, not chased here.

## Component 1 — Whole-source extraction (`src/evidence/extract.py`)

`extract_quotes(text, *, candidate_name, provider, max_tokens=…)` keeps its signature (the pipeline
call at `pipeline.py:53` is unchanged) but changes internally:

- **Window the full text.** Split `text` into overlapping windows of ≈12,000 chars with ≈2,000 chars
  of overlap, preferring to cut on a paragraph/sentence boundary near the target size. Windows cover
  the **whole** page — the 60K input cap is removed. Overlap ensures a quote spanning a boundary
  appears whole in at least one window.
- **Extract per window** with a per-window output cap sized to fit a window's quotes (≈3,000 tokens).
- **Salvage partial JSON.** `parse_extract` first tries a normal parse; on failure it recovers the
  complete `{…}` objects already present in the (possibly truncated) `quotes` array and parses each,
  instead of returning zero. So even a window that still overflows yields its complete quotes.
- **Dedup** the combined candidates across windows by a normalized key (lowercased, whitespace-collapsed
  `text`), keeping the first occurrence. This removes the duplicates that overlap introduces.

**Correctness — verbatim verification is unaffected.** The pipeline verifies each quote against the
**full fetched page** (`verbatim_ok(cand.text, text)`, `pipeline.py:59`), never against a window. So
windowing the extractor input cannot break the verbatim gate. A quote trimmed at a window edge that is
still a contiguous substring of the full page passes; one that is not is correctly dropped, exactly as
today.

**Helpers (pure, unit-tested):** `chunk_text(text, size, overlap) -> list[str]` and the salvage path in
`parse_extract`. Dedup is a small pure function over the candidate list.

## Component 2 — Rendered fetch chain (`src/discovery/feeds.py`)

`fetch_page_text(url, *, max_chars=…, render_fallback=False, renderer=None)` gains a fallback tier:

- **Chain:** attempt the existing plain HTTP fetch (identifying UA → browser-UA fallback → 429 retry,
  all unchanged). If it **raises** or returns text below a small threshold (e.g. < 200 chars — catches
  both the `RemoteDisconnected` case and empty/challenge pages), and `render_fallback` is on, call the
  **renderer**; use its text when it returns more than the plain path did.
- **Renderer:** a new `_fetch_rendered(url) -> str` using **sync Playwright headless Chromium** —
  `goto(url, wait_until="domcontentloaded", timeout=…)`, then extract text through the **same**
  HTML→text path the plain fetch uses (one text-extraction function, one code path). Injectable via the
  `renderer` parameter so tests never launch a real browser.
- **Opt-in + pluggable:** `render_fallback` defaults **off**, so the discovery lane and every other
  caller are untouched and need no browser installed. The evidence runner passes `render_fallback=True`.
  The single fallback seam is where a managed-API tier would later be added.
- **Politeness/robots:** the rendered path honors the same robots check and per-origin pacing as the
  plain path (robots is about permission to fetch, independent of the fetch mechanism).

**Runner wiring (`scripts/evidence_slice.py`):** the injected fetcher becomes
`functools.partial(fetch_page_text, max_chars=args.max_chars, render_fallback=True)`, gated by a
`--render/--no-render` flag (default on for the evidence run). No other pipeline change.

**Dependency:** add `playwright` to `requirements`; document the one-time `playwright install chromium`
in the runner docstring and the plan. Import Playwright lazily inside `_fetch_rendered` so the package
is only needed when the rendered tier actually runs.

## Tests

- **Extractor (offline, mock provider):** `chunk_text` window count / size / overlap and
  boundary-preference; per-window extraction is called for each window and the results merged; dedup
  collapses overlap duplicates; **partial-JSON salvage** recovers complete objects from a truncated
  reply (the exact Bass failure, as a fixture); the whole-text path (a >60K fixture) yields quotes from
  content past char 60,000.
- **Fetch (offline, injected renderer — no real browser):** the chain calls the renderer when the plain
  fetch **raises**; when it returns **empty/short** text; it does **not** call the renderer when the
  plain fetch already returns good text or when `render_fallback` is off; the renderer's text is used
  only when longer than the plain result; robots denial still blocks (rendered path included).
- Full suite stays green.

## Validation (human-gated live run — belongs in the plan, not automated)

Re-run Bass with `--render`: expect real evidence from Ballotpedia (her survey answers) and from
`mayor.lacity.gov`. Record green/flagged/dropped and the fetch-recovery count in the spike artifacts.
Commit the green+flagged rows with `scripts/commit_evidence.py --commit`. Chris then reviews Bass's
batch in the review surface — delivering two-candidate comparability. Report the before/after (Bass
0 → N usable) and note any sites Playwright still cannot fetch.

## Risks / decisions

- **Window size vs. reply cap** is the tuning knob; the plan validates the chosen values against the
  Ballotpedia fixture (must yield Bass's concrete-lever quotes) and adjusts if a window still overflows.
- **Overlap double-counts** are handled by dedup; a quote that overlap splits differently in two windows
  (e.g. one trims a trailing clause) could survive as two near-duplicates — acceptable (the human review
  and the existing dedup unique index catch it), and preferable to losing a boundary quote.
- **Playwright weight** (~300MB Chromium, slower fetch) is bounded to the minority of blocked pages and
  gated off by default; lazy import keeps it out of unrelated code paths.
- **Playwright can still lose** to enterprise anti-bot; that is why the chain stays pluggable for a
  managed-API tier — a decision deferred until evidence demands it.
- **Rendered text differs from plain text** for the same page; the shared HTML→text function keeps the
  downstream verbatim gate consistent regardless of which tier fetched the bytes.

## Success criteria

- Offline: `extract_quotes` returns quotes from a >60K fixture and from a truncated-reply fixture that
  returns zero today; the fetch chain routes to the injected renderer on raise and on empty/short and
  skips it otherwise; full suite green.
- Live (gated): the Bass re-run yields real green+flagged evidence (from Ballotpedia at minimum),
  committed to `inform.evidence_items`, and the review surface then shows two LA Mayor candidates.

## Execution notes

- on-the-record stack: offline unit tests + a manual ONLINE runner. Run with the MAIN checkout
  `.venv/bin/python`; export `OPENROUTER_API_KEY` (the runner does not auto-load `.env.local`) and pass
  `--env-file <ev-accounts>/backend/.env` for the read-only DB. Models: extractor `haiku-or`,
  crosschecker `gemini-flash`, judge `deepseek` (all OpenRouter; direct Anthropic has no credits).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
