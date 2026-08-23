"""Pure scoring for the discovery classifier eval (no filesystem, no network)."""
from __future__ import annotations

from collections import Counter

from src.discovery.models import Verdict

OUTCOMES = ("true_positive", "true_negative", "false_positive",
            "false_negative", "parse_failure")


def classify_outcome(gold_relevant: bool, verdict: Verdict) -> str:
    if verdict.rejected_reason is not None:
        return "parse_failure"
    if verdict.relevant and gold_relevant:
        return "true_positive"
    if verdict.relevant and not gold_relevant:
        return "false_positive"
    if not verdict.relevant and gold_relevant:
        return "false_negative"
    return "true_negative"


def summarize(model: str, outcomes: list) -> dict:
    counts = Counter(outcomes)
    tp = counts["true_positive"]
    fn = counts["false_negative"]
    fp = counts["false_positive"]
    out = {"model": model, "n": len(outcomes)}
    out.update({name: counts[name] for name in OUTCOMES})
    out["recall"] = tp / (tp + fn) if (tp + fn) else None
    out["precision"] = tp / (tp + fp) if (tp + fp) else None
    return out


def calibration(pairs: list) -> dict:
    """pairs = [(gold_relevant, Verdict)]. Brier-scores the probability the
    model implicitly assigns to 'relevant' (confidence when it said relevant,
    1-confidence when it said not). Parse failures are excluded — they are
    already counted by classify_outcome."""
    scored = [(gold, (v.confidence if v.relevant else 1.0 - v.confidence))
              for gold, v in pairs if v.rejected_reason is None]
    if not scored:
        return {"n": 0, "brier": None, "buckets": []}
    brier = sum((p - (1.0 if gold else 0.0)) ** 2 for gold, p in scored) / len(scored)
    buckets = []
    for i in range(5):
        lo, hi = i / 5, (i + 1) / 5
        in_bucket = [(gold, p) for gold, p in scored
                     if lo <= p < hi or (hi == 1.0 and p == 1.0)]
        if in_bucket:
            buckets.append({
                "range": f"{lo:.1f}–{hi:.1f}", "n": len(in_bucket),
                "predicted": sum(p for _, p in in_bucket) / len(in_bucket),
                "actual": sum(1 for gold, _ in in_bucket if gold) / len(in_bucket),
            })
    return {"n": len(scored), "brier": brier, "buckets": buckets}


def tier_accuracy(pairs: list) -> dict:
    """pairs = [(expected_tier, Verdict)]. Non-gating ride-along: measures the
    tier ladder judgment on the subset of examples that carry an expected_tier
    label (unlabeled and parse-failure examples are skipped; a labeled example
    where the model returned no tier counts as wrong)."""
    scored = [(exp, v.source_tier_guess) for exp, v in pairs
              if exp is not None and v.rejected_reason is None]
    if not scored:
        return {"n": 0, "correct": 0, "accuracy": None}
    correct = sum(1 for exp, got in scored if got == exp)
    return {"n": len(scored), "correct": correct,
            "accuracy": correct / len(scored)}
