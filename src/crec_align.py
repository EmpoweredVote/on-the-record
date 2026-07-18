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
from dataclasses import dataclass
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


@dataclass
class LabelResolution:
    speaker_label: str
    member: Optional[CongressMember] = None
    role: Optional[str] = None
    confidence: float = 0.0
    method: str = "unresolved"   # congressional_record | ambiguous | unresolved
    needs_review: bool = False
    matched_turns: int = 0
    total_turns: int = 0


def _confidence(match_fraction: float, vote_fraction: float, mean_overlap: float) -> float:
    """Product of the three 0..1 support factors.

    Isolated on purpose: this formula is expected to be retuned after real-data
    testing, without touching the alignment logic.
    """
    return match_fraction * vote_fraction * mean_overlap


def _aggregate(d_turns, matches, min_confidence: float) -> dict:
    """Aggregate per-label from matched (label, ResolvedSpeaker, overlap) records.

    `matches` is a list of (speaker_label, ResolvedSpeaker, overlap). Majority-vote
    among member identities per label; role-only labels resolve to a role; no
    matches -> unresolved. A tie or sub-gate confidence -> ambiguous/needs_review
    with member=None (member is set only when confident).
    """
    total_by_label = Counter(t.speaker_label for t in d_turns)
    by_label: dict[str, list] = defaultdict(list)
    for label, resolved, ov in matches:
        by_label[label].append((resolved, ov))

    out: dict[str, LabelResolution] = {}
    for label, total in total_by_label.items():
        recs = by_label.get(label, [])
        member_recs = [(r, ov) for r, ov in recs if r.member is not None]
        role_recs = [(r, ov) for r, ov in recs if r.member is None and r.role is not None]

        if member_recs:
            votes = Counter(r.member.bioguide for r, _ in member_recs)
            ranked = votes.most_common()
            winner_bio, winner_votes = ranked[0]
            tie = len(ranked) > 1 and ranked[1][1] == winner_votes
            winner_ovs = [ov for r, ov in member_recs if r.member.bioguide == winner_bio]
            mean_ov = sum(winner_ovs) / len(winner_ovs)
            matched = len(member_recs)
            conf = _confidence(matched / total, winner_votes / matched, mean_ov)
            if not tie and conf >= min_confidence:
                winner_member = next(
                    r.member for r, _ in member_recs if r.member.bioguide == winner_bio)
                out[label] = LabelResolution(
                    speaker_label=label, member=winner_member, confidence=conf,
                    method="congressional_record", needs_review=False,
                    matched_turns=matched, total_turns=total)
            else:
                out[label] = LabelResolution(
                    speaker_label=label, member=None, confidence=conf,
                    method="ambiguous", needs_review=True,
                    matched_turns=matched, total_turns=total)
        elif role_recs:
            role_votes = Counter(r.role for r, _ in role_recs).most_common()
            winner_role, winner_votes = role_votes[0]
            role_tie = len(role_votes) > 1 and role_votes[1][1] == winner_votes
            if role_tie:
                # conflicting roles on one label -> flag, mirror the member guard
                out[label] = LabelResolution(
                    speaker_label=label, confidence=len(role_recs) / total,
                    method="ambiguous", needs_review=True,
                    matched_turns=len(role_recs), total_turns=total)
            else:
                out[label] = LabelResolution(
                    speaker_label=label, role=winner_role, confidence=len(role_recs) / total,
                    method="congressional_record", needs_review=False,
                    matched_turns=len(role_recs), total_turns=total)
        else:
            out[label] = LabelResolution(
                speaker_label=label, method="unresolved",
                matched_turns=0, total_turns=total)
    return out


def align_crec_to_diarization(
    segments,
    annotated_turns,
    *,
    min_confidence: float = 0.5,
) -> dict:
    """Resolve each diarized speaker_label to a CREC identity.

    `segments`: diarized, time-ordered Segments (speaker_label + ASR/caption text).
    `annotated_turns`: [(CrecTurn, ResolvedSpeaker), ...] from Phase 2 annotate_turns.
    Returns {speaker_label: LabelResolution}. Attaches identity only.
    """
    d_turns = _build_diarized_turns(segments)
    d_tokens = [_content_tokens(t.text) for t in d_turns]
    c_tokens = [_content_tokens(ct.text) for ct, _ in annotated_turns]

    pairs = _align(d_tokens, c_tokens)
    matches = []
    for d_idx, c_idx in pairs:
        label = d_turns[d_idx].speaker_label
        resolved = annotated_turns[c_idx][1]
        matches.append((label, resolved, _overlap(d_tokens[d_idx], c_tokens[c_idx])))

    return _aggregate(d_turns, matches, min_confidence)
