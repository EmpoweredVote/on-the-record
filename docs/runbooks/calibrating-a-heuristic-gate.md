# Calibrating a heuristic gate — runbook

How to produce a corpus figure for a non-LLM gate (a snap rule, a classifier
prefilter, a mis-merge detector) that is worth trusting.

Worked example throughout: the trailing self-introduction rule,
`src/word_assign.py::_snap_trailing_intro`, spec at
`docs/superpowers/specs/2026-09-09-trailing-intro-bleed-design.md`.

Gate tooling lives in `bench/` — `calibrate_gate.py`, `diagnose_merge.py`,
`forum_gate.py`. **Commit a funnel instrument there.** A scratch workspace is
the wrong home: `.superpowers/` is gitignored (`.gitignore:50`, zero tracked
files) and the SDD workflow deletes it on completion, so an instrument left
there cannot be re-run by whoever has to re-derive your number, and a
reviewer cannot check what you measured after the fact.

## The rule that matters

**The catch has to come from an agent that did not build the mental model that
produced the error.** Another session, or a fresh-context reviewer you dispatch
yourself. Re-running your own script never works.

Two sessions calibrated one rule on 2026-09-09 and produced four wrong figures
between them. Not one was found by its author re-running the measurement. But
the mechanism that did find each differed, and the difference is the useful
part:

| What caught it | Instances |
| --- | --- |
| the other session measuring independently | 1, 4a, 4b |
| a fresh-context review agent dispatched inside the same session | 2 |
| the author, after an outside challenge sent them back to the raw data | 3 |

**Identity is not the property that matters; freshness of the model is.** That
is why a re-run confirms rather than tests — it replays the model that produced
the error. It is also why this is actionable alone: a reviewer dispatched on a
clean context has not built your model, and in instance 2 that is exactly what
found the error, with no second session involved.

"Instrument the shipped function" (below) is necessary but **not sufficient** —
each session believed it had done exactly that.

So: before a calibrated threshold goes into code, have an agent that did not
derive it re-derive it from the shipped function. Not review the script.
Re-derive the number.

This section is itself an instance. It first claimed a *second party* was
required and that neither author ever caught their own — contradicted by
instance 2 in the failures table below, and it would have sent solo work
hunting for a second session it does not need. The other session caught it.

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

### Trace the exits; do not count the ones you predicted

A hand-written funnel reports the stages you thought to print. A tracer reports
every exit, including the ones you did not predict — which is the difference
between a funnel that confirms your model of the rule and one that tests it.

`bench/intro_gate_funnel.py` is the worked example. It runs the real
`snap_segment_boundaries`, hooks the rule with `sys.settrace`, records the
source line of the `return` that fired for each pair, and reads the gate labels
back out of `src/word_assign.py`. It restates nothing, so it cannot drift.

Two things that approach surfaced on the trailing-intro rule which a
hand-written funnel could not:

- **A gate that was really two gates.** What looked like one continuation check
  is two separate returns — pairs rejected because A's sentence closes, then a
  few more because B does not open lowercase. An independent hand-written
  funnel collapsed both into a single drop of 36 and could not see the split.
- **A gate doing no work at all.** One gate rejected **zero** pairs, because an
  earlier gate already caught every candidate. You cannot discover a dead gate
  by printing the counts you expected; the tracer reports it whether you asked
  or not. That, rather than any argument, is the strongest reason the
  `MAX_INTRO_PREAMBLE` test was dropped (see the last section).

Dedupe to unique pairs. The fixpoint loop evaluates the same pair several times,
so raw call counts overstate the population.

Note what the committed instrument replaced: its scratch predecessor mirrored
the shipped gates by hand, and its own header admitted the gate order had been
"copied by READING the shipped source". It happened to produce correct numbers
because it was copied carefully that time. **Correct-by-carefulness is not a
property you can rely on**, and citing it as the worked example would have been
recommending the anti-pattern. It was rewritten to trace instead.

### Bisect a threshold; never infer it from two points

A two-point probe cannot locate a step. Sweep the whole range.

## The four failures, and what each teaches

| # | Wrong figure | The drift | Truth |
|---|---|---|---|
| 1 | 68 relaxed-gate hits; 141 non-leading cues | Re-implemented cue detection as `" ".join(toks[k:k+3]).startswith("my name is")`, which also matches `"my name island"` and `"my name isn't"`. The shipped `_intro_cue_index` compares tuples exactly and rejects both. | **63** and **131** |
| 2 | Nearest false positive 180 words away | Ablation checked only the word immediately before the cue; the shipped gate scans back `MAX_INTRO_PREAMBLE` = 4. Understated the margin in the unsafe direction. | **21** words, by that session's own re-measurement |
| 3 | "The cap hazard is live" | Reconstructed a segment's tail by retyping it from a funnel print, truncated at the cue. The real tail ran on to `"...running for clerk. All right. Thank you all so much for"`, which reversed the conclusion. | Hazard not live |
| 4a | Exposure band "caps 5–9" | Probed caps 4, 5, 8, 9, 10, 20 — a gap straddling the step — and stated a band without bisecting. Also conflated the synthetic shape's firing threshold with a different rule's real-data coverage gap. | step is at **cap 6** |
| 4b | Threshold `cap >= 8` | Reproduced the other session's instance at cap 8, watched it fire, and restated its threshold as established — having made no probe at all. | **cap >= 6** |

4a and 4b are deliberately separate. They look like one shared mistake and are
not: 4a is a gap probe, 4b is accepting a threshold that arrived with a working
demonstration attached. The second is the easier one to make, because a
reproduced instance feels like a measurement.

Two corollaries worth stating separately:

- **Retyping data from a print is re-implementing it.** Load the words.
- **A number that arrives with a measurement attached still needs the
  measurement that locates it.** Reproducing someone's instance confirms the
  instance, not the threshold.

## Checklist before a calibrated figure goes into code

1. Does the scan **import** the shipped helpers, or restate them? Restating
   fails the review.
2. For a counterfactual, is exactly **one** line different from the shipped
   body?
3. Is every threshold **bisected** across its range, not inferred from two
   points?
4. Is corpus data **loaded**, never retyped from earlier output?
5. Has an agent that **did not derive** the figure re-derived it — another
   session, or a fresh-context reviewer — rather than read the script?
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

Third, and decisively, the tracer showed the cap-dependent gate rejecting
**zero** pairs corpus-wide — an earlier gate caught every candidate first. A
constant that never determines an outcome cannot regress, and no test over the
current corpus could fail because of it.

An `assert MAX_INTRO_PREAMBLE == 4` would have caught none of this, because
there is no failure mode behind it. Record the measurement in the docstring
instead, and prefer a **behavioural test on real corpus shapes** over pinning a
constant.

Note the ordering that made this knowable: the first two points are arguments,
the third is a measurement, and only the tracer could produce it.
