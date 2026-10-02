"""Clean live CART captions (e.g. iga.in.gov) for VTT alignment.

Human real-time (CART) captions differ from YouTube auto-captions in three ways
that matter for aligning them to diarized turns instead of running Whisper:

1. ALL CAPS — sentence-cased here, plus known proper nouns (roster names).
2. ``>>`` speaker-change markers and ``CHAIRPERSON:``-style labels — removed
   from the text, but the ``>>`` times are kept: they are where turns change.
3. Offset — cue times can sit seconds off the audio: live captions trail it,
   and HLS caption timelines carry their own X-TIMESTAMP-MAP origin (the IGA
   Jan 14 2026 hearing measured ~1.5s EARLY). A constant offset is estimated
   by sliding the ``>>`` times against the diarization's speaker changes and
   keeping the shift that lines the most of them up. Positive = captions late.

Pure functions over Word / Segment lists; no I/O.
"""
from __future__ import annotations

import re
from bisect import bisect_left
from typing import Iterable, Optional

from .models import Segment, Word

_MARKER = ">>"
# A label right after ">>": "CHAIRPERSON:", "SEN. KOCH:", "SENATOR TAYLOR:".
_LABEL_MAX_TOKENS = 3
_SENTENCE_END = re.compile(r"[.?!][\"')\]]*$")
# Always capitalized regardless of position.
_ALWAYS_CAP = {"i", "i'm", "i'll", "i've", "i'd", "indiana", "senate", "senator", "senators",
               "house", "representative", "governor", "hoosier", "hoosiers", "indianapolis"}
_UPPER_KEEP = {"sb", "hb", "ic", "us", "u.s.", "fbi", "ok"}

LAG_SEARCH_MIN = -5.0   # seconds; negative = captions early
LAG_SEARCH_MAX = 10.0
LAG_STEP = 0.1
LAG_TOLERANCE = 1.0     # a marker "hits" a speaker change within this many seconds
MIN_MARKERS_FOR_LAG = 10


def looks_like_cart(words: list[Word]) -> bool:
    """Mostly-uppercase text with >> turn markers."""
    tokens = [w.word for w in words if any(c.isalpha() for c in w.word)]
    if not tokens or not any(w.word.startswith(_MARKER) for w in words):
        return False
    upper = sum(1 for t in tokens if t == t.upper())
    return upper / len(tokens) >= 0.9


def _is_label_token(tok: str) -> bool:
    return tok.replace(".", "").replace("'", "").isalpha()


def strip_markers(words: list[Word]) -> tuple[list[Word], list[float], list[int]]:
    """(words without >> and labels, time of each >> marker, index in the
    cleaned list of the first word of each marked turn)."""
    # Split a glued marker (">>THANK") into its own token first.
    tokens: list[Word] = []
    for w in words:
        if w.word.startswith(_MARKER) and len(w.word) > len(_MARKER):
            tokens.append(Word(word=_MARKER, start=w.start, end=w.start))
            tokens.append(Word(word=w.word[len(_MARKER):], start=w.start, end=w.end))
        else:
            tokens.append(w)

    out: list[Word] = []
    marks: list[float] = []
    turn_idx: list[int] = []
    i = 0
    while i < len(tokens):
        if tokens[i].word != _MARKER:
            out.append(tokens[i])
            i += 1
            continue
        marks.append(tokens[i].start)
        turn_idx.append(len(out))
        i += 1
        # Drop a short "LABEL:" (1-3 tokens ending in ':') right after the marker.
        window = tokens[i:i + _LABEL_MAX_TOKENS]
        for k, w in enumerate(window):
            if not _is_label_token(w.word.rstrip(":")):
                break
            if w.word.endswith(":"):
                i += k + 1
                break
    return out, marks, turn_idx


def speaker_change_times(segments: Iterable[Segment]) -> list[float]:
    times = []
    prev = None
    for s in sorted(segments, key=lambda s: s.start_time):
        if prev is not None and s.speaker_label != prev:
            times.append(s.start_time)
        prev = s.speaker_label
    return times


