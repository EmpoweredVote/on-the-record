# Calibrating a heuristic gate — runbook

How to produce a corpus figure for a non-LLM gate (a snap rule, a classifier
prefilter, a mis-merge detector) that is worth trusting.

Worked example throughout: the trailing self-introduction rule,
`src/word_assign.py::_snap_trailing_intro`, spec at
`docs/superpowers/specs/2026-09-09-trailing-intro-bleed-design.md`.

## The rule that matters

**A second party has to make the measurement. Self-review does not catch this
class of error.**

Two sessions calibrated one rule on 2026-09-09 and produced four wrong figures
between them. Every one was caught by the *other* session measuring
independently. Neither caught its own, at any point, despite both re-running.

The reason is that a re-run replays the mental model that produced the error, so
it confirms rather than tests. "Instrument the shipped function" (below) is
necessary but **not sufficient** — each session believed it had done exactly
that.

So: before a calibrated threshold goes into code, have someone else re-derive
the funnel from the shipped function. Not review the script. Re-derive the
number.

## Instrument the shipped function; never restate its gates

The failure is always the same shape: a scan that *re-implements* the rule in
order to measure it, and drifts from it.

Import the real helpers and change only the one gate under test:

```python
from src.word_assign import _intro_cue_index, _ends_sentence, MAX_INTRO_PREAMBLE

# The shipped walk-back, verbatim — not a paraphrase of it.
def split_index(a):
    cue = _intro_cue_index(a.words)
    if cue is None:
        return None, None
    split = cue
    for k in range(cue, max(0, cue - MAX_INTRO_PREAMBLE) - 1, -1):
        if k > 0 and _ends_sentence(a.words[k - 1].word):
            split = k
            break
    return cue, split
```

If a counterfactual needs a different gate, copy the shipped body and edit the
single line. Do not retype the cue detection, the normaliser or the loop bounds.

### Isolation diff: the technique that has never been wrong here

To measure what a rule actually does to the corpus, run the shipped pass twice
with the rule switched off and on, and diff:

```python
from src import word_assign
real = word_assign._snap_trailing_intro

for meeting in corpus:
    word_assign._snap_trailing_intro = lambda a, b: False
    off = snap_and_capture(meeting)
    word_assign._snap_trailing_intro = real
    on = snap_and_capture(meeting)
    # any difference is attributable to this rule alone
```

Every difference is attributable to the rule alone, so catch-up from an earlier
pass cannot be mistaken for it. This is how the trailing-intro rule was proven
to move exactly 6 source turns and 6 destinations across 172 meetings. Every
figure produced this way survived scrutiny; every wrong figure in the table
below came from a re-implemented scan. Assert idempotence per meeting in the
same loop — the boundary backfill re-snaps in place and must stay re-runnable.

### Bisect a threshold; never infer it from two points

A two-point probe cannot locate a step. Sweep the whole range.

## The four failures, and what each teaches

| # | Wrong figure | The drift | Truth |
|---|---|---|---|
| 1 | 68 relaxed-gate hits; 141 non-leading cues | Re-implemented cue detection as `" ".join(toks[k:k+3]).startswith("my name is")`, which also matches `"my name island"` and `"my name isn't"`. The shipped `_intro_cue_index` compares tuples exactly and rejects both. | **63** and **131** |
| 2 | Nearest false positive 180 words away | Ablation checked only the word immediately before the cue; the shipped gate scans back `MAX_INTRO_PREAMBLE` = 4. Understated the margin in the unsafe direction. | **21** words, by that session's own re-measurement |
| 3 | "The cap hazard is live" | Reconstructed a segment's tail by retyping it from a funnel print, truncated at the cue. The real tail ran on to `"...running for clerk. All right. Thank you all so much for"`, which reversed the conclusion. | Hazard not live |
| 4 | Threshold `cap >= 8` | Both sessions probed only caps 4 and 8, then each stated a threshold. One had reproduced the other's instance and inferred its threshold. | **cap >= 6** |

Two corollaries worth stating separately:

- **Retyping data from a print is re-implementing it.** Load the words.
- **A number that arrives with a measurement attached still needs the
  measurement that locates it.** Reproducing someone's instance is not
  establishing their threshold.

## Checklist before a calibrated figure goes into code

1. Does the scan **import** the shipped helpers, or restate them? Restating
   fails the review.
2. For a counterfactual, is exactly **one** line different from the shipped
   body?
3. Is every threshold **bisected** across its range, not inferred from two
   points?
4. Is corpus data **loaded**, never retyped from earlier output?
5. Has a **second party re-derived** the funnel — not read the script?
6. For a rule that mutates a corpus artifact: is there an **isolation diff**
   attributing changes to this rule alone, and an **idempotence** assertion?
7. Are figures produced by re-implementation labelled as such until step 5
   clears them?

## When a threshold does not need a test

A constant with no failure mode behind it is a change-detector, not a test.
`MAX_INTRO_PREAMBLE` = 4 was deliberately left unpinned, on two measurements.

First, a monotonicity property of the walk-back itself: widening the cap can
only move a split *earlier*, because the loop breaks on the **first** sentence
boundary scanning back from the cue, so a wider window is reached only when the
narrower one found nothing. An earlier split means a longer tail. Any downstream
gate that rejects a tail containing a completed sentence therefore gets
*stricter* as the cap widens — the cap cannot be loosened into such a gate.
(`_snap_trailing_intro` as shipped has no such gate; this matters for rules that
add one.)

Second, a widening large enough to matter already fails a real-data test:
at cap ≥ 10 the split on `bloomington-city-council-2026-06-10` segment 454 jumps
from 12 to 2 and `test_trailing_self_intro_moves_off_the_chair_bloomington_june`
breaks. Caps 5–9 leave it unchanged.

An `assert MAX_INTRO_PREAMBLE == 4` would have caught neither, because there is
no failure mode behind it. Record the measurement in the docstring instead, and
prefer a **behavioural test on real corpus shapes** over pinning a constant.
