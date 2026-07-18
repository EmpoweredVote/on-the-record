# src/crec_align.py
"""Phase 3: align identity-annotated CREC turns onto anonymous diarized labels.

Given diarized segments (verbatim ASR/caption text + nameless speaker_labels)
and CREC turns annotated with identities (Phase 2 annotate_turns), align the two
ordered sequences by content-word overlap under a monotonic (order-preserving)
constraint, then aggregate per speaker_label to a resolved identity. Attaches
identity only — never touches timestamps/words (ADR-0001). Pure; no network.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import Optional

from .congress_roster import CongressMember

_STOPWORDS = frozenset("""
the a an and or but of to in on at for with as is are was were be been being
i you he she it we they that this these those my your his her its our their
will would shall should can could may might must do does did have has had not
""".split())

# A backtracked diagonal only counts as a real match above this overlap floor.
_MATCH_FLOOR = 0.1


def _content_tokens(text: str) -> set[str]:
    """Lowercased content-word set: drop punctuation, stopwords, tokens < 3 chars."""
    toks = re.findall(r"[a-z0-9']+", (text or "").lower())
    return {t for t in toks if len(t) >= 3 and t not in _STOPWORDS}


def _overlap(a: set, b: set) -> float:
    """Overlap coefficient: |a∩b| / min(|a|,|b|); 0.0 if either side is empty."""
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


@dataclass
class DiarizedTurn:
    speaker_label: str
    text: str
    index: int


def _build_diarized_turns(segments) -> list[DiarizedTurn]:
    """Group consecutive segments sharing a speaker_label into maximal runs.

    Each run's text is its segments' text joined with spaces. Segment timestamps
    are intentionally not carried — Phase 3 attaches identity only.
    """
    turns: list[DiarizedTurn] = []
    for seg in segments:
        txt = (seg.text or "").strip()
        if turns and turns[-1].speaker_label == seg.speaker_label:
            turns[-1].text = f"{turns[-1].text} {txt}".strip()
        else:
            turns.append(DiarizedTurn(speaker_label=seg.speaker_label, text=txt, index=len(turns)))
    return turns


def _align(d_tokens: list[set], c_tokens: list[set]) -> list[tuple[int, int]]:
    """Monotonic LCS-style alignment of two token-set sequences.

    Maximizes total matched overlap, order-preserving and non-crossing, with free
    gaps on both sides. Returns matched (d_index, c_index) pairs whose overlap
    exceeds `_MATCH_FLOOR`.
    """
    m, n = len(d_tokens), len(c_tokens)
    dp = [[0.0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            diag = dp[i - 1][j - 1] + _overlap(d_tokens[i - 1], c_tokens[j - 1])
            dp[i][j] = max(dp[i - 1][j], dp[i][j - 1], diag)

    pairs: list[tuple[int, int]] = []
    i, j = m, n
    while i > 0 and j > 0:
        sim = _overlap(d_tokens[i - 1], c_tokens[j - 1])
        diag = dp[i - 1][j - 1] + sim
        if diag >= dp[i - 1][j] and diag >= dp[i][j - 1]:
            if sim > _MATCH_FLOOR:
                pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif dp[i - 1][j] >= dp[i][j - 1]:
            i -= 1
        else:
            j -= 1
    pairs.reverse()
    return pairs
