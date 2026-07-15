# Quote curation: the "shows the HOW" differentiation criterion

**Status:** design · **Date:** 2026-07-14 · **Owner:** Empowered Vote (Chris Andrews)

## Problem

The LA Mayor quote audit (2026-07-14) surfaced a gap. A quote can pass the responsiveness
gate (§7.1 of the curation principles) — genuinely answering the topic's framed question —
and still carry no useful ranking signal because it states only an **agreeable goal that no
candidate would contest, with no mechanism or approach for achieving it**.

The triggering example, Nithya Raman on transportation-priorities:

> "My dream for this district and for the city as a whole is that we can make it safer and
> easier for people to be able to move around outside of their cars: have it be not just
> possible, but a pleasant and beautiful experience to get around this city."

It is on-question (it states a non-car-priority direction) but it is a goal almost anyone
would agree with, and it says nothing about *how*. Showing the **differences in the HOW** is a
core purpose of Read & Rank — a quote everyone nods at manufactures no legible contrast between
candidates.

The current principles make **responsiveness** the sole hard gate and deliberately hold
distinctiveness/depth as *secondary* (§7.1, §8), to keep curators from editorializing about
which positions are "substantive enough." So this new criterion must be added **without**
turning into a second hard gate or a license to editorialize.

## The criterion

**Name:** `non-differentiating-goal`
**Principle phrase:** *"shows the HOW, not just an agreeable goal."*

It applies **only to quotes that already pass the responsiveness gate** (§7.1). Among those, it
trips **only when both conditions hold**:

1. **Non-differentiating** — no candidate in the race would plausibly *disagree* with the goal
   the quote states ("who wouldn't want safe, beautiful streets?").
2. **Mechanism-free** — the quote names no approach, means, or *how* the goal would be pursued.

A **contested or directional** goal does **not** trip it, even without a stated mechanism — a
directional stance still creates legible contrast and is rankable (e.g. "we should prioritize
enforcement over services for street camping" states a real, contestable direction). Both
conditions are required; either one alone is fine.

**Strength: a selection preference, not a gate.** Among on-question quotes, prefer the one that
shows the most HOW. A goal-only-and-agreeable quote is a last resort — surfaced for a human to
decide, **never auto-disqualified**. This preserves responsiveness as the only hard gate and
keeps the anti-editorializing stance intact.

## Where it lives

Three documents, each in its natural home.

### 1. `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` (canonical source of truth)

- **New subsection §4.6 "Differentiation — prefer the HOW"** under Selection principles.
  Content:
  - The criterion and the two-condition test.
  - Explicit statement that it is a **preference downstream of the responsiveness gate**, not a
    second gate, and that a contested goal without a mechanism still passes.
  - The anti-editorializing guardrail: this is not a "substance police" check; it triggers only
    on the narrow agreeable-*and*-mechanism-free case.
  - A worked micro-example using the transportation "pleasant and beautiful experience" quote.
- **Cross-references (one line each):**
  - §7.1 (responsiveness gate) → "then, among on-question quotes, prefer the HOW (§4.6)."
  - §8 (portfolio balance) → tie to the existing "distinctiveness only makes contrast legible"
    language.

Because this document is canonical and the skills implement it, it is edited first and the
skills are brought into line with it.

### 2. `.claude/skills/audit-quotes/CHECKS.md`

- **New row in the §3 judgment-checks table** (see wiring below).
- **§4 agent-prompt template** updated in two places:
  - the "rules (summarized)" block gains a short bullet stating the HOW preference and the
    two-condition test;
  - the "Your task" check list and the output check-id list gain `non-differentiating-goal`.
- The check is **judgment-only** (no mechanical detection) — it requires reading the quote
  against the race's field of positions.

### 3. `.claude/skills/publish-quotes/EDITORIAL.md`

- A short bullet under the selection/faithfulness guidance: when choosing among on-question
  candidate quotes, prefer the one that states *how*, and watch for the
  agreeable-goal-no-mechanism trap. Points to the canonical §4.6.

## Audit check wiring

| field | value |
|---|---|
| `check_id` | `non-differentiating-goal` |
| `level` | `quote` |
| `principle` | `shows the HOW, not just an agreeable goal` |
| `severity` | `medium` |
| `fix_class` | `decision-required` |
| what | on-question quote whose goal no candidate in the race would contest *and* that names no mechanism/approach/means |
| suggested_fix | prefer a HOW-bearing on-question quote from the same candidate; if none exists, the candidate may be genuinely absent from the topic — do not manufacture depth |

Because it is `decision-required`, the audit **never auto-applies** it — consistent with the
skill's non-negotiable that decision-required findings are listed for the user, never acted on.
No change to `scripts/` is required: the mechanical checks and the finding schema
(`scripts/models.py` — `check_id`, `level`, `severity`, `fix_class` are free-form strings) already
accommodate a new judgment check id.

## Non-goals / YAGNI

- **No mechanical detection.** "Would any candidate disagree?" and "is there a mechanism?" are
  judgment calls; no regex or script check is added.
- **Not a hard gate.** It never disqualifies a quote on its own and never overrides responsiveness.
- **No schema or `scripts/` changes.** The finding types already carry arbitrary `check_id`s.
- **No retroactive re-audit** of already-published races as part of this change (the LA Mayor
  quote that surfaced it has already been handled). Future audits pick up the new check.

## Success criteria

- The canonical principles doc states the criterion, the two-condition test, the
  preference-not-gate strength, and the anti-editorializing guardrail, with a worked example.
- The audit judgment agent, following `CHECKS.md`, emits a `non-differentiating-goal` finding for
  an on-question quote that is agreeable-and-mechanism-free, and does **not** emit it for a
  contested directional quote that lacks a mechanism.
- `publish-quotes` editorial guidance points curators at the HOW preference during selection.
- All three documents agree; the canonical doc is the reference where they overlap.
