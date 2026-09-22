# Straddling Self-Introduction Bleed Rule — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move a self-introduction that straddles a diarized turn boundary off the previous speaker and onto the speaker it belongs to, removing a live misattribution on `bloomington-city-council-2026-05-06` segment 793.

**Architecture:** Add one function, `_snap_straddling_intro(a, b)`, to `src/word_assign.py`, called from the existing per-pair loop in `snap_segment_boundaries` immediately after `_snap_trailing_intro`. It reuses that rule's cue matching, split logic and move helper, but admits on a different, weaker structural signal and therefore carries four corroborating gates. The two rules are mutually exclusive at their third gate, so neither can double-fire. Every gate reads only data present at word-assignment time, so all four callers get the rule and the pipeline and backfill stay identical.

**Tech Stack:** Python 3, pytest, `src/models.Segment` / `src/models.Word`.

## Global Constraints

- Always run Python as `/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python`. Never system `python3` (3.14 lacks project deps).
- Branch is `claude/wizardly-nobel-a10d18`, based on `e512c25` (tip of `claude/friendly-golick-b1d996`). The shipped `_snap_trailing_intro` rule is unmerged and lives on that base.
- Recorded test baseline at `e512c25`: **2475 passed, 3 skipped**. Every task must leave the suite green with no fewer passing tests than the baseline plus the tests that task adds.
- `MAX_FRAGMENT_WORDS` must not change. It governs the marker paths for all 26,166 corpus segments.
- The new rule must be idempotent. `backfill_boundary_snap.py` re-snaps in place and must stay re-runnable with no drift.
- Corpus lives at `~/CouncilScribe/meetings` (172 meetings). It has **already been backfilled with the shipped rule**, so the shipped gate matches 0 of 104 candidate pairs on the current corpus.
- Do NOT run the backfill for real, and do NOT re-publish, without explicit user confirmation. Task 5 stops for that.
- Spec: `docs/superpowers/specs/2026-09-09-straddling-intro-bleed-design.md`.

---

## File Structure

| File | Responsibility | Change |
| --- | --- | --- |
| `src/word_assign.py` | The boundary-snap pass and its rules | Modify: add `MAX_INTRO_TAIL_WORDS`, add `_snap_straddling_intro`, call it in the loop |
| `tests/test_word_assign.py` | Unit tests for the pass | Modify: add one regression test, six gate negatives, one idempotence test |
| `bench/straddling_intro_scan.py` | Corpus-wide isolation diff for this rule | Create |

---

### Task 1: The rule and its regression test

**Files:**
- Modify: `src/word_assign.py` (constant near line 33; new function after `_snap_trailing_intro`, which ends at line 258; loop call at line 291)
- Test: `tests/test_word_assign.py` (append at end of file)

**Interfaces:**
- Consumes: `_intro_cue_index(words) -> int | None`, `_ends_sentence(word: str) -> bool`, `_move(words: list[Word], src: Segment, dst: Segment) -> bool`, `MAX_INTRO_PREAMBLE = 4` — all already in `src/word_assign.py`.
- Produces: `MAX_INTRO_TAIL_WORDS: int = 10` and `_snap_straddling_intro(a: Segment, b: Segment) -> bool`. Tasks 2 and 3 import `_snap_straddling_intro` by name.

- [ ] **Step 1: Write the failing regression test**

Append to `tests/test_word_assign.py`:

