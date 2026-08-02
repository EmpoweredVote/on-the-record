# Quote Audit — WI U.S. House District 2, Democratic primary

**Date:** 2026-08-01
**Race:** `796d7e3f-77b1-42d7-8edb-b0e35ca82fc8`
**Scope:** all live quotes in the race (3 quotes, all Douglas Alexander)
**Trigger:** three `readrank_selected=true` rows with `editor_note IS NULL` (`note-missing`, high)

## Outcome

| | |
|---|---|
| Findings at open | 6 (3 high, 3 medium) |
| Fixed and committed | 4 (3 × `note-missing`, 1 × meaning-altering clip) |
| Open, decision-required | 3 (`not-rankable`) + 1 stance question |

## Fixed — `note-missing` (high, guided)

All three quotes were inserted outside the `publish-quotes` workflow, which is why the note
requirement was never enforced. Each quote was checked word-for-word against
`alexanderforhope.com/issues` before its note was written; notes were applied via
`apply_fixes.py` (dry-run, then `--commit`).

- `9b896279-f01c-4c3b-9791-5c8358f06d2c` — taxes. **Verbatim**, two consecutive sentences from
  the site's taxes section.
- `3368e904-fc8f-4d58-87a7-15f39ef3607a` — climate-change. **Verbatim**, the opening two
  sentences of the climate section.
- `ecf92afa-540d-4069-9e61-99e63704f67d` — campaign-finance. **Not verbatim** — see below.

Note that `verify_source.py` did **not** verify any of these: `check_source` returns `None` for
non-video sources (`scripts/verify_source.py:118`), so a written-source quote passes the source
pass without ever being checked. The verification here was manual.

## Open — `not-rankable` (medium, decision-required)

Mark Pocan (`59662930-3fe8-4e3e-8f02-2a341d58619f`), the other candidate on this ballot, has
**zero quotes in the DB** — live or draft. All three topics therefore have one voice, and the
race cannot render a comparison at all ("a topic with one voice is not a comparison").

**Recommendation:** source Pocan on taxes, climate-change and campaign-finance, or drop the race
from the live set until he is covered.

## Fixed — meaning-altering clip on the campaign-finance quote

### What was wrong

Stored text:

> unlimited corporate money cannot be spent to sway elections, even without coordination with a
> particular candidate

Source sentence (Citizens United section):

> The best and only sure way to overturn Citizens United is to craft a Constitutional Amendment
> that places language "in stone" (so that no future SCOTUS can misinterpret it) that unlimited
> corporate money cannot be spent to sway elections, even without coordination with a particular
> candidate.

The excerpt keeps only the trailing subordinate clause. It opens mid-clause and lowercase, carries
no ellipsis marking the cut, and drops the candidate's actual, distinctive position — amending the
Constitution to overturn Citizens United. As stored it reads as a bare assertion rather than a
policy proposal.

### What was done

Re-cut and committed. `quote_text` and `deidentified_text` are now both:

> The best and only sure way to overturn Citizens United is to craft a Constitutional Amendment …
> that unlimited corporate money cannot be spent to sway elections, even without coordination with
> a particular candidate.

The single elision drops `that places language "in stone" (so that no future SCOTUS can
misinterpret it)` — a mechanics aside about drafting, which is exactly the kind of material idea
triage is meant to cut. Removing it also avoids nesting quotation marks inside the quote.

Checked before applying:

- Both spans either side of the elision are contiguous verbatim runs of the source sentence, so
  nothing reads across the edit (validated with `verbatim_runs` from `verify_source.py`).
- 33 words, at the p90 of live quotes (median 17). The untrimmed sentence would have been 46.
- `…` (U+2026) with surrounding spaces matches house style — 59 live quotes use it, 47 use `...`.
- Blind text and revealed text remain identical; there is nothing here to de-identify, and the
  passage carries no party tell within a Democratic primary.

The `editor_note` was rewritten to describe the trim and then tightened to two sentences after the
first version tripped `note-too-long` (the check is a hard two-sentence limit — the "unless heavy
editing must be explained" carve-out lives only in the suggested-fix text, not the rule).

## Open — raised by this audit (decision-required)

### Possible campaign-finance stance mismatch

Recorded Compass stance is **1** — "ban all private money in politics and publicly fund
campaigns". The source passage argues for barring unlimited *corporate* spending via constitutional
amendment, which reads closer to **2** — "strictly limit corporate donations and dark money
groups". Nothing on the page proposes public financing.

**Recommendation:** re-check the stance against his full answer set. Left as-is; the interim
`editor_note` records the tension.

## Noted, not blocking — climate selection is not his distinctive position

The selected two sentences are the section's generic opener. The distinctive material further down
is his pro-nuclear argument ("I am concerned about a bias against small, next-generation nuclear
power plants…") and his gradualist close ("until we get comfortable with nuclear energy, it will be
a rocky road to scale back from fossil fuels any time soon"), the latter of which supports his
mid-scale stance more directly than the scarcity opener does. The current selection is defensible
and was left in place; worth revisiting when Pocan is sourced and the contrast can be tuned.

## Source tier

All three quotes are from the candidate's own campaign website — self-published, with no recording
to check against. Each `editor_note` states this plainly.
