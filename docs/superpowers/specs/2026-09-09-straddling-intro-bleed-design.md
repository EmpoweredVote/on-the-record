# Straddling self-introduction bleed — design

**Date:** 2026-09-09
**Status:** approved, ready to implement
**Area:** `src/word_assign.py` (`snap_segment_boundaries`)
**Builds on:** `2026-09-09-trailing-intro-bleed-design.md` (commit `9380f3f`, `_snap_trailing_intro`)

## The defect

One instance of the trailing self-introduction bleed class survives the rule
shipped on `claude/friendly-golick-b1d996`.

`bloomington-city-council-2026-05-06` segment 793, attributed to
`City Common Council - District 6 Zulich`:

    "three - minutes. My name is Jeremy Hackard,"

Segment 794 (`Jeremy Hackerd`, 373 words) continues the sentence verbatim:
`"and I'm instantly regretting that I brought my notes on my phone."` The
trailing five words belong to Jeremy Hackerd.

**Verified live on 2026-09-09.** The row is published, and it carries
`politician_slug = 'sydney-zulich'`, so the public page attributes the words to
a named sitting councilmember. This is a live misattribution of identity, the
same harm the shipped rule was built to remove, not a cosmetic remainder.

## Why the shipped rule cannot see it

`_snap_trailing_intro` gate 3 requires the cue word itself to start past the
segment's own diarized `end_time` — the signature of `_segment_for_gap_word`
dumping a wholly un-diarized stretch onto the preceding turn. Here:

| | value |
| --- | --- |
| cue word `"My"` starts | 13109.952 |
| segment `end_time` | 13111.282 |
| final word `"Hackard,"` starts | 13111.306 |

The cue lies *inside* the diarized span by 1.33 s. Only the last word is past
it. Gate 3 rejects the segment.

This is a distinct shape, and the distinction is worth naming. The shipped rule
covers an introduction dumped **wholly outside** A's span. This one covers an
introduction that **straddles** the span boundary: it begins inside A's own
diarized turn, but A's words run past the turn's end and carry the next
speaker's opening with them.

## Measured negative result: the obvious loosening fails

Replacing gate 3 with "the segment's *last* word starts past `end_time`" admits
63 segments corpus-wide. The brief's earlier count of 68 was taken before the
shipped rule's own repairs were written to disk: the six segments it moves also
satisfy the relaxed gate, and none remain in the population now (the shipped
gate matches 0 of the 104 pairs on the current corpus). The remaining
difference is the non-empty-destination requirement, which excludes the
segment-416 shape. The large majority of the 63 are speakers correctly
introducing themselves at the start of their own turn, where the whole turn's
word timings spill past the diarized end. Cases it would wreck include:

- `2026-05-15-az-superintendent-public-instruction` seg 0 — 285 words of
  Danielle Lerner's own moderator opening onto Thomas Horne.
- `2026-06-24-cd1-republican-primary-debate` seg 0 — `"My name is Steve
  Goldstein."`, correctly attributed, onto Danielle Lerner.

Segment 793's tail is 5 words and Goldstein's is also 5 words, so **no length
threshold separates them at this gate**. A discriminator is required first.

## Two discriminators, both selective — and why the name-free one wins

### The name comparison (rejected)

Comparing the introduced name against A's and B's `speaker_name` is selective:
of the 63, exactly one has a name that fails to match A but matches B — segment
793 (`"Jeremy Hackard"` vs `Zulich`, vs `Jeremy Hackerd`). The remaining 62
split into 31 matching A, 15 where A and B are the same speaker, and 16
matching neither.

It is nevertheless the wrong instrument, for two reasons.

1. **It forces pipeline/backfill divergence.** `speaker_name` is `None` at
   word-assignment time; names are assigned later, at the identify stage. The
   rule could run only in `backfill_boundary_snap.py:47`, never in
   `src/transcribe.py:144`, `src/vtt_align.py:176` or `bench/modal_app.py:1912`.
   The pipeline and the backfill would stop applying identical corrections.
