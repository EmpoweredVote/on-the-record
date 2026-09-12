# Quote-curation skill reconciliation — design

- **Date:** 2026-09-12
- **Status:** Draft for review
- **Supersedes framing from:** an unwritten 2026-07-12 brainstorm (never committed to a spec)
- **Scope:** `on-the-record` skills `publish-quotes`, `audit-quotes`; `ev-accounts` skills `research-stances`, `compass-topic-builder`

## Problem

Four skills across two sibling repos all govern the same domain — turning candidate speech into
faithful, blind-rankable quotes in `essentials.quotes` (the ev-accounts DB). They were reconciled
once, by **copying**, and the copies have drifted.

- `publish-quotes` (on-the-record) — crafts quotes from transcript text, inserts drafts via
  `scripts/insert_quotes.py`. Owns `EDITORIAL.md` (editing/de-id mechanics).
- `audit-quotes` (on-the-record) — audits existing quotes. Owns `CHECKS.md` (mechanical + judgment
  checks) and `scripts/{audit,apply_fixes}.py`.
- `research-stances` (ev-accounts) — discovers stances and, as a byproduct, quotes. It **inlines**
  paraphrases of the selection gates, the de-identification contract, and the editor-note rule, and
  it ships `scripts/build-and-check.mjs`, a hand-port of `audit-quotes/scripts/checks.py` into Node.
- `compass-topic-builder` (ev-accounts) — references the curation principles for the coupling model.

Two structural faults:

1. **The canonical principles doc is vaporware.** `QUOTE-CURATION-PRINCIPLES.md` is referenced by
   every skill (8 files as of 2026-09-12) but does not exist in either working tree. It is referenced
   at the ev-accounts-shaped path `essentials/docs/QUOTE-CURATION-PRINCIPLES.md`, which is not even a
   real directory in on-the-record. Every "source of truth" pointer is dangling.
2. **Reconciliation-by-copy drifts.** `research-stances` carries stale paraphrases, and
   `build-and-check.mjs` re-implements the audit checks in a second language, in a second repo.

### What changed between the brainstorm (2026-07-12) and now (2026-09-12)

The design must account for two months of work on the on-the-record skills (13 commits) and the
ev-accounts skills (3 commits):

- **The check/rule surface grew substantially.** `audit-quotes` added a whole **source-verification**
  family — the audit now re-fetches the cited source and verifies the quote against it (#134 and
  follow-ups; it did not before). New checks include `non-differentiating-goal` (the "show the HOW"
  rule), `stance-label`, `scorecard-source`, and `invalid-source` split into re-attribute vs. delete.
  The `editor_note` rule now permits a **third** sentence (207b99e); `build-and-check.mjs` still
  rejects more than two. The source tiers were reworked into a **questioner-independence** ladder with
  a written-medium verbatim rule (9cf83d6, 41de02f). The drift the brainstorm predicted has already
  happened and widened.
