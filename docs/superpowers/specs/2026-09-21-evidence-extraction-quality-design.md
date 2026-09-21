# Evidence Extraction Quality — own-words + fuller context (capture only)

**Status:** Draft for review (brainstormed with Chris 2026-09-21)
**Repo:** on-the-record only — `src/evidence/extract.py` (prompt + shaping), `src/evidence/crosscheck.py`
(own-words judgment), `src/evidence/verify.py` (tests), tests. **No schema change**
(`inform.evidence_items.context` already exists); no ev-accounts change.
**Follows:** the pipeline-robustness slice (PR #249) and the **Tier-1 quote rule** Chris set while
reviewing Bass (2026-09-21): *a quote must be the candidate's literal words.*

## Scope: this slice CAPTURES quotes; it does NOT editorialize

Chris's clarification (2026-09-21): condensing a quote to its essence is **editorializing**, and that is
already governed by existing rules (`docs/quote-curation/PRINCIPLES.md` + `EDITORIAL.md`, and the
`publish-quotes` skill). So condensation is **a separate step/gate, deferred** — not part of this slice.
**This slice's job is to get the quotes right: faithful, full, and truly the candidate's words.** A
later, rule-governed editorializing step trims them to their essence for display.

## Why (what the Bass review exposed)

Reviewing Bass's committed evidence surfaced three capture problems:

1. **The own-words gate greens third-person paraphrase.** Campaign platforms are written in the third
   person (*"Bass will consolidate…", "As Mayor, Bass will expand…"*). The cross-checker marked these
   `own_words = true` and the pipeline greened them, so ~5 of Bass's 13 green were staff-written
   positions, not her words. Chris rejected them. The fix is a stricter, **text-level** own-words test.
2. **Her real first-person quotes are missed inside third-person sources.** Press releases and news
   articles are third-person overall but usually contain a direct first-person quote
   (*"'We will…,' Bass said."*). The extractor pulled the paraphrase and dropped the quote — so news
   sources yielded zero first-person material even when it was present.
3. **Quotes are fragmented and thin on context.** The same source produced several tiny snippets about
   the same thing, each too short to show its setting. Chris wants a **fuller passage captured** so a
   reader can click in and vet the full context — with the *displayed* condensing left to the separate
   editorializing step.

The rule underneath: **the quote is the candidate's literal words, captured faithfully and in full
context.** That is what makes it defensible ("she can't say 'I never said that'").

## Goals

1. **Own-words = first person OR a direct quotation of the candidate.** First person includes **I and
   we** (I/we/my/our/us) — a "we will…" from the candidate is her words. A sentence describing her in
   the third person (*"Bass will…", "the Mayor has…"*) is **not** own-words. The test is on the **text's
   voice**, applied per quote — **never a per-domain block** (a campaign site that says "I will…" is
   Tier-1 and stays; the same site's "Bass will…" is a position, not a quote).
2. **Isolate first-person quoted spans inside any source.** When a third-person source (press release,
   news article) contains a sentence directly quoted from the candidate, capture **that quoted span** —
   not the surrounding paraphrase. This is how we recover real quotes from the news/press we already
   fetch.
3. **Capture the full coherent verbatim passage, faithfully.** `verbatim_text` is the candidate's
   contiguous words covering one stance and, where present, its mechanism — as many sentences as that
   takes, captured **as-is** (no trimming, no ellipsis editorializing — that is the deferred step's job).
4. **Capture a fuller context passage.** `context` = the surrounding paragraph(s) — enough that a reader
   clicking in sees the full setting — bounded to a sensible size.
5. **Split by stance.** One stance per quote. A passage covering two distinct things becomes two quotes,
   each carrying the same fuller context.

## Non-goals (deferred)

- **All editorializing / condensation.** Trimming a quote to its essence (including ellipsis cuts) is a
  **separate, later step** governed by `docs/quote-curation/PRINCIPLES.md` + `EDITORIAL.md` and the
  `publish-quotes` skill. This slice must **not** condense, trim, or reword — it captures the faithful
  full passage and stores the fuller context for that step (and the eventual read-rank view) to use.
- **No rewording, ever** (in this slice or the next): the evidence record is the candidate's exact words.
- **No schema change** and **no domain gating.**
- **The public click-in UI** (showing full context to voters) is a product-page / read-rank-view concern.
- **Re-committing Bass to prod.** Changing extraction changes `verbatim_text`, so a re-run makes *new*
  rows (the dedup key includes the text) rather than updating the old ones. Validation here is
  **artifact-based** (before/after on Bass); whether to re-commit and retire the prior Bass batch is a
  separate operational decision.
- Disposition thresholds and the verbatim gate's logic are unchanged (own-words simply gets stricter, so
  third-person stops greening; a fuller captured passage also gives the mechanism gate more to see).

## Design

### `src/evidence/extract.py` — the prompt + shaping
- **own_words definition (prompt):** true only if the quote is in the candidate's own voice —
  first person **I or we** (I/we/my/our/us) — **or** a sentence directly quoted from the candidate
  (quotation marks / an attributed "…," she said). A third-person description of the candidate is
  `own_words = false`.
- **Isolate quoted spans:** when the SOURCE is third-person about the candidate but contains a
  directly-quoted sentence from them, capture the quoted sentence as `text` (own_words true) and treat
  the surrounding article as the venue.
- **Capture faithfully, do not editorialize:** `text` is the coherent **contiguous** verbatim passage
  for one stance (+ its mechanism where stated) — captured as-is. **Remove** the current prompt's
  "trim only filler; mark a substantive internal cut with …" instruction from this step; trimming is the
  deferred editorializing step's job. Raise the length guidance from "1 to 3 sentences" so a full
  stance-plus-mechanism passage is captured (no upper trim here).
- **Fuller context:** `context` = the surrounding paragraph(s), enough to vet the quote; bounded.
- **Split by stance:** reaffirm one stance per quote; emit multiple quotes from a multi-stance passage.
- Shaping (`_to_candidate`) is unchanged in structure; it already carries `text` + `context`.

### `src/evidence/crosscheck.py` — align the own-words judgment
The cross-checker's `own_words` check must use the **same** definition (first person incl. "we", or a
direct quotation; third-person paraphrase = false), so it stops confirming third-person platform prose
as her words. This is the gate that greened "Bass will…"; tightening it here is what stops the leak.

### `src/evidence/verify.py` — confirm faithful capture passes
The verbatim gate checks each quote is an exact substring of the full page (ellipsis-tolerant). A
contiguous captured passage is a plain substring and must pass; a reworded quote must fail. Add tests;
no logic change expected.

## Tests (offline, mock provider / fixtures)
- **Parse contract:** given a model reply, `parse_extract` yields the right fields; a fuller `context`
  round-trips; a multi-quote reply from one passage yields multiple candidates.
- **own_words semantics** via fixtures (first-person incl. "we" → true; third-person → false), against
  `parse_extract` / `crosscheck` shaping with a mock provider.
- **verify.py:** a contiguous captured passage that is an exact substring of the full text passes; a
  reworded quote fails.
- Full suite stays green.

## Validation (human-gated, artifact-based)
Re-run Bass (and spot-check Nithya) with the new extractor, writing artifacts only (no prod commit).
Expect: (a) third-person "Bass will…" no longer appears as green (own_words false); (b) direct quotes
recovered from her news/press sources; (c) fuller, fewer, more coherent captured passages with a fuller
`context`. Show Chris a before/after on the same sources; he decides whether/when to re-commit and
retire the prior Bass batch, and when to start the separate editorializing step.

## Risks / decisions
- **Faithful capture, not editorializing.** The verbatim gate is the backstop, but the prompt must be
  unambiguous: capture the candidate's exact contiguous words; do not trim, cut, or reword. Condensation
  is the next, rule-governed step.
- **Isolating quoted spans** depends on the source actually quoting the candidate; where a source only
  paraphrases, nothing is captured (correct — it's a position, not a quote).
- **Fuller context / passage size** is a tuning knob; too large bloats storage and the prompt, too small
  defeats the purpose. Pick a bounded target and confirm on the Bass artifacts.
- **own_words stricter → lower green yield** for incumbents whose web presence is third-person. Intended;
  the material comes from questionnaires, op-eds, direct quotes in news, and spoken sources.

## Success criteria
- Offline: own_words fixtures (first-person incl. "we" true; third-person false) pass; a fuller-context
  reply round-trips; a contiguous captured passage passes the verbatim gate and a reworded one fails;
  full suite green.
- Live (gated, artifacts): on Bass, green contains only first-person/direct-quote material, at least one
  real quote is recovered from a news/press source, and quotes carry a fuller captured passage + context.

## Execution notes
- Run with the MAIN checkout `.venv/bin/python`; `OPENROUTER_API_KEY` exported; `--env-file
  <ev-accounts>/backend/.env` for the read-only DB. Models: extractor `haiku-or`, crosschecker
  `gemini-flash`, judge `deepseek` (OpenRouter).
- Commit trailer: `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.