def estimate_lag(marks: list[float], changes: list[float]) -> Optional[float]:
    """Constant caption lag (s) that best aligns markers to speaker changes.

    None when there are too few markers or no shift beats zero by a margin —
    then no correction is applied.
    """
    if len(marks) < MIN_MARKERS_FOR_LAG or not changes:
        return None
    changes = sorted(changes)

    def score(lag: float) -> tuple[int, float]:
        """(markers within tolerance of a change, -mean distance of those)."""
        n, dist = 0, 0.0
        for m in marks:
            t = m - lag
            k = bisect_left(changes, t)
            near = [abs(changes[x] - t) for x in (k - 1, k) if 0 <= x < len(changes)]
            if near and min(near) <= LAG_TOLERANCE:
                n += 1
                dist += min(near)
        return n, -(dist / n) if n else 0.0

    lo, hi = round(LAG_SEARCH_MIN / LAG_STEP), round(LAG_SEARCH_MAX / LAG_STEP)
    # Hits first; within a plateau of equal hits, the tightest fit (its centre).
    scored = [(*score(i * LAG_STEP), i * LAG_STEP) for i in range(lo, hi + 1)]
    best_hits, _, best_lag = max(scored)
    if best_hits <= score(0.0)[0] * 1.1:
        return None
    return round(best_lag, 2)


def shift(words: list[Word], lag: float) -> None:
    for w in words:
        w.start = max(0.0, round(w.start - lag, 3))
        w.end = max(0.0, round(w.end - lag, 3))


def anchor_pairs(marks: list[float], changes: list[float],
                 tolerance: float = LAG_TOLERANCE) -> list[tuple[float, float]]:
    """(marker_time, speaker_change_time) pairs, strictly increasing in both.

    Each marker is paired with its nearest speaker change within ``tolerance``;
    a change claimed twice, or a pair that would run backwards, is dropped.
    """
    changes = sorted(changes)
    pairs: list[tuple[float, float]] = []
    used: set[int] = set()
    for m in sorted(marks):
        k = bisect_left(changes, m)
        cands = [x for x in (k - 1, k) if 0 <= x < len(changes)]
        if not cands:
            continue
        best = min(cands, key=lambda x: abs(changes[x] - m))
        if abs(changes[best] - m) > tolerance or best in used:
            continue
        if pairs and (m <= pairs[-1][0] or changes[best] <= pairs[-1][1]):
            continue
        used.add(best)
        pairs.append((m, changes[best]))
    return pairs


def warp(words: list[Word], pairs: list[tuple[float, float]]) -> None:
    """Piecewise-linear retime so each marker lands exactly on its paired
    speaker change: words before a marker end at/before the change, words
    after it start at/after. Outside the anchors, times shift by the nearest
    anchor's delta. In place; order-preserving."""
    if not pairs:
        return
    src = [p[0] for p in pairs]
    dst = [p[1] for p in pairs]

    def f(t: float) -> float:
        if t <= src[0]:
            return t + (dst[0] - src[0])
        if t >= src[-1]:
            return t + (dst[-1] - src[-1])
        k = bisect_left(src, t)
        if src[k] == t:
            return dst[k]
        a, b = k - 1, k
        frac = (t - src[a]) / (src[b] - src[a])
        return dst[a] + frac * (dst[b] - dst[a])

    for w in words:
        w.start = max(0.0, round(f(w.start), 3))
        w.end = max(w.start, round(f(w.end), 3))


def sentence_case(words: list[Word], turn_idx: Iterable[int], proper_nouns: Iterable[str] = ()) -> None:
    """Lowercase CART words, then capitalize sentence starts, turn starts and
    known proper nouns. In place."""
    names = {n.casefold() for n in proper_nouns if n}
    turns = set(turn_idx)
    cap_next = True
    for idx, w in enumerate(words):
        raw = w.word
        if any(c.islower() for c in raw):  # already mixed case: leave it
            cap_next = bool(_SENTENCE_END.search(raw))
            continue
        lower = raw.lower()
        core = re.sub(r"^\W+|\W+$", "", lower)
        at_turn = idx in turns
        if core in _UPPER_KEEP:
            word = lower.replace(core, core.upper(), 1)
        elif cap_next or at_turn or core in _ALWAYS_CAP or core in names:
            word = _cap_first(lower)
        else:
            word = lower
        w.word = word
        cap_next = bool(_SENTENCE_END.search(raw))


def _cap_first(s: str) -> str:
    for i, c in enumerate(s):
        if c.isalpha():
            return s[:i] + c.upper() + s[i + 1:]
    return s


def clean_cart_words(
    words: list[Word],
    segments: list[Segment],
    proper_nouns: Iterable[str] = (),
) -> tuple[list[Word], Optional[float]]:
    """Full CART cleanup. Returns (clean words, lag applied or None)."""
    clean, marks, turn_idx = strip_markers(words)
    changes = speaker_change_times(segments)
    lag = estimate_lag(marks, changes)
    if lag:
        shift(clean, lag)  # lag < 0 moves words later
        marks = [max(0.0, round(m - lag, 3)) for m in marks]
        # Only after a confident global offset: pin each marker to its turn.
        pairs = anchor_pairs(marks, changes)
        warp(clean, pairs)
    sentence_case(clean, turn_idx, proper_nouns)
    return clean, lag
