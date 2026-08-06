"""Pure scoring for the summary section-classification replay eval (no
filesystem, no network — scores stored gold sections from an accepted
summary.json against a replayed classify_sections()/_classify_sections_interview()
output). Kept separate from scripts/eval_summary_classify.py so it is
unit-testable with fakes.

Schema note: gold sections (read from summary.json, i.e. SummarySection.to_dict())
key the section type as "section_type"; raw candidate sections (the list of
dicts classify_sections()/_classify_sections_interview() return, straight off
the model's JSON, before they're wrapped into SummarySection) key it as "type".
label_segments() takes the key name explicitly so callers don't mix them up.

Boundary-overlap convention: real summary.json data shows adjacent sections
occasionally sharing one boundary segment (section A's end_segment == section
B's start_segment). label_segments() resolves this by letting later sections
in list order win — applied identically to gold and candidate labeling, so
overlap doesn't bias agreement in either direction.
"""
from __future__ import annotations

from typing import Optional


def label_segments(
    sections: list[dict],
    valid_ids: Optional[set],
    type_key: str,
) -> dict:
    """segment_id -> section type, built by walking `sections` in order and
    filling in every id in [start_segment, end_segment] for each one. Later
    sections win at overlapping boundaries (list order = precedence).

    valid_ids: restrict output to these ids (a specific meeting's actually-
    classified segment population); pass None to skip filtering.
    A section missing/non-numeric start_segment/end_segment, or with
    end_segment < start_segment, is skipped rather than raising.
    """
    labels: dict = {}
    for sec in sections:
        try:
            start = int(sec["start_segment"])
            end = int(sec["end_segment"])
        except (KeyError, TypeError, ValueError):
            continue
        if end < start:
            continue
        sec_type = sec.get(type_key) or "unknown"
        for sid in range(start, end + 1):
            if valid_ids is not None and sid not in valid_ids:
                continue
            labels[sid] = sec_type
    return labels


def gold_sections_valid(gold_sections: list[dict], valid_ids: set) -> tuple:
    """(True, "") when every gold section's start/end_segment is a real
    segment id in THIS meeting's current classified population; otherwise
    (False, reason).

    Guards against a real corpus hazard: backfill_segment_merge.py renumbers
    transcript_named.json's segments (and its embedded summary copy) in
    place, but never rewrites the standalone summary.json — so a subset of
    meetings have an accepted summary.json whose start_segment/end_segment
    values no longer index into the transcript we'd be replaying against.
    Scoring against stale boundaries would silently measure nothing; callers
    should skip the meeting instead.
    """
    if not gold_sections:
        return False, "no gold sections"
    for sec in gold_sections:
        start, end = sec.get("start_segment"), sec.get("end_segment")
        if start is None or end is None:
            return False, "gold section missing start_segment/end_segment"
        if start not in valid_ids or end not in valid_ids:
            return False, (
                f"gold segment range [{start},{end}] outside this transcript's "
                "current segment ids (stale — likely un-republished after a "
                "segment-renumbering backfill)"
            )
    return True, ""


def score_meeting(
    gold_sections: list[dict],
    candidate_sections: list[dict],
    valid_ids: set,
    parse_failures: int = 0,
) -> dict:
    """Score one meeting's replayed classification against its gold sections.

    Callers should only call this after gold_sections_valid() has passed for
    the meeting — this function doesn't re-check.
    """
    gold_labels = label_segments(gold_sections, valid_ids, "section_type")
    cand_labels = label_segments(candidate_sections, valid_ids, "type")
    n = len(gold_labels)
    # Denominator is gold-covered segments only: a candidate labeling segments
    # OUTSIDE gold's coverage is not penalized here by design — that's an
    # over-segmentation/coverage-drift signal, and section_count_delta below
    # is what's meant to catch it, not this per-segment agreement ratio.
    agree = sum(1 for sid, label in gold_labels.items() if cand_labels.get(sid) == label)
    agreement = agree / n if n else None
    return {
        "n_segments": n,
        "agree": agree,
        "agreement": agreement,
        "gold_sections": len(gold_sections),
        "candidate_sections": len(candidate_sections),
        "section_count_delta": len(candidate_sections) - len(gold_sections),
        "parse_failures": parse_failures,
    }


def aggregate(model: str, meeting_rows: list[dict]) -> dict:
    """Combine per-meeting score_meeting() dicts into one model-level row.

    label_agreement is weighted by segment count (total agreeing segments /
    total scored segments across all meetings), not a plain mean of each
    meeting's agreement — a 300-segment meeting should count more than a
    12-segment one.
    """
    n_meetings = len(meeting_rows)
    total_segments = sum(r["n_segments"] for r in meeting_rows)
    total_agree = sum(r["agree"] for r in meeting_rows)
    agreement = total_agree / total_segments if total_segments else None
    avg_delta = (
        sum(r["section_count_delta"] for r in meeting_rows) / n_meetings
        if n_meetings else None
    )
    parse_failures = sum(r["parse_failures"] for r in meeting_rows)
    return {
        "model": model,
        "meetings": n_meetings,
        "segments": total_segments,
        "label_agreement": agreement,
        "avg_section_count_delta": avg_delta,
        "parse_failures": parse_failures,
    }
