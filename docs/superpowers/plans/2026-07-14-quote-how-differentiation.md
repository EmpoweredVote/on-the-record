# Quote "shows the HOW" differentiation criterion — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a "shows the HOW, not just an agreeable goal" selection preference to the quote-curation principles and both quote skills, flagged in the audit as a medium / decision-required judgment check.

**Architecture:** Documentation-only change across two repos. The canonical principles doc (`essentials/`) is the source of truth and is edited first; the `on-the-record` audit-quotes and publish-quotes skills are brought into line with it. No `scripts/`, schema, or code changes — the criterion is judgment-only and the finding schema already accepts arbitrary `check_id`s.

**Tech Stack:** Markdown. Verification via `grep`/`Read` for presence + consistency, and one optional live judgment-agent smoke test.

**Spec:** `docs/superpowers/specs/2026-07-14-quote-how-differentiation-design.md`

**Note on scope refinement from the spec:** the spec named `publish-quotes/EDITORIAL.md` as the home for the selection bullet. During planning, the more accurate home turned out to be `publish-quotes/SKILL.md`, whose Workflow already describes the responsiveness gate (the `Pick the topic_key` step). The bullet goes there instead, next to the gate it extends. EDITORIAL.md is unchanged.

**Repo paths:**
- `essentials/` repo root: `/Users/chrisandrews/Documents/GitHub/essentials`
- `on-the-record/` repo root: `/Users/chrisandrews/Documents/GitHub/on-the-record` (current branch `docs/quote-how-differentiation`)

---

## Task 1: Canonical principles — add §4.6 + cross-references (`essentials/` repo)

**Files:**
- Modify: `/Users/chrisandrews/Documents/GitHub/essentials/docs/QUOTE-CURATION-PRINCIPLES.md`

This is the source of truth and MUST be edited before the skills (per the doc's own §10 and its "this document states the intent and the skill should be updated to match" rule).

- [ ] **Step 1: Insert the new §4.6 subsection**

In `QUOTE-CURATION-PRINCIPLES.md`, find the end of §4.5 — the ellipsis-density example paragraph that ends:

```
  they call it sprawl, I call it the California dream" (1 mark) — two contiguous sentences; "the unions, labor unions"
  is a silent self-correction.*
```

It is immediately followed by a blank line, a `---` horizontal rule, and `## 5. Source principles`.
Insert the following block **between that example paragraph and the `---`** (i.e. as the final subsection of §4, before the rule):

```markdown

### 4.6 Differentiation — prefer the HOW

Responsiveness (§7.1) only guarantees a quote *answers the question*. Among quotes that clear that
gate, prefer the one that shows **how** the candidate would pursue the goal — the mechanism,
approach, or means — not merely that the goal is desirable. Showing the differences in the *how* is
a core purpose of Read & Rank; a quote everyone would nod at manufactures no legible contrast.

This is a **preference, not a gate.** It never disqualifies a quote on its own and never overrides
responsiveness. It flags one narrow case, and only when **both** conditions hold:

1. **Non-differentiating** — no candidate in the race would plausibly *disagree* with the goal the
   quote states ("who wouldn't want safe, beautiful streets?").
2. **Mechanism-free** — the quote names no approach, means, or *how* the goal would be pursued.

A **contested or directional** goal is fine even without a stated mechanism — a directional stance
("prioritize enforcement over services for street camping") is contestable and creates legible
contrast, so it is rankable as-is. Both conditions are required; either one alone passes.

**Guardrail against editorializing.** This is not a "substance police" test — do not use it to
reject positions you find thin, and do not demand a policy white-paper. It triggers only on the
agreeable-*and*-mechanism-free intersection. When a candidate has no HOW-bearing on-question quote,
they may simply be **absent** from the topic (§3) — do not manufacture depth.

*Worked example (real).* "My dream … is that we can make it safer and easier for people to move
around outside of their cars … a pleasant and beautiful experience to get around this city." This
is on-question (it favors non-car mobility) but states a goal no one contests, with no mechanism —
flag it, and prefer a quote that says *how* (dedicated bus lanes, road-diet tradeoffs, parking
policy).
```

- [ ] **Step 2: Add the §7.1 cross-reference**

In §7.1, find the paragraph ending:

```
Comparability is
the precondition for a valid ranking; responsiveness guarantees it.** Distinctiveness only makes a
contrast *legible* — among non-comparable answers it manufactures a *false* one.
```

Immediately after that paragraph (before the blank line preceding `*When a candidate's most distinctive quote is off-question:*`), add this line:

```markdown

**After the gate:** among quotes that clear responsiveness, prefer the one that shows the HOW, not
just an agreeable goal no one would contest (§4.6).
```

- [ ] **Step 3: Add the §8 cross-reference**

In §8, find the first bullet:

```
- **Responsiveness precedes parity (§7.1).** Only on-question quotes are eligible; comparability is
  the precondition for a fair comparison. Never manufacture a head-to-head from quotes answering
  different questions, and never reach for an off-question quote to fill a topic.
```

Append this sentence to the end of that bullet (same bullet, new sentence):

```
 Among the eligible on-question quotes, prefer the one that shows the HOW (§4.6) — but that is a
  selection preference, never a lever to engineer outcome balance.
```

- [ ] **Step 4: Verify presence and internal references**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/essentials
grep -n "4.6 Differentiation\|shows the HOW\|Non-differentiating\|Mechanism-free\|After the gate" docs/QUOTE-CURATION-PRINCIPLES.md
```
Expected: matches for the §4.6 heading, both bullet labels, the §7.1 "After the gate" line, and the §8 mention — 5+ lines. Confirm §4.6 appears **before** `## 5. Source principles` (the new subsection is inside §4):
```bash
grep -n "^### 4.6\|^## 5. Source" docs/QUOTE-CURATION-PRINCIPLES.md
```
Expected: the `### 4.6` line number is smaller than the `## 5. Source` line number.

- [ ] **Step 5: Commit (in the `essentials` repo)**

```bash
cd /Users/chrisandrews/Documents/GitHub/essentials
git checkout -b docs/quote-how-differentiation
git add docs/QUOTE-CURATION-PRINCIPLES.md
git commit -m "docs: add §4.6 'prefer the HOW' differentiation criterion

A selection preference (not a gate) downstream of responsiveness: among
on-question quotes, prefer the one that shows the mechanism/approach. Flags
only the agreeable-and-mechanism-free case. Surfaced by the LA Mayor audit.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 2: Audit skill — new judgment check in `CHECKS.md`

**Files:**
- Modify: `/Users/chrisandrews/Documents/GitHub/on-the-record/.claude/skills/audit-quotes/CHECKS.md`
- Modify: `/Users/chrisandrews/Documents/GitHub/on-the-record/.claude/skills/audit-quotes/SKILL.md`

- [ ] **Step 1: Add the row to the §3 judgment-checks table**

In `CHECKS.md`, find the last row of the §3 table (the `coupling-in-tension` row):

```
| `coupling-in-tension` | The quote pulls against the direction of the candidate's synthesized Compass `value` for this topic (as opposed to reinforcing it or elaborating on a different sub-dimension). This doesn't mean the quote is wrong — it means the tension needs resolving before the quote is surfaced next to the value. | medium | decision-required |
```

Add this new row immediately below it:

```
| `non-differentiating-goal` | The quote clears responsiveness but states only an **agreeable goal no candidate in the race would contest** ("who wouldn't want safe streets?") **and names no mechanism/approach/means** — the HOW. Both conditions required: a contested/directional goal without a mechanism is fine and does not trip this. A preference, not a gate. | medium | decision-required |
```

- [ ] **Step 2: Add the rule to the §4 prompt template "rules (summarized)" block**

In `CHECKS.md` §4, find the last rule bullet in the summarized-rules block, which begins:

```
- **Coupling to the Compass value.** Among quotes that already pass the responsiveness
  gate, a quote's relationship to the candidate's synthesized Compass `value` for this
```

That bullet ends with:

```
  judging reinforcing vs. in-tension.
```

Immediately after that bullet (before the closing ` ``` ` of the fenced prompt block and the `## Your task` heading), add this bullet:

