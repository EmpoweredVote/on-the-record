"""Assign a chronological word stream to diarized segments by timestamp.

Shared by the VTT alignment path and the whole-audio Whisper path. The strategy
(proven in the former vtt_align implementation): assign each word to the segment
whose span contains the word midpoint; fall back to the segment of greatest
temporal overlap; finally snap a zero-overlap word that lands in an
inter-segment gap to the preceding turn (otherwise drop it).

A final post-pass (snap_segment_boundaries) then corrects word-level speaker
bleed at turn boundaries, where diarization boundaries and ASR word timings
disagree by a word or two — see that function for the cheap, non-LLM signals it
uses (the '>>' broadcast speaker-change marker, plus word-gap pauses and
terminal punctuation), and where diarization has missed a stretch of speech
outright it also moves a trailing self-introduction onto the turn that follows.
A second, more heavily gated introduction rule covers the straddling shape,
where the introduction begins inside the turn's own diarized span and only the
turn's trailing words spill past it.
"""

from __future__ import annotations

from .models import Segment, Word

SHORT_TURN_SECONDS = 0.8  # turns shorter than this cannot claim boundary words

# Boundary-snap tuning (see snap_segment_boundaries).
SPEAKER_MARKER = ">>"      # ASR/CART speaker-change token (broadcast captions)
MAX_FRAGMENT_WORDS = 3     # a boundary bleed is at most this many content words
MIN_BLEED_PAUSE = 0.25     # seconds of silence that marks the true (no-marker) split
MAX_CONTINUATION_GAP = 0.2  # a bled word butts against the previous turn (near-zero gap)
MAX_SNAP_PASSES = 8        # fixpoint cap: a fragment can only relay one turn per pass

# Trailing self-introduction tuning (see _snap_trailing_intro). Matched against
# tokens reduced to lowercase letters and apostrophes.
INTRO_CUES = (("my", "name", "is"), ("my", "name's"), ("my", "names"))
MAX_INTRO_PREAMBLE = 4     # words scanned back from the cue for a sentence end
MAX_INTRO_TAIL_WORDS = 10  # a straddling intro bleed is at most this many words


def _duration(seg: Segment) -> float:
    return seg.end_time - seg.start_time


def _overlap(seg_start: float, seg_end: float, w_start: float, w_end: float) -> float:
    """Overlap duration between a segment span and a word span."""
    return max(0.0, min(seg_end, w_end) - max(seg_start, w_start))


def _segment_for_gap_word(word: Word, segments: list[Segment]) -> Segment | None:
    """Snap a zero-overlap word in an inter-segment gap to the preceding turn.

    Returns the preceding turn when the word falls strictly between two turns
    (trailing word of that turn), else None (outside the diarized timeline).
    """
    preceding = None
    following = None
    for seg in segments:
        if seg.end_time <= word.start:
            if preceding is None or seg.end_time > preceding.end_time:
                preceding = seg
        if seg.start_time >= word.end:
            if following is None or seg.start_time < following.start_time:
                following = seg
    if preceding is not None and following is not None:
        return preceding
    return None


def assign_words_to_segments(
    words: list[Word], segments: list[Segment]
) -> list[Segment]:
    """Populate seg.words and seg.text for each diarized segment from `words`."""
    for seg in segments:
        seg.words = []
        seg.text = ""

    for word in words:
        midpoint = (word.start + word.end) / 2
        word_dur = max(word.end - word.start, 1e-9)
        target = None
        for s in segments:
            if not (s.start_time <= midpoint < s.end_time):
                continue
            # A short turn only owns a midpoint-contained word if most of the
            # word actually fits inside it. A long word overflowing a brief
            # backchannel turn belongs to the surrounding continuous speech
            # (e.g. Steve's "where" spilling across a 0.4s listener turn), but a
            # genuine one-word response ("Here." in a roll call) mostly fills
            # its own turn and must stay with that speaker.
            if _duration(s) < SHORT_TURN_SECONDS:
                inside = _overlap(s.start_time, s.end_time, word.start, word.end)
                if inside / word_dur < 0.5:
                    continue
            target = s
            break
        if target is None:
            # Only turns long enough to be a real utterance may claim a word
            # whose midpoint lies outside every turn. This stops a brief
            # backchannel turn from stealing a word from a surrounding speaker.
            claimable = [s for s in segments if _duration(s) >= SHORT_TURN_SECONDS]
            candidates = [
                (_overlap(s.start_time, s.end_time, word.start, word.end), s)
                for s in claimable
            ]
            overlap_dur, target = (
                max(candidates, key=lambda item: item[0])
                if candidates
                else (0.0, None)
            )
            if not overlap_dur or overlap_dur <= 0:
                target = _segment_for_gap_word(word, claimable) or _segment_for_gap_word(word, segments)
                if target is None:
                    continue
        target.words.append(word)

    snap_segment_boundaries(segments)

    for seg in segments:
        seg.text = " ".join(w.word for w in seg.words)
    return segments


