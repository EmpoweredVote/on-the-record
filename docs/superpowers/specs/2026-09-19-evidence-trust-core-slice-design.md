# Evidence Trust Core — Slice 1 of the Sources & Evidence program

**Status:** Draft for review (brainstormed with Chris 2026-09-19)
**Repo:** on-the-record (pipeline + eval); reads ev-accounts (`essentials`/`inform`) read-only
**Supersedes:** the earlier "assisted stance-quote curation bridge" framing from the
2026-09-19 roadmap. That project was reframed during brainstorming — see *Reframe* below.

---

## Reframe (why this replaces the read-rank quote bridge)

The roadmap named "assisted stance-quote curation" as the #1 project — turn a vetted source
into read-rank draft quotes for Chris to approve/reject. During brainstorming Chris stepped
back: read-rank is not the real target. The real product is a **Sources & Evidence layer per
politician** — for each issue, the candidate's words and actions, each pointing to a **primary
source** with enough **context that a reader can follow the link and vet it**. Read-rank quotes
and Compass stance values are *views derived from that evidence*, not separate hand-curation
jobs. Trust and defensibility are the whole game: a candidate must not be able to say "I never
said that" without us holding the receipt.

A live-data diagnostic (2026-09-19) confirmed the gap:

- `inform.politician_context` (the compass stance research) holds **36,987 rows across 4,239
  politicians × 51 topics** — a large, ready input. But most rows cite only 1–3 sources.
- The **cited sources are dominated by pointers and vote records, not verbatim quotes**: top
  domains are `ontheissues.org` (8,522) and `en.wikipedia.org` (7,622) — together ~25% of all
  ~65k cited URLs, and both are banned as sources (pointers only, per
  `docs/quote-curation/PRINCIPLES.md#sourcing` and the `original-sources-only` memory). Then
  legislature/`congress.gov`/`govinfo.gov` (primary, but for **votes**), `ballotpedia.org`,
  `web.archive.org`, and scorecard/quiz sites (`lcv.org`, `isidewith.com`) that are not
  quotable at all.
- The existing evidence table `inform.politician_context_evidence` is **nearly empty and stores
  no quote**: 183 rows / 17 politicians, and the `snippet` field is a literal placeholder
  `[Human verified during review]`. It records "a human agreed this URL supports the stance,"
  not the candidate's verbatim words with context.
- `essentials.quotes` (read-rank) holds 4,188 rows / 3,278 "live" — disconnected from the
  research, and per prior audits mostly unverified seeds.

So the missing middle is exactly this: **nothing lifts a cited source into a stored, verbatim,
context-carrying, defensible evidence item** — and doing so requires following pointers to
primaries, discarding scorecards/quizzes, and turning secondary reporting into leads to chase.

## The program (context, not this slice's scope)

The full vision is a program of sub-projects; this spec covers only sub-project 1.

1. **Evidence model + trust bar** (this slice defines the atom; a later slice writes it to
   `essentials`).
2. **Gather + verify swarm** — multi-agent extract + independent cross-check.
3. **Eval harness** — per-step metrics + gold sets.
4. **Sources & Evidence product page** — the browsable per-politician record.
5. **Derivation** — Compass value reasoning + read-rank quotes as views over evidence.
6. **Judgment surface** — confidence-gated review; Chris steers, does not click all week.

Long-term intent: on-the-record's evidence moves into `essentials` as the comprehensive,
diggable, defensible record per politician.

---

## This slice: the Trustworthy Evidence Core

**Goal.** Prove — with numbers — that a mostly-automated, multi-agent pipeline can turn a
candidate's *already-cited* sources into **verbatim, primary-source, context-carrying, defensible
quote evidence** that we would defend to the candidate, and can surface **leads** to chase the
primaries that matter. Everything else in the program scales from this proven core.

**Anchor race.** Los Angeles Mayor 2026 (`race_id = 9e888818-c50b-4c61-a106-a0839ff2479d`,
election 2026-11-03), roster **Karen Ruth Bass** (`21c9e711-fb18-4afb-884f-08acd2b598ba`) and
**Nithya Raman** (`26dbe16a-9dff-42c0-939f-5b5e529063ca`). Confirmed coverage: Bass 37 topics /
73 cited sources, Raman 28 / 57; a realistic domain mix (candidate sites, official pages, local
news, wikipedia/ontheissues pointers, YouTube, congress/cityclerk vote records).