```
- **Prefer the HOW.** Among quotes that pass the responsiveness gate, prefer the one that
  shows *how* the candidate would pursue the goal — the mechanism, approach, or means — not
  merely that the goal is desirable. Flag a quote **only** when BOTH hold: (1) it is
  *non-differentiating* — no candidate in this race would plausibly disagree with the goal
  ("who wouldn't want safe, beautiful streets?"), and (2) it is *mechanism-free* — it names no
  approach or means. A contested/directional goal without a mechanism is fine (it is still
  rankable contrast). This is a preference, never a gate; do not use it to reject positions you
  find thin.
```

- [ ] **Step 3: Add the check to the §4 "Your task" list**

Still inside the fenced prompt block, find the `## Your task` list. Its last item is:

```
- `coupling-in-tension` — quote pulls against the candidate's Compass value (severity
  medium, decision-required)
```

Add immediately below it:

```
- `non-differentiating-goal` — on-question quote states an agreeable goal no one would
  contest AND names no mechanism/HOW (severity medium, decision-required)
```

- [ ] **Step 4: Update the check-count reference in the §4 output constraints**

Still in `CHECKS.md` §4, find (line ~186):

```
- Do not rewrite `quote_text` or `deidentified_text` yourself, and do not invent findings
  outside the seven check ids above.
```

Change `seven` to `eight`:

```
- Do not rewrite `quote_text` or `deidentified_text` yourself, and do not invent findings
  outside the eight check ids above.
```

- [ ] **Step 5: Update the check-count reference in SKILL.md**

In `audit-quotes/SKILL.md`, find (line ~20):

```
      mechanical checks, the seven judgment checks, the judgment-agent prompt template, and the
```

Change `seven` to `eight`:

```
      mechanical checks, the eight judgment checks, the judgment-agent prompt template, and the
```