def _ends_sentence(word: str) -> bool:
    """True if `word` ends a sentence (terminal punctuation, ignoring quotes)."""
    stripped = word.rstrip('"”’\')')
    return stripped.endswith((".", "?", "!"))


def _move(words: list[Word], src: Segment, dst: Segment) -> bool:
    """Move `words` out of src and into dst, keeping dst chronological.
    Returns True (a move happened) for the caller's fixpoint bookkeeping."""
    moved = set(id(w) for w in words)
    src.words = [w for w in src.words if id(w) not in moved]
    dst.words = sorted(dst.words + words, key=lambda w: w.start)
    return True


def _snap_leading(a: Segment, b: Segment) -> bool:
    """Move B's leading fragment (a completed tail of A's sentence bled into the
    front of B) back onto A. Uses the '>>' marker when present, else an acoustic
    pause + terminal-punctuation signal. Returns True if a word moved."""
    if not a.words or len(b.words) < 2:
        return False

    tokens = [w.word for w in b.words]
    if SPEAKER_MARKER in tokens:
        m = tokens.index(SPEAKER_MARKER)
        # A leading bleed is a short, sentence-completing run sitting before an
        # EARLY marker; a marker far inside B is B's own speech, not a fragment.
        if not (0 < m <= MAX_FRAGMENT_WORDS):
            return False
        if not _ends_sentence(b.words[m - 1].word):
            return False
        if len(b.words) - m < 2:   # keep the marker plus real content in B
            return False
        return _move(b.words[:m], b, a)

    # No marker: a bled word butts against A (near-zero gap) and is cut off from
    # B's own content by a real pause; a word that opens B after a turn-change
    # silence has a large gap_before and stays put.
    w0, w1 = b.words[0], b.words[1]
    if not _ends_sentence(w0.word):
        return False
    gap_before = w0.start - a.words[-1].end
    gap_after = w1.start - w0.end
    # A negative gap_before means A's span extends past w0 (overlapping turns,
    # e.g. a clerk's roll call overlapping a member's "Yes." vote). The word is
    # buried inside A's speech, not trailing off it — not a bleed, leave it.
    if (
        0 <= gap_before <= MAX_CONTINUATION_GAP
        and gap_after >= MIN_BLEED_PAUSE
        and gap_after > gap_before
    ):
        return _move([w0], b, a)
    return False


def _snap_trailing(a: Segment, b: Segment) -> bool:
    """Move A's trailing fragment (the next speaker's opening captured at the end
    of A after a late '>>') forward onto the front of B. Returns True if moved."""
    if len(a.words) < 2:
        return False
    tokens = [w.word for w in a.words]
    marker_idxs = [i for i, t in enumerate(tokens) if t == SPEAKER_MARKER]
    if not marker_idxs:
        return False
    k = marker_idxs[-1]              # last marker = candidate trailer
    if k == 0:                       # marker opens A — A's own turn, not a bleed
        return False
    if len(a.words) - k - 1 > MAX_FRAGMENT_WORDS:   # too long to be an opening
        return False
    # Only a clean single boundary: an optional opener at index 0 plus this one
    # trailer. A marker strictly between them means A is an under-segmented blob
    # (a merged rapid exchange); peeling its pieces one per pass into a single
    # neighbour would misattribute them, so leave resegmentation to another stage.
    if any(0 < i < k for i in marker_idxs):
        return False
    return _move(a.words[k:], a, b)


