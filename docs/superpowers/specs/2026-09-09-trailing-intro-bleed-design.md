# Trailing self-introduction bleed — design

**Date:** 2026-09-09
**Status:** approved, ready to implement
**Area:** `src/word_assign.py` (`snap_segment_boundaries`)

## The defect

A chair's turn swallows the opening words of the next speaker's turn, so the
published transcript shows a councilmember introducing themselves as a member of
the public. Two examples, both live:

- `2026-07-22-bloomington-regular-session` segment 18, attributed to
  `Isak Nti Asare`:
  `"- There is no wrong side Perfect, thank you So good evening, my name is Emma Williams and"`
- `bloomington-city-council-2026-06-10` segment 454, attributed to
  `City Common Council - At Large Asare`:
  `"Thank you. Thank you so much next person in chambers Thank you, my name is Paul Gillard I'm"`

The speaker-level attribution is correct in both. Only the trailing fragment
belongs to the next person. No politician link is wrong.

## Root cause

`_segment_for_gap_word` snaps a word that falls in an inter-segment gap onto the
preceding turn. When diarization misses a stretch of speech entirely, that
fallback dumps the whole gap onto the previous turn. Segment 454's diarized span
is 2.16 s but it carries 14.3 s of words.

## Why the existing pass does not cover it

