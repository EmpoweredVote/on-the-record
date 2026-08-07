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

Two independent scores, because one of them goes blind on most of the corpus:

  score_meeting()["agreement"]  — per-segment section-TYPE match. Silent on
  where boundaries fall, and structurally CONSTANT on interview-kind meetings,
  whose prompt (_classify_sections_interview) offers a single type, "topic".
  Measured 2026-08-07: 99 of 149 corpus meetings are interview-kind, and a
  model that collapsed one meeting's 6 gold topics into 1 section still scored
  agreement=1.00.

  score_meeting()["boundary_f1"] — placement of section starts, scored
  independently of labels, so it stays informative exactly where agreement
  does not. Precision and recall are reported alongside because they name the
  failure: low recall = missed boundaries (under-segmentation), low precision
  = invented ones.

Callers should report these split by whether the meeting is interview-kind;
scripts/eval_summary_classify.py's REPORT_GROUPS does this.
"""
from __future__ import annotations

from bisect import bisect_left
from typing import Optional

# A boundary landing one scored segment away from gold is not a real
# segmentation error — turn granularity is finer than topic granularity.
DEFAULT_BOUNDARY_TOLERANCE = 1


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


def gold_sections_valid(gold_sections: list[dict], all_segment_ids: set) -> tuple:
    """(True, "") when every gold section's start/end_segment is a real segment id
    in THIS meeting's current transcript AND names a forward range; otherwise
    (False, reason).

    Guards against a real corpus hazard: backfill_segment_merge.py renumbers
    transcript_named.json's segments (and its embedded summary copy) in
    place, but never rewrites the standalone summary.json — so a subset of
    meetings have an accepted summary.json whose start_segment/end_segment
    values no longer index into the transcript we'd be replaying against.
    Scoring against stale boundaries would silently measure nothing; callers
    should skip the meeting instead.

    all_segment_ids is EVERY segment id in the current transcript — deliberately
    not label_segments()'s narrower valid_ids (the text-bearing population that
    scoring runs over). That same reindex-by-time backfill leaves plenty of real
    ids carrying empty text, and a gold boundary landing on one of those indexes
    into the current transcript just fine. Narrowing this set to text-bearing
    ids mis-reports those meetings as stale — measured 2026-08-06, it skipped
    48 of the 149-meeting corpus that were perfectly scoreable.

    Membership is not sufficient on its own: end_segment < start_segment names two
    perfectly real ids while describing no range at all. _full_section_transcript
    returns "" for it, and label_segments() skips it outright — so an inverted gold
    section does not fail loudly, it just quietly drops out of the gold labels and
    leaves the meeting scored against an incomplete denominator. Rejecting here
    turns that into a visible skip. Both classifier paths in src/summarize.py now
    clamp inverted ranges before summarizing, so this should only ever fire on gold
    generated before that guard (measured 2026-08-06: one meeting corpus-wide,
    2026-06-24-cd1-republican-primary-debate, since repaired).
    """
    if not gold_sections:
        return False, "no gold sections"
    for sec in gold_sections:
        start, end = sec.get("start_segment"), sec.get("end_segment")
        if start is None or end is None:
            return False, "gold section missing start_segment/end_segment"
        if start not in all_segment_ids or end not in all_segment_ids:
            return False, (
                f"gold segment range [{start},{end}] outside this transcript's "
                "current segment ids (stale — likely un-republished after a "
                "segment-renumbering backfill)"
            )
        if end < start:
            return False, (
                f"gold segment range [{start},{end}] is inverted (end_segment "
                "below start_segment) — it describes no segments, so the section "
                "would silently vanish from the gold labels"
            )
    return True, ""


def section_boundaries(sections: list[dict], ordered_ids: list) -> list:
    """Interior section starts, as POSITIONS in `ordered_ids` (the sorted scored
    segment population) rather than raw segment ids.

    Positions, not ids, because "how far off was this boundary" only means
    something in units of segments that were actually scored — two ids can be
    numerically adjacent with empty-text ids between them.

    A start_segment that isn't itself a scored id (e.g. it lands on an
    empty-text segment) snaps FORWARD to the next scored segment, which is
    where that section's scored content actually begins.

    Position 0 is excluded: every segmentation starts at the beginning, so
    counting it would award every model one free correct boundary and
    compress the metric's range. Positions at or past the end are dropped —
    they describe no scored content. Malformed sections are skipped, matching
    label_segments()'s tolerance.
    """
    n = len(ordered_ids)
    out = set()
    for sec in sections:
        try:
            start = int(sec["start_segment"])
        except (KeyError, TypeError, ValueError):
            continue
        pos = bisect_left(ordered_ids, start)
        if 0 < pos < n:
            out.add(pos)
    return sorted(out)


def boundary_counts(
    gold_boundaries: list,
    candidate_boundaries: list,
    tolerance: int = DEFAULT_BOUNDARY_TOLERANCE,
) -> tuple:
    """(matched, n_gold, n_candidate) under one-to-one matching within
    `tolerance` positions.

    One-to-one matters: without it, a model could emit a cluster of boundaries
    around each gold boundary and have every one of them count as a hit,
    turning recall into a reward for over-segmentation. Greedy two-pointer over
    both sorted sequences — each gold boundary is consumed by at most one
    candidate and vice versa.
    """
    gold = sorted(gold_boundaries)
    cand = sorted(candidate_boundaries)
    matched = i = j = 0
    while i < len(gold) and j < len(cand):
        if abs(gold[i] - cand[j]) <= tolerance:
            matched += 1
            i += 1
            j += 1
        elif gold[i] < cand[j]:
            i += 1
        else:
            j += 1
    return matched, len(gold), len(cand)


def _prf(matched: int, n_gold: int, n_cand: int) -> tuple:
    """(precision, recall, f1) from raw counts; None when undefined.

    Both populations empty (a genuinely single-section meeting) is a perfect
    score, not a failure — there were no boundaries to find and none invented.
    """
    if n_gold == 0 and n_cand == 0:
        return 1.0, 1.0, 1.0
    precision = matched / n_cand if n_cand else 0.0
    recall = matched / n_gold if n_gold else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return precision, recall, f1


def score_meeting(
    gold_sections: list[dict],
    candidate_sections: list[dict],
    valid_ids: set,
    parse_failures: int = 0,
    boundary_tolerance: int = DEFAULT_BOUNDARY_TOLERANCE,
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

    # Boundary placement, scored independently of labels. This is the only
    # signal that survives on single-label (interview-kind) meetings, where
    # every section is type "topic" and `agreement` is therefore a constant.
    ordered = sorted(valid_ids)
    gold_b = section_boundaries(gold_sections, ordered)
    cand_b = section_boundaries(candidate_sections, ordered)
    matched, n_gold_b, n_cand_b = boundary_counts(gold_b, cand_b, boundary_tolerance)
    precision, recall, f1 = _prf(matched, n_gold_b, n_cand_b)

    return {
        "n_segments": n,
        "agree": agree,
        "agreement": agreement,
        "gold_sections": len(gold_sections),
        "candidate_sections": len(candidate_sections),
        "section_count_delta": len(candidate_sections) - len(gold_sections),
        "parse_failures": parse_failures,
        "boundary_matched": matched,
        "n_gold_boundaries": n_gold_b,
        "n_candidate_boundaries": n_cand_b,
        "boundary_precision": precision,
        "boundary_recall": recall,
        "boundary_f1": f1,
    }


def aggregate(model: str, meeting_rows: list[dict]) -> dict:
    """Combine per-meeting score_meeting() dicts into one model-level row.

    label_agreement is weighted by segment count (total agreeing segments /
    total scored segments across all meetings), not a plain mean of each
    meeting's agreement — a 300-segment meeting should count more than a
    12-segment one.

    Boundary precision/recall/F1 are micro-averaged the same way (summed
    counts, then one ratio) rather than averaged per meeting, so a 40-boundary
    council meeting outweighs a 2-boundary press conference. They are None when
    no row carries boundary counts — rows produced before this metric existed
    aggregate without crashing.
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

    scored_boundaries = [r for r in meeting_rows if "boundary_matched" in r]
    if scored_boundaries:
        matched = sum(r["boundary_matched"] for r in scored_boundaries)
        n_gold_b = sum(r["n_gold_boundaries"] for r in scored_boundaries)
        n_cand_b = sum(r["n_candidate_boundaries"] for r in scored_boundaries)
        b_precision, b_recall, b_f1 = _prf(matched, n_gold_b, n_cand_b)
    else:
        matched = n_gold_b = n_cand_b = 0
        b_precision = b_recall = b_f1 = None

    return {
        "model": model,
        "meetings": n_meetings,
        "segments": total_segments,
        "label_agreement": agreement,
        "avg_section_count_delta": avg_delta,
        "parse_failures": parse_failures,
        "boundary_matched": matched,
        "n_gold_boundaries": n_gold_b,
        "n_candidate_boundaries": n_cand_b,
        "boundary_precision": b_precision,
        "boundary_recall": b_recall,
        "boundary_f1": b_f1,
    }
