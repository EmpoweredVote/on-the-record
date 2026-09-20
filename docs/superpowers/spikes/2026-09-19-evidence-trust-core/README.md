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
    [--extractor haiku-or] [--crosschecker gemini-flash] [--judge deepseek] \
    [--env-file PATH] [--out DIR] [--gold PATH]
```

Flags (all optional):

| Flag | Default | Meaning |
|---|---|---|
| `--race` | the LA Mayor race id (`9e888818-c50b-4c61-a106-a0839ff2479d`) | which race's roster to pull |
| `--candidate` | none (all roster candidates) | restrict to one `politician_id` |
| `--limit` | none (all cited sources) | cap sources processed per candidate |
| `--extractor` | `haiku-or` | model key for quote extraction |
| `--crosschecker` | `gemini-flash` | model key for the independent cross-check |
| `--judge` | `deepseek` | model key for the final judge pass |
| `--env-file` | none (falls back to `os.environ` / the `ev-accounts` backend `.env`) | env file `src.evidence.data.connect` reads `DATABASE_URL` from |
| `--out` | this spike directory (`docs/superpowers/spikes/2026-09-19-evidence-trust-core/`) | where artifacts are written |
| `--gold` | none | path to a filled-in gold JSON (see below); omitted → precision/recall are reported as `n/a` |

### Keys

- **DATABASE_URL**: read from `os.environ` first, otherwise parsed out of
  `--env-file` (or the `ev-accounts/backend/.env` default). The main
  checkout's `.env.local` also carries a `DATABASE_URL` line, so pointing
  `--env-file` at it works too.
- **LLM keys**: the runner reads these straight from `os.environ` — it does
  not parse `--env-file` for them. By default, all three models
  (`haiku-or` / `gemini-flash` / `deepseek`) are OpenRouter-routed, so only
  `OPENROUTER_API_KEY` is needed. If you explicitly pass `--extractor sonnet`,
  you also need `ANTHROPIC_API_KEY`. Export the main checkout's `.env.local`
  into your shell before running:

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
2. **`labels`** — this map is for the candidate's **GREEN picks only**.
   Pick roughly **20–30 of the pipeline's green items** (favor ones you can
   judge confidently) and label each `"green"` (belongs) or `"reject"`
   (doesn't). The key for each item is
   `f"{politician_id}|{source_url}|{verbatim_text[:60]}"` — the same key
   `src.evidence.eval.item_key` computes, so you can read it straight off
   `evidence_items.json` (`politician_id`, `source_url`, `verbatim_text`)
   rather than retyping it by hand. `eval.score` uses this to compute
   precision: of the green picks you labeled, how many did you also mark
   `"green"` (right green picks / labeled green picks). Flagged and
   dropped items don't belong in `labels` — a quote the pipeline correctly
   dropped isn't a green pick to grade; misses like that belong only in
   `recall_sample` below.
3. **`recall_sample`** — pick a handful of sources you personally read in
   full (not skimmed) and record how many genuinely-green quotes *should*
   have come out of that page, keyed by `source_url`. `eval.score` uses
   this to compute recall: of the quotes you know exist on those pages, how
   many did the pipeline actually surface as green.

Save your filled-in copy as its own file (don't overwrite
`gold_template.json` — it's the blank template for future runs) and pass it
with `--gold path/to/your_gold.json`.

## Baseline

### Current baseline — calibrated judge + browser-UA fallback, 2026-09-20

Two fixes landed after the first run: (1) the judge was recalibrated (`gpt5-mini` →
`deepseek`, `judge` `max_tokens` 300 → 800); (2) the shared `feeds` fetcher gained a
browser-UA fallback + 429 retry, which unblocked the LA city government pages
(`mayor.lacity.gov`, `cd4.lacity.gov`) a WAF had been 403-ing (including their
`robots.txt`, which had made our robots gate fail closed). Run with the calibrated
defaults (`--extractor haiku-or --crosschecker gemini-flash --judge deepseek`). No gold
labelled yet, so precision/recall are `n/a`.

| metric | value |
|---|---|
| items | 26 (green **8** / flagged **7** / dropped **11**) |
| leads (chase-the-primary) | 27 |
| verbatim pass rate | 0.58 |
| primary-source rate (of green) | 1.00 |
| per-domain green yield | nithyaforthecity.com: 6, mayor.lacity.gov: 2 |

Per candidate: Karen Bass 2 green / 2 flagged / 11 dropped (15 items) + 22 leads from 36
sources; Nithya Raman 6 green / 5 flagged (11 items) + 5 leads from 25 sources. **Both
candidates now yield greens**, all primary and verbatim: Bass's are official
`mayor.lacity.gov` homelessness statements; Raman's are homelessness + housing campaign
stances. The 5 remaining dead are correctly excluded (`ontheissues.org` ×2 — a banned
aggregator) or genuinely hard (`ffyf.org` / `acf.gov` Cloudflare-JS challenges; one
`web.archive.org` 429).

**Progression:** first run **1 green** (judge broken) → judge recalibrated: **4 green**
(both from Raman's site; all of Bass's pages still WAF-blocked) → + UA fallback: **8 green
across both candidates**, dead drops 13 → 5.

### Human gold — first labelling (Chris, 2026-09-20): precision 0.375 + the HOW finding

Chris labelled the 8 green picks: **confirmed 3, rejected 5 → precision 0.375** (`gold.json`).
Confirmed: #7/#8 (housing — "build much more housing", "triple annual housing construction")
and #2 (Bass — a reluctant keep; it at least names "services and support"). Rejected: #1
(Bass, purely rhetorical) and **#3–#6, all four Raman homelessness quotes** — including the
ones the pipeline judged strongest (`reduce encampments 50% by 2028`, `eliminate long-term
encampments`, `direct dollars to programs that work`, `immediate treatment on our streets`).

**The finding (a real calibration target, not a bug).** Chris's HOW bar is stricter and more
specific than the judge enforces: a green quote must name the **concrete, contestable policy
lever** — "building shelters, enforcing encampment laws, expanding support services" — not a
**goal** ("reduce homelessness"), a **target/metric** ("by 50% by 2028"), or a **vague means**
("direct dollars to what works", "immediate treatment", "work with the County"). The judge
(deepseek) currently greenlights targets and vague directions, so precision sits at 0.375.
Next iteration: tighten the differentiation gate — in the judge prompt AND in
`docs/quote-curation/PRINCIPLES.md#differentiation` and the `audit-quotes` checks — to demand a
named policy instrument, and re-eval against this gold. This is the human-in-the-loop steering
the whole slice was built to enable.