`snap_segment_boundaries` (PR #112) is deliberately narrower:

- `MAX_FRAGMENT_WORDS = 3`; these fragments are 3 to 7 words.
- `_snap_trailing` fires only when a `>>` `SPEAKER_MARKER` token is present.
  Both sources are council livestreams with no `>>` markers. Only 124 of 172
  corpus meetings contain any `>>` marker, so the marker-only trailing path
  cannot serve the rest.
- `_snap_leading`'s marker-less path handles the leading direction only.

This is a class the pass does not cover, not a bug in the pass. It is a design
addition.

## Measured scale

The corpus is 172 meetings and 26,166 segments (18,694 with words).

A detector that requires the bleed to *contain* a name matching a nearby
segment finds 2 hits. That undercounts: in 5 of 7 real instances the bleed is
cut off at or before the name (`"...Hello, my name is"`), so a name-matching
detector cannot see them.

A name-free structural detector — a non-leading self-introduction cue whose
words start past the segment's own diarized `end_time` — finds **7 hits**:

| Meeting | Seg | Attributed to | Trailing bleed | Evidence |
| --- | --- | --- | --- | --- |
| 2026-07-22-bloomington-regular-session | 18 | Isak Nti Asare | `my name is Emma Williams and` | next turn is Emma Williams |
| 2026-07-22-bloomington-regular-session | 190 | Isak Nti Asare | `My name is` | next turn opens `Nathan Ferrer. I'm the executive director...` |
| bloomington-city-council-2026-06-10 | 194 | Councilmember Rollo | `Hello, my name is` | next turn opens `Peter Pearson. I'm the chief economist...` |
| bloomington-city-council-2026-06-10 | 416 | At-Large Asare | `My name is Hartzell` | next turn is empty; name does not match — **excluded** |
| bloomington-city-council-2026-06-10 | 440 | At-Large Asare | `My name's Claire Woods. I just also` | next turn opens `want to reiterate support...` |
| bloomington-city-council-2026-06-10 | 454 | At-Large Asare | `my name is Paul Gillard I'm` | next turn is Paul Gillard |
| bloomington-city-council-2026-06-10 | 475 | At-Large Asare | `Hi, my name is` | next turn opens `Alex York, for the record.` |

Four of the five newly-found cases are provable: the sentence continues verbatim
into the next turn.

### Neither gate is selective alone

- Segments with at least one word starting past their own `end_time`: **7,367**
  of 18,694 (3,225 with three or more such words). Word timings routinely spill.
- Segments with a non-leading self-introduction cue: **141**.
- Both together: **7**.

The conjunction is what makes the rule safe.

### No pause or punctuation signal exists at the split

Word timings in both sources are uniformly interpolated. Every inter-word gap is
exactly 0.0, and neither known split point carries terminal punctuation. A rule
built on "large intra-segment pause plus a sentence-terminal boundary" would
never fire on this data. The self-introduction cue is the only signal that
locates these splits.

### The wider sweep is not this class

A looser sweep (any self-introduction naming a non-matching person) returns 24
hits dominated by ASR spelling variants of the same person — `'Bob Costello'` vs
`'Bob Gasillo'`, `'Tim Beyer'` vs `'Tim Byer'`, `'Phillip Sarnecki'` vs
`'Philip Sarnicki'`. Those are not defects. They are excluded structurally: in
those cases the introduction sits inside the speaker's own diarized span, so
gate 3 rejects them. No fuzzy name matching is needed.

## The rule

New function `_snap_trailing_intro(a, b)` in `src/word_assign.py`, called from
the existing per-pair loop in `snap_segment_boundaries`, after `_snap_leading`
and `_snap_trailing`.

All gates must hold:

1. `b.words` is non-empty. The destination must be a real turn.
2. A self-introduction cue starts at word index `j` in `a.words`, with `j > 0`.
   A cue at index 0 is the speaker's own turn.
3. `a.words[j].start > a.end_time`. The cue lies outside `a`'s own diarized
   span, so the gap-snap fallback placed it there — it did not overlap the turn.
4. Split index `k`: scan back at most `MAX_INTRO_PREAMBLE = 4` words from `j`
   and take the first sentence-terminal boundary; else `k = j`. Require
   `k >= 1` so `a` keeps words.
5. Move `a.words[k:]` into `b` via the existing `_move` helper.

Cues, matched on tokens normalised to lowercase letters and apostrophes:
`my name is`, `my name's`, `my names`.

### Resolving the `MAX_FRAGMENT_WORDS` tension

`MAX_FRAGMENT_WORDS = 3` is untouched. Raising it is the wrong lever: the cap
exists to stop the pass stealing real speech, and it applies to all 26,166
segments. The new rule has no length cap, but it replaces the cap with a
semantic gate rather than relaxing it. Gates 2 and 3 admit 7 segments in the
whole corpus.

### Segment 416 is excluded on purpose

Gate 1 skips it. Its destination turn carries no words, and the bleed reads
`My name is Hartzell` while the destination speaker is `Hilary Martel`. Moving
the words there would mint a new and unverifiable public claim, and would turn a
segment that publish currently drops into a live one. Gate 1 is a principled
requirement (a destination must be a real turn), not a special case for this
row.

### Split-point precision

Gate 4 uses terminal punctuation only. No opener word list. This fixes the
identity misattribution in all six cases and never steals chair speech. It
leaves a short cosmetic remainder on the chair in two cases —
`"So good evening,"` in segment 18 and `"Thank you,"` in segment 454. An opener
word list would recover the first of those, but it would have to be calibrated
on seven examples, and no list gets both cases right: including
`thank`/`you` recovers segment 454 but eats the chair's own
`"Perfect, thank you"` in segment 18. The remainder is a cosmetic three-word
tail, not a misattribution of identity, so the simpler rule wins.

## Expected outcome

| Seg | Keeps on the chair | Moves to the next speaker |
| --- | --- | --- |
| 18 | `...Perfect, thank you So good evening,` | `my name is Emma Williams and` |
| 190 | `...Take it away. Good evening.` | `My name is` |
| 194 | `...It's about three minutes.` | `Hello, my name is` |
| 440 | `...go for it. I'll go next.` | `My name's Claire Woods. I just also` |
| 454 | `...next person in chambers Thank you,` | `my name is Paul Gillard I'm` |
| 475 | `...Go ahead, two more people.` | `Hi, my name is` |

Segment 416 is unchanged.

## Fixpoint safety

`snap_segment_boundaries` runs to a fixpoint and must stay idempotent so the
backfill can be applied twice with no drift.

After a move, `a` no longer holds the cue, and in `b` the cue sits at index 0,
which gate 2 rejects. No moved first word carries terminal punctuation
(`my`, `My`, `Hello,`, `Hi,`), so `_snap_leading` cannot pull it back. A test
asserts a second pass moves nothing.

## Callers

The rule is name-free, so it works at pipeline time where `speaker_name` is
still `None` (names are assigned later, at the identify stage). All four callers
get it:

- `src/transcribe.py:144` and `src/vtt_align.py:176`, via
  `assign_words_to_segments`
- `bench/modal_app.py:1912`
- `backfill_boundary_snap.py:47`

## Tests

- Regression tests built from the real segment shapes — exact words and
  timings lifted from the corpus for segments 18, 454, 190, 194, 440, 475.
- Negative: destination turn has no words (the segment 416 shape) → no move.
- Negative: cue at index 0 → no move.
- Negative: cue inside the turn's own diarized span → no move. This is the
  protection against ASR spelling variants.
- Idempotence: a second `snap_segment_boundaries` call moves nothing.
- Corpus proof: run the pass over all 172 meetings and assert exactly 6
  segments change and no other word list moves.

## Delivery

1. Code and tests. Full suite green against the recorded baseline of
   2463 passed / 3 skipped.
2. `backfill_boundary_snap.py --dry-run`, then apply.
3. Report separately which meetings change from the new rule and which change
   from the *existing* pass catching up. The memory note records all 73 local
   meetings backfilled on 2026-07-23, but the corpus is now 172, so some
   changes will be catch-up and must not be confused with the new rule's effect.
4. Re-publish the two live meetings only after the user confirms.

---

# Addendum — the snap was not persisting at all

Found while checking why a backfill still reported 54 pending meetings after
this change. It is a separate defect from the one above, and a larger one.

## The defect

`merge_adjacent_segments` (`src/identify.py`) discarded the output of
`snap_segment_boundaries` on every identify run. Two mechanisms:

1. `snap_segment_boundaries` evaluates **adjacent pairs**, and runs once, at
   word assignment. The identify-stage merge then collapses adjacent
   same-speaker turns hard — 236 segments to 38 is typical, 624 to 109 on a
   long forum. The turns either side of every boundary change, along with their
   spans and their first and last words, so each boundary the snap settled
   becomes a different, never-evaluated boundary.
2. A bleed into a **one-word turn is invisible before the merge**, because
   `_snap_leading` requires two words in the destination. It only becomes
   correctable once that turn is merged.

So every identify run silently re-bled a transcript that transcription had
already corrected. This was not historical debt predating PR #112.

## Evidence

| Measurement | Before | After |
| --- | --- | --- |
| Named transcripts bled | 54 | — |
| …whose raw transcript was CLEAN | 50 | — |
| …whose raw was also bled (predate #112) | 4 | — |
| Merging a clean raw re-creates the bleed | **50 of 50** | **0 of 93** |

Provenance note: `pipeline_state.json` carries no timestamps. Use
`transcript_raw.json` mtime as "when transcribed" — repair tools rewrite
`transcript_named.json` but not the raw file. That is what separated the 50
post-merge cases from the 4 genuinely old ones.

## The fix

`_resnap_merged_boundaries(merged)` is called inside `merge_adjacent_segments`
before it returns, rather than at its four production call sites
(`run_local.py` twice, `src/repair.py`, `backfill_segment_merge.py`), so no
future caller can bypass it. Same reasoning that closed the earlier in-memory
segment-merge hole.

Two constraints the fix respects:

- **Two callers use the function as a length probe**
  (`backfill_segment_merge.py`, `relabel_meeting.py`). Snapping moves words
  between turns and never changes the segment count. A test pins that.
- **`.text` rides alongside `.words` through the merge**, so it is rebuilt for
  the turns whose words actually moved, and only those — a source whose
  transcript arrives without word timings keeps its text. Verified neutral on
  the pre-existing text-versus-words mismatch: 265 segments corpus-wide both
  before and after (the raw input already carries 320).

## Applied

All 54 affected meetings backfilled (0 still bled), and the 43 live ones
republished and verified as an exact segment-text match against production.
Pre-backfill copies of all 172 transcripts are kept at
`~/CouncilScribe/backups/2026-09-09-pre-boundary-resnap/<meeting_id>.json`,
outside `meetings/` so no directory walker picks them up.