```python
# --- Straddling self-introduction bleed --------------------------------------
#
# A weaker shape than the one _snap_trailing_intro covers. There the whole
# introduction is dumped past the turn's diarized end by _segment_for_gap_word.
# Here it straddles the end: the cue starts INSIDE the turn's own span, so that
# rule's third gate rejects it, and only the turn's final word spills past.
# Word lists and timings below are lifted verbatim from the corpus.


def test_straddling_self_intro_moves_off_the_chair_bloomington_may():
    # bloomington-city-council-2026-05-06 seg 793. The cue "My" starts at
    # 13109.952, inside the segment's own span ending 13111.282, so
    # _snap_trailing_intro cannot see it; only "Hackard," (13111.306) spills
    # past. The published page showed councilmember Sydney Zulich saying
    # "My name is Jeremy Hackard," — segment 794 is Jeremy Hackerd and
    # continues the sentence with "and I'm instantly regretting...".
    a = _seg(793, 13109.223, 13111.282, "SPEAKER_36")   # District 6 Zulich
    b = _seg(794, 13111.754, 13246.585, "SPEAKER_19")   # Jeremy Hackerd
    a.words = [
        Word("three", 13108.944, 13109.275),
        Word("-", 13109.275, 13109.613),
        Word("minutes.", 13109.613, 13109.952),
        Word("My", 13109.952, 13110.29),
        Word("name", 13110.29, 13110.629),
        Word("is", 13110.629, 13110.967),
        Word("Jeremy", 13110.967, 13111.306),
        Word("Hackard,", 13111.306, 13111.644),
    ]
    b.words = [
        Word("and", 13111.644, 13111.983),
        Word("I'm", 13111.983, 13112.321),
        Word("instantly", 13112.321, 13112.66),
        Word("regretting", 13112.66, 13112.998),
        Word("that", 13112.998, 13113.337),
        Word("I", 13113.337, 13113.675),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == ["three", "-", "minutes."]
    assert _tokens(b)[:6] == ["My", "name", "is", "Jeremy", "Hackard,", "and"]
```

- [ ] **Step 2: Run the test to verify it fails**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_word_assign.py::test_straddling_self_intro_moves_off_the_chair_bloomington_may -v
```

Expected: FAIL. `_tokens(a)` is still the full eight words `['three', '-', 'minutes.', 'My', 'name', 'is', 'Jeremy', 'Hackard,']` because no rule moves them.

- [ ] **Step 3: Add the tail-length constant**

In `src/word_assign.py`, directly below the existing `MAX_INTRO_PREAMBLE` line (line 33):

```python
MAX_INTRO_TAIL_WORDS = 10  # a straddling intro bleed is at most this many words
```

- [ ] **Step 4: Add the rule**

In `src/word_assign.py`, insert after `_snap_trailing_intro` ends (line 258, immediately before `def snap_segment_boundaries`):

```python
def _snap_straddling_intro(a: Segment, b: Segment) -> bool:
    """Move a self-introduction that straddles A's diarized end onto B.

    _snap_trailing_intro covers an introduction dumped WHOLLY past A's span by
    the gap-snap fallback. This covers the weaker shape: the introduction begins
    inside A's own diarized turn, but A's word timings run past the turn's end
    and carry the next speaker's opening with them. On
    bloomington-city-council-2026-05-06 seg 793 the cue "My" starts 1.33s inside
    the span and only the final word spills, so that rule's gate 3 rejects it
    while a councilmember stays published saying "My name is Jeremy Hackard,".

    Admitting on "A's last word spills" alone is not safe: 63 of the corpus's
    104 non-leading-cue pairs do that, and most are speakers correctly
    introducing themselves at the start of their own turn. Four gates narrow it
    to one. Differing speaker_label cuts 63 to 47. Requiring the sentence to run
    ACROSS the boundary — A's last word not sentence-final, B opening lowercase
    — cuts 47 to 13; that is the gate that spares Steve Goldstein's own
    "My name is Steve Goldstein." on 2026-06-24-cd1-republican-primary-debate,
    which ends a sentence and is followed by a '>>' marker. Requiring a sentence
    boundary within MAX_INTRO_PREAMBLE words before the cue cuts 13 to 6, and
    means the rule never cuts mid-sentence. The tail cap then acts on an already
    clean set: the one true positive moves 5 words and the nearest false
    positive would move 180, so any cap from 5 to 100 gives the same answer.

    Gate 3 cedes the wholly-outside shape to _snap_trailing_intro, so the two
    rules are mutually exclusive by construction and can neither double-fire on
    a segment nor disagree about a split point. Returns True if a word moved.
    """
    if not a.words or not b.words:
        # Publish drops empty segments, so moving words into one would turn a
        # dropped turn into a live attribution.
        return False
    cue = _intro_cue_index(a.words)
    if cue is None:
        return False
    if a.words[cue].start > a.end_time:
        return False   # wholly outside A's span: _snap_trailing_intro owns it
    if a.words[-1].start <= a.end_time:
        return False   # A's words do not spill past its own diarized end
    if a.speaker_label == b.speaker_label:
        return False   # same turn split in two, not a bleed between speakers
    if _ends_sentence(a.words[-1].word):
        return False   # A's sentence closes; nothing runs into B
    if not b.words[0].word.strip()[:1].islower():
        return False   # B opens its own sentence; nothing ran into it
    # Unlike _snap_trailing_intro, where the scan-back only refines a split that
    # defaults to the cue, here its absence rejects the segment: the rule cuts
    # only where the previous speaker's own sentence has ended. It still takes a
    # greeting along when one is present ("...minutes. Hi, my name is X").
    split = None
    for k in range(cue, max(0, cue - MAX_INTRO_PREAMBLE) - 1, -1):
        if k > 0 and _ends_sentence(a.words[k - 1].word):
            split = k
            break
    if split is None or split < 1:
        return False   # A must keep words of its own
    if len(a.words) - split > MAX_INTRO_TAIL_WORDS:
        return False   # a long turn spilling its timings, not a short bleed
    return _move(a.words[split:], a, b)


