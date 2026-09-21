#!/usr/bin/env python3
# scripts/judge_ab.py
"""Pure metric functions for the evidence judge A/B report.

No I/O, no LLM calls, no DB access — just plain dict/list math over rows
already loaded by the harness (Task 3). A ``row`` is a dict:

    {id, human_status, review_reason, arm, mechanism, disposition,
     parse_ok, latency}

Convention for empty-group means: when a group has zero rows, its mean
(and any gap derived from it) is ``None`` rather than ``0.0``. A count of
zero items is not evidence of "zero mechanism score" — treating it as
0.0 would silently manufacture a (likely large, misleading) gap against
a populated group. Callers that need a numeric fallback for display
should coerce ``None`` themselves.
"""

from itertools import combinations
from statistics import mean


def _is_judge_relevant(row):
    """Rows the judge A/B cares about: accepted, or goal-only rejected."""
    if row["human_status"] == "accepted":
        return True
    return row["human_status"] == "rejected" and row["review_reason"] == "goal-only"


def mechanism_separation(rows):
    """Mean `mechanism` on accepted rows vs goal-only-rejected rows.

    Returns {"accept_mean", "goalonly_mean", "gap"}. An empty group's
    mean is None; if either mean is None, gap is also None (see module
    docstring for the empty-group convention).
    """
    accepted = [r["mechanism"] for r in rows if r["human_status"] == "accepted"]
    goalonly = [
        r["mechanism"]
        for r in rows
        if r["human_status"] == "rejected" and r["review_reason"] == "goal-only"
    ]

    accept_mean = mean(accepted) if accepted else None
    goalonly_mean = mean(goalonly) if goalonly else None
    gap = (
        accept_mean - goalonly_mean
        if accept_mean is not None and goalonly_mean is not None
        else None
    )

    return {"accept_mean": accept_mean, "goalonly_mean": goalonly_mean, "gap": gap}


def human_agreement(rows):
    """Agreement between judge disposition and human verdict.

    Restricted to the judge-relevant subset (accepted, or goal-only
    rejected). A row agrees when green<->accepted or flagged<->rejected.
    Returns {"n", "agree", "rate"}; rate is 0.0 when n is 0.
    """
    relevant = [r for r in rows if _is_judge_relevant(r)]
    n = len(relevant)
    agree = 0
    for r in relevant:
        if r["disposition"] == "green" and r["human_status"] == "accepted":
            agree += 1
        elif r["disposition"] == "flagged" and r["human_status"] == "rejected":
            agree += 1
    rate = agree / n if n else 0.0
    return {"n": n, "agree": agree, "rate": rate}


def parse_error_rate(rows):
    """Fraction of rows with parse_ok == False. Empty input -> 0.0."""
    if not rows:
        return 0.0
    failed = sum(1 for r in rows if r["parse_ok"] is False)
    return failed / len(rows)


def _dispositions_by_id(rows_by_arm):
    """{id: {arm: disposition}} across all arms."""
    by_id = {}
    for arm, rows in rows_by_arm.items():
        for r in rows:
            by_id.setdefault(r["id"], {})[arm] = r["disposition"]
    return by_id


def inter_judge_agreement(rows_by_arm):
    """Pairwise fraction of ids where two arms agree on `disposition`.

    Returns {"armA|armB": rate} for every unordered pair of arms present
    in rows_by_arm, computed over ids both arms scored.
    """
    by_id = _dispositions_by_id(rows_by_arm)
    arms = sorted(rows_by_arm.keys())
    result = {}
    for arm_a, arm_b in combinations(arms, 2):
        shared_ids = [
            i for i, dispositions in by_id.items()
            if arm_a in dispositions and arm_b in dispositions
        ]
        if shared_ids:
            agree = sum(
                1 for i in shared_ids
                if by_id[i][arm_a] == by_id[i][arm_b]
            )
            rate = agree / len(shared_ids)
        else:
            rate = 0.0
        result[f"{arm_a}|{arm_b}"] = rate
    return result


def divergent_ids(rows_by_arm):
    """ids where not all arms that scored it agree on `disposition`."""
    by_id = _dispositions_by_id(rows_by_arm)
    return sorted(
        i for i, dispositions in by_id.items()
        if len(set(dispositions.values())) > 1
    )