def _norm(word: str) -> str:
    """`word` reduced to lowercase letters and apostrophes, for cue matching."""
    return "".join(c for c in word.lower() if c.isalpha() or c == "'")


def _intro_cue_index(words: list[Word]) -> int | None:
    """Index of the first self-introduction cue that does NOT open the turn.

    A cue at index 0 is the speaker introducing themselves at the start of
    their own turn, which is the overwhelmingly common case and never a bleed.
    """
    tokens = [_norm(w.word) for w in words]
    for i in range(1, len(tokens)):
        if any(tokens[i:i + len(cue)] == list(cue) for cue in INTRO_CUES):
            return i
    return None


def _snap_trailing_intro(a: Segment, b: Segment) -> bool:
    """Move a trailing self-introduction off A and onto the front of B.

    Where diarization misses a stretch of speech outright, _segment_for_gap_word
    snaps the whole un-diarized gap onto the preceding turn, so a chair's turn
    ends with the next speaker's opening — "...next person in chambers Thank
    you, my name is Paul Gillard I'm" published as the councilmember's own
    words. Neither existing path sees these: they carry no '>>' marker, and in
    livestream sources whose word timings are interpolated there is no pause and
    no terminal punctuation at the true split.

    The signal that does locate them is the introduction itself, admitted only
    when the cue lies OUTSIDE A's own diarized span — the gap-snap fallback put
    it there, no overlap with the turn did. Both halves of that test are needed:
    across the 172-meeting corpus 7,367 segments carry a word past their own
    end_time and 141 carry a non-leading introduction, but only 7 carry both.
    An introduction inside the turn's own span is real speech and stays, which
    is what keeps this rule off ASR spelling variants of the same person
    ("Bob Costello" transcribed "Bob Gasillo").

    Deliberately unbounded in length (these fragments run to 7 words, past
    MAX_FRAGMENT_WORDS) because the length cap is replaced by the semantic gate
    rather than relaxed — raising MAX_FRAGMENT_WORDS would loosen the marker
    paths for every segment in the corpus. Returns True if a word moved.
    """
    if not b.words:
        # No real destination turn. Publish drops empty segments, so moving
        # words here would turn a dropped turn into a live attribution.
        return False
    cue = _intro_cue_index(a.words)
    if cue is None:
        return False
    if a.words[cue].start <= a.end_time:
        return False
    # Take the greeting with the introduction when a sentence ends just before
    # it ("...two more people. | Hi, my name is"), else split at the cue. Only
    # terminal punctuation is trusted here: an opener word list would have to
    # be tuned on seven examples, and could take real speech off the chair.
    split = cue
    for k in range(cue, max(0, cue - MAX_INTRO_PREAMBLE) - 1, -1):
        if k > 0 and _ends_sentence(a.words[k - 1].word):
            split = k
            break
    if split < 1:
        return False   # A must keep words of its own
    return _move(a.words[split:], a, b)


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
    introducing themselves at the start of their own turn. Five gates narrow it
    to one. Differing speaker_label cuts 63 to 47. Requiring the sentence to run
    ACROSS the boundary — A's last word not sentence-final, B opening lowercase
    — cuts 47 to 11; that is the gate that spares Steve Goldstein's own
    "My name is Steve Goldstein." on 2026-06-24-cd1-republican-primary-debate,
    which ends a sentence and is followed by a '>>' marker. Requiring a sentence
    boundary within MAX_INTRO_PREAMBLE words before the cue cuts 11 to 9, and
    means the rule never cuts mid-sentence.

    Nine is not clean enough for a length cap alone. Eight of the nine are
    speakers reading a whole self-introduction of their own; the nearest one is
    Tree Martin-Lucas's 21-word closing statement on
    2026-03-30-lwv-candidate-forum---county-clerk-and-prosecutor seg 116
    ("Again, my name is Tree - Martin Lucas, and I'm running for clerk. All
    right. Thank you all so much for"), and "Again, my name is X" ending a
    forum closing statement is a common enough shape that a shorter instance
    would clear a 10-word cap. So the last gate is semantic, not metric: a tail
    that closes a sentence of its own contradicts gate 6's premise that the
    tail and B are one utterance. That alone separates 1 from 9 with no length
    threshold. MAX_INTRO_TAIL_WORDS is kept as a second line of defence.

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
    # Gate 6 asserted that the tail and B are ONE utterance running across the
    # boundary. A tail that closes a sentence of its own contradicts that
    # premise: it is the speaker's own finished speech, not a fragment bleeding
    # into the next turn. Checked before the length cap because it tests the
    # rule's structural premise rather than a tuned magnitude — the cap is then
    # a pure second line of defence over an already-coherent tail. The slice
    # excludes the LAST word, whose punctuation says nothing about internal
    # structure (and which gate 6 has already required not to end a sentence).
    # Widening MAX_INTRO_PREAMBLE is comparatively safe against this gate but not
    # provably safe. The scan above breaks on the FIRST boundary walking back
    # from the cue, so a wider window can only move the split EARLIER, which
    # lengthens the tail and makes this gate strictly more likely to reject.
    # It is not a guarantee: a longer tail carrying no terminal punctuation of
    # its own still passes. Measured counter-example, at MAX_INTRO_PREAMBLE = 8
    # (the shipped 4 rejects it at the scan-back gate above):
    #   "Right. okay next speaker in chambers Hi, my name is Dana" splits at 1
    #   and takes five of the chair's words with the introduction.
    # No corpus meeting has that shape, and real seg 454 covers the same shape
    # from cap 10 via test_trailing_self_intro_moves_off_the_chair_bloomington_june.
    if any(_ends_sentence(w.word) for w in a.words[split:-1]):
        return False   # the tail finishes a sentence: A's own speech, not a bleed
    if len(a.words) - split > MAX_INTRO_TAIL_WORDS:
        return False   # a long turn spilling its timings, not a short bleed
    return _move(a.words[split:], a, b)


def snap_segment_boundaries(segments: list[Segment]) -> list[Segment]:
    """Correct word-level speaker bleed at diarization turn boundaries.

    Diarization boundaries and ASR word timings routinely disagree by a word or
    two, so the last word of one turn lands in the next turn (or the next
    speaker's opening lands in the previous turn). This post-pass reassigns those
    straddling boundary words using cheap, non-LLM signals — the '>>' broadcast
    speaker-change marker, and word-gap pauses plus terminal punctuation — with
    no acoustic re-analysis and no model call.

    Two separately-gated rules additionally handle self-introduction bleed.
    _snap_trailing_intro takes the case where diarization missed a stretch of
    speech entirely and the gap-snap fallback dumped the next speaker's opening
    self-introduction onto the end of the previous turn.
    _snap_straddling_intro takes the weaker case where that introduction begins
    inside the previous turn's own diarized span and only the turn's trailing
    words spill past it.

    Runs to a fixpoint (bounded by MAX_SNAP_PASSES): most work is done in the
    first sweep, but where diarization is degenerate — zero-duration turns, or
    word timestamps spilling past a turn's own span — a corrected fragment can
    need to relay across another turn, so a second sweep settles it. Converging
    makes the result stable and re-runnable (a backfill can be applied twice with
    no drift).
    """
    ordered = sorted(segments, key=lambda s: s.start_time)
    for _ in range(MAX_SNAP_PASSES):
        moved = False
        for a, b in zip(ordered, ordered[1:]):
            moved |= _snap_leading(a, b)   # tail of A bled into front of B → to A
            moved |= _snap_trailing(a, b)  # opening of B at tail of A → into B
            moved |= _snap_trailing_intro(a, b)  # B's self-intro at tail of A → into B
            moved |= _snap_straddling_intro(a, b)  # B's self-intro straddling A's end → into B
        if not moved:
            break
    return segments