```

- [ ] **Step 5: Call the rule from the loop**

In `src/word_assign.py`, in `snap_segment_boundaries`, find this line (line 291):

```python
            moved |= _snap_trailing_intro(a, b)  # B's self-intro at tail of A → into B
```

Add directly below it:

```python
            moved |= _snap_straddling_intro(a, b)  # B's self-intro straddling A's end → into B
```

- [ ] **Step 6: Document the new rule in the pass docstring**

In `src/word_assign.py`, in the `snap_segment_boundaries` docstring, find this paragraph:

```
    A separately-gated rule (_snap_trailing_intro) additionally handles the case
    where diarization missed a stretch of speech entirely and the gap-snap
    fallback dumped the next speaker's opening self-introduction onto the end of
    the previous turn.
```

Replace it with:

```
    Two separately-gated rules additionally handle self-introduction bleed.
    _snap_trailing_intro takes the case where diarization missed a stretch of
    speech entirely and the gap-snap fallback dumped the next speaker's opening
    self-introduction onto the end of the previous turn.
    _snap_straddling_intro takes the weaker case where that introduction begins
    inside the previous turn's own diarized span and only the turn's trailing
    words spill past it.
```

- [ ] **Step 7: Run the test to verify it passes**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_word_assign.py::test_straddling_self_intro_moves_off_the_chair_bloomington_may -v
```

Expected: PASS.

- [ ] **Step 8: Verify the rule needs no names**

The whole point of this gate set is that it runs at word-assignment time, where
`speaker_name` is still `None`, so the pipeline and the backfill apply identical
corrections. Confirm the function reads no name field:

```bash
sed -n '/^def _snap_straddling_intro/,/^def snap_segment_boundaries/p' src/word_assign.py | grep -n "speaker_name" || echo "OK: no speaker_name reference"
```

Expected: `OK: no speaker_name reference`. If `speaker_name` appears, the rule
has acquired a dependency that `src/transcribe.py:144`, `src/vtt_align.py:176`
and `bench/modal_app.py:1912` cannot satisfy — stop and remove it.

