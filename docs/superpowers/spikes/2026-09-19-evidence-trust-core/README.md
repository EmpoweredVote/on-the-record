# Evidence trust-core slice — manual online runner

## Purpose

`scripts/evidence_slice.py` is the manual, human-gated online runner for the
evidence trust-core slice. It wires together the landed modules end-to-end:

- `src.evidence.data` — read-only DB access (roster + already-cited
  compass-research sources for each candidate; the connection is opened
  `readonly=True`, so this never writes to the database).
- `src.evidence.pipeline` (`run_candidate` / `Providers`) — per-source
  triage → extract → verbatim gate → independent cross-check → judge →
  disposition.
- `src.evidence.eval.score` — scores the run's items/leads, optionally
  against a hand-labeled gold file.
- `src.evidence.report` — renders the Markdown eval report and the static
  HTML review page.
- `src.llm_providers.get_provider` — model provider seam (extractor /
  cross-checker / judge, each independently swappable).
- `src.discovery.feeds.fetch_page_text` — the same robots-gated, paced page
  fetcher the discovery pipeline uses.

It does **not** run automatically as part of any task or CI job. It is a
deliberate, one-shot online run a human kicks off by hand, reviews, and
scores. Running it for real (against the LA Mayor race) is Task 14 — a
separate, later, human-gated step. This task (13) only lands the runner,
the gold template, and this README; no live run has happened yet.

## How to run

Run it with the **main-checkout** venv, not a worktree venv:

```bash
~/Documents/GitHub/on-the-record/.venv/bin/python scripts/evidence_slice.py \
    [--race ID] [--candidate POLITICIAN_ID] [--limit N] \
    [--extractor sonnet] [--crosschecker gemini-flash] [--judge gpt5-mini] \
    [--env-file PATH] [--out DIR] [--gold PATH]
```

Flags (all optional):

| Flag | Default | Meaning |
|---|---|---|
| `--race` | the LA Mayor race id (`9e888818-c50b-4c61-a106-a0839ff2479d`) | which race's roster to pull |
| `--candidate` | none (all roster candidates) | restrict to one `politician_id` |
| `--limit` | none (all cited sources) | cap sources processed per candidate |
| `--extractor` | `sonnet` | model key for quote extraction |
| `--crosschecker` | `gemini-flash` | model key for the independent cross-check |
| `--judge` | `gpt5-mini` | model key for the final judge pass |
| `--env-file` | none (falls back to `os.environ` / the `ev-accounts` backend `.env`) | env file `src.evidence.data.connect` reads `DATABASE_URL` from |
| `--out` | this spike directory (`docs/superpowers/spikes/2026-09-19-evidence-trust-core/`) | where artifacts are written |
| `--gold` | none | path to a filled-in gold JSON (see below); omitted → precision/recall are reported as `n/a` |

### Keys

- **DATABASE_URL**: read from `os.environ` first, otherwise parsed out of
  `--env-file` (or the `ev-accounts/backend/.env` default). The main
  checkout's `.env.local` also carries a `DATABASE_URL` line, so pointing
  `--env-file` at it works too.
- **LLM keys** (`ANTHROPIC_API_KEY` for `sonnet`, `OPENROUTER_API_KEY` for
  the OpenRouter-routed keys like `gemini-flash` / `gpt5-mini`): the runner
  reads these straight from `os.environ` — it does not parse `--env-file`
  for them. Export the main checkout's `.env.local` into your shell before
  running:

  ```bash
  cd ~/Documents/GitHub/on-the-record
  set -a && source .env.local && set +a
  .venv/bin/python scripts/evidence_slice.py
  ```

### Fetch behavior on re-runs

`fetch_page_text` only caches `robots.txt` lookups in-process (per run); it
does not cache page bodies to disk. Every run — including a re-run of the
exact same race/candidate — re-fetches every source page over the network,
respecting robots.txt and per-domain crawl-delay pacing. There is no cheap
"replay" mode; budget re-runs accordingly (and prefer `--limit` /
`--candidate` while iterating).

## Artifacts

Each run writes four files to `--out` (default: this directory):

- `evidence_items.json` — every `EvidenceItem` produced (green, flagged, and
  dropped), as JSON.
- `leads.json` — every `Lead` (a quote reported by a secondary source that
  was never verified against a primary — chase before trusting it).
- `eval_report.md` — headline metrics: counts by status, verbatim pass
  rate, primary-source rate, precision/recall vs. gold (or `n/a` without
  `--gold`), and per-domain green yield.
- `review.html` — a static, single-file review page grouping items by
  status (green / flagged / dropped) plus the leads list, for a human to
  read end-to-end.

These are overwritten on every run — copy out anything you want to keep
before re-running.

## Filling in the gold file

`gold_template.json` is the shape `--gold` expects. To turn it into a real
gold set after a run:

1. Open `review.html` from that run and read through it.
2. **`labels`** — pick roughly **20–30 items** spread across the green,
   flagged, and dropped buckets (favor ones you can judge confidently) and
   label each `"green"` (belongs) or `"reject"` (doesn't). The key for each
   item is `f"{politician_id}|{source_url}|{verbatim_text[:60]}"` — the
   same key `src.evidence.eval.item_key` computes, so you can read it
   straight off `evidence_items.json` (`politician_id`, `source_url`,
   `verbatim_text`) rather than retyping it by hand. `eval.score` uses this
   to compute precision: out of everything you labeled, how many of the
   pipeline's green picks are ones you also labeled `"green"`.
3. **`recall_sample`** — pick a handful of sources you personally read in
   full (not skimmed) and record how many genuinely-green quotes *should*
   have come out of that page, keyed by `source_url`. `eval.score` uses
   this to compute recall: of the quotes you know exist on those pages, how
   many did the pipeline actually surface as green.

Save your filled-in copy as its own file (don't overwrite
`gold_template.json` — it's the blank template for future runs) and pass it
with `--gold path/to/your_gold.json`.

## Baseline

Baseline: to be recorded after the first live run (Task 14).
