"""Attach transcript timestamps to CREC roll-call votes (Slice 2, Federal adapter).

The presiding officer announces each recorded vote's result verbatim — "the yeas
are 236, the nays are 193, the amendment is adopted" — and the pipeline's
transcript carries word-level timestamps. We extract those announcements (tally +
timestamp) and monotonically match them to the Slice-1 RollCallVote objects by
tally. ASR mis-hears a digit occasionally (observed: 230 vs the true 231), so the
match tolerates a small delta and relies on chronological order. Pure; no network.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

from .crec_votes import RollCallVote

_ANNOUNCE_RE = re.compile(
    r"(?:yeas|ayes)\s+are\s+(\d+)[,.\s]+(?:and\s+)?the\s+nays\s+are\s+(\d+)", re.I)
_ANCHOR_WORD_RE = re.compile(r"^(yeas|ayes)\b", re.I)


@dataclass
class VoteAnnouncement:
    yea: int
    nay: int
    timestamp: float
    text: str


def _announcement_timestamp(segment: dict) -> float:
    """Word-level start of the 'yeas'/'ayes' token; falls back to the segment start."""
    for w in segment.get("words") or []:
        token = str(w.get("word", "")).strip().lower()
        if _ANCHOR_WORD_RE.match(token) and isinstance(w.get("start"), (int, float)):
            return float(w["start"])
    return float(segment.get("start_time") or 0.0)


def extract_announcements(segments: list) -> list[VoteAnnouncement]:
    """Vote-result announcements (tally + timestamp) from transcript segments, in order."""
    out: list[VoteAnnouncement] = []
    for seg in segments:
        m = _ANNOUNCE_RE.search(seg.get("text") or "")
        if not m:
            continue
        out.append(VoteAnnouncement(
            yea=int(m.group(1)),
            nay=int(m.group(2)),
            timestamp=_announcement_timestamp(seg),
            text=(seg.get("text") or "").strip(),
        ))
    return out


@dataclass
class VoteTiming:
    roll_number: int
    timestamp: Optional[float]
    tally_delta: Optional[int]
    matched: bool


def _expected_tally(rc: RollCallVote) -> tuple[int, int]:
    return len(rc.positions.get("YEA", [])), len(rc.positions.get("NAY", []))


def match_rolls_to_announcements(
    rolls: list, announcements: list, *, tol: int = 3
) -> list:
    """Match each roll (in roll order) to the next chronological announcement whose
    tally is within `tol` (|Δyea|+|Δnay|). Monotonic: once consumed, later rolls
    only see later announcements — a spurious announcement is skipped, a missing one
    leaves its roll unmatched. Tolerance absorbs ASR digit mis-hears (230 vs 231).
    """
    results: list = []
    ai = 0
    for rc in rolls:
        ey, en = _expected_tally(rc)
        matched = None
        j = ai
        while j < len(announcements):
            a = announcements[j]
            delta = abs(a.yea - ey) + abs(a.nay - en)
            if delta <= tol:
                matched = (j, a, delta)
                break
            j += 1
        if matched is not None:
            j, a, delta = matched
            results.append(VoteTiming(rc.roll_number, a.timestamp, delta, True))
            ai = j + 1
        else:
            results.append(VoteTiming(rc.roll_number, None, None, False))
    return results


def attach_vote_timestamps(rolls: list, transcript_segments: list, *, tol: int = 3) -> list:
    """Extract announcements, match to `rolls`, set each matched RollCallVote.timestamp
    in place; return the VoteTiming list. `rolls` is a flat, roll-order list from a
    Slice-1 FloorStructure: [rc for gv in floor_structure.votes for rc in gv.votes].
    """
    announcements = extract_announcements(transcript_segments)
    timings = match_rolls_to_announcements(rolls, announcements, tol=tol)
    for rc, timing in zip(rolls, timings):
        rc.timestamp = timing.timestamp
    return timings