2. **Name-similarity thresholds are known not to separate.** PR #205 measured
   this directly on this codebase: different people score 0.615–1.000 and
   genuine typos score 0.667–0.941, so no threshold divides them. The
   `matches_neither` bucket shows the same failure here — `'Nick Reinher'` is
   ASR for `Nicholas Reinecker` and `'Mike Beast'` for `Michael Beaster`, and a
   0.85 threshold classified both as non-matches.

### The name-free gate set (adopted)

Signals available at word-assignment time isolate the case just as sharply.
Measured over all 104 segment pairs in the corpus that carry a non-leading
introduction cue and a non-empty destination turn:

| Gate added | Population |
| --- | --- |
| last word starts past `end_time` | 63 |
| `a.speaker_label != b.speaker_label` | 47 |
| continuation across the boundary | 11 |
| a sentence ends within 4 words before the cue | 9 |
| no completed sentence inside the moved tail | 1 |
| moved tail ≤ 10 words | **1** |

The continuation gate is what removes the Goldstein collision: A ends
`"Goldstein."` and B opens `">>"`, so the sentence does not run across the
boundary. Length only failed to separate the two cases *at the relaxed gate
alone*.

> **Correction, 2026-09-09 (post-review).** The rows below the continuation
> gate originally read 6, with a 5-to-100 cap window. Those figures were
> produced by a hand-written ablation script that **re-implemented** the rule,
> and its clean-split gate drifted from the shipped one: it tested only the
> word *immediately* before the cue, where the shipped gate scans back up to
> `MAX_INTRO_PREAMBLE` (4) words. The wider scan admits three more pairs, so
> the real pre-cap population is 9, not 6. The figures now shown were measured
> by instrumenting the shipped `_snap_straddling_intro` itself rather than a
> copy of it — see `.superpowers/sdd/2026-09-09-straddling-intro-bleed/`
> (`verify-funnel.py` and its output `funnel-measured.txt`) for the script and
> the full census. The method changed because the old method was wrong; treat
> any figure in this document that was not re-measured that way with the same
> suspicion.

The corrected census makes the tail cap's margin much narrower than claimed.
The one true positive still has a 5-word tail, but the nearest false positive
has **21**, not 180 —
`2026-03-30-lwv-candidate-forum---county-clerk-and-prosecutor` seg 116, Tree
Martin-Lucas's closing statement, whose tail reads `"Again, my name is Tree -
Martin Lucas, and I'm running for clerk. All right. Thank you all so much
for"`. With a cap of 21 or more the rule would move her own self-identification
onto Tanner Dale Branham. The remaining seven are 147, 180, 192, 256, 409, 428
and 516 words.

| tail cap | 3 | 4 | **5–20** | 21–146 | unbounded |
| --- | --- | --- | --- | --- | --- |
| hits | 0 | 0 | **1** | 2 | 9 |

So "any cap from 5 to 100 gives the same answer" is **false**; the safe window
is 5 to 20. A four-fold margin around a shape as common as "Again, my name is
X" ending a candidate-forum closing statement is not a margin worth relying on
— a shorter instance of the same sentence would fire at
`MAX_INTRO_TAIL_WORDS = 10`.

### The completed-sentence gate

The fix is a semantic gate, not a wider cap. Gate 6 has already asserted that
the tail and B are **one utterance running across the boundary**. A tail that
contains a completed sentence contradicts that premise: it is the speaker's own
finished speech, not a fragment bleeding into the next turn.

    if any(_ends_sentence(w.word) for w in a.words[split:-1]):
        return False

The slice excludes the last word, whose punctuation says nothing about the
tail's internal structure (and which gate 6 has already required not to end a
sentence). Measured on the current corpus this gate alone separates 1 from 9
with no length threshold at all: every one of the eight false positives closes
at least one sentence inside its tail, and the seg-793 true positive
(`"My name is Jeremy Hackard,"`) closes none.

`MAX_INTRO_TAIL_WORDS = 10` is kept as a second line of defence, not removed.
The two gates fail independently — one on coherence, one on magnitude — so a
shape that defeats either still has to defeat the other.

## The rule

New function `_snap_straddling_intro(a, b)` in `src/word_assign.py`, called from
the existing per-pair loop in `snap_segment_boundaries`, immediately after
`_snap_trailing_intro`.

All gates must hold:

