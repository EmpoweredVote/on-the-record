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

Three signals available at word-assignment time isolate the case just as
sharply. Measured over all 104 segment pairs in the corpus that carry a
non-leading introduction cue and a non-empty destination turn:

| Gate added | Population |
| --- | --- |
| last word starts past `end_time` | 63 |
| `a.speaker_label != b.speaker_label` | 47 |
| continuation across the boundary | 13 |
| a sentence ends within 4 words before the cue | 6 |
| moved tail ≤ 10 words | **1** |

The continuation gate is what removes the Goldstein collision: A ends
`"Goldstein."` and B opens `">>"`, so the sentence does not run across the
boundary. Length only failed to separate the two cases *at the relaxed gate
alone*.

The tail cap then acts on an already-clean set of 6. Its margin is wide, not
knife-edge:

| tail cap | 3 | 4 | **5–100** | unbounded |
| --- | --- | --- | --- | --- |
| hits | 0 | 0 | **1** | 6 |

The one true positive has a 5-word tail; the nearest false positive has 180
(the others are 256, 285, 409, 516). Any cap in a 20-fold range gives the same
answer. `MAX_INTRO_TAIL_WORDS = 10` sits comfortably inside it.

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
8. `len(a.words) - k <= MAX_INTRO_TAIL_WORDS` (new constant, 10).
9. Move `a.words[k:]` into `b` via the existing `_move` helper.

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
exist at word-assignment time, so all four callers get the rule and the
pipeline and the backfill continue to apply identical corrections:

- `src/transcribe.py:144` and `src/vtt_align.py:176`, via
  `assign_words_to_segments`
- `bench/modal_app.py:1912`
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
  move.
- Negative, gate 8: a long self-introduction shape (tail well over 10 words) →
  no move.
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