**Type.** Verbatim **quotes only**. Votes/actions are out of scope (fast-follow; the model
leaves room for them).

**Reach.** **Artifacts only — no DB writes, no migration.** The target `essentials` schema is
*designed* here (see *The evidence item*) but *applied* in a later slice.

**Input.** The sources the LA Mayor candidates' compass research **already cites**
(`inform.politician_context.sources[]`). No new ingestion.

### Goals

- Define the **evidence item** atom (output JSON now; the future `essentials` schema).
- Build the **gather + verify swarm** (triage → fetch → extract → verbatim gate → independent
  cross-check → context → judge → disposition).
- Emit **ingestion leads** — secondary reporting of spoken statements becomes a prioritized
  "chase the primary" queue rather than a dead end.
- Stand up the **eval harness** (deterministic + LLM-judge + a thin human gold) that measures
  every step.
- Produce a **review page** Chris skims to judge a sample and set the gold.

### Non-goals (later sub-projects / explicitly out)

- Votes and action-summaries as evidence types.
- Writing to `essentials` (`politician_context_evidence` or a successor) and its migration.
- The public Sources & Evidence product page.
- Deriving read-rank quotes or Compass stance values from evidence.
- Breadth beyond LA Mayor.
- Transcribing/ingesting video (leads are emitted instead).
- **"Ingest broadly, then triage what's worth looking at"** — Chris flagged this as a possible
  *future* direction; explicitly undecided and out of scope now.

---

## The evidence item (the atom)

One item = one piece of primary-source proof of a candidate's view on an issue. Slice 1 emits
these as JSON; a later slice maps them to an `essentials`/`inform` table. Fields:

| field | meaning |
|---|---|
| `politician_id` | uuid → `essentials.politicians` |
| `issue` | a Compass `topic_key` (from `inform.compass_topics`) when the claim maps to one; otherwise a **free issue label** — the record is expansive beyond the current compass |
| `evidence_type` | `quote` (slice 1); future: `vote`, `action` |
| `verbatim_text` | the candidate's exact words, substring-verified against the fetched source |
| `source_url` | the **primary** source URL (after following any pointer) |
| `cited_via` | the original compass-research citation this trail started from (provenance; e.g. the ontheissues/wikipedia pointer that led here) |
| `context` | the surrounding passage + date + setting — enough for a reader to vet it |
| `deep_link` | URL (+ anchor/`&t=` timestamp when applicable) to the exact moment |
| `source_type` | `primary` \| `pointer_followed` \| `secondary_lead` \| `scorecard_quiz` \| `vote_record` \| `video_unfetched` \| `dead` |
| `gates` | `{verbatim, own_words, in_context, primary, tag, judge_score, dispute_risk}` |
| `status` | `green` \| `flagged` (+reasons) \| `dropped` (+reason) |
| `provenance` | extractor model, cross-checker model, judge model, batch id, timestamps |

**Trust invariants.**

- `verbatim_text` must be an exact normalized substring of the fetched primary text. No substring
  → the item is dropped. This is the single strongest, free trust lever.
- An item is `green` **only** when it comes from a **primary** source. A quote reported by a
  secondary source is never green from that secondary (see *Ingestion leads*).
- Aggregators (ontheissues, wikipedia) and scorecard/quiz sites (lcv, isidewith) are never a
  `source_url` for a green item — they are pointers to follow or sources to drop.

---

## The pipeline (the swarm that checks itself)

Per candidate, over each cited source URL:

