"""Pure scoring logic for the Layer-3 speaker-ID eval harness.

Kept separate from the CLI (scripts/eval_speaker_id.py) so it is unit-testable
without touching the filesystem or any model API.
"""
from __future__ import annotations

import difflib
import re
from typing import Optional

_HONORIFICS = {
    "mr", "mrs", "ms", "dr", "rep", "sen", "senator", "representative",
    "president", "chair", "chairman", "chairwoman", "councilmember", "mayor",
    "the", "hon", "gov", "governor", "speaker",
}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _surname(name: str) -> str:
    toks = [t for t in _norm(name).split() if len(t) >= 2 and t not in _HONORIFICS]
    return toks[-1] if toks else ""


def is_null_gold(name: Optional[str]) -> bool:
    """True when the gold/predicted label means 'no real name'."""
    if not name or not str(name).strip():
        return True
    return "unidentified" in str(name).lower()


def _name_match(gold: str, pred: str, fuzzy: float = 0.85) -> bool:
    g, p = _surname(gold), _surname(pred)
    if not g or not p:
        return False
    return g == p or difflib.SequenceMatcher(None, g, p).ratio() >= fuzzy


def classify(gold_name: Optional[str], predicted_name: Optional[str]) -> str:
    """One of: correct | safe_null | hallucination | miss | wrong."""
    gold_null = is_null_gold(gold_name)
    pred_null = is_null_gold(predicted_name)
    if gold_null and pred_null:
        return "safe_null"
    if gold_null and not pred_null:
        return "hallucination"
    if not gold_null and pred_null:
        return "miss"
    return "correct" if _name_match(gold_name, predicted_name) else "wrong"


def summarize(model: str, outcomes: list[str]) -> dict:
    """Aggregate outcome labels into counts + accuracy for one model.

    accuracy = (correct + safe_null) / n : credit for both right names and
    correctly abstaining.
    """
    n = len(outcomes)
    counts = {k: outcomes.count(k) for k in
              ("correct", "safe_null", "hallucination", "miss", "wrong")}
    accuracy = (counts["correct"] + counts["safe_null"]) / n if n else 0.0
    return {"model": model, "n": n, "accuracy": round(accuracy, 3), **counts}