1. `a.words` and `b.words` are non-empty. A destination must be a real turn —
   publish drops empty segments, so moving words into one would turn a dropped
   turn into a live attribution. Same principle as the shipped rule's gate 1.
2. A self-introduction cue starts at word index `j > 0` in `a.words`, via the
   existing `_intro_cue_index`. A cue at index 0 opens the speaker's own turn.
3. `a.words[j].start <= a.end_time`. **Cedes to the shipped rule.** The two
   rules are mutually exclusive by construction, so neither can double-fire on
   a segment nor race the other for a different split point.
4. `a.words[-1].start > a.end_time`. A's words spill past its own diarized span,
   which is what put the next speaker's opening on this turn.
5. `a.speaker_label != b.speaker_label`. A name-free identity check.
   `speaker_label` is populated at word-assignment time; `speaker_name` is not.
6. Continuation: `not _ends_sentence(a.words[-1].word)` **and** `b.words[0]`
   begins with a lowercase letter. The sentence runs across the boundary, so
   the tail and B are one utterance by one speaker.
7. Split index `k`: scan back at most `MAX_INTRO_PREAMBLE` (4) words from `j`
   for a sentence-terminal boundary. **Require one to exist** and `k >= 1`.
   Unlike the shipped rule, where the scan-back only refines a split point that
   defaults to the cue, here its absence rejects the segment outright — the
   rule never cuts mid-sentence. It still takes a greeting along when one is
   present (`"...minutes. Hi, my name is X"` splits before `"Hi,"`).
8. No completed sentence inside the moved tail:
   `not any(_ends_sentence(w.word) for w in a.words[k:-1])`. Gate 6 asserted
   the tail and B are one utterance; a tail that closes a sentence of its own
   contradicts that. The slice excludes the last word, whose punctuation says
   nothing about internal structure. Checked before the cap because it tests
   the rule's structural premise rather than a tuned magnitude.
9. `len(a.words) - k <= MAX_INTRO_TAIL_WORDS` (new constant, 10). Retained as a
   second, independent line of defence.
10. Move `a.words[k:]` into `b` via the existing `_move` helper.

Cue matching, `_norm`, `_ends_sentence`, `_intro_cue_index` and `_move` are all
reused unchanged. `MAX_FRAGMENT_WORDS` is untouched, as in the shipped rule.

### Why this is a second function, not a loosened gate 3

The two rules share a repair but not an admission test. The shipped rule admits
on a single strong structural signal (the cue is outside the diarized span) and
needs no corroboration. This rule admits on a weak signal (the turn's words
spill) and needs four corroborating gates to be safe. Folding both into one
function would mean a gate list where half the gates apply only on one branch.
Two functions, mutually exclusive at gate 3, keep each admission test readable
on its own.

## No divergence

Every gate reads `speaker_label`, word timings, or punctuation. All of these
exist at word-assignment time, so all five callers get the rule and the
pipeline and the backfill continue to apply identical corrections:

- `src/transcribe.py:144` and `src/vtt_align.py:176`, via
  `assign_words_to_segments`
- `bench/modal_app.py:1912`
- `src/identify.py:541`, via `_resnap_merged_boundaries` — added by PR #207 after
  this spec was first written. `merge_adjacent_segments` collapses adjacent
  same-speaker turns, which moves every boundary the transcription-time snap had
  already settled, so the snap must run again afterwards.

The first three of those run before names exist. `backfill_boundary_snap.py` and
`_resnap_merged_boundaries` both run after: `apply_mappings_to_segments`
immediately precedes `merge_adjacent_segments` at every call site
(`run_local.py:1684`->`1696`, `run_local.py:1970`, `src/repair.py:211`->`212`),
and `merge_adjacent_segments`' own docstring specifies "Segments with
speaker_name populated".

So the case against the name-matching alternative is **not** that names are
unavailable everywhere — at two of the five callers they are. It is that a
name-dependent gate would make `snap_segment_boundaries` do different things
depending on who called it. That is the same hazard class as the merge defect
PR #207 fixed, where one caller silently discarded the pass's work. A pass with
five callers must have one behaviour. Caller-independence is the argument, not
availability.
- `backfill_boundary_snap.py:47`

## Fixpoint safety