- [ ] **Step 9: Run the full suite**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q
```

Expected: `2476 passed, 3 skipped` (baseline 2475 plus this task's one test). If any previously-passing test now fails, the new rule is firing where it should not — do not adjust the failing test, diagnose the rule.

- [ ] **Step 10: Commit**

```bash
git add src/word_assign.py tests/test_word_assign.py
git commit -m "fix(word-assign): move a straddling self-introduction onto the next turn"
```

---

### Task 2: Gate negatives

Seven tests covering gates 1 and 3 through 8, proving each is load-bearing. Six reuse the segment-793 shape with a single field changed, so any test that starts passing for the wrong reason is easy to spot. One uses real corpus data for the collision that motivated the design. Gate 2 (a cue at index 0 opens the speaker's own turn) needs no new test: it lives in the shared `_intro_cue_index` helper and is already pinned by `test_leading_self_intro_is_the_speakers_own_turn`.

**Files:**
- Test: `tests/test_word_assign.py` (append)

**Interfaces:**
- Consumes: `_snap_straddling_intro(a, b) -> bool` and `MAX_INTRO_TAIL_WORDS` from Task 1; `_seg`, `_tokens`, `Word`, `snap_segment_boundaries` already in the test module.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the seven failing tests**

Append to `tests/test_word_assign.py`:

```python
def test_straddling_rule_cedes_the_wholly_outside_shape():
    # bloomington-city-council-2026-06-10 seg 454, which _snap_trailing_intro
    # owns: the cue "my" starts at 9280.216, past A's end_time of 9273.029. The
    # straddling rule must decline outright so the two rules can never both fire
    # on one segment or pick different split points.
    from src.word_assign import _snap_straddling_intro

    a = _seg(454, 9270.869, 9273.029, "SPEAKER_22")
    b = _seg(455, 9285.297, 9338.69, "SPEAKER_01")
    a.words = [
        Word("Thank", 9270.655, 9271.452), Word("you.", 9271.452, 9272.248),
        Word("Thank", 9272.248, 9273.045), Word("you", 9273.045, 9273.842),
        Word("so", 9273.842, 9274.639), Word("much", 9274.639, 9275.435),
        Word("next", 9275.435, 9276.232), Word("person", 9276.232, 9277.029),
        Word("in", 9277.029, 9277.826), Word("chambers", 9277.826, 9278.622),
        Word("Thank", 9278.622, 9279.419), Word("you,", 9279.419, 9280.216),
        Word("my", 9280.216, 9281.013), Word("name", 9281.013, 9281.809),
        Word("is", 9281.809, 9282.606), Word("Paul", 9282.606, 9283.403),
        Word("Gillard", 9283.403, 9284.2), Word("I'm", 9284.2, 9284.996),
    ]
    b.words = [Word("a", 9284.996, 9285.793), Word("former", 9285.793, 9286.59)]

    assert _snap_straddling_intro(a, b) is False
    assert _tokens(a)[-1] == "I'm"


def test_straddling_self_intro_needs_the_turn_to_spill_past_its_span():
    # The seg-793 shape with a diarized end_time that actually covers the words.
    # Nothing spilled, so the introduction is real speech inside this speaker's
    # own turn and stays. This is what keeps the rule off ASR spelling variants
    # of the same person ("Bob Costello" transcribed "Bob Gasillo"), the same
    # protection _snap_trailing_intro gets from its own span test.
    a = _seg(793, 13109.223, 13112.000, "SPEAKER_36")   # span covers every word
    b = _seg(794, 13112.100, 13246.585, "SPEAKER_19")
    a.words = [
        Word("three", 13108.944, 13109.275),
        Word("-", 13109.275, 13109.613),
        Word("minutes.", 13109.613, 13109.952),
        Word("My", 13109.952, 13110.29),
        Word("name", 13110.29, 13110.629),
        Word("is", 13110.629, 13110.967),
        Word("Jeremy", 13110.967, 13111.306),
        Word("Hackard,", 13111.306, 13111.644),
    ]
    b.words = [Word("and", 13111.644, 13111.983)]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "Hackard,"


def test_straddling_self_intro_needs_a_different_speaker_label():
    # The seg-793 shape with one turn split across two segments of the same
    # diarized speaker. Nothing bled between speakers, so nothing moves.
    a = _seg(793, 13109.223, 13111.282, "SPEAKER_36")
    b = _seg(794, 13111.754, 13246.585, "SPEAKER_36")   # same label as A
    a.words = [
        Word("three", 13108.944, 13109.275),
        Word("-", 13109.275, 13109.613),
        Word("minutes.", 13109.613, 13109.952),
        Word("My", 13109.952, 13110.29),
        Word("name", 13110.29, 13110.629),
        Word("is", 13110.629, 13110.967),
        Word("Jeremy", 13110.967, 13111.306),
        Word("Hackard,", 13111.306, 13111.644),
    ]
    b.words = [Word("and", 13111.644, 13111.983)]

    snap_segment_boundaries([a, b])

    assert _tokens(a) == [
        "three", "-", "minutes.", "My", "name", "is", "Jeremy", "Hackard,",
    ]