1. **Triage** — classify the URL:
   - `primary` — the candidate's own venue: their campaign site, official page, their signed
     op-ed, **or an outlet's own interview/Q&A with the candidate** (the article *is* the primary
     record of that exchange). → extract.
   - `pointer` — wikipedia / ontheissues / votesmart landing / web.archive wrapper. → follow to
     what it cites, then re-triage the target.
   - `secondary` — news *reporting on* a separate event where the candidate spoke ("at Tuesday's
     debate she said…"). → **ingestion lead** (do not treat as a green source).
   - `scorecard_quiz` — lcv, isidewith, and the like. → drop (nothing quotable).
   - `vote_record` — legislature/congress/cityclerk. → log for the future votes track; not
     extracted in slice 1.
   - `video` — YouTube etc. Verbatim needs a transcript. → **ingestion lead** (or use ready
     captions if trivially available); not transcribed in slice 1.
   - `dead` — url_broken / robots_disallowed (reuse the `researchVerifier` verdicts vocabulary).

   The primary-vs-secondary call is the crucial nuance and is made jointly by the extractor and
   the cross-checker (step 5), not by domain alone.

2. **Fetch** — reuse `src/discovery/feeds.py::fetch_page_text` (robots-aware, browser-compatible
   identifying UA; full-text, not the 6 KB peek). Cache fetches for reproducibility.

3. **Extract** — an LLM agent (via `src/llm_providers.py`) pulls the candidate's **verbatim**
   quotes that state a view on an issue: one claim per quote, following the `publish-quotes`
   EDITORIAL discipline (honest `…`, `[bracket]` inserts, no summaries). It captures the
   surrounding context window, the date, and the setting.

4. **Verbatim gate (deterministic).** Normalize (whitespace, quotes/dashes, ellipsis handling)
   and require the quote to be a substring of the fetched text. Fail → **drop** (a hallucination).

5. **Independent cross-check.** A second agent — a **different model** for independence — reads
   the source cold (no sight of the extractor's reasoning) and judges: is this the candidate's own
   words (not the interviewer/author)? in context? a primary source? correctly issue-tagged?
   Disagreement on any dimension → **flag** (never a silent drop).

6. **Context / citation.** Ensure the captured context + `deep_link` lets a reader see it
   themselves (the "follow the link and vet" guarantee).

7. **Judge + disposition.** An LLM-judge scores the judgment dimensions (tag correctness, context
   sufficiency, dispute-resistance = "would the candidate say 'I never said that' / 'out of
   context'"). Disposition:
   - **green** = verbatim pass ∧ source_type `primary` ∧ cross-checker agrees on all dimensions ∧
     judge ≥ thresholds.
   - **flagged** (surfaced to Chris) = any cross-checker disagreement or borderline judge score.
   - **dropped** (logged, not shown) = verbatim-fail, scorecard/quiz, dead.

   `secondary` and `video` sources do not produce evidence items at all; they produce
   **ingestion leads** (a separate output — see below).

**Model independence.** Extractor, cross-checker, and judge should be *different* models (all via
OpenRouter, per the `openrouter-llm-migration` policy) so the swarm genuinely catches its own
mistakes rather than sharing a blind spot. Exact model choices are a tuning parameter; higher-
stakes than discovery, so a stronger class than the discovery deepseek default.

---

## Ingestion leads (chase the primaries that matter)

A secondary source that attributes a *spoken* statement to a primary event (town hall, interview,
debate, podcast, speech) is a **lead**, not a dead end — it tells us which primary is worth
ingesting. This turns the large secondary corpus into a prioritized "chase the primary" queue
instead of ingesting everything.

Each lead carries: `politician_id`, the **reported quote text marked "reported by <secondary>,
NOT verified against a primary — chase to confirm"**, the issue, the event (venue + date), the
secondary URL as proof-it-exists, and any locatable primary handle (e.g. a YouTube link named in
the article). A lead's reported text is **never green** until the primary is ingested and
verbatim-verified.

In slice 1 (no writes) leads are a `leads.json` artifact. Later they feed the existing
"chase the primary" discovery lane (`essentials.discovered_sources`, `route='ingest'`), which
already exists (see the `discovery-review-reorg-design` memory, lane 2).

---

## The eval harness (eval-first)

Every step gets a number, so quality is measured, not assumed. Three layers:

**Deterministic (no gold needed).**
- Verbatim pass rate (substring check).
- Primary-source rate — share of green items whose `source_type = primary`.
- Pointer-follow success rate — of pointers, how many resolved to a primary.
- Per-domain yield — green items per source domain.
- Lead yield — number of chase-worthy primaries surfaced.
- Green / flagged / dropped counts (with drop reasons).

**LLM-judge (a separate model).**
- Tag correctness, context sufficiency, dispute-resistance — scored per item.

**Thin human gold (Chris, once).**
- Chris labels ~20–30 items from the run: for each, is this a real, in-context, own-words quote
  on the right issue that we would stake the org on? (precision.)
- Plus a small **recall** sample: for a handful of the candidates' sources, Chris lists the
  quotes that *should* have been found, to measure misses.
- The gold **calibrates the judge thresholds** and yields precision + recall for the slice.

The harness is a repeatable regression asset (like the discovery hub-recall eval): re-runnable,
metric-averaged over runs where a step is nondeterministic.

---

## Outputs (artifacts)

Written under `docs/superpowers/spikes/2026-09-19-evidence-trust-core/` (and/or a `.runs/` dir):

- `evidence_items.json` — every item with all fields and disposition.
- `leads.json` — the chase-the-primary queue.
- `eval_report.md` — the metrics above + the gold comparison (precision/recall).
- `review.html` — a generated static page grouping green / flagged / dropped evidence items per
  candidate × issue (each row: the quote, its context, the click-through `deep_link`, the
  source_type, the gate results) plus the **leads** list, so Chris skims and judges a sample and
  sets the gold. (May be sent via SendUserFile / published as an Artifact for review.)

---

## Architecture / modules (on-the-record)

New package `src/evidence/`, each unit pure/testable where possible:

- `triage.py` — URL → source_type (rules + LLM); pointer following.
- `extract.py` — source text → candidate verbatim quote candidates + context.
- `verify.py` — deterministic verbatim substring gate + normalization.
- `crosscheck.py` — independent cold re-read; per-dimension agree/disagree.
- `judge.py` — judgment-dimension scoring.
- `leads.py` — secondary/video → lead records.
- `evidence_eval.py` — pure scorer over items + gold (offline-testable).
- `report.py` — render `eval_report.md` + `review.html`.

Runner: `scripts/evidence_slice.py --race <id> [--candidate <id>] [--limit N]`.

**Reuse:** `src/llm_providers.py` + `src/config.py` (LLM), `src/discovery/feeds.py` (fetch,
robots, CC/window helpers), `src/source_key.py` (source identity), the `publish-quotes` EDITORIAL
discipline and `docs/quote-curation/PRINCIPLES.md` (the *why*), and the `audit-quotes` checks as
the gate vocabulary.

**Data access (read-only):** `inform.politician_context(.sources[])` for input; roster from
`essentials.race_candidates` + `essentials.politicians`; issue tags validated against
`inform.compass_topics`. Connect via the ev-accounts `backend/.env` `DATABASE_URL`, read-only.

---

## Risks and mitigations

- **Primary-vs-secondary misclassification** (an outlet's own interview wrongly called secondary,
  or reporting wrongly called primary). → the extractor+cross-checker decide jointly; the gold
  measures it; disagreements flag, never silently drop.
- **Pointer-following dead ends** (ontheissues cites nothing followable). → recorded as a metric,
  not a failure; the item just does not become green.
- **Judge blind spot.** → the thin human gold calibrates the judge; deterministic checks carry
  the highest-stakes dimension (verbatim).
- **Video-heavy candidates** yield leads, not green items, in slice 1. → expected; lead yield is
  itself a success measure.
- **Cost.** Three models per item over ~130 sources is bounded for one race; cache fetches;
  batch. Not a concern at slice scale.

---

## Success criteria

For LA Mayor, from the candidates' already-cited sources:

- A set of **green** quotes we would defend to the candidate, with **near-zero verbatim failures**
  (deterministic).
- A **measured** primary-source rate, precision, and recall against Chris's thin gold — the
  numbers exist and are believable, even if not yet high.
- A **leads** list that names real primaries worth ingesting.
- Every pipeline step reports a number Chris can watch as the program scales.

The bar for the slice is *trustworthy and measured*, not *high-volume*. Volume is a later
sub-project once the core is proven.

---

## Execution notes (for the plan)

- Build in on-the-record; **run with the main checkout `.venv/bin/python`** (the worktree has no
  venv — see the GUI QoL memory lesson).
- ev-accounts is **read-only** in this slice; no migration, no writes.
- Offline unit tests for the pure units (verify, eval scorer, leads shaping, report render);
  the online extract/crosscheck/judge run behind a manual runner like the hub-recall eval.
- LLM traffic via OpenRouter per the `openrouter-llm-migration` policy; different models for
  extractor / cross-checker / judge.
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