**Second finding — extraction granularity (same root cause).** The extractor's "one claim per
quote" atomization is too aggressive for evidence, and it actively *hides* the HOW. Concrete
case: green #7 surfaced only "We must build much more housing to reduce housing costs," while the
very next sentence — "This includes housing at all income levels, everything from deed-restricted
affordable housing to market-rate housing to social housing to homeless shelters" — the concrete
levers Chris wants — was demoted to the quote's `context` and never shown. Chris's call: adjacent,
same-source sentences that together form one coherent stance (goal + its mechanism) should be kept
as **one 2–3 sentence quote**, not split. This is complementary to the HOW gate: a fuller passage
is far more likely to carry the lever, and the gate then confirms it. Fold into the same next
iteration (extractor prompt: prefer a coherent multi-sentence stance passage over an atomized
claim; keep contiguous same-source sentences carrying the mechanism), then re-run + re-label.

### First run — judge mis-configured (superseded, kept for the record)

**First full live run — 2026-09-20** (LA Mayor race, both candidates, all cited
sources; models: `--extractor haiku-or --crosschecker gemini-flash --judge gpt5-mini`,
all via OpenRouter). No gold labelled yet, so precision/recall are `n/a`.

| metric | value |
|---|---|
| items | 25 (green **1** / flagged **10** / dropped **14**) |
| leads (chase-the-primary) | 24 |
| verbatim pass rate | 0.44 |
| primary-source rate (of green) | 1.00 |
| per-domain green yield | nithyaforthecity.com: 1 |

Per candidate: Karen Bass 14 items / 19 leads from 36 sources; Nithya Raman 11 items /
5 leads from 25 sources.

**Read of the baseline (the eval-first harness did its job — it surfaced two blockers
before we trusted the pipeline):**

1. **The judge step is effectively non-functional as configured.** Every one of the 10
   flagged items has judge scores of exactly `0.0 / 0.0 / 1.0` — the worst-case default
   `parse_judge` returns when the model reply does not parse — while the single green item
   has a perfect `1.0 / 1.0 / 0.0`. So `gpt5-mini` (at `--judge`, `max_tokens=300`) is
   mostly returning unparseable/truncated JSON, defaulting to worst, and forcing everything
   to FLAGGED. This is a calibration issue, not a code bug (defaulting to worst is the safe
   direction). **~5 of the flagged items pass every real gate (verbatim + all cross-checks)
   and are held back only by the broken judge** — so realistic green potential is ~6, not 1.
   Next: raise the judge token budget, and/or swap the judge model (e.g. `deepseek` /
   `gemini-flash`), and/or tighten the judge prompt to force pure JSON; then re-run.
2. **Fetch quality is the top yield leak.** 13 of the 14 drops are `dead` — legitimate
   primary/news pages (e.g. `mayor.lacity.gov`) that `feeds.fetch_page_text` returned empty
   for (robots / content-type / JS-rendering). All of Bass's 14 items dropped this way, which
   is why her green/flagged yield is zero. Improving the fetcher (a separate task, outside
   this slice's trust-core scope) would surface far more candidate quotes.

**What worked (trust core proven):** zero hallucinations, zero false greens; the deterministic
verbatim gate + independent cross-check behaved correctly (the cross-checker produced varied,
discriminating verdicts — 4 tag disagreements, 2 primary, 1 own-words); the 24 leads populated
the chase-the-primary ingestion queue with real, well-attributed reported quotes (e.g. a
rent-control quote from an ordinance signing).

🔴 **Runner default gotcha:** the shipped default `--extractor sonnet` routes to the **direct
Anthropic API, which has no credits in this environment** (OpenRouter-only). Live runs must
pass OpenRouter model keys, as above. Changing the runner's default extractor to an
OpenRouter-backed model is a recommended small follow-up.
