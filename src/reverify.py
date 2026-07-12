"""Scoped acoustic re-verification of short "wedged" turns.

Whisper transcribes each diarized turn's audio slice independently, so a short
turn placed (by diarization) inside a longer continuous run of another speaker
will steal whatever word dominates that slice — e.g. the listener's faint
"yeah" turn captures the interviewee's "abortion". This module re-checks only
those short wedged turns acoustically: it embeds the turn's audio and compares
it to the two neighbouring speakers' voice centroids, proposing reassignment
when the turn's voice clearly matches the interrupted (dominant) speaker.

Legitimate short interjections (roll-call "Here.", a vote "Second.") match the
interrupter's own voice and are left untouched.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np
from scipy.spatial.distance import cosine

from .models import Segment

# Defaults tuned from the cross-meeting measurement (see docs/notes).
DUR_MAX = 0.7      # seconds: a "short" turn
WORDS_MAX = 2      # at most this many words
GAP_MAX = 1.0      # seconds: neighbour must be this close to count as one run
MARGIN = 0.10      # min cosine-similarity lead before reassigning
MIN_EMBED_DUR = 0.3  # seconds: shortest slice the embedder treats as reliable


@dataclass
class Proposal:
    """A re-verification verdict for one wedged turn (no mutation applied)."""

    index: int
    text: str
    interrupter_label: str
    dominant_label: str
    decision: str  # "dominant" | "interrupter" | "ambiguous" | "unembeddable"
    action: str    # "reassign" | "keep" | "flag"
    sim_dominant: Optional[float] = None
    sim_interrupter: Optional[float] = None


def decide_speaker(
    turn_emb: np.ndarray,
    dominant_emb: np.ndarray,
    interrupter_emb: np.ndarray,
    *,
    margin: float = MARGIN,
) -> str:
    """Classify whose voice a wedged turn belongs to.

    Compares the turn's embedding to the interrupted (dominant) speaker's
    centroid and the interrupter's own centroid by cosine similarity.

    Returns ``"dominant"`` if the turn clearly matches the interrupted speaker
    (its word was stolen and should be reassigned), ``"interrupter"`` if it
    matches the interrupter's own voice (a legitimate interjection), or
    ``"ambiguous"`` when the two similarities are within ``margin``.
    """
    sim_dom = 1.0 - cosine(turn_emb, dominant_emb)
    sim_int = 1.0 - cosine(turn_emb, interrupter_emb)
    if sim_dom - sim_int > margin:
        return "dominant"
    if sim_int - sim_dom > margin:
        return "interrupter"
    return "ambiguous"


def find_wedged_turns(
    segments: list[Segment],
    *,
    dur_max: float = DUR_MAX,
    words_max: int = WORDS_MAX,
    gap_max: float = GAP_MAX,
) -> list[int]:
    """Return indices of short turns wedged inside another speaker's run.

    A segment qualifies when both neighbours share a speaker label different
    from its own, the gaps to both neighbours are <= gap_max, its duration is
    < dur_max, and it carries 1..words_max words.
    """
    wedged: list[int] = []
    for i in range(1, len(segments) - 1):
        prev, cur, nxt = segments[i - 1], segments[i], segments[i + 1]
        if prev.speaker_label != nxt.speaker_label:
            continue
        if cur.speaker_label == prev.speaker_label:
            continue
        if cur.start_time - prev.end_time > gap_max:
            continue
        if nxt.start_time - cur.end_time > gap_max:
            continue
        if cur.end_time - cur.start_time >= dur_max:
            continue
        if not (0 < len(cur.words) <= words_max):
            continue
        wedged.append(i)
    return wedged


def reverify(
    segments: list[Segment],
    centroids: dict[str, np.ndarray],
    embed_fn: Callable[[float, float], Optional[np.ndarray]],
    *,
    dur_max: float = DUR_MAX,
    words_max: int = WORDS_MAX,
    gap_max: float = GAP_MAX,
    margin: float = MARGIN,
    min_embed_dur: float = MIN_EMBED_DUR,
) -> list[Proposal]:
    """Acoustically re-verify each wedged turn and return proposals (no mutation).

    ``embed_fn(start, end)`` returns the turn's voice embedding, or ``None`` if
    the slice is too short/unreliable to embed. Turns shorter than
    ``min_embed_dur`` are flagged for human review rather than auto-decided.
    """
    proposals: list[Proposal] = []
    for i in find_wedged_turns(
        segments, dur_max=dur_max, words_max=words_max, gap_max=gap_max
    ):
        cur = segments[i]
        dominant_label = segments[i - 1].speaker_label
        interrupter_label = cur.speaker_label

        base = dict(
            index=i,
            text=cur.text,
            interrupter_label=interrupter_label,
            dominant_label=dominant_label,
        )

        # Can't compare voices without both centroids.
        if dominant_label not in centroids or interrupter_label not in centroids:
            proposals.append(Proposal(decision="unembeddable", action="flag", **base))
            continue

        # Too short to embed reliably -> defer to human review.
        if cur.end_time - cur.start_time < min_embed_dur:
            proposals.append(Proposal(decision="unembeddable", action="flag", **base))
            continue

        emb = embed_fn(cur.start_time, cur.end_time)
        if emb is None:
            proposals.append(Proposal(decision="unembeddable", action="flag", **base))
            continue

        dom = centroids[dominant_label]
        intr = centroids[interrupter_label]
        decision = decide_speaker(emb, dom, intr, margin=margin)
        action = {
            "dominant": "reassign",
            "interrupter": "keep",
            "ambiguous": "flag",
        }[decision]
        proposals.append(
            Proposal(
                decision=decision,
                action=action,
                sim_dominant=float(1.0 - cosine(emb, dom)),
                sim_interrupter=float(1.0 - cosine(emb, intr)),
                **base,
            )
        )
    return proposals


def apply_proposals(segments: list[Segment], proposals: list[Proposal]) -> int:
    """Relabel wedged turns whose proposal is ``reassign``. Returns the count.

    The reassigned segment is relabelled to the interrupted (dominant) speaker
    and stamped with ``id_method = "acoustic_reverify"`` for provenance. A later
    same-speaker merge pass naturally stitches it back into the dominant run.
    """
    applied = 0
    for p in proposals:
        if p.action != "reassign":
            continue
        seg = segments[p.index]
        seg.speaker_label = p.dominant_label
        seg.id_method = "acoustic_reverify"
        applied += 1
    return applied