def test_straddling_self_intro_leaves_a_closed_sentence_alone():
    # 2026-06-24-cd1-republican-primary-debate seg 0. Steve Goldstein really is
    # introducing himself, and his 49 words spill past a 29.849 end_time, so the
    # spill gate alone would move them onto Danielle Lerner. His sentence closes
    # ("Goldstein.") and hers opens with a '>>' marker, so nothing runs across
    # the boundary. This is the collision the continuation gate exists for: the
    # moved tail would be 5 words, exactly as long as segment 793's.
    a = _seg(0, 13.649, 29.849, "SPEAKER_04")    # Steve Goldstein
    b = _seg(1, 30.035, 48.8, "SPEAKER_00")      # Danielle Lerner
    a.words = [
        Word("state's", 26.5, 26.63),
        Word("non-partisan", 27.775, 28.002),
        Word("voter", 28.002, 28.229),
        Word("education", 28.229, 28.456),
        Word("agency.", 28.456, 28.683),
        Word("My", 28.683, 28.91),
        Word("name", 29.559, 29.687),
        Word("is", 29.687, 29.814),
        Word("Steve", 29.814, 29.942),
        Word("Goldstein.", 29.942, 30.07),
    ]
    b.words = [
        Word(">>", 30.483, 30.584),
        Word("And", 30.584, 30.685),
        Word("I'm", 30.685, 30.785),
        Word("Danielle", 30.785, 30.886),
        Word("Lerner,", 30.886, 30.987),
    ]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-5:] == ["My", "name", "is", "Steve", "Goldstein."]
    assert _tokens(b)[0] == ">>"


def test_straddling_self_intro_needs_a_sentence_end_before_the_cue():
    # The seg-793 shape with "minutes." unpunctuated. No sentence boundary
    # within MAX_INTRO_PREAMBLE words before the cue, so there is no split point
    # the rule trusts and it declines rather than cutting mid-sentence.
    a = _seg(793, 13109.223, 13111.282, "SPEAKER_36")
    b = _seg(794, 13111.754, 13246.585, "SPEAKER_19")
    a.words = [
        Word("three", 13108.944, 13109.275),
        Word("-", 13109.275, 13109.613),
        Word("minutes", 13109.613, 13109.952),   # no terminal punctuation
        Word("My", 13109.952, 13110.29),
        Word("name", 13110.29, 13110.629),
        Word("is", 13110.629, 13110.967),
        Word("Jeremy", 13110.967, 13111.306),
        Word("Hackard,", 13111.306, 13111.644),
    ]
    b.words = [Word("and", 13111.644, 13111.983)]

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "Hackard,"


def test_straddling_self_intro_stays_put_when_the_next_turn_has_no_words():
    # A destination turn with no words is dropped at publish, so moving words
    # into it would turn a dropped turn into a live attribution.
    a = _seg(793, 13109.223, 13111.282, "SPEAKER_36")
    b = _seg(794, 13111.754, 13246.585, "SPEAKER_19")
    a.words = [
        Word("three", 13108.944, 13109.275),
        Word("-", 13109.275, 13109.613),
        Word("minutes.", 13109.613, 13109.952),
        Word("My", 13109.952, 13110.29),
        Word("name", 13110.29, 13110.629),
        Word("is", 13110.629, 13110.967),
        Word("Jeremy", 13110.967, 13111.306),
        Word("Hackard,", 13111.306, 13111.644),
    ]
    b.words = []

    snap_segment_boundaries([a, b])

    assert _tokens(a)[-1] == "Hackard,"
    assert _tokens(b) == []