- [ ] **Step 6: Verify presence and counts**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record/.claude/skills/audit-quotes
grep -cn "non-differentiating-goal" CHECKS.md          # expect 3 (table row, task list, rules bullet references it by concept — count the literal id)
grep -n "non-differentiating-goal" CHECKS.md
grep -rn "seven check ids\|seven judgment" CHECKS.md SKILL.md   # expect NO matches
grep -rn "eight check ids\|eight judgment" CHECKS.md SKILL.md   # expect 2 matches
```
Expected: `non-differentiating-goal` appears literally in the §3 table row and the §4 task list (2 literal id occurrences; the rules bullet describes it by concept). No remaining "seven" count references; two "eight" references.

- [ ] **Step 7: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add .claude/skills/audit-quotes/CHECKS.md .claude/skills/audit-quotes/SKILL.md
git commit -m "feat(audit-quotes): add non-differentiating-goal judgment check

Flags on-question quotes that state an agreeable, mechanism-free goal.
Medium / decision-required; judgment-only (no mechanical detection).
Implements essentials §4.6.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 3: Publish skill — extend the responsiveness-gate step in `SKILL.md`

**Files:**
- Modify: `/Users/chrisandrews/Documents/GitHub/on-the-record/.claude/skills/publish-quotes/SKILL.md`

- [ ] **Step 1: Append the HOW preference to the `Pick the topic_key` step**

In `publish-quotes/SKILL.md`, find the end of the `Pick the topic_key` workflow step, which currently ends:

```
      A candidate who only spoke in record/attacks (no forward position) is **absent** — don't
      launder record into a pseudo-position. See `essentials/docs/QUOTE-CURATION-PRINCIPLES.md` §7.1.
```

Add this sentence to the end of that step, after the existing `§7.1` reference (same bullet):

```
      Among on-question candidates, prefer the quote that shows *how* the candidate would act, not
      just an agreeable goal no one would contest (`QUOTE-CURATION-PRINCIPLES.md` §4.6).
```

- [ ] **Step 2: Verify**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
grep -n "shows \*how\*\|§4.6" .claude/skills/publish-quotes/SKILL.md
```
Expected: one line matching the new sentence with the §4.6 reference.

- [ ] **Step 3: Commit**

```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record
git add .claude/skills/publish-quotes/SKILL.md
git commit -m "docs(publish-quotes): prefer the HOW when selecting among on-question quotes

Extends the responsiveness-gate step with the §4.6 differentiation preference.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task 4: Cross-document consistency check + optional live smoke test

**Files:** none modified (verification only).

- [ ] **Step 1: Confirm all three documents agree**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub
grep -rl "§4.6\|4.6 Differentiation\|non-differentiating-goal\|shows the HOW\|shows \*how\*" \
  essentials/docs/QUOTE-CURATION-PRINCIPLES.md \
  on-the-record/.claude/skills/audit-quotes/CHECKS.md \
  on-the-record/.claude/skills/publish-quotes/SKILL.md
```
Expected: all three file paths listed — each references the criterion.

- [ ] **Step 2 (optional but recommended): Live judgment-agent smoke test**

Verify the check actually fires on (A) and not on (B). Build a 2-quote bundle by hand and run one judgment subagent using the updated `CHECKS.md` §4 prompt (see the audit-quotes SKILL.md judgment fan-out step). Craft:

- Quote A — on-question, agreeable goal, no mechanism (should flag `non-differentiating-goal`):
  > "Every family deserves a safe, clean neighborhood to call home."  (topic: `city-sanitation`)
- Quote B — on-question, contested/directional, no mechanism (should NOT flag):
  > "We should prioritize enforcement over services for people camping in public spaces."  (topic: `homelessness`)

Expected: the returned JSON array contains exactly one `non-differentiating-goal` finding, whose `quote_id` is Quote A's; Quote B produces no `non-differentiating-goal` finding.

If the agent flags B (or misses A), the §4 rule wording in `CHECKS.md` is ambiguous — tighten the "both conditions required" language in Task 2 Step 2 and re-run.

- [ ] **Step 3: Final review of the two commits**

Run:
```bash
cd /Users/chrisandrews/Documents/GitHub/on-the-record && git log --oneline -3
cd /Users/chrisandrews/Documents/GitHub/essentials && git log --oneline -1
```
Expected: `on-the-record` shows the audit-quotes and publish-quotes commits; `essentials` shows the principles commit on its `docs/quote-how-differentiation` branch.

---

## Self-review notes

- **Spec coverage:** §4.6 principles subsection (Task 1), §7.1 + §8 cross-refs (Task 1 Steps 2–3), audit §3 table row + §4 prompt wiring + count updates (Task 2), publish-quotes selection bullet (Task 3, relocated from EDITORIAL.md to SKILL.md — noted at top), consistency + behavior validation (Task 4). All spec success-criteria mapped.
- **No scripts/schema changes** — consistent with the spec's non-goals; the finding schema already accepts arbitrary `check_id`s.
- **Naming consistency:** the id `non-differentiating-goal`, the phrase "shows the HOW", and the two conditions ("non-differentiating" / "mechanism-free") are used identically across all tasks and match the spec.
- **Two-repo commit reality:** Task 1 commits in `essentials/`; Tasks 2–3 commit in `on-the-record/`. Each repo gets its own `docs/quote-how-differentiation` branch.
