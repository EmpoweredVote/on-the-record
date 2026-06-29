"""Assign a chronological word stream to diarized segments by timestamp.

Shared by the VTT alignment path and the whole-audio Whisper path. The strategy
(proven in the former vtt_align implementation): assign each word to the segment
whose span contains the word midpoint; fall back to the segment of greatest
temporal overlap; finally snap a zero-overlap word that lands in an
inter-segment gap to the preceding turn (otherwise drop it).
"""

from __future__ import annotations

from .models import Segment, Word


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
        target = next(
            (s for s in segments if s.start_time <= midpoint < s.end_time),
            None,
        )
        if target is None:
            candidates = [
                (_overlap(s.start_time, s.end_time, word.start, word.end), s)
                for s in segments
            ]
            overlap_dur, target = max(candidates, key=lambda item: item[0])
            if overlap_dur <= 0:
                target = _segment_for_gap_word(word, segments)
                if target is None:
                    continue
        target.words.append(word)

    for seg in segments:
        seg.text = " ".join(w.word for w in seg.words)
    return segments