def test_straddling_self_intro_declines_a_long_spilling_turn():
    # Synthetic, modelled on 2026-06-26-tn-governor seg 235, where Leanne Martin
    # introduces herself at the start of her own 190-word turn and the whole
    # turn's timings spill past a missed diarized end. That real segment cannot
    # be used verbatim here (truncating it drops the spill that gate 4 reads),
    # so the shape is reproduced at test scale: every other gate passes and only
    # the tail cap rejects it. The corpus scan in Task 4 covers the real row.
    def build(tail_words):
        a = _seg(1, 99.0, 104.0, "SPEAKER_07")
        b = _seg(2, 112.0, 130.0, "SPEAKER_06")
        words = [
            Word("Okay.", 99.0, 99.5),
            Word("Thank", 99.5, 100.0),
            Word("you.", 100.0, 100.5),
        ]
        for i, w in enumerate(tail_words):
            start = 100.5 + 0.5 * i
            words.append(Word(w, start, start + 0.5))
        a.words = words
        # B starts after a real pause so the marker-less _snap_leading path,
        # which needs a near-zero gap, cannot pull its opening word back into A.
        b.words = [Word("for", 112.0, 112.5), Word("the", 112.5, 113.0)]
        return a, b

    long_tail = [
        "My", "name", "is", "Leanne", "Martin.", "I'm", "your", "East",
        "Tennessee", "field", "rep",
    ]
    assert len(long_tail) == MAX_INTRO_TAIL_WORDS + 1
    a, b = build(long_tail)
    snap_segment_boundaries([a, b])
    assert _tokens(a)[-1] == "rep", "the tail cap must reject an 11-word tail"

    # The same shape one word shorter clears the cap and does move, proving the
    # fixture fails on the tail cap alone and not on some other gate.
    a, b = build(long_tail[:-1])
    snap_segment_boundaries([a, b])
    assert _tokens(a) == ["Okay.", "Thank", "you."]
    assert _tokens(b)[0] == "My"
```

Add the import for `MAX_INTRO_TAIL_WORDS` at the top of `tests/test_word_assign.py`. Change line 4 from:

```python
from src.word_assign import assign_words_to_segments, snap_segment_boundaries
```

to:

```python
from src.word_assign import (
    MAX_INTRO_TAIL_WORDS,
    assign_words_to_segments,
    snap_segment_boundaries,
)
```

- [ ] **Step 2: Run the tests**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_word_assign.py -k straddling -v
```

