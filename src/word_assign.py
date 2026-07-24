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
terminal punctuation).
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


def snap_segment_boundaries(segments: list[Segment]) -> list[Segment]:
    """Correct word-level speaker bleed at diarization turn boundaries.

    Diarization boundaries and ASR word timings routinely disagree by a word or
    two, so the last word of one turn lands in the next turn (or the next
    speaker's opening lands in the previous turn). This post-pass reassigns those
    straddling boundary words using cheap, non-LLM signals — the '>>' broadcast
    speaker-change marker, and word-gap pauses plus terminal punctuation — with
    no acoustic re-analysis and no model call.

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
        if not moved:
            break
    return segments