`snap_segment_boundaries` runs to a fixpoint and the backfill re-snaps in
place, so the rule must be idempotent. After a move, three independent gates
reject a second firing:

- the cue now sits at index 0 in B — gate 2 rejects;
- A's remaining last word (`"minutes."`, start 13109.613) no longer starts past
  A's `end_time` of 13111.282 — gate 4 rejects;
- B now opens with an uppercase `"My"` — gate 6 rejects.

`_snap_leading` cannot pull the fragment back either: it requires a `>>` marker
or a pause plus terminal punctuation, and the moved first word `"My"` carries
neither.

Verified empirically by a prototype run over all 172 meetings: running the
combined pass twice changes nothing on the second run, in any meeting.

## Expected outcome

Corpus-wide isolation diff — the pass run with and without the new rule — is
exactly two segments:

| Meeting | Seg | Keeps | Moves to next turn |
| --- | --- | --- | --- |
| bloomington-city-council-2026-05-06 | 793 | `three - minutes.` | `My name is Jeremy Hackard,` |
| bloomington-city-council-2026-05-06 | 794 | — | gains the five words at its front |

No other word list in the corpus moves.

## Corpus state

The on-disk transcripts under `~/CouncilScribe/meetings` have **already been
backfilled with the shipped rule** — segment 454 of
`bloomington-city-council-2026-06-10` no longer carries its bleed. All figures
in this spec are measured against that post-backfill state, which is the
correct base for a rule that runs after the shipped one. It also means the
backfill for this change should produce a small, clean diff, and any meeting
that changes beyond segments 793/794 must be investigated before applying.

## Tests

- Regression: the real segment 793/794 shapes, exact words and timings lifted
  from the corpus, asserting the five words move and the split lands after
  `"minutes."`.
- Negative, gate 3: cue starts past `end_time` → this rule declines and leaves
  the segment to `_snap_trailing_intro`.
- Negative, gate 5: A and B share a `speaker_label` → no move.
- Negative, gate 6: the real Goldstein shape (A ends `"Goldstein."`, B opens
  `">>"`) → no move. This is the collision that motivates the gate.
- Negative, gate 7: no sentence boundary within 4 words before the cue → no
  move. The fixture must punctuate the word before the run-up (`"Thank you."`,
  not `"Thank you,"`) or it dies at gate 7 for the wrong reason and passes with
  gate 3 deleted.
- Negative, gate 8: the real seg-116 shape from
  `2026-03-30-lwv-candidate-forum---county-clerk-and-prosecutor` (Tree
  Martin-Lucas) → no move. Shortened so the tail is inside the cap, so the cap
  cannot be the blocker; paired with a control that flips the one internal
  period to a comma and does move, proving gates 1–7 all pass.
- Negative, gate 9: a long self-introduction shape (tail well over 10 words) →
  no move. Its tail must carry no internal period, or gate 8 rejects it first
  and the cap is never reached.
- Negative, gate 1: destination turn has no words → no move.
- Idempotence: a second `snap_segment_boundaries` call moves nothing.
- Corpus proof: run the pass over all 172 meetings with and without the rule
  and assert exactly segments 793 and 794 of
  `bloomington-city-council-2026-05-06` differ.

## Delivery

1. Code and tests. Full suite green against the recorded baseline of
   **2475 passed / 3 skipped** at `e512c25`. (The brief's 2473 was measured at
   `9380f3f`; the `--meeting` filter commit `e512c25` adds two tests.)
2. `backfill_boundary_snap.py --dry-run`, scoped with `--meeting` to the
   affected meeting first, then corpus-wide to confirm nothing else moves.
3. **Confirm with the user before applying the backfill.**
4. **Confirm with the user before re-publishing.** Re-publishing
   `bloomington-city-council-2026-05-06` is what removes the live
   misattribution from `sydney-zulich`.

## Out of scope

`bloomington-city-council-2026-06-10` segment 479 carries
`"My name is Tessa Donahue."` **mid**-turn, with the chair resuming afterwards
(`"Thank you so much. - Thank you. Thank you. back"`), and its next segment has
no words. That is under-segmentation, not a trailing bleed: the correct repair
splits one segment into three, which no trailing rule can do. Gate 1 declines
it, consistent with `_snap_trailing` leaving resegmentation to another stage.