Expected: all eight straddling tests PASS (Task 1's regression plus these seven). These are negatives against a rule that already exists, so they pass on first run. That is expected and is not a reason to skip them: their value is that they fail if a later change loosens a gate. Confirm that by temporarily deleting the `if a.speaker_label == b.speaker_label:` guard from `src/word_assign.py`, re-running, seeing `test_straddling_self_intro_needs_a_different_speaker_label` FAIL, then restoring the guard.

- [ ] **Step 3: Run the full suite**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q
```

Expected: `2483 passed, 3 skipped`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_word_assign.py
git commit -m "test(word-assign): pin every gate of the straddling-intro rule"
```

---

### Task 3: Idempotence

**Files:**
- Test: `tests/test_word_assign.py` (append)

**Interfaces:**
- Consumes: `snap_segment_boundaries`, `_seg`, `_tokens`, `Word`.
- Produces: nothing consumed by later tasks.

- [ ] **Step 1: Write the test**

Append to `tests/test_word_assign.py`:

```python
def test_straddling_self_intro_snap_is_idempotent():
    # backfill_boundary_snap.py re-snaps in place, so a second pass must move
    # nothing. Three gates independently prevent a re-fire: after the move the
    # cue sits at index 0 in B, A's remaining last word ("minutes." at
    # 13109.613) no longer starts past A's end_time of 13111.282, and B now
    # opens with an uppercase "My".
    def build():
        a = _seg(793, 13109.223, 13111.282, "SPEAKER_36")
        b = _seg(794, 13111.754, 13246.585, "SPEAKER_19")
        a.words = [
            Word("three", 13108.944, 13109.275),
            Word("-", 13109.275, 13109.613),
            Word("minutes.", 13109.613, 13109.952),
            Word("My", 13109.952, 13110.29),
            Word("name", 13110.29, 13110.629),
            Word("is", 13110.629, 13110.967),
            Word("Jeremy", 13110.967, 13111.306),
            Word("Hackard,", 13111.306, 13111.644),
        ]
        b.words = [
            Word("and", 13111.644, 13111.983),
            Word("I'm", 13111.983, 13112.321),
            Word("instantly", 13112.321, 13112.66),
        ]
        return [a, b]

    segs = build()
    snap_segment_boundaries(segs)
    once = [_tokens(s) for s in segs]
    snap_segment_boundaries(segs)
    twice = [_tokens(s) for s in segs]

    assert once == twice
    assert once[0] == ["three", "-", "minutes."]
```

- [ ] **Step 2: Run the test**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest tests/test_word_assign.py::test_straddling_self_intro_snap_is_idempotent -v
```

Expected: PASS.

- [ ] **Step 3: Run the full suite**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q
```

Expected: `2484 passed, 3 skipped`.

- [ ] **Step 4: Commit**

```bash
git add tests/test_word_assign.py
git commit -m "test(word-assign): straddling-intro snap is idempotent"
```

---

### Task 4: Corpus isolation diff

A committed scanner, following the `bench/` convention for corpus-wide tools (`bench/calibrate_gate.py`, `bench/diagnose_merge.py`). It runs the pass twice per meeting — once with the new rule suppressed, once with it live — and diffs the word lists. This is the calibration evidence and stays re-runnable as the corpus grows.

**Files:**
- Create: `bench/straddling_intro_scan.py`
- Reads: `~/CouncilScribe/meetings/*/transcript_named.json` (read-only; the scanner never writes)

**Interfaces:**
- Consumes: `src.word_assign.snap_segment_boundaries`, `src.word_assign._snap_straddling_intro`, `src.models.Segment`, `src.models.Word`.
- Produces: a CLI tool. No importable API other tasks depend on.

- [ ] **Step 1: Write the scanner**

Create `bench/straddling_intro_scan.py`:

```python
"""Corpus isolation diff for the straddling self-introduction rule.

Runs snap_segment_boundaries over every local meeting twice — once with
_snap_straddling_intro suppressed, once with it live — and reports every
segment whose word list differs. Also re-runs the pass over its own output to
prove idempotence, which the backfill relies on.

    .venv/bin/python bench/straddling_intro_scan.py [--corpus DIR]

Read-only: it never writes to a transcript.
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from src import word_assign
from src.models import Segment, Word


def load(path: pathlib.Path) -> list[Segment]:
    data = json.loads(path.read_text())
    raw = data["segments"] if isinstance(data, dict) else data
    if not isinstance(raw, list):
        return []
    segs = [
        Segment(
            segment_id=s.get("segment_id", 0),
            start_time=s.get("start_time", 0.0),
            end_time=s.get("end_time", 0.0),
            speaker_label=s.get("speaker_label") or "",
            text=s.get("text") or "",
            words=[
                Word(w.get("word", ""), w.get("start", 0.0), w.get("end", 0.0))
                for w in (s.get("words") or [])
            ],
            speaker_name=s.get("speaker_name"),
        )
        for s in raw
        if isinstance(s, dict)
    ]
    return segs


def fingerprint(segs: list[Segment]) -> dict[int, list[str]]:
    return {s.segment_id: [w.word for w in s.words] for s in segs}


def run(segs: list[Segment], *, with_rule: bool) -> list[Segment]:
    """Run the pass with the straddling rule live or suppressed."""
    if with_rule:
        return word_assign.snap_segment_boundaries(segs)
    original = word_assign._snap_straddling_intro
    word_assign._snap_straddling_intro = lambda a, b: False
    try:
        return word_assign.snap_segment_boundaries(segs)
    finally:
        word_assign._snap_straddling_intro = original


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", default="~/CouncilScribe/meetings",
                    help="directory of meeting folders (default: %(default)s)")
    args = ap.parse_args()

    root = pathlib.Path(args.corpus).expanduser()
    meetings = sorted(p for p in root.glob("*/transcript_named.json"))
    if not meetings:
        sys.exit(f"no transcripts under {root}")

    changed: list[tuple[str, int, list[str], list[str]]] = []
    non_idempotent: list[str] = []
    for path in meetings:
        name = path.parent.name
        try:
            base = load(path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"  !! {name}: unreadable ({exc})")
            continue
        if not base:
            continue
        without = fingerprint(run(copy.deepcopy(base), with_rule=False))
        after = run(copy.deepcopy(base), with_rule=True)
        with_ = fingerprint(after)
        for sid, words in without.items():
            if words != with_.get(sid):
                changed.append((name, sid, words, with_.get(sid, [])))
        if with_ != fingerprint(run(copy.deepcopy(after), with_rule=True)):
            non_idempotent.append(name)

    print(f"scanned {len(meetings)} meetings\n")
    print(f"segments changed by the straddling rule: {len(changed)}")
    for name, sid, before, after_words in changed:
        print(f"  {name} seg {sid}")
        print(f"      without rule: {before[-8:]}")
        print(f"      with rule   : {after_words[:8]}")
    print(f"\nnon-idempotent meetings: {non_idempotent or 'NONE'}")
    if non_idempotent:
        sys.exit(1)


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run the scanner**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python bench/straddling_intro_scan.py
```

Expected, exactly:

```
scanned 172 meetings

segments changed by the straddling rule: 2
  bloomington-city-council-2026-05-06 seg 793
      without rule: ['three', '-', 'minutes.', 'My', 'name', 'is', 'Jeremy', 'Hackard,']
      with rule   : ['three', '-', 'minutes.']
  bloomington-city-council-2026-05-06 seg 794
      without rule: ['to', 'make', 'the', 'conservation', 'district', 'proposal', 'possible.', '-']
      with rule   : ['My', 'name', 'is', 'Jeremy', 'Hackard,', 'and', "I'm", 'instantly']

non-idempotent meetings: NONE
```

If any meeting other than `bloomington-city-council-2026-05-06` appears, STOP. A gate is looser than the spec measured. Report the extra rows and do not continue to Task 5.

- [ ] **Step 3: Run the full suite**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python -m pytest -q
```

Expected: `2484 passed, 3 skipped` (the scanner adds no tests).

- [ ] **Step 4: Commit**

```bash
git add bench/straddling_intro_scan.py
git commit -m "bench: corpus isolation diff for the straddling-intro rule"
```

---

### Task 5: Backfill dry-run and confirmation gate

**Files:**
- Runs: `backfill_boundary_snap.py` (no source change)

**Interfaces:**
- Consumes: `backfill_boundary_snap.py` CLI, which accepts `--dry-run` and repeatable `--meeting ID`.
- Produces: nothing. This task ends by stopping for the user.

- [ ] **Step 1: Dry-run the affected meeting**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python backfill_boundary_snap.py --dry-run --meeting bloomington-city-council-2026-05-06
```

Expected: one `[dry-run] bloomington-city-council-2026-05-06: boundary words re-snapped` line.

- [ ] **Step 2: Dry-run the whole corpus**

```bash
/Users/chrisandrews/Documents/GitHub/on-the-record/.venv/bin/python backfill_boundary_snap.py --dry-run
```

Expected: only `bloomington-city-council-2026-05-06` reported as changing. Any other meeting means the shipped rule is catching up on a meeting added since the 2026-07-23 backfill, or the new rule is over-firing. Task 4's scanner distinguishes the two: it suppresses only the new rule, so anything it did not list is catch-up from the existing pass. Report both sets separately and do not merge them into one number.

- [ ] **Step 3: Report and STOP**

Report to the user:
- the dry-run output for both scopes,
- which meetings change from the new rule (Task 4's scanner output) and which from the existing pass catching up,
- the full-suite result against the 2475 baseline.

Then STOP. Do NOT run the backfill without `--dry-run`, and do NOT re-publish. Both need explicit user confirmation, per the spec's delivery section. Re-publishing `bloomington-city-council-2026-05-06` is what removes the live misattribution from `sydney-zulich`, and it is the user's call when that happens.

---

## Verification Summary

| Check | Command | Expected |
| --- | --- | --- |
| Baseline before any change | `pytest -q` at `e512c25` | 2475 passed, 3 skipped |
| After Task 1 | `pytest -q` | 2476 passed, 3 skipped |
| After Task 2 | `pytest -q` | 2483 passed, 3 skipped |
| After Task 3 | `pytest -q` | 2484 passed, 3 skipped |
| Corpus isolation diff | `bench/straddling_intro_scan.py` | 2 segments changed, both in `bloomington-city-council-2026-05-06`; non-idempotent: NONE |
| Backfill scope | `backfill_boundary_snap.py --dry-run` | only `bloomington-city-council-2026-05-06` from the new rule |