- **A new model concept landed: the per-race "ranking question" (#78).** The off-question gate is now
  judged against a race's ranking question, not only the topic. `research-stances` has no concept of
  this.
- **The three "dangling" branches are obsolete, not pending.** `docs/quote-curation-{punctuation,
  responsiveness,tier4-absent}` were superseded on `main` by #64/#65/#66 and later tier work. They
  must be confirmed-superseded and deleted, not merged.
- **The compass moved to a revision + season model (#196, #222),** which also re-pointed the
  `research-stances` reads. The principles doc's coupling section and the compass references must
  align with the season model.

## Goals

1. **Kill the drift.** One source of truth for curation rules; all four skills consume it, so a rule
   change updates everywhere.
2. **Fix quote quality.** `research-stances`' quote output must meet the same bar as the curation
   skills — enforced by construction, not by a stale paraphrase.
3. **Land the principles doc.** Author `QUOTE-CURATION-PRINCIPLES.md` for real and make every
   reference point at it.

## Non-goals

- **Unifying the insert path.** `insert_quotes.py` (publish-quotes) and the inline Node inserts
  (research-stances) stay as separate write paths. Explicitly out of scope.
- **The monorepo question.** Whether to merge the two repos is a separate, larger decision with its
  own criteria (CI, deploy, workflow). This design is deliberately **monorepo-agnostic**: its one
  cross-repo fragility is exactly what a monorepo would remove, and a future merge must be a no-op for
  this work. The monorepo gets its own brainstorm.

## Approach

**Chosen: A — canonical corpus in on-the-record; consumers reference it and inject its text at
runtime.** Rejected alternatives:

- **B — extract a standalone shared `quote-curation` package/submodule both repos vendor.**
  Over-engineered for a four-skill toolchain; submodule/packaging overhead not justified.
- **C — collapse the quote path so `research-stances` does discovery only and `publish-quotes` is the
  sole crafter/inserter.** Conceptually cleanest, but it unifies the insert path, which is a non-goal.

### Why on-the-record is the home

The principles doc is genuinely cross-cutting (its coupling model is an ev-accounts/compass concern;
its editing/anonymity philosophy parents the on-the-record mechanics). Either home forces one side to
reach across. on-the-record minimizes **net new** cross-repo coupling: `EDITORIAL.md` and `CHECKS.md`
already live there and are costly to move; `publish`/`audit` become fully local; `research-stances`
already reaches into on-the-record (it runs `audit.py` cross-repo today) and that pattern is accepted;
only `compass-topic-builder` gains one new cross-repo edge (principles §coupling).

## Detailed design

### 1. Canonical corpus — home and layout

- **New file, authored for real:** `on-the-record/docs/quote-curation/PRINCIPLES.md`. A neutral,
  skill-agnostic home (not inside a skill directory, because it is cross-cutting). This is the "why"
  that `EDITORIAL.md` and `CHECKS.md` already claim to descend from.
- **`EDITORIAL.md` and `CHECKS.md` stay in their skill directories.** They are legitimately
  skill-specific mechanics (publish owns editing; audit owns checks). Only the cross-cutting
  principles doc needs the neutral home. *(Open decision 1 below records the fuller alternative.)*
- **Reference strategy = monorepo-agnostic.** Skills refer to the corpus by **logical location** —
  "the on-the-record repo, `docs/quote-curation/PRINCIPLES.md`" — plus a one-line resolution note
  ("sibling checkout today: `../on-the-record/...`"). A future monorepo migration edits only the
  resolution note, not every reference.

### 2. Land the corpus complete, then repoint (revised)

- **Confirm the three legacy branches are superseded, then delete them.** `docs/quote-curation-
  punctuation`, `-responsiveness`, `-tier4-absent` were absorbed into `main` via #64/#65/#66 and later
  tier work. Verify each carries nothing novel against current `main`, then delete. **(This replaces
  the brainstorm's now-wrong "merge the three branches first" step.)**
- **Author `PRINCIPLES.md`** from the philosophy the current mechanics already encode. Required
  sections, updated for the 2026-09 state:
  - selection philosophy;
  - the Compass **coupling model** (the section `compass-topic-builder` points at) — **aligned with
    the revision + season model** (#196/#222);
  - the **ranking-question** model (#78): the race-level question a topic's quotes are ranked to
    answer, and how it governs the on-question gate;
  - anonymity / blind-card model;
  - accountability (policy/office vs. person);
  - **sourcing**: the questioner-independence tier ladder, the written-medium verbatim rule, and
    **source verification** (the quote must be checked against its cited, ingested source);
  - the **differentiation** principle (show the HOW / mechanism, not just a shared goal);
  - responsiveness / on-question gate; absent-not-launder; the five-chairs framing note.

### 3. Kill the rule drift — runtime injection (core mechanism, widened)

- `research-stances` **deletes its inlined paraphrases** of the gates, the de-id contract, and the
  editor-note rule.
- The orchestrator **injects the canonical text at runtime**, exactly as it already pastes the live
  topic-scale JSON into the research sub-agent prompt. The prompt carries placeholders filled from the
  canonical files each run.
- **Injectable span set (widened for 2026-09):**
  - quote-selection **gates** — from `CHECKS.md` (forward-not-record, on-question, not-attack);
  - the **ranking-question** rule (#78) — the on-question gate now resolves against the race's ranking
    question, not only the topic;
  - the **differentiation** rule (`non-differentiating-goal`);
  - the **de-identification contract** — from `EDITORIAL.md` "Two layers";
  - the **editor-note** rule — from `EDITORIAL.md` (note the 2→3 sentence change, 207b99e).
- **Injection-fence markers** (`<!-- inject:gates:start … end -->`) delimit each injectable span in
  `EDITORIAL.md`/`CHECKS.md`, so extraction is deterministic. One copy of the rules; the sub-agent
  always receives current text; drift is impossible by construction. This is what simultaneously fixes
  quote quality — the researcher stops working from a stale summary.

### 4. Kill the check drift — fixture-pinned parity (widened)

- Code cannot be cheaply shared across Python/Node/repos, but a **contract** can. Add a shared
  fixtures file (JSON cases → expected findings) to the corpus. Both `checks.py` (audit) and
  `build-and-check.mjs` (research-stances) run it in their test suites and must produce identical
  findings for the checks they share.
- `build-and-check.mjs` **keeps its bundle-building** (genuine CSV→audit-bundle glue) and its
  pre-insert mechanical checks, but those checks are pinned to the fixture contract instead of
  free-floating. This preserves the "CSV is the source of truth" pre-insert workflow.
- **Scope note:** the pre-insert Node checks cover only the deterministic, CSV-visible subset.
  **Source verification** (#134 family) needs the ingested OTR transcripts and cannot run pre-insert;
  it stays exclusively in `audit.py`, invoked on the drafts (research-stances STEP 4e already does
  this). The fixture contract therefore pins the shared deterministic checks and explicitly documents
  which audit checks are audit-only.

### 5. Reference sweep — repoint all references

Repoint every `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` reference to the real logical location,
across both repos — **8 files as of 2026-09-12** (publish SKILL/EDITORIAL/REFERENCE, audit
SKILL/CHECKS, research-stances, compass-topic-builder, plus the eighth added since July). Re-verify
the exact file list at implementation time.

### 6. Season-model alignment (new step)

Reconcile the principles doc's coupling section and the compass references with the revision + season
model (#196/#222). Confirm `research-stances` and `compass-topic-builder` reads still resolve after
the reference sweep, given #196 already re-pointed those reads.

## Open decisions (proposed defaults — confirm at review)

1. **Corpus scope.** *Proposed:* (a) minimal — only `PRINCIPLES.md` gets the neutral home;
   `EDITORIAL.md`/`CHECKS.md` stay in skill dirs. *Alternative:* (b) move all three into
   `docs/quote-curation/` for one physical corpus (cleaner, more churn, touches more skills).
2. **`build-and-check` dedupe.** *Proposed:* (c) keep the Node checks, pin with fixtures. *Alternative:*
   (a) delete the Node checks and rely only on post-insert `audit.py` — simpler, but breaks fix-in-CSV
   and reintroduces CSV↔DB drift.

## Implementation sequence

1. Confirm-superseded and delete the three legacy branches (§2).
2. Author `PRINCIPLES.md` at the neutral location (§1, §2), season-model-aligned (§6).
3. Add injection-fence markers to `EDITORIAL.md`/`CHECKS.md`; add the shared fixtures file (§3, §4).
4. Refactor `research-stances`: remove inlined paraphrases; add the runtime-injection step covering the
   widened span set incl. ranking question + differentiation (§3).
5. Repoint `build-and-check.mjs` checks to the fixture contract; document audit-only checks (§4).
6. Reference sweep across all 8 files (§5).
7. Verification (below).

## Verification

- **Parity test green:** `checks.py` ≡ `build-and-check.mjs` on the shared fixtures.
- **Injection test:** render a research sub-agent prompt; assert the canonical gate / ranking-question
  / differentiation / de-id / note text appears verbatim, and that no stale paraphrase remains in
  `research-stances/SKILL.md`.
- **Reference test:** grep finds zero `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` and zero dead
  corpus links across both repos.
- **Season-model test:** `research-stances` and `compass-topic-builder` reads resolve against current
  ev-accounts `main`.

## Risks

- **Cross-repo path fragility.** An AFK/CI agent that checks out only one repo cannot resolve the
  corpus. Accepted (pre-existing, already relied on by STEP 4e). A monorepo removes it later.
- **Fixture drift.** If a new audit check is added without a fixture case, parity silently narrows.
  Mitigation: a test that every shared check id has at least one fixture case.
- **Injection completeness.** If an injectable span is renamed without updating the fence marker, the
  sub-agent silently loses that rule. Mitigation: the injection test asserts each named span resolves
  non-empty.
