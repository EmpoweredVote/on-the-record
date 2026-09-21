# Evidence Extraction Quality — own-words, fuller context, condensed essence

**Status:** Draft for review (brainstormed with Chris 2026-09-21)
**Repo:** on-the-record only — `src/evidence/extract.py` (prompt + shaping), `src/evidence/crosscheck.py`
(own-words judgment), `src/evidence/verify.py` (confirm ellipsis condensation passes), tests. **No
schema change** (`inform.evidence_items.context` already exists); no ev-accounts change.
**Follows:** the pipeline-robustness slice (PR #249) and the **Tier-1 quote rule** Chris set while
reviewing Bass (2026-09-21): *a quote must be the candidate's literal words.*

## Why (what the Bass review exposed)

Reviewing Bass's committed evidence surfaced three problems, all in extraction:

1. **The own-words gate greens third-person paraphrase.** Campaign platforms are written in the third
   person (*"Bass will consolidate…", "As Mayor, Bass will expand…"*). The cross-checker marked these
   `own_words = true` and the pipeline greened them, so ~5 of Bass's 13 green were positions written by
   staff, not her words. Chris rejected them. The fix is a stricter, **text-level** own-words test.
2. **Her real first-person quotes are being missed inside third-person sources.** Press releases and
   news articles are third-person overall but usually contain a direct first-person quote
   (*"'We will…,' Bass said."*). The extractor pulled the paraphrase and dropped the quote — so news
   sources yielded zero first-person material even when it was present.
3. **Quotes are fragmented.** The same source produced several tiny snippets about the same thing, each
   too short to carry context. Chris wants a **fuller passage captured for context** (click in and vet),
   with the displayed quote **condensed to its essence** — still her exact words, filler removed with
   "…", never reworded — and split into separate quotes only when a passage genuinely covers two things.

The rule underneath all three: **the quote is the candidate's literal words; the reader can always see
the full surrounding context.** That is what makes the evidence defensible ("she can't say 'I never
said that'").

## Goals

1. **Own-words = first person OR a direct quotation of the candidate.** First person includes **I and
   we** (I/we/my/our/us) — a "we will…" from the candidate is her words. A sentence describing her in
   the third person (*"Bass will…", "the Mayor has…"*) is **not** own-words. The test is on the **text's
   voice**, applied per quote — **never a per-domain block** (a campaign site that says "I will…" is
   Tier-1 and stays; the same site's "Bass will…" is a position, not a quote).
2. **Isolate first-person quoted spans inside any source.** When a third-person source (press release,
   news article) contains a sentence directly quoted from the candidate, extract **that quoted span** as
   the quote — not the surrounding paraphrase. This is how we recover real quotes from the news/press we
   already fetch.
3. **Capture a fuller context passage.** `context` should hold the surrounding paragraph(s) — enough
   that a reader clicking in sees the full setting of the quote — bounded so it stays reasonable.
4. **Condense the displayed quote to its essence, verbatim.** `verbatim_text` is the point of the
   passage: contiguous verbatim spans with filler trimmed and any substantive internal cut marked "…".
   **Exact words only — no rewording or paraphrase.** Allow more sentences than today (up to ~6–8 when a
   coherent stance-plus-mechanism needs them), then trim to essence.
5. **Split by stance.** One stance per quote. A passage covering two distinct things becomes two quotes,
   each carrying the same fuller context.

## Non-goals (deferred)

- **No rewording.** "Condense" means ellipsis-trimming her exact words, not editing them. Any display
  cleanup beyond that belongs to the *derived read-rank view*, not the evidence record.
- **No schema change** and **no domain gating.**
- **The public click-in UI** (showing full context to voters) is a product-page / read-rank-view
  concern — this slice only *stores* the fuller context so that UI is possible later.
- **Re-committing Bass to prod.** Changing extraction changes `verbatim_text`, so a re-run makes *new*
  rows (the dedup key includes the text) rather than updating the old ones. Validation here is
  **artifact-based** (before/after on Bass); whether to re-commit and retire the prior Bass batch is a
  separate operational decision.
- Disposition thresholds and the verbatim gate's logic are unchanged (own-words simply gets stricter,
  so third-person stops greening).

## Design

### `src/evidence/extract.py` — the prompt + shaping
- **own_words definition (prompt):** true only if the quote is in the candidate's own voice —
  first person **I or we** (I/we/my/our/us) — **or** a sentence directly quoted from the candidate
  (quotation marks / an attributed "…," she said). A third-person description of the candidate is
  `own_words = false`.
- **Isolate quoted spans:** instruct that when the SOURCE is third-person about the candidate but
  contains a directly-quoted sentence from them, extract the quoted sentence as `text` (own_words true)
  and treat the surrounding article as the venue.
- **Fuller context:** `context` = the surrounding paragraph(s), enough to vet the quote (target a
  richer passage than today; bounded to a sensible size).
- **Condensed essence, verbatim:** keep the current "trim filler, mark substantive cuts with …" rule;
  make explicit that the aim is the *essence* of a possibly longer passage, using only her exact words.
  Raise the stance-length guidance from "1 to 3 sentences" to allow the coherent stance + mechanism
  (up to ~6–8 sentences) before condensing.
- **Split by stance:** reaffirm one stance per quote; emit multiple quotes from a multi-stance passage.
- Shaping (`_to_candidate`) is unchanged in structure; it already carries `text` + `context`.

### `src/evidence/crosscheck.py` — align the own-words judgment
The cross-checker's `own_words` check must use the **same** definition (first person incl. "we", or a
direct quotation; third-person paraphrase = false), so it stops confirming third-person platform prose
as her words. This is the gate that greened "Bass will…"; tightening it here is what stops the leak.

### `src/evidence/verify.py` — confirm condensation passes
The verbatim gate is already ellipsis-tolerant (each retained span must be an exact substring of the
full page). Confirm a multi-span "…"-condensed quote drawn from a fuller passage passes, and that a
reworded (non-verbatim) quote fails. Add tests; no logic change expected.

## Tests (offline, mock provider / fixtures)
- **Extractor prompt-shaping is prompt-level**, so test the *parseable contract*, not the LLM: given a
  model reply, `parse_extract` still yields the right fields; a fuller `context` round-trips; a
  multi-quote reply from one passage yields multiple candidates.
- **own_words semantics** are exercised with fixtures the way the current suite does (first-person incl.
  "we" → own_words true; third-person → false), against `parse_extract`/`crosscheck` shaping with a mock
  provider.
- **verify.py:** a "…"-condensed quote whose spans are exact substrings of the full text passes; a
  reworded quote fails.
- Full suite stays green.

## Validation (human-gated, artifact-based)
Re-run Bass (and spot-check Nithya) with the new extractor, writing artifacts only (no prod commit).
Expect: (a) third-person "Bass will…" no longer appears as green (own_words false); (b) direct quotes
recovered from her news/press sources; (c) fewer tiny fragments — coherent condensed quotes with a
fuller `context`. Show Chris a before/after on the same sources; he decides whether/when to re-commit
and retire the prior Bass batch.

## Risks / decisions
- **"Condense" must never become "reword."** The verbatim gate is the backstop, but the prompt must be
  unambiguous: exact words, ellipsis cuts only. This is the defensibility guarantee.
- **Isolating quoted spans** depends on the source actually quoting the candidate; where a source only
  paraphrases, nothing is extracted (correct — it's a position, not a quote).
- **Fuller context size** is a tuning knob; too large bloats storage and the prompt, too small defeats
  the purpose. Pick a bounded target and confirm on the Bass artifacts.
- **own_words stricter → lower green yield** for incumbents whose web presence is third-person. That is
  intended; the material comes from questionnaires, op-eds, direct quotes in news, and spoken sources.

## Success criteria
- Offline: own_words fixtures (first-person incl. "we" true; third-person false) pass; a fuller-context
  reply round-trips; a condensed multi-span quote passes the verbatim gate and a reworded one fails;
  full suite green.
- Live (gated, artifacts): on Bass, green contains only first-person/direct-quote material, at least one
  real quote is recovered from a news/press source, and quotes carry a fuller context passage.

## Execution notes
- Run with the MAIN checkout `.venv/bin/python`; `OPENROUTER_API_KEY` exported; `--env-file
  <ev-accounts>/backend/.env` for the read-only DB. Models: extractor `haiku-or`, crosschecker
  `gemini-flash`, judge `deepseek` (OpenRouter).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
