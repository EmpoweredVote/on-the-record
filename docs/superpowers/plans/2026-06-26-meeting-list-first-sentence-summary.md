# Meeting-list First-Sentence Summary Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show the full first sentence of each meeting's executive summary on the homepage list, never truncated mid-word.

**Architecture:** ev-accounts computes the list `summaryPreview` as the first sentence of `executive_summary` (read-time transform, no DB change); the `web/` app stops CSS-clamping the preview so the full sentence renders.

**Tech Stack:** Plain CSS in `web/app/globals.css` (static-export Next.js 16). The ev-accounts side is in a separate repo and is captured here as a contract.

**Spec:** `docs/superpowers/specs/2026-06-26-meeting-list-first-sentence-summary-design.md`

---

## File structure

- **Modify** `web/app/globals.css` — remove the 2-line clamp on `.meetingPreview`.
- **(Separate repo)** ev-accounts list serializer — change `summaryPreview` derivation. Captured as a contract in Task 2; not executed in this repo.

This repo's executable change is a single CSS rule. There is no JS test runner path for CSS, so the web task is verified in the browser preview (matching how the meeting-list redesign was verified). The ev-accounts extractor is unit-tested in its own repo.

---

## Task 1: Remove the preview line-clamp (web/)

**Files:**
- Modify: `web/app/globals.css` (the `.meetingPreview` rule)

- [ ] **Step 1: Confirm the current rule**

Run: `grep -n -A8 "^.meetingPreview {" web/app/globals.css`
Expected: the rule currently reads
```css
.meetingPreview {
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.5;
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
```

- [ ] **Step 2: Edit the rule to remove the clamp**

Replace that entire rule with:
```css
.meetingPreview {
  color: var(--muted);
  font-size: 0.85rem;
  line-height: 1.5;
}
```
(Removes `display: -webkit-box`, `-webkit-line-clamp: 2`, `-webkit-box-orient: vertical`, and `overflow: hidden`. Leaves color/size/line-height unchanged.)

- [ ] **Step 3: Lint and build**

Run (from `web/`): `npm run lint && npm run build`
Expected: lint shows only the pre-existing `SiteHeader.tsx` `react-hooks/set-state-in-effect` error (no new errors); build succeeds. If the build fails only on an external DB/network error (not a compile error), `npx tsc --noEmit` is an acceptable substitute — note it.

- [ ] **Step 4: Verify in the browser**

Start the dev server (`preview_start` with the `web` launch config) and load `/`. Confirm:
  - Each meeting row's summary line renders without a mid-word `…` cut.
  - With the current (still char-truncated) API data, longer previews now wrap to as many lines as needed instead of being clamped to two — this is the expected interim look until ev-accounts ships Task 2.
  - Check light mode, dark mode, and the 375px mobile width: the preview text wraps cleanly with no horizontal overflow (`document.body.scrollWidth === window.innerWidth`).

Capture a screenshot (desktop) to share as proof.

- [ ] **Step 5: Commit**

```bash
git add web/app/globals.css
git commit -m "style(web): show full meeting summary preview (drop 2-line clamp)"
```

---

## Task 2: First-sentence extractor (ev-accounts — separate repo, contract)

> This task is implemented in the ev-accounts repository, not here. It is specified
> as a contract so it can be executed in that repo's session and language. Do NOT
> create files for it in this repo.

**Where:** the ev-accounts list serializer for `GET /api/meetings`, at the point
where it currently derives `summaryPreview` from the meeting's `summary`
(`executive_summary`) by character truncation.

**Behavior to implement:** replace the character truncation with first-sentence
extraction:

1. Read `executive_summary` (the existing source string).
2. Trim leading/trailing whitespace.
3. Return everything up to and including the first sentence terminator — a `.`,
   `!`, or `?` that is immediately followed by whitespace or end-of-string.
4. No length cap — return the entire first sentence however long.
5. If `executive_summary` is empty/missing → return empty/null.
6. If no terminator is found → return the whole trimmed string.

A reference regex for "first sentence" (adapt to the repo's language): match from
the start through the first `[.!?]` that is followed by whitespace or end of
string, e.g. `^.*?[.!?](?=\s|$)`. Accepted imperfection: this can mis-split on
abbreviations/decimals inside the first sentence ("Dr.", "U.S.", "No. 5",
"$1.5M"); handling those is out of scope for the MVP.

- [ ] **Step 1: Write failing unit tests** for the extractor in the ev-accounts
  repo (use that repo's test framework). Cover these cases:

  | Input `executive_summary` | Expected `summaryPreview` |
  |---|---|
  | `"The council approved the rezoning. Then they recessed."` | `"The council approved the rezoning."` |
  | `"The League of Women Voters hosted two candidate forums on June 9, 2026—one for Indiana House District 61 and one for Monroe County Commissioner."` | the entire string (one long sentence, returned whole) |
  | `""` | `""` (or null, per the repo's convention) |
  | `"   Leading and trailing spaces.  Second."` | `"Leading and trailing spaces."` (trimmed) |
  | `"A fragment with no terminator"` | `"A fragment with no terminator"` |
  | `"What happened next? A lot."` | `"What happened next?"` |

- [ ] **Step 2: Run the tests** — confirm they fail (extractor not yet wired in).

- [ ] **Step 3: Implement** the extraction at the `summaryPreview` derivation site.

- [ ] **Step 4: Run the tests** — confirm they pass.

- [ ] **Step 5: Verify the field shape is unchanged** — the response still has a
  `summaryPreview` string; no other list fields change. The detail endpoint
  `GET /api/meetings/{id}/summary` and the stored `summary` JSON are NOT touched.

- [ ] **Step 6: Commit** in the ev-accounts repo.

---

## Deploy ordering (non-blocking)

Task 1 (web) and Task 2 (ev-accounts) are independent and tolerate either deploy
order — see the spec's "Deploy ordering" section. No coordination required.

---

## Self-review notes

- **Spec coverage:** web clamp removal (Task 1); ev-accounts first-sentence
  extraction with no cap, edge cases, and the abbreviation caveat (Task 2);
  detail page / stored summary untouched (Task 2 Step 5); testing in both repos
  (Task 1 Step 4 browser; Task 2 Step 1 unit tests). No DB migration is needed,
  consistent with the spec.
- **Placeholder scan:** none — the web edit shows the full before/after CSS; the
  ev-accounts contract gives a concrete behavior list, a reference regex, and a
  table of input/expected test cases.
- **Consistency:** the field is referred to as `summaryPreview` (API) /
  `summary_preview` (web mapping) throughout, matching `web/lib/queries.ts`.
