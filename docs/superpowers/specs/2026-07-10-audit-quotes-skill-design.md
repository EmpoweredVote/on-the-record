# Design: `audit-quotes` skill (+ `publish-quotes` handoff)

**Date:** 2026-07-10 · **Status:** approved design, pre-implementation

## Problem

Read & Rank quotes are curated into `essentials.quotes` (ev-accounts DB). The principles that
govern them are codified in `essentials/docs/QUOTE-CURATION-PRINCIPLES.md`, and the sourcing tool
(`publish-quotes`, on-the-record) has been hardened to match. But there is **no reusable way to
audit quotes already in the DB** against those principles — the backlog spans many races and dozens
of quotes each, and stress-testing surfaced real defects in live rows (unmarked de-id paraphrase,
partisan tell, off-question live quote, non-verbatim summary). We need a standalone, reusable audit
that can sweep the **whole backlog across all races**, tied to sourcing so new quotes are vetted too.

## Shape (approved)

- A **new standalone `audit-quotes` skill** — sweeps existing `essentials.quotes`, **defaulting to
  all races**, narrowable by flags.
- **Update `publish-quotes`** so its final step **auto-runs `audit-quotes`** on the ids it just
  inserted (handoff), showing findings before the human selects the live quote.

## Location & source of truth

- `audit-quotes` lives in **on-the-record**, next to `publish-quotes` (paired tools; both operate on
  `essentials.quotes` through the `ev-accounts/backend` connection — see `publish-quotes/REFERENCE.md`).
- The audit **reads `essentials/docs/QUOTE-CURATION-PRINCIPLES.md`** as its check source of truth,
  so checks never drift from the canonical principles.

## Interface / targeting

- `audit-quotes` with **no scope → all races** (primary use case: backlog-wide sweep).
- Scope flags narrow it: `--race "<name/id>"`, `--candidate "<name>"`, `--topic <key>`, or an
  explicit list of quote ids (the `publish-quotes` handoff path).
- Default quote set = **live quotes** (`readrank_selected = true`); `--include-drafts` widens.
- **Scope confirmation gate:** before the (LLM-cost) judgment fan-out, print the resolved scope —
  N races, M quotes, ~K judgment agents — and confirm, the way `research-stances` confirms scope.
  The mechanical pre-pass runs first and is free, so a lot surfaces before any confirmation.

## Execution — pre-pass, fan-out judgment, portfolio, report, gated fix

**Step 0 — resolve scope → races → quote sets + context.** For every target quote pull *live*:
`quote_text`, `deidentified_text`, `editor_note`, source, `topic_key`, `readrank_selected`, and the
candidate's **Compass stance** (`inform.politician_answers.value` + the five `inform.compass_stances`
chair texts + `inform.compass_topics.question_text`). Accurate stance + spectrum every run.

**Step 1 — mechanical pre-pass (SQL, deterministic, all quotes at once):** `editor_note`
null/too-short/§-refs/>2-sentences · `deidentified_text` null · live-count per (candidate,topic) ≠ 1 ·
topics with <2 candidates · trailing-ellipsis regex · blind==canonical where a first-person/self-ID/
partisan word survives · source-tier heuristic (campaign-site domain → tier-4).

**Step 2 — judgment pass, orchestrated fan-out.** Parallel **`Agent`-tool subagents** (one per
race, or per race×topic for large races) — not the Workflow harness — each reading its quotes'
layers + stance + spectrum + the principles doc and returning **structured findings**.
Concurrency-capped; the skill aggregates. Checks:
forward-not-record · position-not-attack (policy/institution carve-out) · responsiveness
(answers the framed question?) · de-id honesty (blind strips self-ID / partisan tells / named
persons, *marked* not paraphrased) · editor_note quality (self-contained, states stance alignment) ·
source verbatim-not-summary · coupling relationship (reinforcing / elaborating / in-tension).

**Step 3 — portfolio §8 skew audit, per race.** Over each race's topic set: does coverage
systematically flatter one candidate (one sharp across topics while another is absent from most)?
Flag as a signal to investigate (true reflection vs. effort gap) — never an instruction to
engineer balance. Cheap to add here because the fan-out already holds the whole field per race.

**Step 4 — consolidated report.** One report for the whole sweep: a **cross-race summary** (races
ranked by count/severity of findings; per-race coverage-skew flags) plus **per-race sections**
listing quote/topic findings grouped by severity then fix-class. Written to a report file at
`on-the-record/docs/audits/<YYYY-MM-DD>-quote-audit.md` (scoped runs suffix the scope, e.g.
`-ca-governor`) + an inline summary.

**Step 5 — gated fixes, per race.** Mechanical + guided findings are applied **race by race** — for
each race, a dry-run transaction (BEGIN → apply → SELECT diff → ROLLBACK), show the diff, **commit
only on explicit OK**. Reviewing per-race keeps batches comprehensible instead of one giant
undifferentiated write. Decision-required findings are listed per race and never auto-touched. No
unattended writes, ever.

## Findings model

Each finding: `{ target (quote id / topic / race), level (quote|topic|portfolio), principle,
severity, what's wrong, suggested fix, fix-class }`.

- **Severity:** `high` (misrepresentation, accuracy error, blindness leak) · `medium` (note quality,
  wrong source tier, coverage skew) · `low` (style/ellipsis).
- **Fix-class:**
  - **mechanical** — audit generates exact SQL, applies on OK (null/§-ref/>2-sentence note,
    partisan tell in blind, trailing ellipsis, `deidentified_text` null).
  - **guided** — audit drafts a fix needing source/text verification before apply (rewrite a
    paraphrased blind text, restore a verbatim sentence, rewrite a note).
  - **decision-required** — audit only flags; the human decides (off-question → re-home/absent,
    coupling in-tension, ≥2-rankability, non-verbatim summary with no verbatim source, portfolio skew).

## Data access

Reuse the `ev-accounts/backend` connection via `node --import tsx` (same pattern as `publish-quotes`
scripts and `research-stances`). Read-only for the audit passes; writes only in the gated fix step.

## `publish-quotes` change

Add a final workflow step: after the insert (`--commit`), auto-run `audit-quotes` on the inserted
ids (a scoped, single-race run) and show findings before directing the user to select the live quote
in `/admin/readrank-quotes`.

## Scale considerations

- Mechanical pre-pass is O(rows) SQL — runs across all races cheaply, first.
- Judgment fan-out is the cost driver → the scope-confirmation gate + concurrency cap bound it.
- Report is the primary deliverable; fixes are opt-in and reviewed per race.

## Out of scope (later)

- Standing **edit-history / corrections-log** tables (tracked as net-new in the principles doc);
  the audit records fixes it applies, but the durable append-only history is separate work.
- Auto-remediation of decision-required findings (always human).
