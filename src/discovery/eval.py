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
